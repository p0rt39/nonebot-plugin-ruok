"""System metrics, bot info, WS status, plugin inventory, and aggregation."""

from __future__ import annotations

import sys
import json
import time
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime, timezone

import nonebot
from nonebot import get_driver

from ..config import ScopedConfig
from .modules import list_modules
from .sessions import list_sessions
from ..protocol import (
    CheckResult,
    MetricPoint,
    ProcessInfo,
    ModuleStatus,
    StatusResult,
    ProcessSnapshot,
    AggregatedStatus,
    PluginHealthInfo,
    BotConnectionStatus,
    FastMetricsSnapshot,
)

# ────────────────────────────────
# 1. System metrics (psutil)
# ────────────────────────────────


async def _collect_system_metrics(config: ScopedConfig) -> list[CheckResult]:
    """Collect CPU / memory / swap / disk / network metrics."""
    import psutil

    results: list[CheckResult] = []

    # CPU
    try:
        psutil.cpu_percent()
        await asyncio.sleep(0.5)
        cpu_pct = psutil.cpu_percent()
        per_core = psutil.cpu_percent(percpu=True)
        results.append(
            CheckResult(
                name="cpu",
                status="healthy",
                message=f"Total {cpu_pct:.1f}%",
                details={"total_percent": cpu_pct, "per_core": per_core},
            )
        )
    except (psutil.AccessDenied, OSError) as exc:
        results.append(CheckResult(name="cpu", status="unknown", error=str(exc)))

    # Memory
    try:
        mem = psutil.virtual_memory()
        results.append(
            CheckResult(
                name="memory",
                status="healthy" if mem.percent < 90 else "degraded",
                message=(
                    f"{mem.percent:.1f}% used "
                    f"({_bytes_str(mem.used)} / {_bytes_str(mem.total)})"
                ),
                details={
                    "total": mem.total,
                    "available": mem.available,
                    "used": mem.used,
                    "percent": mem.percent,
                },
            )
        )
    except (psutil.AccessDenied, OSError) as exc:
        results.append(CheckResult(name="memory", status="unknown", error=str(exc)))

    # Swap
    try:
        swap = psutil.swap_memory()
        if swap.total > 0:
            results.append(
                CheckResult(
                    name="swap",
                    status="healthy",
                    message=f"{swap.percent:.1f}% used",
                    details={
                        "total": swap.total,
                        "used": swap.used,
                        "percent": swap.percent,
                    },
                )
            )
    except (psutil.AccessDenied, OSError, NotImplementedError):
        pass  # Swap info is optional / not supported on all platforms

    # Disk
    try:
        disk_info: dict[str, dict[str, Any]] = {}
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disk_info[part.mountpoint] = {
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                    "fstype": getattr(part, "fstype", "") or "",
                }
            except (OSError, PermissionError, psutil.AccessDenied):
                continue
        worst = max((d["percent"] for d in disk_info.values()), default=0)
        results.append(
            CheckResult(
                name="disk",
                status="healthy" if worst < 90 else "degraded",
                message=f"{len(disk_info)} partition(s), max {worst:.1f}%",
                details=disk_info,
            )
        )
    except (psutil.AccessDenied, OSError) as exc:
        results.append(CheckResult(name="disk", status="unknown", error=str(exc)))

    # Network
    try:
        net = psutil.net_io_counters()
        results.append(
            CheckResult(
                name="network",
                status="healthy",
                details={
                    "bytes_sent": net.bytes_sent,
                    "bytes_recv": net.bytes_recv,
                    "packets_sent": net.packets_sent,
                    "packets_recv": net.packets_recv,
                },
            )
        )
    except (psutil.AccessDenied, OSError):
        pass

    return results


def _bytes_str(n: float) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ────────────────────────────────
# 2. Bot info
# ────────────────────────────────


def _collect_bot_info() -> list[CheckResult]:
    """Collect version / uptime / adapter info."""
    results: list[CheckResult] = []

    # Versions
    try:
        import platform

        results.append(
            CheckResult(
                name="versions",
                status="healthy",
                details={
                    "nonebot": nonebot.__version__,
                    "python": sys.version,
                    "os": platform.platform(),
                },
            )
        )
    except (OSError, ImportError, RuntimeError) as exc:
        results.append(CheckResult(name="versions", status="unknown", error=str(exc)))

    # Adapters
    try:
        driver = get_driver()
        adapter_names = [
            a.get_name() for a in getattr(driver, "_adapters", {}).values()
        ]
        results.append(
            CheckResult(
                name="adapters",
                status="healthy",
                details={"adapters": adapter_names},
            )
        )
    except (AttributeError, RuntimeError) as exc:
        results.append(CheckResult(name="adapters", status="unknown", error=str(exc)))

    return results


# ────────────────────────────────
# 3. WS connection status
# ────────────────────────────────

_connection_history: dict[str, BotConnectionStatus] = {}


