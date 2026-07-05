"""LogMonitor — intercepts ERROR/CRITICAL logs via loguru sink."""
from __future__ import annotations

import json
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime, timezone

from nonebot import logger

from ..config import ScopedConfig
from .sessions import (
    _save_session,
    _gen_session_id,
    _make_signature,
    _record_to_dict,
    _find_existing_session,
)
from ..protocol import Session, Occurrence, ReporterInfo


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


def _make_log_sink(
    config: ScopedConfig, data_dir: Path, loop: asyncio.AbstractEventLoop
):
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
        logger.warning(
            f"RuOK: new session {session.session_id} for {plugin_id}"
        )
        if config.notify_superusers:
            # Late import to avoid circular dependency
            from ..collector import _notify_new_session

            loop.call_soon_threadsafe(_notify_new_session, session)

    return _sink
