from __future__ import annotations

import json
from pathlib import Path

from webui_test_utils import (
    _client,
    _login_user,
    _login_admin,
    _webui_config,
)


def test_webui_users_page_admin_can_manage_users(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

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
    created_user = WebUIAuth(config, tmp_path).get_user("紧急 用户/a")
    assert created_user is not None
    assert created_user.auth_key_value
    assert created_user.auth_key_value in created.text

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
    issued_user = WebUIAuth(config, tmp_path).get_user("renamed user")
    assert issued_user is not None
    assert issued_user.auth_key_value
    assert issued_user.auth_key_value in issued.text

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
    assert 'hx-post="/ruok/_actions/account-rebind-key"' in page.text

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


def test_webui_unbound_user_page_shows_and_regenerates_auth_key(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    result = WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    page = client.get("/ruok/users")
    assert page.status_code == 200
    assert result.auth_key in page.text

    regenerated = client.post("/ruok/_actions/account-auth-key")
    assert regenerated.status_code == 200
    assert "Auth key issued" in regenerated.headers["HX-Trigger"]
    stored = WebUIAuth(config, tmp_path).get_user("alice")
    assert stored is not None
    assert stored.auth_key_value
    assert stored.auth_key_value in regenerated.text