async def _collect_connection_status(
    config: ScopedConfig,
) -> list[BotConnectionStatus]:
    """Collect WS connection health for all known bots."""
    bots = nonebot.get_bots()
    result: list[BotConnectionStatus] = []

    online_ids = set(bots.keys())

    for self_id, bot in bots.items():
        entry = _connection_history.get(self_id)
        if entry is None:
            entry = BotConnectionStatus(
                self_id=self_id,
                adapter=bot.type,
                connected=True,
                connected_at=datetime.now(timezone.utc),
            )
            _connection_history[self_id] = entry
        else:
            entry.connected = True

        # OneBot WS-level check
        try:
            for adapter in getattr(get_driver(), "_adapters", {}).values():
                conns = getattr(adapter, "connections", None)
                if conns is not None and self_id in conns:
                    entry.ws_closed = conns[self_id].closed
                    break
        except (AttributeError, KeyError):
            pass

        # Deep e2e check
        if config.enable_deep_ws_check:
            try:
                t0 = time.monotonic()
                await asyncio.wait_for(
                    bot.get_status(), timeout=config.ws_deep_check_timeout
                )
                entry.latency_ms = (time.monotonic() - t0) * 1000
            except asyncio.TimeoutError:
                entry.latency_ms = None
                entry.error = "get_status() timed out"
            except Exception as exc:
                # Broad catch: Console adapter (ApiNotAvailable),
                # network errors, etc. — all are expected failures.
                entry.latency_ms = None
                entry.error = f"{type(exc).__name__}: {exc}"

        result.append(entry)

    # Mark disconnected bots
    for self_id, entry in _connection_history.items():
        if self_id not in online_ids and entry.connected:
            entry.connected = False
            entry.disconnected_at = datetime.now(timezone.utc)
        if self_id not in online_ids:
            result.append(entry)

    return result


# ────────────────────────────────
# 4. Plugin inventory (L1-L3)
# ────────────────────────────────


def _collect_plugin_inventory(*, skip_ruok: bool = True) -> list[PluginHealthInfo]:
    """Collect loaded / metadata / matcher info for all plugins (L1-L3).

    Set *skip_ruok=False* to include the RuOK plugin itself (for module
    association / detail pages).
    """
    plugins: list[PluginHealthInfo] = []

    for plugin in nonebot.get_loaded_plugins():
        if skip_ruok and plugin.id_ == "nonebot_plugin_ruok":
            continue

        loaded = plugin.module is not None

        meta = None
        if plugin.metadata:
            meta = {
                "name": plugin.metadata.name,
                "type": plugin.metadata.type,
                "description": plugin.metadata.description,
            }

        matcher_infos: list[dict[str, Any]] = []
        for m_cls in plugin.matcher:
            matcher_infos.append(
                {
                    "type": m_cls.type,
                    "priority": m_cls.priority,
                    "block": m_cls.block,
                    "handler_count": len(m_cls.handlers),
                }
            )

        health_hint = "matchers_ok"
        if not loaded:
            health_hint = "load_failed"
        elif not matcher_infos:
            health_hint = "no_matchers"

        plugins.append(
            PluginHealthInfo(
                name=plugin.name,
                loaded=loaded,
                metadata=meta,
                matchers=matcher_infos,
                health_hint=health_hint,
            )
        )

    return plugins


# ────────────────────────────────
# 5. Aggregation
# ────────────────────────────────

_startup_time: float | None = None


async def collect_all_statuses(
    config: ScopedConfig, data_dir: Path
) -> AggregatedStatus:
    """Gather all health data into a single AggregatedStatus."""
    global _startup_time
    if _startup_time is None:
        _startup_time = time.time()

    # System + bot info
    sys_results = await _collect_system_metrics(config)
    bot_results = _collect_bot_info()

    # Uptime
    uptime_sec = time.time() - _startup_time
    sys_results.append(
        CheckResult(name="uptime", status="healthy", details={"seconds": uptime_sec})
    )

    all_checks = sys_results + bot_results

    worst_status: Any = "healthy"
    for c in all_checks:
        if c.status == "unhealthy":
            worst_status = "unhealthy"
        elif c.status == "degraded" and worst_status != "unhealthy":
            worst_status = "degraded"
        elif c.status == "unknown" and worst_status == "healthy":
            worst_status = "unknown"

    bot_status = StatusResult(
        plugin_name="builtin",
        plugin_type="builtin",
        status=worst_status,
        checks=all_checks,
    )

    connections: list[BotConnectionStatus] = []
    try:
        connections = await _collect_connection_status(config)
    except Exception:
        # Connection check is best-effort; failure → empty list
        pass
    plugins = _collect_plugin_inventory()

    # Derive overall — modules > connections > system health
    overall: ModuleStatus = "available"

    # 1. Check module status (most important)
    try:
        modules = list_modules(data_dir, config)
    except (json.JSONDecodeError, OSError, ValueError):
        modules = []
    if any(m.status == "unavailable" for m in modules):
        overall = "unavailable"
    elif any(m.status == "degraded" for m in modules):
        overall = "degraded"

    # 2. Check connections (if no module issue)
    if overall == "available":
        if any(not c.connected for c in connections):
            overall = (
                "unavailable"
                if all(not c.connected for c in connections)
                else "degraded"
            )

    # 3. Check system health (if still ok)
    if overall == "available" and worst_status in ("unhealthy", "degraded"):
        overall = "degraded"

    result = AggregatedStatus(
        overall=overall,
        bot=bot_status,
        connections=connections,
        plugins=plugins,
    )

    # ── Append time-series metric point ──
    try:
        from ..collector import MetricsStore  # late import: avoid circular

        cpu_pct = _extract_cpu_percent(sys_results)
        mem_pct = _extract_memory_percent(sys_results)
        disk_pct = _extract_disk_percent(sys_results)
        sessions = list_sessions(data_dir)
        MetricsStore.append(
            data_dir,
            MetricPoint(
                ts=datetime.now(timezone.utc).isoformat(),
                cpu_percent=cpu_pct,
                memory_percent=mem_pct,
                disk_percent=disk_pct,
                sessions_total=len(sessions),
                sessions_pending=sum(1 for s in sessions if s.status == "pending"),
                sessions_unsolved=sum(1 for s in sessions if s.status == "unsolved"),
                connections_total=len(connections),
                connections_online=sum(1 for c in connections if c.connected),
            ),
            retention_days=config.metrics_retention_days,
        )
    except (json.JSONDecodeError, OSError, ValueError):
        pass  # Metrics are best-effort

    return result


