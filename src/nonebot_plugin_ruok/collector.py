"""Core engine: health collection, LogMonitor, session storage, module derivation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nonebot
from nonebot import get_driver, logger

from .config import ScopedConfig
from .protocol import (
    AggregatedStatus,
    BotConnectionStatus,
    CheckResult,
    ModuleDefinition,
    ModuleStatus,
    Occurrence,
    PluginHealthInfo,
    ReporterInfo,
    Session,
    StatusResult,
)

# ────────────────────────────────
# 1. System metrics (psutil)
# ────────────────────────────────


async def _collect_system_metrics(config: ScopedConfig) -> list[CheckResult]:
    """Collect CPU / memory / swap / disk / network metrics."""
    results: list[CheckResult] = []

    try:
        import psutil
    except ImportError:
        return [
            CheckResult(
                name="psutil",
                status="unknown",
                message="psutil not installed — hardware metrics unavailable",
            )
        ]

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

    return AggregatedStatus(
        overall=overall,
        bot=bot_status,
        connections=connections,
        plugins=plugins,
    )


# ────────────────────────────────
# 6. LogMonitor (L5 – loguru sink)
# ────────────────────────────────


class LogMonitor:
    """Intercepts ERROR/CRITICAL logs via loguru sink, creates automatic Sessions."""

    def __init__(self, config: ScopedConfig, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self._handler_id: int | None = None

    # ── lifecycle ──

    def start(self) -> None:
        if not self.config.auto_session_enabled:
            return
        from nonebot.log import logger as nb_logger

        self._handler_id = nb_logger.add(
            _make_log_sink(self.config, self.data_dir),
            level="ERROR",
        )
        logger.info("RuOK LogMonitor started (level=ERROR)")

    def stop(self) -> None:
        if self._handler_id is not None:
            from nonebot.log import logger as nb_logger

            nb_logger.remove(self._handler_id)
            self._handler_id = None


def _make_log_sink(config: ScopedConfig, data_dir: Path):
    """Create a loguru-compatible sink closure."""

    def _sink(record: Any) -> None:
        level_name: str = record["level"].name
        if level_name not in ("ERROR", "CRITICAL"):
            return

        plugin_id: str = record["name"]
        message: str = record["message"]
        exception: Any = record.get("exception")

        signature = _make_signature(plugin_id, str(exception), message)

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
            description=f"```\n{message}\n{exception}\n```",
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
            _notify_new_session(session)

    return _sink


def _make_signature(plugin: str, exc: str, msg: str) -> str:
    raw = f"{plugin}:{exc}:{msg[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _gen_session_id() -> str:
    return f"ruok-{secrets.token_hex(4)}"


def _record_to_dict(record: dict) -> dict[str, Any]:
    """Extract safe fields from a loguru record."""
    return {
        "name": record.get("name"),
        "level": record["level"].name,
        "message": record.get("message"),
        "exception": str(record.get("exception")) if record.get("exception") else None,
        "file": str(record.get("file", {}).get("name", "")),
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
    return session


def get_session(data_dir: Path, session_id: str) -> Session | None:
    return _load_session(data_dir, session_id)


def list_sessions(
    data_dir: Path,
    status: str | None = None,
    module_name: str | None = None,
    reporter_user_id: str | None = None,
) -> list[Session]:
    sessions: list[Session] = []
    statuses = set(s.strip() for s in status.split(",")) if status else None
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
        sessions.append(s)
    sessions.sort(key=lambda s: s.last_seen_at, reverse=True)
    return sessions


def update_session(data_dir: Path, session_id: str, updates: dict[str, Any]) -> Session | None:
    session = _load_session(data_dir, session_id)
    if session is None:
        return None

    allowed = {"status", "developer_notes", "last_seen_at", "linked_sessions"}
    for k, v in updates.items():
        if k in allowed:
            setattr(session, k, v)

    if "status" in updates and updates["status"] in ("solved", "ignored"):
        session.resolved_at = datetime.now(timezone.utc)

    _save_session(data_dir, session)
    return session


def link_sessions(data_dir: Path, session_id_a: str, session_id_b: str) -> bool:
    sa = _load_session(data_dir, session_id_a)
    sb = _load_session(data_dir, session_id_b)
    if sa is None or sb is None:
        return False
    if session_id_b not in sa.linked_sessions:
        sa.linked_sessions.append(session_id_b)
    if session_id_a not in sb.linked_sessions:
        sb.linked_sessions.append(session_id_a)
    _save_session(data_dir, sa)
    _save_session(data_dir, sb)
    return True


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
