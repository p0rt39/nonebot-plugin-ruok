"""Tests for RUOK configuration validation."""

import math

import pytest
from pydantic import ValidationError


def test_cache_ttl_defaults_to_ten_seconds() -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    assert ScopedConfig().cache_ttl == 10.0


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
