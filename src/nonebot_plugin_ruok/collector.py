"""Core engine: health collection, LogMonitor, session storage, module derivation.

This module is now a thin re-export layer.  The actual implementations live in
the ``collectors/`` subpackage.
"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta

import nonebot
from nonebot import logger, get_driver

from .protocol import Session, MetricPoint

# ── Re-export everything from the collectors subpackage ──
from .collectors import (  # noqa: F401 — re-export
    # monitor
    LogMonitor,
    # trackers
    DiskRateTracker,
    NetworkRateTracker,
    get_module,
    get_session,
    # modules
    list_modules,
    _disk_tracker,
    _load_session,
    _save_session,
    # metrics
    _startup_time,
    delete_module,
    link_sessions,
    list_sessions,
    upsert_module,
    _make_log_sink,
    create_session,
    unlink_session,
    update_session,
    # sessions
    _gen_session_id,
    _make_signature,
    _record_to_dict,
    _network_tracker,
    _collect_bot_info,
    get_session_stats,
    _handle_ruok_error,
    _connection_history,
    get_linked_sessions,
    collect_all_statuses,
    collect_fast_metrics,
    derive_module_status,
    _find_existing_session,
    _publish_session_event,
    _collect_system_metrics,
    collect_process_snapshot,
    _collect_plugin_inventory,
    _collect_connection_status,
)

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
        _tasks: list[asyncio.Task] = []
        for uid in superusers:
            try:
                _tasks.append(
                    asyncio.create_task(
                        bot.send_private_msg(user_id=int(uid), message=text)
                    )
                )
            except (ValueError, RuntimeError) as exc:
                logger.warning(
                    f"RuOK: failed to notify superuser {uid}: {exc}"
                )
    except (RuntimeError, KeyError) as exc:
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
        return (
            MetricsStore._metrics_dir(data_dir)
            / f"{datetime.now(timezone.utc).date().isoformat()}.json"
        )

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
        file_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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
        yesterday_str = (
            datetime.now(timezone.utc).date() - timedelta(days=1)
        ).isoformat()

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
                except (ValueError, KeyError):
                    continue
                except Exception as exc:
                    _handle_ruok_error(
                        exc, "MetricsStore.query 解析记录", data_dir
                    )
                    continue

        points.sort(key=lambda p: p.ts)
        return points

    @staticmethod
    def _cleanup(data_dir: Path, retention_days: int) -> None:
        """Remove metric files older than *retention_days*."""
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).date()
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
