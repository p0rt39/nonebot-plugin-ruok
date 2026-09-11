"""Core engine: health collection, LogMonitor, session storage, module derivation.

This module is now a thin re-export layer.  The actual implementations live in
the ``collectors/`` subpackage.
"""

from __future__ import annotations

import os
import json
import time
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta

from nonebot import logger

from .protocol import MetricPoint

# ── Re-export everything from the collectors subpackage ──
from .collectors import (
    # monitor
    LogMonitor,
    # trackers
    DiskRateTracker,
    NetworkRateTracker,
    SessionPluginValidationError,
    SessionUpdateValidationError,
    get_module,
    get_session,
    # modules
    list_modules,
    _disk_tracker,
    _startup_time,
    delete_module,
    link_sessions,
    list_sessions,
    upsert_module,
    create_session,
    unlink_session,
    update_session,
    # sessions
    _network_tracker,
    get_session_stats,
    _handle_ruok_error,
    _connection_history,
    get_linked_sessions,
    build_plugin_impacts,
    collect_all_statuses,
    collect_fast_metrics,
    derive_module_status,
    dispatch_notification,
    parse_filter_datetime,
    rebuild_plugin_impacts,
    resolve_module_display,
    confirm_session_plugins,
    collect_process_snapshot,
    # metrics (webui/router)
    _collect_plugin_inventory,
    list_module_related_sessions,
    derive_module_status_with_reasons,
)
from .collectors.storage import locked_file, atomic_write_text

__all__ = [
    "DiskRateTracker",
    "LogMonitor",
    "MetricsStore",
    "NetworkRateTracker",
    "SessionPluginValidationError",
    "SessionUpdateValidationError",
    "_collect_plugin_inventory",
    "_connection_history",
    "_disk_tracker",
    "_network_tracker",
    "_startup_time",
    "build_plugin_impacts",
    "collect_all_statuses",
    "collect_fast_metrics",
    "collect_process_snapshot",
    "confirm_session_plugins",
    "create_session",
    "delete_module",
    "derive_module_status",
    "derive_module_status_with_reasons",
    "dispatch_notification",
    "get_linked_sessions",
    "get_module",
    "get_session",
    "get_session_stats",
    "link_sessions",
    "list_module_related_sessions",
    "list_modules",
    "list_sessions",
    "parse_filter_datetime",
    "rebuild_plugin_impacts",
    "resolve_module_display",
    "unlink_session",
    "update_session",
    "upsert_module",
]

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
        metrics_dir = MetricsStore._metrics_dir(data_dir)
        file_path = MetricsStore._today_file(data_dir)
        with locked_file(metrics_dir), locked_file(file_path):
            records: list[dict[str, Any]] = []
            if file_path.exists():
                try:
                    raw_records = json.loads(file_path.read_text("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    if not MetricsStore._quarantine_corrupt_file(file_path):
                        logger.warning(
                            f"RUOK: cannot preserve corrupt metrics file "
                            f"{file_path}: {exc}"
                        )
                        return
                    logger.warning(
                        f"RUOK: quarantined corrupt metrics file {file_path}: {exc}"
                    )
                except OSError as exc:
                    logger.warning(f"RUOK: cannot read metrics file {file_path}: {exc}")
                    return
                else:
                    if not isinstance(raw_records, list):
                        if not MetricsStore._quarantine_corrupt_file(file_path):
                            logger.warning(
                                f"RUOK: cannot preserve invalid metrics file "
                                f"{file_path}"
                            )
                            return
                        logger.warning(
                            f"RUOK: quarantined invalid metrics file {file_path}"
                        )
                    else:
                        records = raw_records
            records.append(point.model_dump(mode="json"))
            atomic_write_text(
                file_path,
                json.dumps(records, indent=2, ensure_ascii=False),
            )
            # Clean old files while the metrics directory lock is held.
            MetricsStore._cleanup(data_dir, retention_days)

    @staticmethod
    def _quarantine_corrupt_file(path: Path) -> bool:
        """Move a malformed JSON file aside before replacing its live path."""
        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}-{os.getpid()}")
        try:
            os.replace(path, backup)
        except OSError:
            return False
        return True

    @staticmethod
    def query(data_dir: Path, hours: float = 24.0) -> list[MetricPoint]:
        """Return metrics from the last *hours* hours.

        Loads today's file and yesterday's if needed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        metrics_dir = MetricsStore._metrics_dir(data_dir)
        points: list[MetricPoint] = []

        today = datetime.now(timezone.utc).date()
        days = max(1, int(hours // 24) + 2)

        for offset in range(days):
            date_str = (today - timedelta(days=offset)).isoformat()
            fpath = metrics_dir / f"{date_str}.json"
            if not fpath.exists():
                continue
            try:
                records = json.loads(fpath.read_text("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning(f"RUOK: invalid metrics file {fpath}: {exc}")
                continue
            except OSError as exc:
                logger.warning(f"RUOK: cannot read metrics file {fpath}: {exc}")
                continue
            if not isinstance(records, list):
                logger.warning(f"RUOK: metrics file must contain an array: {fpath}")
                continue
            for rec in records:
                try:
                    pt = MetricPoint.model_validate(rec)
                    ts = datetime.fromisoformat(pt.ts)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                    if ts >= cutoff:
                        points.append(pt)
                except (ValueError, KeyError):
                    continue
                except Exception as exc:
                    _handle_ruok_error(exc, "MetricsStore.query 解析记录", data_dir)
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
        with locked_file(metrics_dir):
            for f in metrics_dir.glob("*.json"):
                try:
                    file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if file_date < cutoff_date:
                    with locked_file(f):
                        try:
                            f.unlink()
                        except OSError:
                            pass
