"""Tests for collector.py — system metrics, trackers, process snapshot.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""

import pytest


class TestMetricsStore:
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

        async def _fake_notify(session, config, data_dir):
            return None

        def _fake_run_coroutine_threadsafe(coro, loop):
            coro.close()
            calls.append((coro, loop))
            return _DoneFuture()

        monkeypatch.setattr(
            "nonebot_plugin_ruok.collector._notify_new_session",
            _fake_notify,
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
        try:
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
            loop.close()

        sessions = list_sessions(tmp_path, module_name="uvicorn.error")
        impacts = (tmp_path / "plugin_impacts.json").read_text("utf-8")
        assert len(sessions) == 1
        assert sessions[0].source == "automatic"
        assert sessions[0].session_id in impacts
        assert calls
