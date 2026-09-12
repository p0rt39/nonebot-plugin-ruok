from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from webui_test_utils import (
    ADMIN_PASSWORD,
    _client,
    _login_user,
    _login_admin,
    _webui_config,
)


def test_webui_admin_password_login(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def test_webui_session_cookie_has_configured_ttl_and_default_flags(
    tmp_path: Path,
) -> None:
    client = _client(_webui_config(webui_session_ttl=600), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 302
    session_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.lower().startswith("session=")
    ).lower()
    assert "max-age=600" in session_cookie
    assert "httponly" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "secure" not in session_cookie


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
    assert users[0]["auth_key_value"]


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
        auth.bind_auth_key(expired.auth_key, "10001", "OneBot V11")
    expired_user = auth.get_user("expired")
    assert expired_user is not None
    assert expired_user.auth_key_hash is None
    assert expired_user.auth_key_value is None

    active = auth.register_user("active", "secret")
    bound = auth.bind_auth_key(active.auth_key, "10002", "OneBot V11")

    assert bound.bound_user_id == "10002"
    assert auth.is_user_bound("10002", "OneBot V11")
    with pytest.raises(AuthKeyAlreadyUsed):
        auth.bind_auth_key(active.auth_key, "10003", "OneBot V11")


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
    assert user.has_legacy_binding
    assert not user.is_bound


def test_webui_binding_and_reset_are_platform_scoped(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import (
        WebUIAuth,
        AuthKeyInvalid,
        UserManagementError,
    )

    auth = WebUIAuth(_webui_config(), tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10002", "OneBot V11")

    assert auth.is_user_bound("10002", "OneBot V11")
    assert not auth.is_user_bound("10002", "Console")
    with pytest.raises(UserManagementError):
        auth.issue_password_reset_key("10002", "Console")

    other = auth.register_user("bob", "secret")
    for missing_platform in (None, "", " "):
        assert not auth.is_user_bound("10002", missing_platform)
        with pytest.raises(UserManagementError):
            auth.issue_password_reset_key("10002", missing_platform)
        with pytest.raises(AuthKeyInvalid):
            auth.bind_auth_key(other.auth_key, "10002", missing_platform)
    auth.bind_auth_key(other.auth_key, "10002", "Console")
    assert auth.is_user_bound("10002", "Console")
    reset = auth.issue_password_reset_key("10002", "Console")
    assert reset.user.username == "bob"
    assert auth.reset_password_with_key(reset.reset_key, "new-secret").username == "bob"
    assert auth.verify_user_password("alice", "secret")


def test_webui_legacy_binding_requires_rebind_before_reset(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import (
        WebUIAuth,
        UserManagementError,
        hash_password,
    )

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
            }
        ),
        encoding="utf-8",
    )
    auth = WebUIAuth(_webui_config(), tmp_path)

    assert not auth.is_user_bound("10002", "OneBot V11")
    with pytest.raises(UserManagementError):
        auth.issue_password_reset_key("10002", "OneBot V11")


@pytest.mark.parametrize(
    "binding",
    [
        {"bound_qq": "10002"},
        {"bound_user_id": "10002"},
        {"bound_user_id": "10002", "bound_platform": ""},
        {"bound_user_id": "10002", "bound_platform": " "},
    ],
)
def test_webui_rebind_migrates_legacy_identity(
    tmp_path: Path, binding: dict[str, str]
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.webui.auth import WebUIAuth, hash_password
    from nonebot_plugin_ruok.collectors.sessions import create_session

    users_path = tmp_path / "webui_users.json"
    users_path.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "username": "legacy",
                        "password_hash": hash_password("secret"),
                        "role": "user",
                        **binding,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = _webui_config()
    auth = WebUIAuth(config, tmp_path, superuser_provider=lambda: {"10002"})
    current = auth.authenticate("legacy", "secret")
    assert current is not None
    assert not current.is_bound
    assert not current.is_admin
    session = create_session(
        tmp_path,
        "music",
        "private issue",
        ReporterInfo(type="user", user_id="10002", platform="OneBot V11"),
    )
    client = _client(config, tmp_path, superusers={"10002"})
    _login_user(client, "legacy", "secret")
    dashboard = client.get("/ruok")
    assert dashboard.status_code == 200
    assert "重新绑定" in dashboard.text
    assert session.session_id not in dashboard.text
    assert 'hx-post="/ruok/_actions/session-create"' not in dashboard.text
    assert client.get("/ruok/modules").status_code == 403
    denied = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "music", "description": "denied"},
    )
    assert denied.status_code == 403
    account = client.get("/ruok/users")
    assert "需重新绑定" in account.text
    assert 'hx-post="/ruok/_actions/account-auth-key"' in account.text
    assert client.post("/ruok/_actions/account-auth-key").status_code == 200
    stored = auth.get_user("legacy")
    assert stored is not None
    assert stored.auth_key_value
    migrated = auth.bind_auth_key(stored.auth_key_value, "10002", "OneBot V11")

    assert migrated.is_bound
    assert not migrated.has_legacy_binding
    assert auth.is_user_bound("10002", "OneBot V11")
    assert auth.is_superuser_account(migrated)
    assert client.get("/ruok/modules").status_code == 200
    persisted = json.loads(users_path.read_text("utf-8"))["users"][0]
    assert persisted["bound_platform"] == "OneBot V11"
    assert "bound_qq" not in persisted


@pytest.mark.parametrize("change", ["legacy_key", "rebind", "clear"])
def test_webui_reset_key_rejects_previous_identity(tmp_path: Path, change: str) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth, UserManagementError

    auth = WebUIAuth(_webui_config(), tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10002", "OneBot V11")
    reset = auth.issue_password_reset_key("10002", "OneBot V11")
    if change == "legacy_key":
        users_path = tmp_path / "webui_users.json"
        users = json.loads(users_path.read_text("utf-8"))
        users["users"][0].pop("password_reset_platform")
        users_path.write_text(json.dumps(users), encoding="utf-8")
    elif change == "rebind":
        key = auth.issue_auth_key("alice", allow_bound=True)
        auth.bind_auth_key(key.auth_key, "10002", "Console")
    else:
        auth.clear_binding("alice")

    with pytest.raises(UserManagementError):
        auth.reset_password_with_key(reset.reset_key, "stolen")
    assert auth.verify_user_password("alice", "secret")


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
    assert "cdn.jsdelivr.net" not in response.text
    assert "unpkg.com" not in response.text
    assert "/ruok/static/vendor/pico/pico.min.css" in response.text
    assert 'data-password-toggle="login-password"' in response.text
    assert 'aria-pressed="false"' in response.text
    assert 'autocomplete="username"' in response.text
    assert 'name="remember_username"' in response.text
    assert 'name="remember_login"' in response.text
    assert "/ruok/reset-password" in response.text
    assert ">显示<" not in response.text


def test_webui_serves_vendored_static_assets(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)

    response = client.get("/ruok/static/vendor/htmx/htmx.min.js")

    assert response.status_code == 200
    assert "htmx" in response.text


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
