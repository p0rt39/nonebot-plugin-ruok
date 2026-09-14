"""LogMonitor — intercept ERROR/CRITICAL logs and create Sessions."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Mapping, Callable

from nonebot import logger

from ..config import ScopedConfig
from .modules import resolve_log_source
from .sessions import (
    _save_session,
    _gen_session_id,
    _make_signature,
    _find_existing_session,
    _publish_session_event,
    rebuild_plugin_impacts,
    _automatic_session_lock,
)
from ..protocol import Session, ReporterInfo

_MAX_LOG_TEXT = 20_000
_BRIDGED_RECORD_ATTR = "ruok_loguru_bridged"
_INTERNAL_RECORD_ATTR = "ruok_internal"


def _safe_log(level: str, message: str) -> None:
    """Write monitor diagnostics without feeding them back into the monitor."""
    try:
        getattr(logger.bind(ruok_internal=True), level)(message)
    except Exception:
        pass


class _BridgeMarkerFilter(logging.Filter):
    """Mark stdlib records that are already forwarded to Loguru by NoneBot."""

    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, _BRIDGED_RECORD_ATTR, True)
        return True


class LogMonitor:
    """Capture Loguru and selected stdlib errors as automatic Sessions."""

    def __init__(self, config: ScopedConfig, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self._handler_id: int | None = None
        self._logging_handler: _StdlibLogHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge_filters: list[tuple[logging.Handler, logging.Filter]] = []

    # ── lifecycle ──

    def start(self) -> None:
        """Install capture handlers on a best-effort basis.

        Monitoring is auxiliary to the bot. A failed Loguru or stdlib
        registration is reported and does not prevent the other channel from
        being installed.
        """
        if not self.config.auto_session_enabled:
            _safe_log("info", "RUOK LogMonitor disabled (auto_session_enabled=false)")
            return
        if self._handler_id is not None or self._logging_handler is not None:
            _safe_log("info", "RUOK LogMonitor already started")
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            self._loop = None
            _safe_log("warning", f"RUOK LogMonitor disabled: no running loop ({exc})")
            return

        active_channels: list[str] = []

        # ── loguru sink (NoneBot / plugin logs) ──
        try:
            from nonebot.log import logger as nb_logger

            self._handler_id = nb_logger.add(
                _make_log_sink(self.config, self.data_dir, self._loop),
                level="ERROR",
                enqueue=True,
                serialize=False,
                diagnose=False,
            )
            active_channels.append("loguru")
        except Exception as exc:
            _safe_log("warning", f"RUOK LogMonitor Loguru registration failed: {exc}")

        # ── stdlib logging handler (uvicorn, starlette, etc.) ──
        try:
            handler = _StdlibLogHandler(self.config, self.data_dir, self._loop)
            logging.getLogger().addHandler(handler)
            self._logging_handler = handler
            active_channels.append("stdlib")
            self._install_bridge_markers()
        except Exception as exc:
            _safe_log("warning", f"RUOK LogMonitor stdlib registration failed: {exc}")

        if active_channels:
            _safe_log(
                "info",
                "RUOK LogMonitor enabled "
                f"(level=ERROR, channels={','.join(active_channels)})",
            )
        else:
            _safe_log("warning", "RUOK LogMonitor disabled (no active channels)")

    def _install_bridge_markers(self) -> None:
        """Mark existing NoneBot Loguru bridge handlers before root propagation."""
        try:
            from nonebot.log import LoguruHandler
        except ImportError:
            return

        manager = logging.Logger.manager
        loggers: list[logging.Logger] = [logging.getLogger()]
        loggers.extend(
            item
            for item in manager.loggerDict.values()
            if isinstance(item, logging.Logger)
        )
        for target in loggers:
            for handler in target.handlers:
                if not isinstance(handler, LoguruHandler):
                    continue
                if any(
                    existing_handler is handler
                    for existing_handler, _ in self._bridge_filters
                ):
                    continue
                marker = _BridgeMarkerFilter()
                handler.addFilter(marker)
                self._bridge_filters.append((handler, marker))

    def stop(self) -> None:
        """Remove each installed channel independently."""
        if self._handler_id is not None:
            try:
                from nonebot.log import logger as nb_logger

                nb_logger.remove(self._handler_id)
            except Exception as exc:
                _safe_log("warning", f"RUOK LogMonitor Loguru removal failed: {exc}")
            finally:
                self._handler_id = None

        if self._logging_handler is not None:
            try:
                logging.getLogger().removeHandler(self._logging_handler)
            except Exception as exc:
                _safe_log("warning", f"RUOK LogMonitor stdlib removal failed: {exc}")
            finally:
                self._logging_handler = None

        for handler, marker in self._bridge_filters:
            try:
                handler.removeFilter(marker)
            except Exception:
                pass
        self._bridge_filters.clear()
        self._loop = None


class _StdlibLogHandler(logging.Handler):
    """Intercept Python stdlib ERROR/CRITICAL logs and create Sessions.

    In non-strict mode (default) only captures framework-level loggers:
    uvicorn, starlette, fastapi, asyncio, multipart.
    Set RUOK__strict_exception_capture=true to capture all loggers.
    """

    _FRAMEWORK_PREFIXES: tuple[str, ...] = (
        "uvicorn",
        "starlette",
        "fastapi",
        "asyncio",
        "multipart",
    )

    def __init__(
        self,
        config: ScopedConfig,
        data_dir: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self._config = config
        self._data_dir = data_dir
        self._loop = loop
        self._strict: bool = config.strict_exception_capture

    def _is_framework(self, name: str) -> bool:
        return name.startswith(self._FRAMEWORK_PREFIXES)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, _INTERNAL_RECORD_ATTR, False):
            return
        # A NoneBot LoguruHandler has already sent this record through the
        # Loguru sink. Root propagation must not create a second Session.
        if getattr(record, _BRIDGED_RECORD_ATTR, False):
            return
        if not self._strict and not self._is_framework(record.name):
            return
        try:
            plugin_id = record.name
            msg_text = record.getMessage()
            exc_text = ""
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            _capture_log_event(
                self._config,
                self._data_dir,
                self._loop,
                plugin_id,
                msg_text,
                exc_text,
                rendered_text=msg_text,
            )
        except Exception as exc:
            # Logging handlers must never raise back into the application.
            _safe_log("warning", f"RUOK LogMonitor stdlib handler failed: {exc}")


def _record_mapping(message: Any) -> tuple[Mapping[str, Any], str]:
    """Extract a Loguru record from Message or serialized compatibility input."""
    record = getattr(message, "record", None)
    rendered_text = str(message) if message is not None else ""
    if isinstance(record, Mapping):
        return record, rendered_text

    if isinstance(message, bytes):
        message = message.decode("utf-8")
    if isinstance(message, str):
        import json

        payload = json.loads(message)
        if not isinstance(payload, Mapping):
            return {}, rendered_text
        nested = payload.get("record")
        if isinstance(nested, Mapping):
            text = payload.get("text")
            return nested, text if isinstance(text, str) else rendered_text
        return payload, rendered_text
    return {}, rendered_text


def _exception_text(exception: Any) -> str:
    """Format Loguru's RecordException or a serialized exception mapping."""
    if exception is None:
        return ""

    if isinstance(exception, Mapping):
        exc_type = exception.get("type")
        value = exception.get("value", "")
        tb = exception.get("traceback")
        if isinstance(tb, str) and tb.strip():
            return tb
        if isinstance(value, str):
            if isinstance(exc_type, str) and exc_type:
                return f"{exc_type}: {value}"
            return value
        return str(value)

    exc_type = getattr(exception, "type", None)
    value = getattr(exception, "value", None)
    tb = getattr(exception, "traceback", None)
    if exc_type is not None and value is not None:
        try:
            return "".join(traceback.format_exception(exc_type, value, tb))
        except Exception:
            pass
    return str(value if value is not None else exception)


