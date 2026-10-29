"""Tests for RUOK configuration validation."""

import math

import pytest
from pydantic import ValidationError


def test_cache_ttl_defaults_to_ten_seconds() -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    assert ScopedConfig().cache_ttl == 10.0


def test_webui_session_defaults_to_expiring_cookie() -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    config = ScopedConfig()

    assert config.webui_session_ttl == 3600


@pytest.mark.parametrize("webui_session_ttl", [0, -1, None, 1.5, math.inf, math.nan])
def test_webui_session_ttl_must_be_a_positive_integer(
    webui_session_ttl: object,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    with pytest.raises(ValidationError):
        ScopedConfig.model_validate({"webui_session_ttl": webui_session_ttl})


@pytest.mark.parametrize("cache_ttl", [0, -1, 0.49, math.inf, math.nan])
def test_cache_ttl_rejects_non_positive_or_non_finite_values(cache_ttl: float) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    with pytest.raises(ValidationError):
        ScopedConfig(cache_ttl=cache_ttl)


@pytest.mark.parametrize("cache_ttl", [0.5, 1.0, 1.25])
def test_cache_ttl_accepts_supported_values(cache_ttl: float) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    assert ScopedConfig(cache_ttl=cache_ttl).cache_ttl == cache_ttl


def test_sse_tick_count_has_at_least_one_tick() -> None:
    from nonebot_plugin_ruok.webui.sse import _tick_count_from_ttl

    assert _tick_count_from_ttl(0.1) == 1
    assert _tick_count_from_ttl(0) == 1
    assert _tick_count_from_ttl(-10) == 1
    assert _tick_count_from_ttl(1.1) == 2


@pytest.mark.parametrize("cache_ttl", [math.inf, math.nan])
def test_sse_tick_count_rejects_non_finite_values(cache_ttl: float) -> None:
    from nonebot_plugin_ruok.webui.sse import _tick_count_from_ttl

    with pytest.raises(ValueError, match="finite"):
        _tick_count_from_ttl(cache_ttl)


async def test_sse_reports_collection_failure_with_last_known_state() -> None:
    """A failed collection must be visible to clients, not silently stale."""
    import json
    from datetime import datetime, timezone

    from nonebot_plugin_ruok.webui.sse import EventBus, sse_event_generator

    class _Status:
        def __init__(self) -> None:
            self.overall = "degraded"
            self.timestamp = datetime.now(timezone.utc)
            self.connections: list[object] = []
            self.status_reasons = ["degraded for test"]

    calls = {"count": 0}

    async def collect():
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("collector exploded")
        return _Status()

    events: list[tuple[str, dict]] = []
    generator = sse_event_generator(0.5, collect, EventBus())
    try:
        async for chunk in generator:
            name, payload = chunk.strip().split("\n", 1)
            events.append((name.removeprefix("event: "), json.loads(payload[6:])))
            if len([e for e in events if e[0] == "status"]) >= 2:
                break
    finally:
        await generator.aclose()

    statuses = [payload for name, payload in events if name == "status"]
    assert statuses[0]["collection_ok"] is True
    assert statuses[0]["overall"] == "degraded"
    # The failure keeps the last known overall value and flags itself.
    assert statuses[1]["collection_ok"] is False
    assert statuses[1]["overall"] == "degraded"
    assert "collector exploded" in statuses[1]["collection_error"]
    assert statuses[1]["timestamp"] == statuses[0]["timestamp"]
