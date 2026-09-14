"""Tests for collector.py — system metrics, trackers, process snapshot.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""

import pytest


class TestMetricsStore:
    def test_append_serializes_concurrent_updates(self, tmp_path) -> None:
        import json
        from datetime import datetime, timezone, timedelta
        from concurrent.futures import ThreadPoolExecutor

        from nonebot_plugin_ruok.protocol import MetricPoint
        from nonebot_plugin_ruok.collector import MetricsStore

        base = datetime.now(timezone.utc)

        def append_point(index: int) -> None:
            MetricsStore.append(
                tmp_path,
                MetricPoint(
                    ts=(base + timedelta(seconds=index)).isoformat(),
                    cpu_percent=float(index),
                ),
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(append_point, range(32)))

        metrics_file = next((tmp_path / "metrics").glob("*.json"))
        records = json.loads(metrics_file.read_text(encoding="utf-8"))
        assert len(records) == 32
        assert {record["cpu_percent"] for record in records} == set(range(32))

    def test_append_quarantines_corrupt_file(self, tmp_path) -> None:
        import json
        from datetime import datetime, timezone

        from nonebot_plugin_ruok.protocol import MetricPoint
        from nonebot_plugin_ruok.collector import MetricsStore

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        today = datetime.now(timezone.utc).date().isoformat()
        metrics_file = metrics_dir / f"{today}.json"
        metrics_file.write_text("[{", encoding="utf-8")

        MetricsStore.append(
            tmp_path,
            MetricPoint(ts=datetime.now(timezone.utc).isoformat(), cpu_percent=42.0),
        )

        backups = list(metrics_dir.glob(f"{today}.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "[{"
        assert (
            json.loads(metrics_file.read_text(encoding="utf-8"))[0]["cpu_percent"]
            == 42.0
        )

    def test_query_accepts_naive_timestamp(self, tmp_path) -> None:
        import json
        from datetime import datetime, timezone, timedelta

        from nonebot_plugin_ruok.protocol import MetricPoint
        from nonebot_plugin_ruok.collector import MetricsStore

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)
        point = MetricPoint(
            ts=timestamp.replace(tzinfo=None).isoformat(),
            cpu_percent=12.0,
        )
        (metrics_dir / f"{timestamp.date().isoformat()}.json").write_text(
            json.dumps([point.model_dump(mode="json")]),
            encoding="utf-8",
        )

        points = MetricsStore.query(tmp_path, hours=1)

        assert len(points) == 1
        assert points[0].cpu_percent == 12.0

    def test_query_loads_more_than_two_days(self, tmp_path) -> None:
        import json
        from datetime import datetime, timezone, timedelta

        from nonebot_plugin_ruok.protocol import MetricPoint
        from nonebot_plugin_ruok.collector import MetricsStore

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        old_ts = datetime.now(timezone.utc) - timedelta(days=3)
        point = MetricPoint(ts=old_ts.isoformat(), cpu_percent=12.0)
        (metrics_dir / f"{old_ts.date().isoformat()}.json").write_text(
            json.dumps([point.model_dump(mode="json")]),
            encoding="utf-8",
        )

        points = MetricsStore.query(tmp_path, hours=96)

        assert len(points) == 1
        assert points[0].cpu_percent == 12.0


class TestCollectFastMetrics:
    @pytest.mark.asyncio
    async def test_returns_valid_snapshot(self) -> None:
        from nonebot_plugin_ruok.collector import collect_fast_metrics

        fm = await collect_fast_metrics()
        assert 0.0 <= fm.cpu_percent <= 100.0
        assert isinstance(fm.cpu_per_core, list)
        assert fm.memory_total > 0
        assert fm.process_count > 0
        assert fm.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_cpu_percent_not_single_core(self) -> None:
        from nonebot_plugin_ruok.collector import collect_fast_metrics

        fm = await collect_fast_metrics()
        cores = fm.cpu_per_core
        if len(cores) >= 2:
            avg = sum(cores) / len(cores)
            assert abs(fm.cpu_percent - avg) < 30.0, (
                f"cpu={fm.cpu_percent:.1f}%, avg_cores={avg:.1f}%"
            )

    @pytest.mark.asyncio
    async def test_boot_time_valid(self) -> None:
        import time

        from nonebot_plugin_ruok.collector import collect_fast_metrics

        fm = await collect_fast_metrics()
        now = time.time()
        assert fm.boot_time_epoch > 0
        assert fm.boot_time_epoch <= now

    @pytest.mark.asyncio
    async def test_cpu_temp_optional(self) -> None:
        from nonebot_plugin_ruok.collector import collect_fast_metrics

        fm = await collect_fast_metrics()
        if fm.cpu_temp is not None:
            assert 0 <= fm.cpu_temp <= 120


class TestCollectConnectionStatus:
    @pytest.mark.asyncio
    async def test_recovery_clears_previous_failure_metadata(self, monkeypatch) -> None:
        from datetime import datetime, timezone, timedelta

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import BotConnectionStatus
        from nonebot_plugin_ruok.collectors import metrics

        class FakeBot:
            self_id = "bot-1"
            type = "fake"

            async def get_status(self):
                return {"online": True, "good": True}

        old_disconnect = datetime.now(timezone.utc) - timedelta(minutes=1)
        metrics._connection_history.clear()
        metrics._connection_history["bot-1"] = BotConnectionStatus(
            self_id="bot-1",
            adapter="fake",
            connected=False,
            connected_at=old_disconnect - timedelta(minutes=5),
            disconnected_at=old_disconnect,
            ws_closed=True,
            latency_ms=12.0,
            error="previous failure",
        )
        monkeypatch.setattr(metrics.nonebot, "get_bots", lambda: {"bot-1": FakeBot()})
        monkeypatch.setattr(metrics, "get_driver", lambda: type("Driver", (), {})())

        try:
            result = await metrics._collect_connection_status(ScopedConfig())
        finally:
            metrics._connection_history.clear()

        entry = result[0]
        assert entry.connected is True
        assert entry.connected_at is not None
        assert entry.connected_at > old_disconnect
        assert entry.disconnected_at is None
        assert entry.ws_closed is None
        assert entry.latency_ms is not None
        assert entry.error is None

    @pytest.mark.asyncio
    async def test_closed_ws_marks_present_bot_disconnected(self, monkeypatch) -> None:
        from typing import ClassVar

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors import metrics

        class FakeBot:
            self_id = "bot-1"
            type = "fake"

            async def get_status(self):
                raise AssertionError("deep check is disabled")

        class FakeConnection:
            closed = True

        class FakeAdapter:
            connections: ClassVar = {"bot-1": FakeConnection()}

        class FakeDriver:
            _adapters: ClassVar = {"fake": FakeAdapter()}

        metrics._connection_history.clear()
        monkeypatch.setattr(metrics.nonebot, "get_bots", lambda: {"bot-1": FakeBot()})
        monkeypatch.setattr(metrics, "get_driver", lambda: FakeDriver())

        try:
            result = await metrics._collect_connection_status(
                ScopedConfig(enable_deep_ws_check=False)
            )
        finally:
            metrics._connection_history.clear()

        entry = result[0]
        assert entry.connected is False
        assert entry.ws_closed is True
        assert entry.disconnected_at is not None

    @pytest.mark.asyncio
    async def test_offline_status_payload_marks_present_bot_disconnected(
        self, monkeypatch
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors import metrics

        class FakeBot:
            self_id = "bot-1"
            type = "fake"

            async def get_status(self):
                return {"online": False, "good": False}

        metrics._connection_history.clear()
        monkeypatch.setattr(metrics.nonebot, "get_bots", lambda: {"bot-1": FakeBot()})
        monkeypatch.setattr(metrics, "get_driver", lambda: type("Driver", (), {})())

        try:
            result = await metrics._collect_connection_status(ScopedConfig())
        finally:
            metrics._connection_history.clear()

        entry = result[0]
        assert entry.connected is False
        assert entry.ws_closed is None
        assert entry.error is None
        assert entry.disconnected_at is not None


class TestCollectProcessSnapshot:
    @pytest.mark.asyncio
    async def test_returns_valid_snapshot(self) -> None:
        from nonebot_plugin_ruok.protocol import ProcessSnapshot
        from nonebot_plugin_ruok.collector import collect_process_snapshot

        ps = await collect_process_snapshot()
        assert isinstance(ps, ProcessSnapshot)
        assert ps.bot_rss > 0
        assert ps.bot_threads > 0
        assert isinstance(ps.top_processes, list)
        assert len(ps.top_processes) <= 5

    @pytest.mark.asyncio
    async def test_no_pid_zero(self) -> None:
        from nonebot_plugin_ruok.collector import collect_process_snapshot

        ps = await collect_process_snapshot()
        for p in ps.top_processes:
            assert p.pid != 0, f"PID 0 not filtered: {p.name}"


class TestNetworkRateTracker:
    def test_get_rate(self) -> None:
        from nonebot_plugin_ruok.protocol import NetworkRate
        from nonebot_plugin_ruok.collector import _network_tracker

        nr = _network_tracker.get_rate()
        assert isinstance(nr, NetworkRate)
        assert nr.bytes_sent_per_sec >= 0.0


class TestDiskRateTracker:
    def test_get_rate(self) -> None:
        from nonebot_plugin_ruok.protocol import DiskIORate
        from nonebot_plugin_ruok.collector import _disk_tracker

        agg, per_disk = _disk_tracker.get_rate()
        assert isinstance(agg, DiskIORate)
        assert isinstance(per_disk, dict)
        assert agg.read_bytes_per_sec >= 0.0

    def test_per_disk_keys(self) -> None:
        from nonebot_plugin_ruok.protocol import DiskIORate
        from nonebot_plugin_ruok.collector import _disk_tracker

        __, per_disk = _disk_tracker.get_rate()
        for name, rate in per_disk.items():
            assert isinstance(name, str)
            assert isinstance(rate, DiskIORate)


class TestLogMonitor:
    def test_loguru_message_record_creates_mapped_session(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        import asyncio

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.monitor import _make_log_sink
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        class _Message(str):
            record: dict

            def __new__(cls, record):
                obj = str.__new__(cls, "rendered error")
                obj.record = record
                return obj

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.monitor._schedule_session_notification",
            lambda *args: None,
        )
        upsert_module(
            tmp_path,
            ModuleDefinition(name="music", plugins=["nonebot_plugin_music"]),
        )
        loop = asyncio.new_event_loop()
        try:
            sink = _make_log_sink(ScopedConfig(), tmp_path, loop)
            sink(
                _Message(
                    {
                        "level": {"name": "ERROR"},
                        "name": "nonebot_plugin_music",
                        "message": "production boom",
                        "exception": None,
                        "extra": {},
                    }
                )
            )
        finally:
            loop.close()

        sessions = list_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0].module_name == "music"
        assert "日志来源: nonebot_plugin_music" in sessions[0].description

    def test_loguru_exception_keeps_traceback(self, tmp_path, monkeypatch) -> None:
        import sys
        import asyncio
        from types import SimpleNamespace

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.monitor import _make_log_sink
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        class _Message(str):
            record: dict

            def __new__(cls, record):
                obj = str.__new__(cls, "rendered exception")
                obj.record = record
                return obj

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.monitor._schedule_session_notification",
            lambda *args: None,
        )
        try:
            raise ZeroDivisionError("boom")
        except ZeroDivisionError:
            exc_type, exc_value, tb = sys.exc_info()

        loop = asyncio.new_event_loop()
        try:
            sink = _make_log_sink(ScopedConfig(), tmp_path, loop)
            sink(
                _Message(
                    {
                        "level": {"name": "ERROR"},
                        "name": "worker",
                        "message": "calculation failed",
                        "exception": SimpleNamespace(
                            type=exc_type,
                            value=exc_value,
                            traceback=tb,
                        ),
                        "extra": {},
                    }
                )
            )
        finally:
            loop.close()

        session = list_sessions(tmp_path)[0]
        assert "ZeroDivisionError" in session.description
        assert "calculation failed" in session.description

    def test_loguru_and_stdlib_bridge_event_is_deduplicated(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        import asyncio

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.monitor import _capture_log_event
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.monitor._schedule_session_notification",
            lambda *args: None,
        )
        loop = asyncio.new_event_loop()
        try:
            config = ScopedConfig()
            _capture_log_event(config, tmp_path, loop, "worker", "same boom", "")
            _capture_log_event(config, tmp_path, loop, "uvicorn.error", "same boom", "")
        finally:
            loop.close()

        assert len(list_sessions(tmp_path)) == 1

    def test_closed_loop_does_not_leak_notification_coroutine(
        self, tmp_path, monkeypatch
    ) -> None:
        import asyncio

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import Session, ReporterInfo
        from nonebot_plugin_ruok.collectors.monitor import (
            _schedule_session_notification,
        )

        async def _dispatch(session, config, data_dir):
            return None

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.notifications.dispatch_notification",
            _dispatch,
        )
        loop = asyncio.new_event_loop()
        loop.close()
        session = Session(
            session_id="ruok-12345678",
            source="automatic",
            module_name="worker",
            reporter=ReporterInfo(type="automatic"),
        )
        _schedule_session_notification(session, ScopedConfig(), tmp_path, loop)

    def test_loguru_sink_ignores_malformed_records(self, tmp_path) -> None:
        import asyncio

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.monitor import _make_log_sink

        loop = asyncio.new_event_loop()
        try:
            sink = _make_log_sink(ScopedConfig(), tmp_path, loop)
            sink('{"level": {"name": "ERROR"}}')
            sink("[]")
        finally:
            loop.close()

        assert not (tmp_path / "sessions").exists()

    def test_serialized_loguru_envelope_keeps_rendered_text(
        self, tmp_path, monkeypatch
    ) -> None:
        import json
        import asyncio

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.monitor import _make_log_sink
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.monitor._schedule_session_notification",
            lambda *args: None,
        )
        loop = asyncio.new_event_loop()
        try:
            sink = _make_log_sink(ScopedConfig(), tmp_path, loop)
            sink(
                json.dumps(
                    {
                        "text": "rendered traceback line",
                        "record": {
                            "level": {"name": "ERROR"},
                            "name": "worker",
                            "message": "serialized boom",
                            "exception": {"value": "boom"},
                            "extra": {},
                        },
                    }
                )
            )
        finally:
            loop.close()

        assert "rendered traceback line" in list_sessions(tmp_path)[0].description

    def test_stdlib_log_session_rebuilds_impacts_and_notifies(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        import asyncio
        import logging

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.monitor import _StdlibLogHandler
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        class _DoneFuture:
            pass

        calls = []

        async def _fake_dispatch(session, config, data_dir):
            return None

        def _fake_run_coroutine_threadsafe(coro, loop):
            coro.close()
            calls.append((coro, loop))
            return _DoneFuture()

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collectors.notifications.dispatch_notification",
            _fake_dispatch,
        )
        monkeypatch.setattr(
            "asyncio.run_coroutine_threadsafe",
            _fake_run_coroutine_threadsafe,
        )

        upsert_module(
            tmp_path,
            ModuleDefinition(name="uvicorn.error", plugins=["uvicorn_plugin"]),
        )
        loop = asyncio.new_event_loop()
        original_is_running = loop.is_running
        try:
            monkeypatch.setattr(loop, "is_running", lambda: True)
            handler = _StdlibLogHandler(ScopedConfig(), tmp_path, loop=loop)
            record = logging.LogRecord(
                name="uvicorn.error",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="framework boom",
                args=(),
                exc_info=None,
            )

            handler.emit(record)
        finally:
            loop.is_running = original_is_running
            loop.close()

        sessions = list_sessions(tmp_path, module_name="uvicorn.error")
        impacts = (tmp_path / "plugin_impacts.json").read_text("utf-8")
        assert len(sessions) == 1
        assert sessions[0].source == "automatic"
        assert sessions[0].session_id in impacts
        assert calls