def _clip_text(value: str, limit: int = _MAX_LOG_TEXT) -> str:
    if len(value) <= limit:
        return value
    suffix = "\n...[truncated by RUOK]"
    return value[: max(0, limit - len(suffix))] + suffix


def _source_marker(plugin_id: str) -> str:
    return f"日志来源: {plugin_id}"


def _find_existing_log_session(
    data_dir: Path,
    signature: str,
    plugin_id: str,
    exception_text: str,
    message_text: str,
) -> Session | None:
    """Find exact duplicates and bridge duplicates for one log event."""
    existing = _find_existing_session(data_dir, signature)
    if existing is not None:
        return existing

    # Uvicorn's stdlib record can be converted to Loguru with a different
    # logger name. For a framework bridge, compare the event content without
    # the logger name so the two capture paths collapse to one Session.
    current_is_framework = plugin_id.startswith(_StdlibLogHandler._FRAMEWORK_PREFIXES)
    neutral_signature = _make_signature("", exception_text, message_text)
    for candidate in _pending_sessions(data_dir):
        source = _session_source(candidate)
        candidate_is_framework = source.startswith(
            _StdlibLogHandler._FRAMEWORK_PREFIXES
        )
        if not (current_is_framework or candidate_is_framework):
            continue
        if _session_neutral_signature(candidate) == neutral_signature:
            return candidate
    return None


def _pending_sessions(data_dir: Path) -> list[Session]:
    from .sessions import list_sessions

    return [
        session
        for session in list_sessions(data_dir)
        if session.status in ("pending", "unsolved")
    ]


def _session_source(session: Session) -> str:
    for line in session.description.splitlines():
        if line.startswith("日志来源: "):
            return line.removeprefix("日志来源: ").strip()
    return ""


def _session_neutral_signature(session: Session) -> str:
    marker = "日志内容签名:"
    for line in session.developer_notes.splitlines() if session.developer_notes else ():
        if line.startswith(marker):
            return line.removeprefix(marker).strip()
    return ""


