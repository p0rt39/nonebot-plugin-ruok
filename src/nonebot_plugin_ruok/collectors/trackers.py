"""Network and disk I/O rate trackers — delta-based per-second counters."""

from __future__ import annotations

import time

from ..protocol import DiskIORate, NetworkRate


class NetworkRateTracker:
    """Track per-second network transfer rate via delta of io counters."""

    def __init__(self) -> None:
        self._prev: dict[str, float] = {}
        self._prev_time: float = 0.0

    def get_rate(self) -> NetworkRate:
        """Return current bytes/sec rate, or zeros on first call."""
        import psutil

        now = time.time()
        try:
            net = psutil.net_io_counters()
        except (psutil.AccessDenied, OSError, AttributeError):
            return NetworkRate()

        cur = {
            "bs": float(net.bytes_sent),
            "br": float(net.bytes_recv),
            "ps": float(net.packets_sent),
            "pr": float(net.packets_recv),
        }

        if not self._prev or self._prev_time == 0.0:
            self._prev = cur
            self._prev_time = now
            return NetworkRate()

        elapsed = now - self._prev_time
        if elapsed <= 0:
            return NetworkRate()

        rate = NetworkRate(
            bytes_sent_per_sec=(cur["bs"] - self._prev["bs"]) / elapsed,
            bytes_recv_per_sec=(cur["br"] - self._prev["br"]) / elapsed,
            packets_sent_per_sec=(cur["ps"] - self._prev["ps"]) / elapsed,
            packets_recv_per_sec=(cur["pr"] - self._prev["pr"]) / elapsed,
        )
        self._prev = cur
        self._prev_time = now
        return rate


# Singleton
_network_tracker = NetworkRateTracker()


class DiskRateTracker:
    """Track per-second disk I/O rate via delta of io counters (per-disk)."""

    def __init__(self) -> None:
        self._prev: dict[str, dict[str, float]] = {}
        self._prev_time: float = 0.0

    def get_rate(self) -> tuple[DiskIORate, dict[str, DiskIORate]]:
        """Return (aggregated_rate, per_disk_rates) or zeros on first call."""
        import psutil

        now = time.time()
        try:
            per_disk = psutil.disk_io_counters(perdisk=True)
        except (psutil.AccessDenied, OSError, AttributeError, RuntimeError):
            return DiskIORate(), {}

        if not per_disk:
            return DiskIORate(), {}

        cur: dict[str, dict[str, float]] = {}
        for name, io in per_disk.items():
            cur[name] = {
                "rb": float(io.read_bytes),
                "wb": float(io.write_bytes),
                "rc": float(io.read_count),
                "wc": float(io.write_count),
            }

        if not self._prev or self._prev_time == 0.0:
            self._prev = cur
            self._prev_time = now
            return DiskIORate(), {}

        elapsed = now - self._prev_time
        if elapsed <= 0:
            return DiskIORate(), {}

        agg = DiskIORate()
        per_disk_rates: dict[str, DiskIORate] = {}

        for name, c in cur.items():
            p = self._prev.get(name, c)
            dr = DiskIORate(
                read_bytes_per_sec=(c["rb"] - p["rb"]) / elapsed,
                write_bytes_per_sec=(c["wb"] - p["wb"]) / elapsed,
                read_count_per_sec=(c["rc"] - p["rc"]) / elapsed,
                write_count_per_sec=(c["wc"] - p["wc"]) / elapsed,
            )
            per_disk_rates[name] = dr
            agg.read_bytes_per_sec += dr.read_bytes_per_sec
            agg.write_bytes_per_sec += dr.write_bytes_per_sec
            agg.read_count_per_sec += dr.read_count_per_sec
            agg.write_count_per_sec += dr.write_count_per_sec

        self._prev = cur
        self._prev_time = now
        return agg, per_disk_rates


# Singleton
_disk_tracker = DiskRateTracker()
