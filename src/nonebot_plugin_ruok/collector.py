"""Core engine: health collection, LogMonitor, session storage, module derivation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import nonebot
from nonebot import get_driver, logger

from .config import ScopedConfig
from .protocol import (
    AggregatedStatus,
    BotConnectionStatus,
    CheckResult,
    DiskIORate,
    FastMetricsSnapshot,
    MetricPoint,
    ModuleDefinition,
    ModuleStatus,
    NetworkRate,
    Occurrence,
    PluginHealthInfo,
    ProcessInfo,
    ProcessSnapshot,
    ReporterInfo,
    Session,
    SessionStats,
    StatusResult,
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
    except Exception as exc:
        results.append(CheckResult(name="cpu", status="unknown", error=str(exc)))

    # Memory
    try:
        mem = psutil.virtual_memory()
        results.append(
            CheckResult(
                name="memory",
                status="healthy" if mem.percent < 90 else "degraded",
                message=f"{mem.percent:.1f}% used ({_bytes_str(mem.used)} / {_bytes_str(mem.total)})",
                details={
                    "total": mem.total,
                    "available": mem.available,
                    "used": mem.used,
                    "percent": mem.percent,
                },
            )
        )
    except Exception as exc:
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
                    details={"total": swap.total, "used": swap.used, "percent": swap.percent},
                )
            )
    except Exception:
        pass  # Swap info is optional

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
                }
            except Exception:
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
    except Exception as exc:
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
    except Exception:
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
    except Exception as exc:
        results.append(CheckResult(name="versions", status="unknown", error=str(exc)))

    # Adapters
    try:
        driver = get_driver()
        adapter_names = [a.get_name() for a in getattr(driver, "_adapters", {}).values()]
        results.append(
            CheckResult(name="adapters", status="healthy", details={"adapters": adapter_names})
        )
    except Exception as exc:
        results.append(CheckResult(name="adapters", status="unknown", error=str(exc)))

    return results


# ────────────────────────────────
# 3. WS connection status
# ────────────────────────────────

# connection history recorded by WS hooks in __init__.py
_connection_history: dict[str, BotConnectionStatus] = {}


async def _collect_connection_status(config: ScopedConfig) -> list[BotConnectionStatus]:
    """Collect WS connection health for all known bots."""
    bots = nonebot.get_bots()
    result: list[BotConnectionStatus] = []

    online_ids = set(bots.keys())

    # Update / create entries for online bots
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

        # OneBot WS-level check (check all adapters for .connections attr)
        try:
            for adapter in getattr(get_driver(), "_adapters", {}).values():
                conns = getattr(adapter, "connections", None)
                if conns is not None and self_id in conns:
                    entry.ws_closed = conns[self_id].closed
                    break
        except Exception:
            pass  # Not using OneBot or adapter doesn't expose connections

        # Deep e2e check
        if config.enable_deep_ws_check:
            try:
                t0 = time.monotonic()
                await asyncio.wait_for(bot.get_status(), timeout=config.ws_deep_check_timeout)
                entry.latency_ms = (time.monotonic() - t0) * 1000
            except asyncio.TimeoutError:
                entry.latency_ms = None
                entry.error = "get_status() timed out"
            except Exception as exc:
                entry.latency_ms = None
                entry.error = str(exc)

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


def _collect_plugin_inventory() -> list[PluginHealthInfo]:
    """Collect loaded / metadata / matcher info for all plugins (L1-L3)."""
    plugins: list[PluginHealthInfo] = []

    for plugin in nonebot.get_loaded_plugins():
        if plugin.id_ == "nonebot_plugin_ruok":
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

# set by __init__.py at startup
_startup_time: float | None = None


async def collect_all_statuses(config: ScopedConfig, data_dir: Path) -> AggregatedStatus:
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

    connections = await _collect_connection_status(config)
    plugins = _collect_plugin_inventory()

    # Derive overall — modules > connections > system health
    overall: ModuleStatus = "available"

    # 1. Check module status (most important)
    try:
        modules = list_modules(data_dir, config)
    except Exception:
        modules = []
    if any(m.status == "unavailable" for m in modules):
        overall = "unavailable"
    elif any(m.status == "degraded" for m in modules):
        overall = "degraded"

    # 2. Check connections (if no module issue)
    if overall == "available":
        if any(not c.connected for c in connections):
            overall = (
                "unavailable" if all(not c.connected for c in connections) else "degraded"
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
    except Exception:
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
            return max((d.get("percent", 0) for d in c.details.values() if isinstance(d, dict)), default=None)
    return None


# ────────────────────────────────
# 5b. Fast metrics (~3s) for gauges
# ────────────────────────────────


async def collect_fast_metrics() -> FastMetricsSnapshot:
    """Lightweight snapshot for real-time gauges (no disk/network/plugins)."""
    import os

    def _get():
        import psutil

        # interval=1.0 gives stable readings matching Task Manager (~1s refresh)
        # Step 1: system-wide CPU (blocks 1s, establishes baseline)
        cpu_overall = psutil.cpu_percent(interval=1.0)
        # Step 2: per-core CPU (returns immediately, uses baseline from step 1)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Load average (Unix) or emulate with CPU on Windows
        load_1m = load_5m = load_15m = None
        if hasattr(os, "getloadavg"):
            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except OSError:
                pass

        proc_count = len(psutil.pids())

        # Boot time / bot process create time
        boot_time = psutil.boot_time()
        bot_create_time = psutil.Process().create_time()

        # Bot process memory
        bot_proc = psutil.Process()
        bot_mem = bot_proc.memory_info()
        bot_rss = bot_mem.rss

        # CPU temperature (platform-dependent)
        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        cpu_temp = entries[0].current
                        break
        except Exception:
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

        # Top 5 processes by CPU (skip PID 0 — Windows System Idle Process)
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
                procs.append(ProcessInfo(
                    name=name,
                    pid=info["pid"] or 0,
                    cpu_percent=info["cpu_percent"] or 0.0,
                    mem_rss=rss,
                ))
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


# ────────────────────────────────
# 5c. Network rate tracker
# ────────────────────────────────


class NetworkRateTracker:
    """Track per-second network transfer rate via delta of io counters."""

    def __init__(self) -> None:
        self._prev: dict[str, float] = {}
        self._prev_time: float = 0.0

    def get_rate(self) -> NetworkRate:
        """Return current bytes/sec rate, or zeros on first call."""
        import psutil

        now = time.time()
        try:
            net = psutil.net_io_counters()
        except Exception:
            return NetworkRate()

        cur = {
            "bs": float(net.bytes_sent),
            "br": float(net.bytes_recv),
            "ps": float(net.packets_sent),
            "pr": float(net.packets_recv),
        }

        if not self._prev or self._prev_time == 0.0:
            self._prev = cur
            self._prev_time = now
            return NetworkRate()

        elapsed = now - self._prev_time
        if elapsed <= 0:
            return NetworkRate()

        rate = NetworkRate(
            bytes_sent_per_sec=(cur["bs"] - self._prev["bs"]) / elapsed,
            bytes_recv_per_sec=(cur["br"] - self._prev["br"]) / elapsed,
            packets_sent_per_sec=(cur["ps"] - self._prev["ps"]) / elapsed,
            packets_recv_per_sec=(cur["pr"] - self._prev["pr"]) / elapsed,
        )
        self._prev = cur
        self._prev_time = now
        return rate


# Singleton
_network_tracker = NetworkRateTracker()


# ────────────────────────────────
# 5d. Disk I/O rate tracker
# ────────────────────────────────


class DiskRateTracker:
    """Track per-second disk I/O rate via delta of io counters (per-disk)."""

    def __init__(self) -> None:
        self._prev: dict[str, dict[str, float]] = {}
        self._prev_time: float = 0.0

    def get_rate(self) -> tuple[DiskIORate, dict[str, DiskIORate]]:
        """Return (aggregated_rate, per_disk_rates) or zeros on first call."""
        import psutil

        now = time.time()
        try:
            per_disk = psutil.disk_io_counters(perdisk=True)
        except Exception:
            return DiskIORate(), {}

        if not per_disk:
            return DiskIORate(), {}

        cur: dict[str, dict[str, float]] = {}
        for name, io in per_disk.items():
            cur[name] = {
                "rb": float(io.read_bytes),
                "wb": float(io.write_bytes),
                "rc": float(io.read_count),
                "wc": float(io.write_count),
            }

        if not self._prev or self._prev_time == 0.0:
            self._prev = cur
            self._prev_time = now
            return DiskIORate(), {}

        elapsed = now - self._prev_time
        if elapsed <= 0:
            return DiskIORate(), {}

        agg = DiskIORate()
        per_disk_rates: dict[str, DiskIORate] = {}

        for name, c in cur.items():
            p = self._prev.get(name, c)
            dr = DiskIORate(
                read_bytes_per_sec=(c["rb"] - p["rb"]) / elapsed,
                write_bytes_per_sec=(c["wb"] - p["wb"]) / elapsed,
                read_count_per_sec=(c["rc"] - p["rc"]) / elapsed,
                write_count_per_sec=(c["wc"] - p["wc"]) / elapsed,
            )
            per_disk_rates[name] = dr
            agg.read_bytes_per_sec += dr.read_bytes_per_sec
            agg.write_bytes_per_sec += dr.write_bytes_per_sec
            agg.read_count_per_sec += dr.read_count_per_sec
            agg.write_count_per_sec += dr.write_count_per_sec

        self._prev = cur
        self._prev_time = now
        return agg, per_disk_rates


# Singleton
_disk_tracker = DiskRateTracker()


# ────────────────────────────────
# 6. LogMonitor (L5 – loguru sink)
# ────────────────────────────────


class LogMonitor:
    """Intercepts ERROR/CRITICAL logs via loguru sink, creates automatic Sessions."""

    def __init__(self, config: ScopedConfig, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self._handler_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ──

    def start(self) -> None:
        if not self.config.auto_session_enabled:
            return
        from nonebot.log import logger as nb_logger

        self._loop = asyncio.get_running_loop()
        self._handler_id = nb_logger.add(
            _make_log_sink(self.config, self.data_dir, self._loop),
            level="ERROR",
            enqueue=True,
            serialize=True,
        )
        logger.info("RuOK LogMonitor started (level=ERROR)")

    def stop(self) -> None:
        if self._handler_id is not None:
            from nonebot.log import logger as nb_logger

            nb_logger.remove(self._handler_id)
            self._handler_id = None


def _make_log_sink(config: ScopedConfig, data_dir: Path, loop: asyncio.AbstractEventLoop):
    """Create a loguru-compatible sink closure.

    With serialize=True, the sink receives a JSON string.  Session I/O runs
    in the worker thread; notification is dispatched to the main event loop
    via call_soon_threadsafe.
    """

    def _sink(message: str) -> None:
        try:
            record: dict = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return

        level_info: dict = record.get("level", {})
        if not isinstance(level_info, dict):
            return
        level_name: str = level_info.get("name", "")
        if level_name not in ("ERROR", "CRITICAL"):
            return

        plugin_id: str = record["name"]
        msg_text: str = record["message"]
        exception: Any = record.get("exception")
        exception_str = ""
        if isinstance(exception, dict):
            exception_str = exception.get("value", "")

        signature = _make_signature(plugin_id, exception_str, msg_text)

        existing = _find_existing_session(data_dir, signature)
        if existing:
            occurrence = Occurrence(
                record=_record_to_dict(record),
                source="automatic",
            )
            existing.occurrences.append(occurrence)
            existing.last_seen_at = datetime.now(timezone.utc)
            _save_session(data_dir, existing)
            return

        session = Session(
            session_id=_gen_session_id(),
            source="automatic",
            status="pending",
            module_name=plugin_id,
            error_signature=signature,
            reporter=ReporterInfo(type="automatic"),
            description=f"```\n{msg_text}\n{exception_str}\n```",
            occurrences=[
                Occurrence(
                    record=_record_to_dict(record),
                    source="automatic",
                )
            ],
        )
        _save_session(data_dir, session)
        logger.warning(f"RuOK: new session {session.session_id} for {plugin_id}")
        if config.notify_superusers:
            loop.call_soon_threadsafe(_notify_new_session, session)

    return _sink


def _make_signature(plugin: str, exc: str, msg: str) -> str:
    raw = f"{plugin}:{exc}:{msg[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _gen_session_id() -> str:
    return f"ruok-{secrets.token_hex(4)}"


def _record_to_dict(record: dict) -> dict[str, Any]:
    """Extract safe fields from a loguru record (serialized format)."""
    level: dict = record.get("level", {})
    file_info: dict = record.get("file", {})
    return {
        "name": record.get("name"),
        "level": level.get("name", ""),
        "message": record.get("message"),
        "exception": str(record.get("exception")) if record.get("exception") else None,
        "file": file_info.get("name", ""),
        "function": record.get("function"),
        "line": record.get("line"),
        "time": str(record.get("time", "")),
    }


# ────────────────────────────────
# 7. Session storage
# ────────────────────────────────


def _sessions_dir(data_dir: Path) -> Path:
    d = data_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(data_dir: Path, session_id: str) -> Path:
    return _sessions_dir(data_dir) / f"{session_id}.json"


def _save_session(data_dir: Path, session: Session) -> None:
    path = _session_path(data_dir, session.session_id)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def _load_session(data_dir: Path, session_id: str) -> Session | None:
    path = _session_path(data_dir, session_id)
    if not path.exists():
        return None
    return Session.model_validate_json(path.read_text(encoding="utf-8"))


def _find_existing_session(data_dir: Path, signature: str) -> Session | None:
    """Find a session with the same signature that is still pending or unsolved."""
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.error_signature == signature and s.status in ("pending", "unsolved"):
            return s
    return None


# ── public CRUD ──


def create_session(
    data_dir: Path,
    module_name: str,
    description: str,
    reporter: ReporterInfo,
    source: str = "manual",
) -> Session:
    session = Session(
        session_id=_gen_session_id(),
        source=source,  # type: ignore[arg-type]
        status="pending",
        module_name=module_name,
        reporter=reporter,
        description=description,
        occurrences=[
            Occurrence(
                source=source,  # type: ignore[arg-type]
            )
        ],
    )
    _save_session(data_dir, session)
    # Publish event for SSE
    _publish_session_event("created", session)
    return session


def get_session(data_dir: Path, session_id: str) -> Session | None:
    return _load_session(data_dir, session_id)


def list_sessions(
    data_dir: Path,
    status: str | None = None,
    module_name: str | None = None,
    reporter_user_id: str | None = None,
    search: str | None = None,
    first_seen_after: datetime | None = None,
    first_seen_before: datetime | None = None,
    plugin_name: str | None = None,
) -> list[Session]:
    """List sessions with optional advanced filters.

    Args:
        search: Full-text search across session_id, module_name, description,
                reporter user_id, error_signature.
        first_seen_after: Only sessions first seen after this time (UTC).
        first_seen_before: Only sessions first seen before this time.
        plugin_name: Only sessions whose module_name matches a ModuleDefinition
                     that includes this plugin in its ``plugins`` list.
    """
    sessions: list[Session] = []
    statuses = set(s.strip() for s in status.split(",")) if status else None

    # Pre-compute module→plugin mapping if needed
    module_plugin_names: dict[str, set[str]] | None = None
    if plugin_name:
        module_plugin_names = _build_module_plugin_map(data_dir)

    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if statuses and s.status not in statuses:
            continue
        if module_name and s.module_name != module_name:
            continue
        if reporter_user_id and s.reporter.user_id != reporter_user_id:
            continue

        # ── time range ──
        if first_seen_after and s.first_seen_at < first_seen_after:
            continue
        if first_seen_before and s.first_seen_at > first_seen_before:
            continue

        # ── plugin filter ──
        if plugin_name and module_plugin_names:
            allowed_modules = module_plugin_names.get(plugin_name, set())
            if s.module_name not in allowed_modules:
                continue

        # ── full-text search ──
        if search:
            q = search.lower()
            if not _session_matches_search(s, q):
                continue

        sessions.append(s)
    sessions.sort(key=lambda s: s.last_seen_at, reverse=True)
    return sessions


def _build_module_plugin_map(data_dir: Path) -> dict[str, set[str]]:
    """Build mapping: plugin_name → set of module_names that reference it."""
    mapping: dict[str, set[str]] = {}
    modules_path = _modules_path(data_dir)
    if not modules_path.exists():
        return mapping
    try:
        modules_data = json.loads(modules_path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return mapping
    for m in modules_data:
        mod_name = m.get("name", "")
        plugins = m.get("plugins", [])
        for p in plugins:
            mapping.setdefault(p, set()).add(mod_name)
    return mapping


def _session_matches_search(s: Session, q: str) -> bool:
    """Check if *q* appears in any searchable field of *s*."""
    if q in s.session_id.lower():
        return True
    if q in s.module_name.lower():
        return True
    if q in s.description.lower():
        return True
    if s.reporter.user_id and q in s.reporter.user_id.lower():
        return True
    if s.error_signature and q in s.error_signature.lower():
        return True
    return False


def update_session(data_dir: Path, session_id: str, updates: dict[str, Any]) -> Session | None:
    session = _load_session(data_dir, session_id)
    if session is None:
        return None

    allowed = {"status", "developer_notes", "last_seen_at", "link_group"}
    old_status = session.status
    for k, v in updates.items():
        if k in allowed:
            setattr(session, k, v)

    if "status" in updates and updates["status"] in ("solved", "ignored"):
        session.resolved_at = datetime.now(timezone.utc)

    _save_session(data_dir, session)
    # Publish event for SSE if status changed
    if "status" in updates and updates["status"] != old_status:
        _publish_session_event("updated", session, old_status=old_status)
    return session


def _publish_session_event(
    action: str, session: Session, old_status: str | None = None
) -> None:
    """Publish a session lifecycle event to the SSE EventBus (best-effort)."""
    try:
        from .webui.sse import event_bus

        event_bus.publish(
            "session_update",
            {
                "action": action,
                "session_id": session.session_id,
                "module_name": session.module_name,
                "status": session.status,
                "old_status": old_status,
                "last_seen_at": session.last_seen_at.isoformat(),
            },
        )
    except Exception:
        pass  # SSE is best-effort, never crash the collector


def link_sessions(data_dir: Path, session_id_a: str, session_id_b: str) -> bool:
    """Link two sessions into the same link_group (group-based, no cross-linking).

    Scenarios:
    - Both have no group → create new group, both join.
    - One has a group → the other joins that group.
    - Different groups → merge all sessions in group B into group A.
    - Same group → no-op (idempotent).
    """
    sa = _load_session(data_dir, session_id_a)
    sb = _load_session(data_dir, session_id_b)
    if sa is None or sb is None:
        return False
    if sa.session_id == sb.session_id:
        return False

    ga = sa.link_group
    gb = sb.link_group

    if ga is None and gb is None:
        # New group
        gid = f"ruok-grp-{secrets.token_hex(4)}"
        sa.link_group = gid
        sb.link_group = gid
        _save_session(data_dir, sa)
        _save_session(data_dir, sb)
    elif ga and gb is None:
        sb.link_group = ga
        _save_session(data_dir, sb)
    elif ga is None and gb:
        sa.link_group = gb
        _save_session(data_dir, sa)
    elif ga == gb:
        return True  # Already same group
    else:
        # Merge groups: move all gb → ga
        _merge_link_groups(data_dir, gb, ga)

    return True


def unlink_session(data_dir: Path, session_id: str) -> bool:
    """Remove a session from its link_group."""
    s = _load_session(data_dir, session_id)
    if s is None or s.link_group is None:
        return False
    s.link_group = None
    _save_session(data_dir, s)
    return True


def get_linked_sessions(
    data_dir: Path, session_id: str
) -> list[Session]:
    """Return all sessions in the same link_group (excluding self)."""
    s = _load_session(data_dir, session_id)
    if s is None or s.link_group is None:
        return []
    group = s.link_group
    linked: list[Session] = []
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            other = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if other.link_group == group and other.session_id != session_id:
            linked.append(other)
    linked.sort(key=lambda x: x.last_seen_at, reverse=True)
    return linked


def _merge_link_groups(data_dir: Path, from_group: str, to_group: str) -> None:
    """Move all sessions in *from_group* to *to_group*."""
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.link_group == from_group:
            s.link_group = to_group
            _save_session(data_dir, s)


# ────────────────────────────────
# 8. Module management
# ────────────────────────────────


def _modules_path(data_dir: Path) -> Path:
    return data_dir / "modules.json"


def list_modules(data_dir: Path, config: ScopedConfig) -> list[ModuleDefinition]:
    """Return all defined modules with real-time derived status."""
    path = _modules_path(data_dir)
    if not path.exists():
        return []

    modules = [ModuleDefinition.model_validate(m) for m in json.loads(path.read_text("utf-8"))]
    for mod in modules:
        mod.status = derive_module_status(data_dir, mod.name)
    return modules


def get_module(data_dir: Path, config: ScopedConfig, name: str) -> ModuleDefinition | None:
    for m in list_modules(data_dir, config):
        if m.name == name:
            return m
    return None


def upsert_module(data_dir: Path, definition: ModuleDefinition) -> ModuleDefinition:
    path = _modules_path(data_dir)
    modules: list[dict[str, Any]] = []
    if path.exists():
        modules = json.loads(path.read_text("utf-8"))

    existing_idx = next((i for i, m in enumerate(modules) if m["name"] == definition.name), None)
    data = definition.model_dump()
    if existing_idx is not None:
        modules[existing_idx] = data
    else:
        modules.append(data)

    path.write_text(json.dumps(modules, indent=2, ensure_ascii=False), encoding="utf-8")
    return definition


def delete_module(data_dir: Path, name: str) -> bool:
    path = _modules_path(data_dir)
    if not path.exists():
        return False
    modules: list[dict[str, Any]] = json.loads(path.read_text("utf-8"))
    new_modules = [m for m in modules if m["name"] != name]
    if len(new_modules) == len(modules):
        return False
    path.write_text(json.dumps(new_modules, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


# ────────────────────────────────
# 9. Module status derivation
# ────────────────────────────────


def derive_module_status(data_dir: Path, module_name: str) -> ModuleStatus:
    """Real-time module status from its sessions."""
    sessions = list_sessions(data_dir, module_name=module_name)
    if any(s.status == "unsolved" for s in sessions):
        return "unavailable"
    if any(s.status == "pending" for s in sessions):
        return "degraded"
    return "available"


# ────────────────────────────────
# 10. Notification
# ────────────────────────────────


def _notify_new_session(session: Session) -> None:
    """Send private-chat notification to SUPERUSERS about a new session."""
    try:
        bots = nonebot.get_bots()
        if not bots:
            return
        bot = next(iter(bots.values()))
        superusers = get_driver().config.superusers
        if not superusers:
            return

        text = (
            f"🔔 新的异常事件\n"
            f"Session: {session.session_id}\n"
            f"来源: {session.source}\n"
            f"模块: {session.module_name}\n"
            f"描述: {session.description[:200]}\n"
            f"时间: {session.first_seen_at}"
        )
        for uid in superusers:
            try:
                # schedule async send without awaiting (fire-and-forget)
                asyncio.ensure_future(bot.send_private_msg(user_id=int(uid), message=text))
            except Exception:
                logger.warning(f"RuOK: failed to notify superuser {uid}")
    except Exception as exc:
        logger.warning(f"RuOK: notification failed: {exc}")


# ────────────────────────────────
# 11. Time-series Metrics Store
# ────────────────────────────────


class MetricsStore:
    """Time-series metrics storage backed by daily JSON files.

    File layout::

        {data_dir}/metrics/
        ├── 2026-07-05.json
        ├── 2026-07-04.json
        └── ...

    Each file is a JSON array of MetricPoint dicts.
    """

    @staticmethod
    def _metrics_dir(data_dir: Path) -> Path:
        d = data_dir / "metrics"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _today_file(data_dir: Path) -> Path:
        return MetricsStore._metrics_dir(data_dir) / f"{datetime.now(timezone.utc).date().isoformat()}.json"

    @staticmethod
    def append(data_dir: Path, point: MetricPoint, retention_days: int = 7) -> None:
        """Append one MetricPoint to today's file and clean old files."""
        file_path = MetricsStore._today_file(data_dir)
        records: list[dict[str, Any]] = []
        if file_path.exists():
            try:
                records = json.loads(file_path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                records = []
        records.append(point.model_dump(mode="json"))
        file_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        # Clean old files
        MetricsStore._cleanup(data_dir, retention_days)

    @staticmethod
    def query(data_dir: Path, hours: float = 24.0) -> list[MetricPoint]:
        """Return metrics from the last *hours* hours.

        Loads today's file and yesterday's if needed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        metrics_dir = MetricsStore._metrics_dir(data_dir)
        points: list[MetricPoint] = []

        # We only look at the last 2 days of files (today + yesterday)
        today_str = datetime.now(timezone.utc).date().isoformat()
        yesterday_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

        for date_str in (today_str, yesterday_str):
            fpath = metrics_dir / f"{date_str}.json"
            if not fpath.exists():
                continue
            try:
                records = json.loads(fpath.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for rec in records:
                try:
                    pt = MetricPoint.model_validate(rec)
                    ts = datetime.fromisoformat(pt.ts)
                    if ts >= cutoff:
                        points.append(pt)
                except Exception:
                    continue

        points.sort(key=lambda p: p.ts)
        return points

    @staticmethod
    def _cleanup(data_dir: Path, retention_days: int) -> None:
        """Remove metric files older than *retention_days*."""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).date()
        metrics_dir = MetricsStore._metrics_dir(data_dir)
        for f in metrics_dir.glob("*.json"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff_date:
                try:
                    f.unlink()
                except OSError:
                    pass


# ────────────────────────────────
# 12. Session statistics
# ────────────────────────────────


def get_session_stats(data_dir: Path) -> SessionStats:
    """Compute aggregate session statistics."""
    all_sessions = list_sessions(data_dir)
    stats = SessionStats(total=len(all_sessions))
    by_module: dict[str, int] = {}
    for s in all_sessions:
        if s.status == "pending":
            stats.pending += 1
        elif s.status == "unsolved":
            stats.unsolved += 1
        elif s.status == "solved":
            stats.solved += 1
        elif s.status == "ignored":
            stats.ignored += 1
        by_module[s.module_name] = by_module.get(s.module_name, 0) + 1
    stats.by_module = by_module
    return stats
