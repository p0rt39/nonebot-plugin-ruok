"""SSE (Server-Sent Events) endpoint and EventBus for real-time WebUI updates."""
from __future__ import annotations

import asyncio
import json
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

    Mixes periodic full-status heartbeats with event-driven session updates.
    """
    # Subscribe to session lifecycle events
    session_q = event_bus_ref.subscribe("session_update")
    status_q = event_bus_ref.subscribe("status_update")

    try:
        while True:
            # ── Periodic status heartbeat ──
            try:
                status = await collect_status_fn()
                yield _sse_event(
                    "status",
                    {
                        "overall": status.overall,
                        "timestamp": status.timestamp.isoformat(),
                        "connections_online": sum(
                            1 for c in status.connections if c.connected
                        ),
                        "connections_total": len(status.connections),
                    },
                )
            except Exception as exc:
                logger.warning(f"RuOK SSE: status collection failed: {exc}")

            # ── Wait for events or heartbeat tick ──
            deadline = asyncio.get_event_loop().time() + config_ttl
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break

                try:
                    event = await asyncio.wait_for(
                        _merge_queues(session_q, status_q),
                        timeout=remaining,
                    )
                    yield _sse_event(event["event"], event["data"])
                except asyncio.TimeoutError:
                    break  # heartbeat time
    except asyncio.CancelledError:
        pass
    finally:
        event_bus_ref.unsubscribe("session_update", session_q)
        event_bus_ref.unsubscribe("status_update", status_q)


async def _merge_queues(
    session_q: asyncio.Queue[dict[str, Any]],
    status_q: asyncio.Queue[dict[str, Any]],
) -> dict[str, Any]:
    """Race two queues — return whichever produces an event first."""
    done, _ = await asyncio.wait(
        [
            asyncio.create_task(session_q.get()),
            asyncio.create_task(status_q.get()),
        ],
        return_when=asyncio.FIRST_COMPLETED,
    )
    return await next(iter(done))


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE message."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
