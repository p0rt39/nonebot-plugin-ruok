from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from webui_test_utils import (
    _client,
    _login_user,
    _login_admin,
    _webui_config,
)


def test_webui_user_dashboard_hides_admin_surfaces(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "/ruok/sessions" not in response.text
    assert "/ruok/modules" not in response.text
    assert "/ruok/notifications" not in response.text
    assert "/ruok/users" in response.text
    assert "dashboard-trends" not in response.text
    assert "dashboard-system" not in response.text
    assert "/ruok bind" in response.text
    assert "我的上报" in response.text


def test_webui_unbound_user_dashboard_does_not_refresh_auth_key(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    registered = WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    first = client.get("/ruok")
    second = client.get("/ruok")
    stored = WebUIAuth(config, tmp_path).get_user("alice")

    assert first.status_code == 200
    assert second.status_code == 200
    assert registered.auth_key in first.text
    assert registered.auth_key in second.text
    assert stored is not None
    assert stored.auth_key_value == registered.auth_key


def test_webui_unbound_user_dashboard_shows_expired_auth_key(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    users_path = tmp_path / "webui_users.json"
    users = json.loads(users_path.read_text("utf-8"))
    users["users"][0]["auth_key_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    users_path.write_text(json.dumps(users), encoding="utf-8")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "当前绑定码已过期" in response.text
    assert "前往用户页" in response.text


def test_webui_user_cannot_access_admin_routes(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    assert client.get("/ruok/modules").status_code == 403
    assert client.get("/ruok/notifications").status_code == 403
    assert client.get("/ruok/sessions").status_code == 403
    assert client.get("/ruok/_partials/dashboard-trends-data").status_code == 403
    assert client.get("/ruok/users").status_code == 200
    assert (
        client.post(
            "/ruok/_actions/module-upsert",
            data={"name": "demo"},
        ).status_code
        == 403
    )


def test_webui_user_must_bind_before_manual_report(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "music", "description": "boom"},
    )

    assert response.status_code == 403
    assert "绑定平台账号" in response.text


def test_webui_bound_user_report_uses_bound_user_id_and_sees_own_sessions(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.collectors.sessions import list_sessions, create_session

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    create_session(
        tmp_path,
        "music",
        "other user issue",
        ReporterInfo(type="user", user_id="20002"),
    )
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "music", "description": "mine"},
    )

    sessions = list_sessions(tmp_path)
    mine = [session for session in sessions if session.description == "mine"]
    assert response.status_code == 200
    assert len(mine) == 1
    assert mine[0].reporter.user_id == "10001"
    assert mine[0].reporter.platform == "webui"
    assert "mine" in response.text
    assert "alice（OneBot V11: 10001）" in response.text
    assert "other user issue" not in response.text


def test_dashboard_trends_partial_uses_webui_metrics_context(tmp_path: Path) -> None:
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
    client = _client(_webui_config(api_key="secret"), tmp_path)
    _login_admin(client)

    response = client.get("/ruok?_partial=dashboard-trends")

    assert response.status_code == 200
    assert "fetch('/ruok/api/metrics/history?hours=1')" not in response.text
    assert "/ruok/_partials/dashboard-trends-data?hours=1" in response.text
    assert "var initialMetrics =" in response.text
    assert '"cpu_percent": 12.5' in response.text


def test_dashboard_trends_data_uses_webui_auth_not_api_key(tmp_path: Path) -> None:
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
    client = _client(_webui_config(api_key="secret"), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/_partials/dashboard-trends-data?hours=1")

    assert response.status_code == 200
    assert response.json()[0]["cpu_percent"] == 22.5


def test_dashboard_trends_do_not_replace_canvas_with_htmx(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert '<div id="dashboard-trends">' in response.text
    assert "ruok:metrics" in response.text
