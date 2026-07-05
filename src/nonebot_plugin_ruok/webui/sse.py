"""SSE (Server-Sent Events) endpoint and EventBus for real-time WebUI updates."""

from __future__ import annotations

import json
import asyncio
from typing import Any
from datetime import datetime, timezone
from collections.abc import AsyncGenerator

from nonebot import logger


class EventBus:
    """Simple pub/sub event bus backed by asyncio.Queue.

    Components (collector, API, etc.) publish typed events;
    SSE endpoints subscribe and stream them to browsers.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Push an event to all subscribers of *topic*."""
        subs = self._subscribers.get(topic, [])
        if not subs:
            return
        for q in subs:
            try:
                q.put_nowait({"event": topic, "data": data})
            except asyncio.QueueFull:
                # Drop slow consumer
                pass

    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        """Create a subscription queue for *topic*."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(topic, []).append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscription queue."""
        subs = self._subscribers.get(topic)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                del self._subscribers[topic]


# ── Global singleton ──

event_bus = EventBus()


# ── SSE endpoint factory ──


async def sse_event_generator(
    config_ttl: float,
    collect_status_fn,
    event_bus_ref: EventBus,
) -> AsyncGenerator[str, None]:
    """Async generator yielding SSE text/event-stream content.

    - Full status (event: status) every *config_ttl* seconds.
    - Lightweight tick (event: tick) every 1 second with cached timestamp.
    - Session updates (event: session_update) pushed immediately.
    """
    session_q = event_bus_ref.subscribe("session_update")

    # Cached values from the last full status collection
    cached_overall: str = "unknown"
    cached_connections: dict[str, int] = {"online": 0, "total": 0}
    tick_count = int(config_ttl)  # number of 1s ticks between full collections

    try:
        from ..collector import (
            _disk_tracker,
            _network_tracker,
            collect_fast_metrics,
            collect_process_snapshot,
        )

        tick_count = int(config_ttl)
        metrics_interval = 1  # fire lightweight collection every tick
        ticks_since_metrics = 0
        _tick_index = 0  # for alternating heavy/light collection

        # Background metrics task to avoid blocking tick interval
        _metrics_task: asyncio.Task | None = None
        _latest_metrics: dict[str, Any] | None = None
        _cached_proc: dict[str, Any] = {}  # carry forward process data on light cycles

        async def _collect_metrics_background(full: bool = True) -> None:
            """Collect metrics in background, update cache on completion.

            *full=True*: collect everything including process snapshot.
            *full=False*: reuse last process snapshot for faster 1s cycle.
            """
            nonlocal _latest_metrics, _cached_proc
            try:
                metrics_snap = await collect_fast_metrics()
                net_rate = _network_tracker.get_rate()
                disk_io, disk_per = _disk_tracker.get_rate()

                metrics: dict[str, Any] = {
                    "cpu": metrics_snap.cpu_percent,
                    "cpu_cores": metrics_snap.cpu_per_core,
                    "mem_pct": metrics_snap.memory_percent,
                    "mem_used": metrics_snap.memory_used,
                    "mem_total": metrics_snap.memory_total,
                    "swap_pct": metrics_snap.swap_percent,
                    "swap_used": metrics_snap.swap_used,
                    "swap_total": metrics_snap.swap_total,
                    "load_1m": metrics_snap.load_1m,
                    "load_5m": metrics_snap.load_5m,
                    "load_15m": metrics_snap.load_15m,
                    "procs": metrics_snap.process_count,
                    "uptime": metrics_snap.uptime_seconds,
                    "boot_time": metrics_snap.boot_time_epoch,
                    "bot_start_time": metrics_snap.bot_process_create_time,
                    "bot_rss": metrics_snap.bot_rss_bytes,
                    "cpu_temp": metrics_snap.cpu_temp,
                    "net_up": net_rate.bytes_sent_per_sec,
                    "net_down": net_rate.bytes_recv_per_sec,
                    "net_packets_up": net_rate.packets_sent_per_sec,
                    "net_packets_down": net_rate.packets_recv_per_sec,
                    "disk_read": disk_io.read_bytes_per_sec,
                    "disk_write": disk_io.write_bytes_per_sec,
                    "disk_read_count": disk_io.read_count_per_sec,
                    "disk_write_count": disk_io.write_count_per_sec,
                }

                # Per-disk rates (lightweight, always collect)
                metrics["disk_per"] = {
                    name: dr.model_dump(mode="json") for name, dr in disk_per.items()
                }

                # Process snapshot (heavy — only on full cycles, ~3s cadence)
                if full:
                    proc_snapshot = await collect_process_snapshot()
                    _cached_proc = {
                        "bot_vms": proc_snapshot.bot_vms,
                        "bot_threads": proc_snapshot.bot_threads,
                        "bot_cpu": proc_snapshot.bot_cpu_percent,
                        "top_processes": [
                            p.model_dump(mode="json")
                            for p in proc_snapshot.top_processes
                        ],
                    }
                # Always include process data (fresh or cached)
                metrics.update(_cached_proc)

                _latest_metrics = metrics
            except Exception as exc:
                logger.warning(f"RuOK SSE: fast metrics failed: {exc}")

        # ── Background status collection (also async to avoid blocking ticks) ──
        _status_task: asyncio.Task | None = None
        _cached_status: dict[str, Any] | None = None
        # Prime: fire immediately so first status event doesn't wait 10s
        _tick_counter = tick_count  # emit status on first iteration

        async def _collect_status_background() -> None:
            """Collect full status in background, update cache on completion."""
            nonlocal cached_overall, cached_connections, _cached_status
            try:
                status = await collect_status_fn()
                cached_overall = status.overall
                cached_connections = {
                    "online": sum(1 for c in status.connections if c.connected),
                    "total": len(status.connections),
                }
                _cached_status = {
                    "overall": cached_overall,
                    "timestamp": status.timestamp.isoformat(),
                    "connections_online": cached_connections["online"],
                    "connections_total": cached_connections["total"],
                    "status_reasons": status.status_reasons,
                }
            except Exception as exc:
                logger.warning(f"RuOK SSE: status collection failed: {exc}")

        while True:
            # Fire background status collection when due and not already running
            if _tick_counter >= tick_count:
                _tick_counter = 0
                if _status_task is None or _status_task.done():
                    _status_task = asyncio.create_task(_collect_status_background())

            # Emit cached status result if ready
            if _cached_status is not None:
                yield _sse_event("status", _cached_status)
                _cached_status = None

            for _ in range(tick_count):
                await asyncio.sleep(1.0)
                _tick_counter += 1
                while not session_q.empty():
                    try:
                        event = session_q.get_nowait()
                        yield _sse_event(event["event"], event["data"])
                    except asyncio.QueueEmpty:
                        break
                yield _sse_event(
                    "tick",
                    {
                        "overall": cached_overall,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "connections_online": cached_connections["online"],
                        "connections_total": cached_connections["total"],
                    },
                )
                ticks_since_metrics += 1
                _tick_index += 1
                if ticks_since_metrics >= metrics_interval:
                    ticks_since_metrics = 0
                    # Skip if previous task still running (overlap protection)
                    if _metrics_task is None or _metrics_task.done():
                        # Full collection (with process snapshot) every ~3s
                        is_full = _tick_index % 3 == 0
                        _metrics_task = asyncio.create_task(
                            _collect_metrics_background(full=is_full)
                        )
                # If background task finished, emit cached result
                if _latest_metrics is not None:
                    yield _sse_event("metrics", _latest_metrics)
                    _latest_metrics = None
    except asyncio.CancelledError:
        pass
    finally:
        event_bus_ref.unsubscribe("session_update", session_q)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE message."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