def _capture_log_event(
    config: ScopedConfig,
    data_dir: Path,
    loop: asyncio.AbstractEventLoop,
    plugin_id: str,
    msg_text: str,
    exception_text: str,
    *,
    rendered_text: str = "",
) -> Session | None:
    """Persist one log event and schedule its notification."""
    if not plugin_id or not msg_text:
        return None
    module_name, matched_modules = resolve_log_source(data_dir, plugin_id)
    signature_plugin = module_name if module_name != plugin_id else plugin_id
    signature = _make_signature(signature_plugin, exception_text, msg_text)
    neutral_signature = _make_signature("", exception_text, msg_text)
    description_parts = [_source_marker(plugin_id)]
    if module_name != plugin_id:
        description_parts.append(f"模块映射: {module_name}")
    if matched_modules and len(matched_modules) > 1:
        description_parts.append("共享插件模块: " + ", ".join(matched_modules))
    description_parts.append(msg_text)
    if exception_text:
        description_parts.append(exception_text)
    if rendered_text and rendered_text.strip() != msg_text.strip():
        description_parts.append(rendered_text)
    description = "```\n" + _clip_text("\n".join(description_parts)) + "\n```"

    with _automatic_session_lock(data_dir):
        existing = _find_existing_log_session(
            data_dir, signature, plugin_id, exception_text, msg_text
        )
        if existing is not None:
            existing.last_seen_at = datetime.now(timezone.utc)
            _persist_automatic_session_update(data_dir, existing)
            return existing

        session = Session(
            session_id=_gen_session_id(),
            source="automatic",
            status="pending",
            module_name=module_name,
            error_signature=signature,
            reporter=ReporterInfo(type="automatic"),
            description=description,
            developer_notes=f"日志内容签名: {neutral_signature}",
        )
        _persist_automatic_session_update(data_dir, session, created=True)

    _safe_log("warning", f"RUOK: new session {session.session_id} for {module_name}")
    _schedule_session_notification(session, config, data_dir, loop)
    return session


def _make_log_sink(
    config: ScopedConfig, data_dir: Path, loop: asyncio.AbstractEventLoop
) -> Callable[[Any], None]:
    """Create a Loguru sink using Message.record.

    The sink deliberately accepts serialized strings too, which keeps old
    callers and tests compatible while production uses serialize=False.
    """

    def _sink(message: Any) -> None:
        try:
            record, rendered_text = _record_mapping(message)
            extra = record.get("extra", {})
            if isinstance(extra, Mapping) and extra.get(_INTERNAL_RECORD_ATTR):
                return
            level_info = record.get("level", {})
            level_name = (
                level_info.get("name")
                if isinstance(level_info, Mapping)
                else getattr(level_info, "name", None)
            )
            if level_name not in ("ERROR", "CRITICAL"):
                return

            plugin_id = record.get("name")
            msg_text = record.get("message")
            if not isinstance(plugin_id, str) or not isinstance(msg_text, str):
                return
            exception_text = _exception_text(record.get("exception"))
            _capture_log_event(
                config,
                data_dir,
                loop,
                plugin_id,
                msg_text,
                exception_text,
                rendered_text=rendered_text,
            )
        except Exception as exc:
            # Sink failures must never affect the application logger.
            _safe_log("warning", f"RUOK LogMonitor Loguru sink failed: {exc}")

    return _sink


def _persist_automatic_session_update(
    data_dir: Path,
    session: Session,
    *,
    created: bool = False,
) -> None:
    """Persist an automatic session and run normal best-effort side effects."""
    _save_session(data_dir, session)
    rebuild_plugin_impacts(data_dir)
    _publish_session_event("created" if created else "updated", session)


def _schedule_session_notification(
    session: Session,
    config: ScopedConfig,
    data_dir: Path,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Schedule notification dispatch without leaking a coroutine on shutdown."""
    try:
        from .notifications import dispatch_notification
    except Exception as exc:
        _safe_log("warning", f"RUOK: notification import failed: {exc}")
        return

    try:
        coro = dispatch_notification(session, config, data_dir)
    except Exception as exc:
        _safe_log("warning", f"RUOK: notification dispatch creation failed: {exc}")
        return
    try:
        loop_closed = loop.is_closed()
        loop_running = loop.is_running()
    except Exception as exc:
        coro.close()
        _safe_log("warning", f"RUOK: notification loop state unavailable: {exc}")
        return
    if loop_closed or not loop_running:
        coro.close()
        _safe_log("warning", "RUOK: automatic notification loop is not running")
        return
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        coro.close()
        _safe_log("warning", f"RUOK: automatic notification scheduling failed: {exc}")
        return

    def _report_future_result(done: Any) -> None:
        try:
            done.result()
        except BaseException as exc:
            _safe_log("warning", f"RUOK: automatic notification failed: {exc}")

    add_done_callback = getattr(future, "add_done_callback", None)
    if callable(add_done_callback):
        add_done_callback(_report_future_result)
