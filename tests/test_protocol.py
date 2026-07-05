"""Tests for Pydantic data models in protocol.py.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""
import pytest


class TestFastMetricsSnapshot:
    def test_default_values(self) -> None:
        from nonebot_plugin_ruok.protocol import FastMetricsSnapshot

        fm = FastMetricsSnapshot()
        assert fm.cpu_percent == 0.0
        assert fm.cpu_per_core == []
        assert fm.memory_percent == 0.0
        assert fm.memory_used == 0
        assert fm.memory_total == 0
        assert fm.load_1m is None
        assert fm.process_count == 0
        assert fm.uptime_seconds == 0
        assert fm.boot_time_epoch == 0.0
        assert fm.bot_process_create_time == 0.0
        assert fm.bot_rss_bytes == 0
        assert fm.cpu_temp is None

    def test_cpu_percent_range(self) -> None:
        from nonebot_plugin_ruok.protocol import FastMetricsSnapshot

        fm = FastMetricsSnapshot(cpu_percent=75.5)
        assert 0 <= fm.cpu_percent <= 100
        fm2 = FastMetricsSnapshot(cpu_percent=0.0)
        assert fm2.cpu_percent == 0.0
        fm3 = FastMetricsSnapshot(cpu_percent=100.0)
        assert fm3.cpu_percent == 100.0

    def test_serialization(self) -> None:
        from nonebot_plugin_ruok.protocol import FastMetricsSnapshot

        fm = FastMetricsSnapshot(cpu_percent=45.2, cpu_per_core=[30.0, 50.0],
                                 load_1m=1.5, cpu_temp=48.0)
        data = fm.model_dump(mode="json")
        assert data["cpu_percent"] == 45.2
        assert data["cpu_per_core"] == [30.0, 50.0]
        assert data["load_1m"] == 1.5
        assert data["cpu_temp"] == 48.0


class TestDiskIORate:
    def test_default_values(self) -> None:
        from nonebot_plugin_ruok.protocol import DiskIORate

        dr = DiskIORate()
        assert dr.read_bytes_per_sec == 0.0
        assert dr.write_bytes_per_sec == 0.0
        assert dr.read_count_per_sec == 0.0
        assert dr.write_count_per_sec == 0.0

    def test_serialization(self) -> None:
        from nonebot_plugin_ruok.protocol import DiskIORate

        dr = DiskIORate(read_bytes_per_sec=1024.5, write_bytes_per_sec=512.0)
        data = dr.model_dump(mode="json")
        assert data["read_bytes_per_sec"] == 1024.5
        assert data["write_bytes_per_sec"] == 512.0


class TestProcessInfo:
    def test_default_values(self) -> None:
        from nonebot_plugin_ruok.protocol import ProcessInfo

        pi = ProcessInfo()
        assert pi.name == ""
        assert pi.pid == 0
        assert pi.cpu_percent == 0.0
        assert pi.mem_rss == 0


class TestProcessSnapshot:
    def test_default_values(self) -> None:
        from nonebot_plugin_ruok.protocol import ProcessSnapshot

        ps = ProcessSnapshot()
        assert ps.bot_rss == 0
        assert ps.bot_vms == 0
        assert ps.bot_threads == 0
        assert ps.bot_cpu_percent == 0.0
        assert ps.top_processes == []

    def test_top_processes_list(self) -> None:
        from nonebot_plugin_ruok.protocol import ProcessInfo, ProcessSnapshot

        procs = [
            ProcessInfo(name="python", pid=1234, cpu_percent=25.0, mem_rss=256000),
            ProcessInfo(name="chrome", pid=5678, cpu_percent=10.0, mem_rss=512000),
        ]
        ps = ProcessSnapshot(top_processes=procs)
        assert len(ps.top_processes) == 2
        assert ps.top_processes[0].name == "python"


class TestNetworkRate:
    def test_default_values(self) -> None:
        from nonebot_plugin_ruok.protocol import NetworkRate

        nr = NetworkRate()
        assert nr.bytes_sent_per_sec == 0.0
        assert nr.bytes_recv_per_sec == 0.0
        assert nr.packets_sent_per_sec == 0.0
        assert nr.packets_recv_per_sec == 0.0
