from __future__ import annotations

import asyncio
from pathlib import Path

from webui_test_utils import (
    _client,
    _login_admin,
    _webui_config,
    _upsert_module,
    _load_notification_rules,
    _save_notification_rules,
)


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


def test_notification_rules_are_loaded_from_webui_storage_only(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import NotificationRule
    from nonebot_plugin_ruok.collectors.notifications import _load_rules

    config = ScopedConfig()
    object.__setattr__(
        config,
        "notification_rules",
        [NotificationRule(name="env-like")],
    )

    rules = _load_rules(tmp_path)

    assert [rule.name for rule in rules] == ["superusers"]


def test_invalid_persisted_notification_rule_is_ignored(tmp_path: Path) -> None:
    import json

    from nonebot_plugin_ruok.collectors.notifications import _load_rules

    (tmp_path / "notification_rules.json").write_text(
        json.dumps(
            [
                {"name": "valid"},
                {"name": "invalid", "cooldown_minutes": "not-a-number"},
            ]
        ),
        encoding="utf-8",
    )

    rules = _load_rules(tmp_path)

    assert [rule.name for rule in rules] == ["valid"]


def test_naive_cooldown_timestamp_is_interpreted_as_utc() -> None:
    from datetime import datetime, timezone

    from nonebot_plugin_ruok.collectors.notifications import _is_cooling_down

    cooldowns = {"rule": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}

    assert _is_cooling_down("rule", 1, cooldowns) is True


async def test_dispatch_notification_is_independent_from_auto_session_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import Session, ReporterInfo, NotificationRule
    from nonebot_plugin_ruok.collectors.notifications import dispatch_notification

    calls = []

    async def fake_send_bot_dm(session):
        calls.append(session.session_id)

    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.notifications._send_bot_dm",
        fake_send_bot_dm,
    )
    config = ScopedConfig(auto_session_enabled=False)
    from nonebot_plugin_ruok.collectors.notifications import _save_rules

    _save_rules(
        tmp_path,
        [NotificationRule(name="test", channels=["bot_dm"], cooldown_minutes=0)],
    )
    session = Session(
        session_id="ruok-1234abcd",
        source="manual",
        module_name="music",
        reporter=ReporterInfo(type="user"),
    )

    await dispatch_notification(session, config, tmp_path)

    assert calls == ["ruok-1234abcd"]


async def test_dispatch_notification_respects_notification_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import Session, ReporterInfo, NotificationRule
    from nonebot_plugin_ruok.collectors.notifications import dispatch_notification

    calls = []

    async def fake_send_bot_dm(session):
        calls.append(session.session_id)

    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.notifications._send_bot_dm",
        fake_send_bot_dm,
    )
    config = ScopedConfig(notification_enabled=False)
    from nonebot_plugin_ruok.collectors.notifications import _save_rules

    _save_rules(
        tmp_path,
        [NotificationRule(name="test", channels=["bot_dm"], cooldown_minutes=0)],
    )
    session = Session(
        session_id="ruok-1234abcd",
        source="manual",
        module_name="music",
        reporter=ReporterInfo(type="user"),
    )

    await dispatch_notification(session, config, tmp_path)

    assert calls == []


async def test_failed_notification_does_not_start_cooldown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import Session, ReporterInfo, NotificationRule
    from nonebot_plugin_ruok.collectors.notifications import (
        _save_rules,
        dispatch_notification,
    )

    calls = []

    async def failing_send_bot_dm(session):
        calls.append(session.session_id)
        raise RuntimeError("bot offline")

    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.notifications._send_bot_dm",
        failing_send_bot_dm,
    )
    _save_rules(
        tmp_path,
        [NotificationRule(name="test", channels=["bot_dm"], cooldown_minutes=60)],
    )
    session = Session(
        session_id="ruok-1234abcd",
        source="manual",
        module_name="music",
        reporter=ReporterInfo(type="user"),
    )

    await dispatch_notification(session, ScopedConfig(), tmp_path)
    await dispatch_notification(session, ScopedConfig(), tmp_path)

    assert calls == ["ruok-1234abcd", "ruok-1234abcd"]
    assert not (tmp_path / "notification_cooldowns.json").exists()


async def test_concurrent_notifications_atomically_claim_cooldown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import Session, ReporterInfo, NotificationRule
    from nonebot_plugin_ruok.collectors.notifications import (
        _save_rules,
        dispatch_notification,
    )

    calls: list[str] = []
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def delayed_send(session: Session) -> bool:
        calls.append(session.session_id)
        send_started.set()
        await release_send.wait()
        return True

    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.notifications._send_bot_dm",
        delayed_send,
    )
    _save_rules(
        tmp_path,
        [NotificationRule(name="test", channels=["bot_dm"], cooldown_minutes=60)],
    )
    session = Session(
        session_id="ruok-1234abcd",
        source="manual",
        module_name="music",
        reporter=ReporterInfo(type="user"),
    )

    first = asyncio.create_task(
        dispatch_notification(session, ScopedConfig(), tmp_path)
    )
    await asyncio.wait_for(send_started.wait(), timeout=1)
    await dispatch_notification(session, ScopedConfig(), tmp_path)

    assert calls == ["ruok-1234abcd"]
    release_send.set()
    await first