def _extract_cpu_percent(checks: list[CheckResult]) -> float | None:
    for c in checks:
        if c.name == "cpu" and c.details:
            return c.details.get("total_percent")
    return None


def _extract_memory_percent(checks: list[CheckResult]) -> float | None:
    for c in checks:
        if c.name == "memory" and c.details:
            return c.details.get("percent")
    return None


def _extract_disk_percent(checks: list[CheckResult]) -> float | None:
    for c in checks:
        if c.name == "disk" and c.details:
            return max(
                (
                    d.get("percent", 0)
                    for d in c.details.values()
                    if isinstance(d, dict)
                ),
                default=None,
            )
    return None


# ────────────────────────────────
# 5b. Fast metrics (~3s) for gauges
# ────────────────────────────────


async def collect_fast_metrics() -> FastMetricsSnapshot:
    """Lightweight snapshot for real-time gauges (no disk/network/plugins)."""
    import os

    def _get() -> dict[str, Any]:
        import psutil

        cpu_overall = psutil.cpu_percent(interval=1.0)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        load_1m = load_5m = load_15m = None
        if hasattr(os, "getloadavg"):
            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except OSError:
                pass

        proc_count = len(psutil.pids())

        boot_time = psutil.boot_time()
        bot_create_time = psutil.Process().create_time()

        bot_proc = psutil.Process()
        bot_mem = bot_proc.memory_info()
        bot_rss = bot_mem.rss

        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        cpu_temp = entries[0].current
                        break
        except (AttributeError, NotImplementedError, OSError):
            pass

        return FastMetricsSnapshot(
            cpu_percent=cpu_overall,
            cpu_per_core=cpu_per_core if len(cpu_per_core) > 1 else [],
            memory_percent=mem.percent,
            memory_used=mem.used,
            memory_total=mem.total,
            swap_percent=swap.percent,
            swap_used=swap.used,
            swap_total=swap.total,
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
            process_count=proc_count,
            uptime_seconds=int(time.time() - (_startup_time or time.time())),
            timestamp=datetime.now(timezone.utc).isoformat(),
            boot_time_epoch=boot_time,
            bot_process_create_time=bot_create_time,
            bot_rss_bytes=bot_rss,
            cpu_temp=cpu_temp,
        )

    return await asyncio.to_thread(_get)


async def collect_process_snapshot() -> ProcessSnapshot:
    """Collect Bot process details + top 5 system processes by CPU."""

    def _get() -> ProcessSnapshot:
        import psutil

        bot_proc = psutil.Process()
        bot_mem = bot_proc.memory_info()
        bot_rss = bot_mem.rss
        bot_vms = bot_mem.vms
        bot_threads = bot_proc.num_threads()
        bot_cpu = bot_proc.cpu_percent()

        procs = []
        for p in psutil.process_iter(["name", "pid", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                name = info["name"] or ""
                pid = info["pid"] or 0
                if pid == 0:
                    continue
                mem = info["memory_info"]
                rss = mem.rss if mem else 0
                procs.append(
                    ProcessInfo(
                        name=name,
                        pid=info["pid"] or 0,
                        cpu_percent=info["cpu_percent"] or 0.0,
                        mem_rss=rss,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x.cpu_percent, reverse=True)
        return ProcessSnapshot(
            bot_rss=bot_rss,
            bot_vms=bot_vms,
            bot_threads=bot_threads,
            bot_cpu_percent=bot_cpu,
            top_processes=procs[:5],
        )

    return await asyncio.to_thread(_get)
