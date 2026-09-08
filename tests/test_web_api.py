from __future__ import annotations

from pathlib import Path

from webui_test_utils import _client


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    assert client.get("/ruok/api/sessions").status_code == 401
    header_response = client.get(
        "/ruok/api/sessions",
        headers={"X-RUOK-API-Key": "secret"},
    )
    bearer_response = client.get(
        "/ruok/api/sessions",
        headers={"Authorization": "Bearer secret"},
    )
    assert header_response.status_code == 200
    assert bearer_response.status_code == 200


def test_sessions_stats_route_is_not_shadowed(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)
    response = client.get("/ruok/api/sessions/stats")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_health_endpoint_caches_successful_status_collection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import AggregatedStatus

    calls = 0

    async def fake_collect(config, data_dir):
        nonlocal calls
        calls += 1
        return AggregatedStatus(overall="available")

    monkeypatch.setattr("nonebot_plugin_ruok.api.collect_all_statuses", fake_collect)
    client = _client(ScopedConfig(), tmp_path)

    first = client.get("/ruok/api/health")
    second = client.get("/ruok/api/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1


def test_api_session_datetime_filters_accept_naive_utc_and_reject_invalid(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))
    client = _client(ScopedConfig(), tmp_path)

    valid = client.get("/ruok/api/sessions?after=2000-01-01T00:00:00")
    invalid = client.get("/ruok/api/sessions?after=not-a-datetime")

    assert valid.status_code == 200
    assert len(valid.json()) == 1
    assert invalid.status_code == 400


def test_api_create_session_dispatches_rule_notification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    calls = []

    async def fake_dispatch(session, config, data_dir):
        calls.append(session.session_id)

    monkeypatch.setattr(
        "nonebot_plugin_ruok.api.dispatch_notification",
        fake_dispatch,
    )
    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    response = client.post(
        "/ruok/api/sessions",
        headers={"X-RUOK-API-Key": "secret"},
        json={"module_name": "music", "description": "api issue"},
    )

    assert response.status_code == 200
    assert calls == [response.json()["session_id"]]
