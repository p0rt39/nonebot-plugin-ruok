from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(config, data_dir: Path) -> TestClient:
    from starlette.middleware.sessions import SessionMiddleware

    from nonebot_plugin_ruok.api import create_ruok_router
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.webui.router import create_webui_router

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(WebUIAuth(config.webui_password).create_router())
    app.include_router(create_ruok_router(config, data_dir))
    app.include_router(create_webui_router(config, data_dir))
    return TestClient(app)


def _save_notification_rules(config, data_dir: Path, rules) -> None:
    from nonebot_plugin_ruok.collectors.notifications import _save_rules

    _save_rules(data_dir, rules)


def _load_notification_rules(config, data_dir: Path):
    from nonebot_plugin_ruok.collectors.notifications import _load_rules

    return _load_rules(data_dir, config)


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    assert client.get("/ruok/api/sessions").status_code == 401
    header_response = client.get(
        "/ruok/api/sessions",
        headers={"X-RuOK-API-Key": "secret"},
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


def test_webui_plaintext_password_login(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(webui_password="mysecret"), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"password": "mysecret"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def test_webui_protected_action_requires_login(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(webui_password="mysecret"), tmp_path)
    response = client.post(
        "/ruok/_actions/module-upsert",
        data={"name": "demo"},
    )

    assert response.status_code == 401


def test_dashboard_trends_partial_uses_webui_metrics_context(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import MetricPoint
    from nonebot_plugin_ruok.collector import MetricsStore

    MetricsStore.append(
        tmp_path,
        MetricPoint(
            ts=datetime.now(timezone.utc).isoformat(),
            cpu_percent=12.5,
            memory_percent=34.5,
        ),
    )
    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    response = client.get("/ruok?_partial=dashboard-trends")

    assert response.status_code == 200
    assert "fetch('/ruok/api/metrics/history?hours=1')" not in response.text
    assert "/ruok/_partials/dashboard-trends-data?hours=1" in response.text
    assert "var initialMetrics =" in response.text
    assert '"cpu_percent": 12.5' in response.text


def test_dashboard_trends_data_uses_webui_auth_not_api_key(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import MetricPoint
    from nonebot_plugin_ruok.collector import MetricsStore

    MetricsStore.append(
        tmp_path,
        MetricPoint(
            ts=datetime.now(timezone.utc).isoformat(),
            cpu_percent=22.5,
            memory_percent=44.5,
        ),
    )
    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    response = client.get("/ruok/_partials/dashboard-trends-data?hours=1")

    assert response.status_code == 200
    assert response.json()[0]["cpu_percent"] == 22.5


def test_dashboard_trends_do_not_replace_canvas_with_htmx(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert '<div id="dashboard-trends">' in response.text
    assert "ruok:metrics" in response.text


def test_notifications_page_uses_form_panel_and_card_actions(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)

    response = client.get("/ruok/notifications")

    assert response.status_code == 200
    assert 'id="notification-form-panel"' in response.text
    assert 'id="notifications-container"' in response.text
    assert 'hx-target="closest details"' not in response.text
    assert "notification-edit-form?" in response.text
    assert 'hx-post="/ruok/_actions/notification-delete"' in response.text


def test_notification_edit_form_loads_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = ScopedConfig()
    rule_name = "紧急 通知/a"
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name=rule_name, channels=["webhook"])],
    )
    client = _client(config, tmp_path)

    response = client.get(
        "/ruok/_actions/notification-edit-form",
        params={"name": rule_name},
    )

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert f'value="{rule_name}"' in response.text


def test_notification_edit_renames_without_duplicate(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = ScopedConfig()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="old/name")],
    )
    client = _client(config, tmp_path)

    response = client.post(
        "/ruok/_actions/notification-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "enabled": "on",
            "on_status": "pending",
            "on_module": "",
            "cooldown_minutes": "15",
            "channels": "bot_dm,webhook",
            "webhook_url": "https://example.com/hook",
        },
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 200
    assert [rule.name for rule in rules] == ["new name"]
    assert rules[0].channels == ["bot_dm", "webhook"]
    assert response.headers["HX-Trigger"]


def test_notification_rename_conflict_returns_400(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = ScopedConfig()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="first"), NotificationRule(name="second")],
    )
    client = _client(config, tmp_path)

    response = client.post(
        "/ruok/_actions/notification-upsert",
        data={
            "original_name": "first",
            "name": "second",
            "enabled": "on",
        },
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 400
    assert [rule.name for rule in rules] == ["first", "second"]


def test_notification_delete_accepts_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = ScopedConfig()
    rule_name = "紧急 通知/a"
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name=rule_name), NotificationRule(name="keep")],
    )
    client = _client(config, tmp_path)

    response = client.post(
        "/ruok/_actions/notification-delete",
        data={"name": rule_name},
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 200
    assert [rule.name for rule in rules] == ["keep"]
