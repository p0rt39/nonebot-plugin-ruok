"""Tests for collector.py — system metrics, trackers, process snapshot.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""
import pytest


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
