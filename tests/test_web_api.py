from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections.abc import Iterable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "admin-secret"


def _client(
    config,
    data_dir: Path,
    *,
    superusers: Iterable[str] = (),
) -> TestClient:
    from starlette.middleware.sessions import SessionMiddleware

    from nonebot_plugin_ruok.api import create_ruok_router
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.webui.router import create_webui_router

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    auth = WebUIAuth(config, data_dir, superuser_provider=lambda: superusers)
    app.include_router(auth.create_router())
    app.include_router(create_ruok_router(config, data_dir))
    app.include_router(create_webui_router(config, data_dir, auth))
    return TestClient(app)


def _webui_config(**kwargs):
    from nonebot_plugin_ruok.config import ScopedConfig

    return ScopedConfig(webui_admin_password=ADMIN_PASSWORD, **kwargs)


def _login_admin(client: TestClient, password: str = ADMIN_PASSWORD) -> None:
    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def _login_user(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/ruok/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def _save_notification_rules(config, data_dir: Path, rules) -> None:
    from nonebot_plugin_ruok.collectors.notifications import _save_rules

    _save_rules(data_dir, rules)


def _load_notification_rules(config, data_dir: Path):
    from nonebot_plugin_ruok.collectors.notifications import _load_rules

    return _load_rules(data_dir, config)


def _upsert_module(data_dir: Path, module) -> None:
    from nonebot_plugin_ruok.collectors.modules import upsert_module

    upsert_module(data_dir, module)


def _module_names(config, data_dir: Path) -> list[str]:
    from nonebot_plugin_ruok.collectors.modules import list_modules

    return [module.name for module in list_modules(data_dir, config)]


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


def test_webui_admin_password_login(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def test_webui_without_admin_password_cannot_login(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": "anything"},
    )

    assert response.status_code == 200
    assert "RUOK__WEBUI_ADMIN_PASSWORD" in response.text


def test_webui_user_registers_with_auth_key(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.post(
        "/ruok/register",
        data={
            "username": "alice",
            "password": "secret",
            "password_confirm": "secret",
        },
    )

    assert response.status_code == 200
    assert "/ruok bind" in response.text
    users = json.loads((tmp_path / "webui_users.json").read_text("utf-8"))["users"]
    assert users[0]["username"] == "alice"
    assert users[0]["role"] == "user"
    assert users[0]["auth_key_hash"]


def test_webui_register_rejects_admin_and_duplicate_user(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth, UserRegistrationError

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)

    with pytest.raises(UserRegistrationError):
        auth.register_user("admin", "secret")

    auth.register_user("alice", "secret")
    with pytest.raises(UserRegistrationError):
        auth.register_user("Alice", "secret")


def test_webui_auth_key_binding_expires_and_cannot_be_reused(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import (
        WebUIAuth,
        AuthKeyExpired,
        AuthKeyAlreadyUsed,
    )

    auth = WebUIAuth(_webui_config(), tmp_path)
    expired = auth.register_user("expired", "secret")
    users = json.loads((tmp_path / "webui_users.json").read_text("utf-8"))
    users["users"][0]["auth_key_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    (tmp_path / "webui_users.json").write_text(
        json.dumps(users, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(AuthKeyExpired):
        auth.bind_auth_key(expired.auth_key, "10001")

    active = auth.register_user("active", "secret")
    bound = auth.bind_auth_key(active.auth_key, "10002")

    assert bound.bound_user_id == "10002"
    assert auth.is_user_bound("10002")
    with pytest.raises(AuthKeyAlreadyUsed):
        auth.bind_auth_key(active.auth_key, "10003")


def test_webui_auth_loads_legacy_bound_qq(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth, hash_password

    (tmp_path / "webui_users.json").write_text(
        json.dumps(
            {
                "users": [
                    {
                        "username": "legacy",
                        "password_hash": hash_password("secret"),
                        "role": "user",
                        "bound_qq": "10002",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    user = WebUIAuth(_webui_config(), tmp_path).get_user("legacy")

    assert user is not None
    assert user.bound_user_id == "10002"


def test_webui_auth_rejects_duplicate_platform_binding(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth, AuthUserAlreadyBound

    auth = WebUIAuth(_webui_config(), tmp_path)
    first = auth.register_user("alice", "secret")
    second = auth.register_user("bob", "secret")
    auth.bind_auth_key(first.auth_key, "10002", "OneBot V11")

    with pytest.raises(AuthUserAlreadyBound):
        auth.bind_auth_key(second.auth_key, "10002", "OneBot V11")


def test_webui_superuser_bound_account_is_dynamic_admin(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10002", "OneBot V11")

    client = _client(config, tmp_path, superusers={"10002"})
    _login_user(client, "alice", "secret")

    response = client.get("/ruok/modules")
    assert response.status_code == 200
    assert "SUPERUSER" in client.get("/ruok/users").text

    client_without_superuser = _client(config, tmp_path)
    _login_user(client_without_superuser, "alice", "secret")
    assert client_without_superuser.get("/ruok/modules").status_code == 403

    users = json.loads((tmp_path / "webui_users.json").read_text("utf-8"))["users"]
    assert users[0]["role"] == "user"


def test_webui_login_ui_is_modern_auth_shell(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.get("/ruok/login")

    assert response.status_code == 200
    assert 'class="auth-shell"' in response.text
    assert 'class="auth-panel auth-card"' in response.text
    assert 'class="auth-form"' in response.text
    assert 'class="auth-input-wrap"' in response.text
    assert 'data-password-toggle="login-password"' in response.text
    assert 'aria-pressed="false"' in response.text
    assert 'autocomplete="username"' in response.text
    assert 'name="remember_username"' in response.text
    assert 'name="remember_login"' in response.text
    assert "/ruok/reset-password" in response.text
    assert ">显示<" not in response.text


def test_webui_register_password_toggle_is_icon_only(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.get("/ruok/register")

    assert response.status_code == 200
    assert 'data-password-toggle="register-password"' in response.text
    assert 'data-password-toggle="register-password-confirm"' in response.text
    assert 'class="password-toggle__icon"' in response.text
    assert ">显示<" not in response.text


def test_webui_remember_username_cookie(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.post(
        "/ruok/login",
        data={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "remember_username": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert client.cookies.get("ruok_login_username") == "admin"

    client.get("/ruok/logout", follow_redirects=False)
    login_page = client.get("/ruok/login")
    assert 'value="admin"' in login_page.text
    assert "remember_username" in login_page.text
    assert "checked" in login_page.text


def test_webui_remember_login_cookie_restores_session(tmp_path: Path) -> None:
    config = _webui_config()
    client = _client(config, tmp_path)

    response = client.post(
        "/ruok/login",
        data={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "remember_login": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    remember = client.cookies.get("ruok_remember")
    assert remember

    fresh = _client(config, tmp_path)
    fresh.cookies.set("ruok_remember", remember)
    restored = fresh.get("/ruok", follow_redirects=False)
    assert restored.status_code == 200
    assert "Overall" in restored.text


def test_webui_remember_login_invalidated_after_password_change(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    auth.register_user("alice", "secret")
    client = _client(config, tmp_path)
    response = client.post(
        "/ruok/login",
        data={
            "username": "alice",
            "password": "secret",
            "remember_login": "1",
        },
        follow_redirects=False,
    )
    remember = client.cookies.get("ruok_remember")
    assert response.status_code == 302
    assert remember

    auth.change_user_password("alice", "secret", "new-secret")

    fresh = _client(config, tmp_path)
    fresh.cookies.set("ruok_remember", remember)
    restored = fresh.get("/ruok", follow_redirects=False)
    assert restored.status_code == 401


def test_webui_reset_password_with_key(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    issued = auth.issue_password_reset_key("10001", "OneBot V11")
    client = _client(config, tmp_path)

    page = client.get("/ruok/reset-password")
    assert page.status_code == 200
    assert 'autocomplete="one-time-code"' in page.text

    changed = client.post(
        "/ruok/reset-password",
        data={
            "reset_key": issued.reset_key,
            "new_password": "new-secret",
            "new_password_confirm": "new-secret",
        },
    )

    assert changed.status_code == 200
    assert "密码已重设" in changed.text
    _login_user(_client(config, tmp_path), "alice", "new-secret")
    reused = client.post(
        "/ruok/reset-password",
        data={
            "reset_key": issued.reset_key,
            "new_password": "again",
            "new_password_confirm": "again",
        },
    )
    assert "reset key 无效" in reused.text


def test_webui_reset_password_key_expires(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    issued = auth.issue_password_reset_key("10001", "OneBot V11")
    users_path = tmp_path / "webui_users.json"
    users = json.loads(users_path.read_text("utf-8"))
    users["users"][0]["password_reset_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    users_path.write_text(json.dumps(users), encoding="utf-8")
    client = _client(config, tmp_path)

    expired = client.post(
        "/ruok/reset-password",
        data={
            "reset_key": issued.reset_key,
            "new_password": "new-secret",
            "new_password_confirm": "new-secret",
        },
    )

    assert "reset key 已过期" in expired.text


def test_webui_protected_action_requires_login(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    response = client.post(
        "/ruok/_actions/module-upsert",
        data={"name": "demo"},
    )

    assert response.status_code == 401


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
    assert "mine" in response.text
    assert "other user issue" not in response.text


def test_webui_admin_manual_report_uses_admin_identity(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "ruok", "description": "admin report"},
    )

    sessions = list_sessions(tmp_path)
    assert response.status_code == 200
    assert sessions[0].reporter.user_id == "webui-admin"


def test_webui_users_page_admin_can_manage_users(tmp_path: Path) -> None:
    config = _webui_config()
    client = _client(config, tmp_path)
    _login_admin(client)

    page = client.get("/ruok/users")
    assert page.status_code == 200
    assert "新增普通用户" in page.text
    assert "admin" in page.text

    created = client.post(
        "/ruok/_actions/user-create",
        data={"username": "紧急 用户/a", "password": "secret"},
    )
    created_trigger = json.loads(created.headers["HX-Trigger"])
    assert created.status_code == 200
    assert created_trigger == {"toast": "User created", "toastType": "success"}
    assert "/ruok bind" in created.text
    assert "紧急 用户/a" in created.text

    updated = client.post(
        "/ruok/_actions/user-update",
        data={
            "username": "紧急 用户/a",
            "new_username": "renamed user",
            "new_password": "new-secret",
        },
    )
    updated_trigger = json.loads(updated.headers["HX-Trigger"])
    assert updated.status_code == 200
    assert updated_trigger == {"toast": "User saved", "toastType": "success"}
    assert "renamed user" in updated.text

    _login_user(_client(config, tmp_path), "renamed user", "new-secret")

    issued = client.post(
        "/ruok/_actions/user-auth-key",
        data={"username": "renamed user"},
    )
    issued_trigger = json.loads(issued.headers["HX-Trigger"])
    assert issued.status_code == 200
    assert issued_trigger == {"toast": "Auth key issued", "toastType": "success"}
    assert "renamed user" in issued.text
    assert "/ruok bind" in issued.text

    cleared = client.post(
        "/ruok/_actions/user-clear-binding",
        data={"username": "renamed user"},
    )
    cleared_trigger = json.loads(cleared.headers["HX-Trigger"])
    assert cleared.status_code == 200
    assert cleared_trigger == {"toast": "Binding cleared", "toastType": "info"}

    deleted = client.post(
        "/ruok/_actions/user-delete",
        data={"username": "renamed user"},
    )
    deleted_trigger = json.loads(deleted.headers["HX-Trigger"])
    assert deleted.status_code == 200
    assert deleted_trigger == {"toast": "User deleted", "toastType": "info"}
    users = json.loads((tmp_path / "webui_users.json").read_text("utf-8"))["users"]
    assert users == []


def test_webui_users_page_filters_users(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    alice = auth.register_user("alice", "secret")
    bob = auth.register_user("bob", "secret")
    auth.bind_auth_key(alice.auth_key, "10001", "OneBot V11")
    auth.bind_auth_key(bob.auth_key, "20002", "Console")
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/users", params={"search": "10001"})

    assert response.status_code == 200
    assert "alice" in response.text
    assert "bob" not in response.text
    assert "搜索 “10001”" in response.text

    partial = client.get(
        "/ruok/users",
        params={"search": "console"},
        headers={"HX-Request": "true"},
    )

    assert partial.status_code == 200
    assert 'id="users-panel"' in partial.text
    assert "<!DOCTYPE html>" not in partial.text
    assert "<strong" in partial.text
    assert ">bob</strong>" in partial.text
    assert ">alice</strong>" not in partial.text


def test_webui_normal_user_can_change_password_rebind_and_delete(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    page = client.get("/ruok/users")
    assert page.status_code == 200
    assert "修改密码" in page.text
    assert "新增普通用户" not in page.text

    bad_password = client.post(
        "/ruok/_actions/account-password",
        data={
            "current_password": "wrong",
            "new_password": "new-secret",
            "new_password_confirm": "new-secret",
        },
    )
    assert bad_password.status_code == 400

    changed = client.post(
        "/ruok/_actions/account-password",
        data={
            "current_password": "secret",
            "new_password": "new-secret",
            "new_password_confirm": "new-secret",
        },
    )
    changed_trigger = json.loads(changed.headers["HX-Trigger"])
    assert changed.status_code == 200
    assert changed_trigger == {"toast": "Password changed", "toastType": "success"}
    _login_user(_client(config, tmp_path), "alice", "new-secret")

    rebind = client.post(
        "/ruok/_actions/account-rebind-key",
        data={"current_password": "new-secret"},
    )
    rebind_trigger = json.loads(rebind.headers["HX-Trigger"])
    assert rebind.status_code == 200
    assert rebind_trigger == {"toast": "Auth key issued", "toastType": "success"}
    assert "/ruok bind" in rebind.text
    rebound_user = WebUIAuth(config, tmp_path).get_user("alice")
    assert rebound_user is not None
    assert rebound_user.bound_user_id == "10001"

    deleted = client.post(
        "/ruok/_actions/account-delete",
        data={"current_password": "new-secret", "confirm_username": "alice"},
        follow_redirects=False,
    )
    assert deleted.status_code == 200
    assert deleted.headers["HX-Redirect"] == "/ruok/login"
    assert WebUIAuth(config, tmp_path).get_user("alice") is None


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


def test_session_detail_renders_manual_description_separately(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    session = create_session(
        tmp_path,
        "music",
        "用户描述第一行\n用户描述第二行",
        ReporterInfo(type="user", user_id="u1"),
        source="manual",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "用户提交说明" in response.text
    assert 'class="session-description-block"' in response.text
    assert "用户描述第一行" in response.text
    assert "<strong>描述</strong>" not in response.text


def test_webui_session_buttons_and_status_labels_are_consistent(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(
        tmp_path,
        "music",
        "pending issue",
        ReporterInfo(type="user", user_id="u1"),
        source="manual",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "待确认" in response.text
    assert "未解决" in response.text
    assert "Pending" not in response.text
    assert "Unsolved" not in response.text
    assert 'class="ruok-button-row--grid"' in response.text
    assert 'class="secondary outline ruok-button-danger"' in response.text


def test_session_detail_renders_automatic_traceback_as_code_block(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    session = create_session(
        tmp_path,
        "ruok",
        "**异常类型**: RuntimeError\n\n```\nTraceback line 1\nRuntimeError: boom\n```",
        ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "异常摘要" in response.text
    assert "Traceback" in response.text
    assert '<pre class="session-traceback"><code>Traceback line 1' in response.text
    assert "RuntimeError: boom" in response.text


def test_notifications_page_uses_form_panel_and_card_actions(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="紧急 通知/a")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/notifications")

    assert response.status_code == 200
    assert 'id="notification-form-panel"' in response.text
    assert 'id="notifications-container"' in response.text
    assert 'hx-target="closest details"' not in response.text
    assert (
        "/ruok/notifications/%E7%B4%A7%E6%80%A5%20%E9%80%9A%E7%9F%A5%2Fa"
        in response.text
    )
    assert "notification-edit-form?" not in response.text
    assert 'hx-post="/ruok/_actions/notification-delete"' not in response.text


def test_notification_detail_contains_edit_and_delete_actions(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition, NotificationRule

    config = _webui_config()
    rule_name = "紧急 通知/a"
    _upsert_module(tmp_path, ModuleDefinition(name="module_a", display_name="A"))
    _save_notification_rules(
        config,
        tmp_path,
        [
            NotificationRule(
                name=rule_name,
                on_status=["pending", "unsolved"],
                on_module=["module_a"],
                channels=["webhook"],
                webhook_url="https://example.com/hook",
            )
        ],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/notifications/%E7%B4%A7%E6%80%A5%20%E9%80%9A%E7%9F%A5%2Fa"
    )

    assert response.status_code == 200
    assert "← 返回规则列表" in response.text
    assert 'name="original_name"' in response.text
    assert 'name="return_to_detail"' in response.text
    assert 'hx-post="/ruok/_actions/notification-delete"' in response.text
    assert 'hx-target="closest details"' in response.text
    assert 'class="ruok-form-actions"' in response.text
    assert "notification-edit-form?" in response.text
    assert "取消" in response.text
    assert "待确认" in response.text
    assert "未解决" in response.text


def test_notification_form_supports_multiple_module_selection(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="module_a", display_name="A"))
    _upsert_module(tmp_path, ModuleDefinition(name="module_b", display_name="B"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/notification-upsert",
        data={
            "name": "multi modules",
            "enabled": "on",
            "on_status": "pending",
            "selected_modules": ["module_a", "module_b"],
            "on_module": "module_c\nmodule_a",
            "cooldown_minutes": "15",
            "channels": "bot_dm",
        },
    )

    rules = _load_notification_rules(config, tmp_path)
    rule = next(rule for rule in rules if rule.name == "multi modules")
    assert response.status_code == 200
    assert rule.on_module == ["module_a", "module_b", "module_c"]


def test_notification_edit_form_loads_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    rule_name = "紧急 通知/a"
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name=rule_name, channels=["webhook"])],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/_actions/notification-edit-form",
        params={"name": rule_name},
    )

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert f'value="{rule_name}"' in response.text


def test_notification_edit_form_checks_configured_modules(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition, NotificationRule

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="module_a", display_name="A"))
    _upsert_module(tmp_path, ModuleDefinition(name="module_b", display_name="B"))
    _save_notification_rules(
        config,
        tmp_path,
        [
            NotificationRule(
                name="module rule",
                on_module=["module_a", "module_c"],
            )
        ],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/_actions/notification-edit-form",
        params={"name": "module rule"},
    )

    assert response.status_code == 200
    assert 'name="selected_modules" value="module_a"' in response.text
    assert "checked" in response.text
    assert ">module_c</textarea>" in response.text


def test_notification_edit_renames_without_duplicate(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="old/name")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

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


def test_notification_detail_edit_redirects_to_detail(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="old/name")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/notification-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "enabled": "on",
            "on_status": "pending",
            "channels": "bot_dm",
            "return_to_detail": "true",
        },
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 200
    assert [rule.name for rule in rules] == ["new name"]
    assert response.headers["HX-Redirect"] == "/ruok/notifications/new%20name"


def test_notification_rename_conflict_returns_400(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="first"), NotificationRule(name="second")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

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
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    rule_name = "紧急 通知/a"
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name=rule_name), NotificationRule(name="keep")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/notification-delete",
        data={"name": rule_name},
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 200
    assert [rule.name for rule in rules] == ["keep"]


def test_notification_detail_delete_redirects_to_list(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import NotificationRule

    config = _webui_config()
    _save_notification_rules(
        config,
        tmp_path,
        [NotificationRule(name="old/name"), NotificationRule(name="keep")],
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/notification-delete",
        data={"name": "old/name", "redirect_to": "/ruok/notifications"},
    )

    rules = _load_notification_rules(config, tmp_path)
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/ruok/notifications"
    assert [rule.name for rule in rules] == ["keep"]


def test_modules_page_links_to_details_without_inline_actions(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    assert 'id="module-form-panel"' in response.text
    assert 'id="module-list"' in response.text
    assert 'hx-target="closest tr"' not in response.text
    assert "module-edit-form?" not in response.text
    assert 'hx-post="/ruok/_actions/module-delete"' not in response.text


def test_module_detail_contains_edit_and_delete_actions(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(
        tmp_path,
        ModuleDefinition(
            name="demo",
            display_name="Demo",
            plugins=["nonebot_plugin_ruok"],
        ),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules/demo")

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert 'name="return_to_detail"' in response.text
    assert 'hx-post="/ruok/_actions/module-delete"' in response.text
    assert 'class="ruok-picker"' in response.text
    assert 'data-field-name="selected_plugins"' in response.text
    assert 'name="selected_plugins" value="nonebot_plugin_ruok"' in response.text
    assert 'name="enabled"' not in response.text
    assert "← 返回模块列表" in response.text
    assert response.text.count("返回模块列表") == 1
    assert "取消" in response.text
    assert "module-edit-form?" in response.text
    assert 'hx-target="closest details"' in response.text
    assert 'class="ruok-form-actions"' in response.text
    assert "Back to Modules" not in response.text
    assert 'class="secondary outline ruok-button-danger"' in response.text


def test_module_edit_form_loads_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    module_name = "mod with/slash"
    _upsert_module(
        tmp_path,
        ModuleDefinition(
            name=module_name,
            display_name="特殊模块",
            plugins=["plugin_a"],
            description="desc",
        ),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/_actions/module-edit-form",
        params={"name": module_name},
    )

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert f'value="{module_name}"' in response.text
    assert "plugin_a" in response.text


def test_module_edit_renames_without_duplicate(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="old/name", display_name="Old"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "display_name": "New",
            "description": "updated",
            "plugins": "plugin_a,plugin_b",
        },
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 200
    assert "old/name" not in names
    assert "new name" in names
    assert response.headers["HX-Trigger"]


def test_module_detail_edit_redirects_and_accepts_multiple_plugins(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="old/name", display_name="Old"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "display_name": "New",
            "description": "updated",
            "selected_plugins": ["plugin_a", "plugin_b"],
            "plugins": "plugin_c\nplugin_a",
            "return_to_detail": "true",
        },
    )

    from nonebot_plugin_ruok.collectors.modules import get_module

    module = get_module(tmp_path, config, "new name")
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/ruok/modules/new%20name"
    assert module is not None
    assert module.plugins == ["plugin_a", "plugin_b", "plugin_c"]


def test_module_rename_conflict_returns_400(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="first", display_name="First"))
    _upsert_module(tmp_path, ModuleDefinition(name="second", display_name="Second"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "first",
            "name": "second",
            "display_name": "First",
        },
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 400
    assert "first" in names
    assert "second" in names


def test_module_delete_accepts_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    module_name = "mod with/slash"
    _upsert_module(tmp_path, ModuleDefinition(name=module_name))
    _upsert_module(tmp_path, ModuleDefinition(name="keep"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-delete",
        data={"name": module_name},
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 200
    assert module_name not in names
    assert "keep" in names
