"""LogMonitor — intercepts ERROR/CRITICAL logs via loguru sink + stdlib logging."""

from __future__ import annotations

import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Callable

from nonebot import logger

from ..config import ScopedConfig
from .sessions import (
    _save_session,
    _gen_session_id,
    _make_signature,
    _find_existing_session,
    _publish_session_event,
    rebuild_plugin_impacts,
)
from ..protocol import Session, ReporterInfo


class LogMonitor:
    """Intercepts ERROR/CRITICAL logs via loguru sink and stdlib logging,
    creating automatic Sessions for both."""

    def __init__(self, config: ScopedConfig, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self._handler_id: int | None = None
        self._logging_handler: _StdlibLogHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ──

    def start(self) -> None:
        if not self.config.auto_session_enabled:
            return
        from nonebot.log import logger as nb_logger

        self._loop = asyncio.get_running_loop()

        # ── loguru sink (nonebot / plugin logs) ──
        self._handler_id = nb_logger.add(
            _make_log_sink(self.config, self.data_dir, self._loop),
            level="ERROR",
            enqueue=True,
            serialize=True,
        )

        # ── stdlib logging handler (uvicorn, starlette, etc.) ──
        self._logging_handler = _StdlibLogHandler(
            self.config, self.data_dir, self._loop
        )
        logging.getLogger().addHandler(self._logging_handler)

        logger.info("RUOK LogMonitor started (level=ERROR, loguru + stdlib)")

    def stop(self) -> None:
        if self._handler_id is not None:
            from nonebot.log import logger as nb_logger

            nb_logger.remove(self._handler_id)
            self._handler_id = None
        if self._logging_handler is not None:
            logging.getLogger().removeHandler(self._logging_handler)
            self._logging_handler = None


class _StdlibLogHandler(logging.Handler):
    """Intercept Python stdlib ERROR/CRITICAL logs and create RUOK sessions.

    In non-strict mode (default) only captures framework-level loggers:
    ``uvicorn``, ``starlette``, ``fastapi``, ``asyncio``, ``multipart``.
    Set ``RUOK__strict_exception_capture=true`` to capture all loggers.
    """

    _FRAMEWORK_PREFIXES: tuple[str, ...] = (
        "uvicorn",
        "starlette",
        "fastapi",
        "asyncio",
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
        # Non-strict mode: only intercept framework loggers
        if not self._strict and not self._is_framework(record.name):
            return
        try:
            plugin_id: str = record.name
            msg_text: str = self.format(record)
            exc_text = ""
            if record.exc_info:
                import traceback

                exc_text = "".join(traceback.format_exception(*record.exc_info))

            signature = _make_signature(plugin_id, exc_text, msg_text)

            existing = _find_existing_session(self._data_dir, signature)
            if existing is not None:
                existing.last_seen_at = datetime.now(timezone.utc)
                _persist_automatic_session_update(self._data_dir, existing)
                return

            session = Session(
                session_id=_gen_session_id(),
                source="automatic",
                status="pending",
                module_name=plugin_id,
                error_signature=signature,
                reporter=ReporterInfo(type="automatic"),
                description=(f"```\n{msg_text}\n{exc_text}\n```"),
            )
            _persist_automatic_session_update(self._data_dir, session, created=True)
            logger.warning(f"RUOK: new session {session.session_id} for {plugin_id}")
            _schedule_session_notification(
                session,
                self._config,
                self._data_dir,
                self._loop,
            )
        except Exception:
            # logging handler must never raise — try best-effort logging
            try:
                logger.exception("RUOK LogMonitor: stdlib handler failed")
            except Exception:
                pass  # cannot even log, give up silently


def _make_log_sink(
    config: ScopedConfig, data_dir: Path, loop: asyncio.AbstractEventLoop
) -> Callable[[str], None]:
    """Create a loguru-compatible sink closure.

    With serialize=True, the sink receives a JSON string.  Session I/O runs
    in the worker thread; notification is dispatched to the main event loop
    via call_soon_threadsafe.
    """

    def _sink(message: str) -> None:
        try:
            record: dict = json.loads(message)
            if not isinstance(record, dict):
                return

            level_info = record.get("level", {})
            if not isinstance(level_info, dict):
                return
            if level_info.get("name") not in ("ERROR", "CRITICAL"):
                return

            plugin_id = record.get("name")
            msg_text = record.get("message")
            if not isinstance(plugin_id, str) or not isinstance(msg_text, str):
                return

            exception = record.get("exception")
            exception_str = ""
            if isinstance(exception, dict):
                value = exception.get("value", "")
                exception_str = value if isinstance(value, str) else str(value)

            signature = _make_signature(plugin_id, exception_str, msg_text)

            existing = _find_existing_session(data_dir, signature)
            if existing:
                existing.last_seen_at = datetime.now(timezone.utc)
                _persist_automatic_session_update(data_dir, existing)
                return

            session = Session(
                session_id=_gen_session_id(),
                source="automatic",
                status="pending",
                module_name=plugin_id,
                error_signature=signature,
                reporter=ReporterInfo(type="automatic"),
                description=f"```\n{msg_text}\n{exception_str}\n```",
            )
            _persist_automatic_session_update(data_dir, session, created=True)
            logger.warning(f"RUOK: new session {session.session_id} for {plugin_id}")
            _schedule_session_notification(session, config, data_dir, loop)
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as exc:
            logger.warning(f"RUOK LogMonitor: loguru sink failed: {exc}")

    return _sink


def _persist_automatic_session_update(
    data_dir: Path,
    session: Session,
    *,
    created: bool = False,
) -> None:
    """Persist an automatic session and run the normal best-effort side effects."""
    _save_session(data_dir, session)
    rebuild_plugin_impacts(data_dir)
    _publish_session_event("created" if created else "updated", session)


def _schedule_session_notification(
    session: Session,
    config: ScopedConfig,
    data_dir: Path,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Schedule notification dispatch for a newly created automatic session."""
    try:
        from .notifications import dispatch_notification

        asyncio.run_coroutine_threadsafe(
            dispatch_notification(session, config, data_dir),
            loop,
        )
    except RuntimeError as exc:
        logger.warning(f"RUOK: automatic session notification scheduling failed: {exc}")
