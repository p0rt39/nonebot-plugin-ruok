"""SSE (Server-Sent Events) endpoint and EventBus for real-time WebUI updates."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import StreamingResponse
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
) -> str:
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
        from ..collector import collect_fast_metrics, _network_tracker

        tick_count = int(config_ttl)
        ticks_since_metrics = 0
        metrics_interval = 3

        while True:
            try:
                status = await collect_status_fn()
                cached_overall = status.overall
                cached_connections = {
                    "online": sum(1 for c in status.connections if c.connected),
                    "total": len(status.connections),
                }
                yield _sse_event(
                    "status",
                    {
                        "overall": cached_overall,
                        "timestamp": status.timestamp.isoformat(),
                        "connections_online": cached_connections["online"],
                        "connections_total": cached_connections["total"],
                    },
                )
            except Exception as exc:
                logger.warning(f"RuOK SSE: status collection failed: {exc}")

            for _ in range(tick_count):
                await asyncio.sleep(1.0)
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
                if ticks_since_metrics >= metrics_interval:
                    ticks_since_metrics = 0
                    try:
                        fm = await collect_fast_metrics()
                        nr = _network_tracker.get_rate()
                        yield _sse_event(
                            "metrics",
                            {
                                "cpu": fm.cpu_percent,
                                "cpu_cores": fm.cpu_per_core,
                                "mem_pct": fm.memory_percent,
                                "mem_used": fm.memory_used,
                                "mem_total": fm.memory_total,
                                "swap_pct": fm.swap_percent,
                                "swap_used": fm.swap_used,
                                "swap_total": fm.swap_total,
                                "load_1m": fm.load_1m,
                                "load_5m": fm.load_5m,
                                "load_15m": fm.load_15m,
                                "procs": fm.process_count,
                                "uptime": fm.uptime_seconds,
                                "net_up": nr.bytes_sent_per_sec,
                                "net_down": nr.bytes_recv_per_sec,
                            },
                        )
                    except Exception as exc:
                        logger.warning(f"RuOK SSE: fast metrics failed: {exc}")
    except asyncio.CancelledError:
        pass
    finally:
        event_bus_ref.unsubscribe("session_update", session_q)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE message."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
