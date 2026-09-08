"""Tests for /ruok bot commands using nonebug + OneBot V11 fake events."""

from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from fake import fake_group_message_event_v11, fake_private_message_event_v11
from nonebug import App


def _session_list_line(session) -> str:
    icon = {
        "pending": "🟡",
        "unsolved": "🔴",
        "solved": "🟢",
        "ignored": "⚪",
    }.get(session.status, "❓")
    return (
        f"{icon} {session.session_id} | {session.module_name} | "
        f"{session.status} | {session.last_seen_at.astimezone().strftime('%H:%M')}"
    )


def _session_lookup_text(session) -> str:
    status_icon = {
        "pending": "🟡",
        "unsolved": "🔴",
        "solved": "🟢",
        "ignored": "⚪",
    }.get(session.status, "❓")
    lines = [
        f"{status_icon} Session: {session.session_id}",
        f"状态: {session.status} | 来源: {session.source}",
    ]
    if session.reporter.user_id:
        lines.append(f"用户: {session.reporter.user_id}")
    if session.reporter.group_id:
        lines.append(f"群号: {session.reporter.group_id}")
    if session.reporter.platform:
        lines.append(f"平台: {session.reporter.platform}")
    lines += [
        f"模块: {session.module_name}",
        f"描述: {session.description[:200]}",
        "创建: "
        f"{session.first_seen_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"最近: {session.last_seen_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)


@pytest.mark.asyncio
async def test_ruok_matcher_loads(app: App) -> None:
    """Verify ruok_cmd matcher can be imported."""
    from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]

    assert ruok_cmd is not None


@pytest.mark.asyncio
async def test_ruok_no_args_shows_usage(app: App) -> None:
    """/ruok (no args) should show usage help."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="/ruok")
    from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]

    async with app.test_matcher(ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "RUOK — 用法:\n"
            "/ruok no <模块> <描述> — 上报问题\n"
            "/ruok bind <auth_key> — 绑定 WebUI 账户\n"
            "/ruok reset — 重设 WebUI 密码\n"
            "/ruok status — 查看状态\n"
            "/ruok list — 查看可见 session\n"
            "/ruok lookup <id> — 查看可见详情\n"
            "/ruok confirm <id> [插件...] | solve <id> | ignore <id> — 管理\n"
            "/ruok raise [message] — SUPERUSER 测试内部异常",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_bind_auth_key(app: App, tmp_path: Path, monkeypatch) -> None:
    """/ruok bind should bind the auth_key to the sender platform user."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = ScopedConfig(webui_admin_password="admin-secret")
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot_plugin_ruok, "webui_auth", auth)

    event = fake_group_message_event_v11(
        message=f"/ruok bind {result.auth_key}",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "✅ WebUI 用户 alice 已绑定平台账号 12345678",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    assert auth.is_user_bound("12345678")


@pytest.mark.asyncio
async def test_ruok_reset_private_message(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok reset in private chat should return a reset key directly."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = ScopedConfig(webui_admin_password="admin-secret")
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "12345678", "OneBot V11")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot_plugin_ruok, "webui_auth", auth)
    import nonebot_plugin_ruok.webui.auth as webui_auth_module

    monkeypatch.setattr(
        webui_auth_module.secrets,
        "token_urlsafe",
        lambda _n: "fixed-reset-key",
    )

    event = fake_private_message_event_v11(
        message="/ruok reset",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "RUOK WebUI 用户 alice 的一次性密码重置码:\n"
            "fixed-reset-key\n"
            "请在 10 分钟内打开 /ruok/reset-password 完成重设。",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    stored = auth.get_user("alice")
    assert stored is not None
    assert stored.password_reset_key_hash


@pytest.mark.asyncio
async def test_ruok_reset_group_message_sends_private_key(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok reset in group chat should send the reset key privately."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = ScopedConfig(webui_admin_password="admin-secret")
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "12345678", "OneBot V11")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot_plugin_ruok, "webui_auth", auth)
    import nonebot_plugin_ruok.webui.auth as webui_auth_module

    monkeypatch.setattr(
        webui_auth_module.secrets,
        "token_urlsafe",
        lambda _n: "fixed-reset-key",
    )

    event = fake_group_message_event_v11(
        message="/ruok reset",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        private_calls = []

        async def fake_call_api(api: str, **data):
            private_calls.append((api, data))
            return None

        monkeypatch.setattr(bot, "call_api", fake_call_api)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "✅ 重置码已通过私聊发送，请在 10 分钟内使用。",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    assert private_calls
    api, data = private_calls[0]
    assert api == "send_private_msg"
    assert data["user_id"] == 12345678
    assert "一次性密码重置码" in data["message"]


@pytest.mark.asyncio
async def test_ruok_status_builtin_module(app: App) -> None:
    """/ruok status should show built-in RUOK module."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="/ruok status")
    from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]

    async with app.test_matcher(ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "🟢 RUOK — available",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_status_image_mode_sends_rendered_image(
    app: App,
    monkeypatch,
) -> None:
    """/ruok status should send a rendered image when image mode succeeds."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig

    sent_images: list[bytes] = []
    rendered_module_counts: list[int] = []
    config = ScopedConfig(chat_render_mode="image")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)

    async def fake_render_status_image(modules, config):
        rendered_module_counts.append(len(modules))
        return b"status-png"

    async def fake_send_chat_image(bot, event, image: bytes) -> None:
        sent_images.append(image)

    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "render_status_image",
        fake_render_status_image,
    )
    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "send_chat_image",
        fake_send_chat_image,
    )

    event = fake_group_message_event_v11(message="/ruok status")

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_finished()

    assert sent_images == [b"status-png"]
    assert rendered_module_counts == [1]


@pytest.mark.asyncio
async def test_ruok_status_image_render_failure_falls_back_to_text(
    app: App,
    monkeypatch,
) -> None:
    """/ruok status should fall back to text when image rendering fails."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig

    config = ScopedConfig(chat_render_mode="image")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)

    async def fake_render_status_image(modules, config):
        raise RuntimeError("render boom")

    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "render_status_image",
        fake_render_status_image,
    )

    event = fake_group_message_event_v11(message="/ruok status")

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "🟢 RUOK — available",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_status_image_send_failure_falls_back_to_text(
    app: App,
    monkeypatch,
) -> None:
    """/ruok status should fall back to text when image sending fails."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig

    config = ScopedConfig(chat_render_mode="image")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)

    async def fake_render_status_image(modules, config):
        return b"status-png"

    async def fake_send_chat_image(bot, event, image: bytes) -> None:
        raise RuntimeError("send boom")

    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "render_status_image",
        fake_render_status_image,
    )
    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "send_chat_image",
        fake_send_chat_image,
    )

    event = fake_group_message_event_v11(message="/ruok status")

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "🟢 RUOK — available",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_list_user_shows_own_recent_sessions(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok list should show only the sender's latest five sessions."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session, update_session

    config = ScopedConfig()
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", set())
    base_time = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    own_sessions = []
    for index, status in enumerate(
        ["pending", "unsolved", "solved", "ignored", "pending", "solved"]
    ):
        session = create_session(
            tmp_path,
            f"own-{index}",
            f"own issue {index}",
            ReporterInfo(type="user", user_id="12345678"),
        )
        update_session(
            tmp_path,
            session.session_id,
            {
                "status": status,
                "last_seen_at": base_time + timedelta(minutes=index),
            },
        )
        own_sessions.append(session)
    other = create_session(
        tmp_path,
        "other",
        "other issue",
        ReporterInfo(type="user", user_id="99999999"),
    )
    update_session(
        tmp_path,
        other.session_id,
        {"last_seen_at": base_time + timedelta(hours=1)},
    )

    expected_sessions = []
    from nonebot_plugin_ruok.collectors.sessions import get_session

    for session in reversed(own_sessions[-5:]):
        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        expected_sessions.append(reloaded)

    event = fake_group_message_event_v11(
        message="/ruok list",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "\n".join(
                ["你的最近 5 个 session:"]
                + [_session_list_line(session) for session in expected_sessions]
                + ["... 还有 1 个，使用 /ruok lookup <id> 查看详情"]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_list_superuser_shows_recent_active_sessions(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok list should show SUPERUSER the latest fifteen active sessions."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import (
        get_session,
        create_session,
        update_session,
    )

    config = ScopedConfig()
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})
    base_time = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    active_sessions = []
    for index in range(16):
        session = create_session(
            tmp_path,
            f"active-{index}",
            f"active issue {index}",
            ReporterInfo(type="user", user_id=str(index)),
        )
        update_session(
            tmp_path,
            session.session_id,
            {
                "status": "unsolved" if index % 2 else "pending",
                "last_seen_at": base_time + timedelta(minutes=index),
            },
        )
        active_sessions.append(session)
    solved = create_session(
        tmp_path,
        "solved",
        "not active",
        ReporterInfo(type="user", user_id="12345678"),
    )
    update_session(
        tmp_path,
        solved.session_id,
        {
            "status": "solved",
            "last_seen_at": base_time + timedelta(hours=1),
        },
    )

    expected_sessions = []
    for session in reversed(active_sessions[-15:]):
        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        expected_sessions.append(reloaded)

    event = fake_group_message_event_v11(
        message="/ruok list",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "\n".join(
                ["共 16 个活跃 session:"]
                + [_session_list_line(session) for session in expected_sessions]
                + ["... 还有 1 个，使用 /ruok lookup <id> 查看详情"]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_list_image_mode_user_uses_own_recent_sessions(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok list image mode should render only the sender's own sessions."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import (
        get_session,
        create_session,
        update_session,
    )

    config = ScopedConfig(chat_render_mode="image")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", set())
    base_time = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    own_sessions = []
    for index in range(6):
        session = create_session(
            tmp_path,
            f"own-{index}",
            f"own issue {index}",
            ReporterInfo(type="user", user_id="12345678"),
        )
        update_session(
            tmp_path,
            session.session_id,
            {"last_seen_at": base_time + timedelta(minutes=index)},
        )
        own_sessions.append(session)
    other = create_session(
        tmp_path,
        "other",
        "other issue",
        ReporterInfo(type="user", user_id="99999999"),
    )
    update_session(
        tmp_path,
        other.session_id,
        {"last_seen_at": base_time + timedelta(hours=1)},
    )

    expected_ids = []
    for session in reversed(own_sessions[-5:]):
        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        expected_ids.append(reloaded.session_id)

    rendered = []
    sent_images: list[bytes] = []

    async def fake_render_session_list_image(
        *,
        title,
        sessions,
        total_count,
        hidden_count,
        config,
    ):
        rendered.append((title, [session.session_id for session in sessions]))
        assert total_count == 6
        assert hidden_count == 1
        return b"list-png"

    async def fake_send_chat_image(bot, event, image: bytes) -> None:
        sent_images.append(image)

    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "render_session_list_image",
        fake_render_session_list_image,
    )
    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "send_chat_image",
        fake_send_chat_image,
    )

    event = fake_group_message_event_v11(
        message="/ruok list",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_finished()

    assert rendered == [("你的 Session", expected_ids)]
    assert sent_images == [b"list-png"]


@pytest.mark.asyncio
async def test_ruok_list_image_mode_superuser_uses_active_sessions(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok list image mode should render SUPERUSER's global active view."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import (
        get_session,
        create_session,
        update_session,
    )

    config = ScopedConfig(chat_render_mode="image")
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})
    base_time = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    active_sessions = []
    for index in range(16):
        session = create_session(
            tmp_path,
            f"active-{index}",
            f"active issue {index}",
            ReporterInfo(type="user", user_id=str(index)),
        )
        update_session(
            tmp_path,
            session.session_id,
            {
                "status": "unsolved" if index % 2 else "pending",
                "last_seen_at": base_time + timedelta(minutes=index),
            },
        )
        active_sessions.append(session)
    solved = create_session(
        tmp_path,
        "solved",
        "not active",
        ReporterInfo(type="user", user_id="12345678"),
    )
    update_session(
        tmp_path,
        solved.session_id,
        {
            "status": "solved",
            "last_seen_at": base_time + timedelta(hours=1),
        },
    )

    expected_ids = []
    for session in reversed(active_sessions[-15:]):
        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        expected_ids.append(reloaded.session_id)

    rendered = []
    sent_images: list[bytes] = []

    async def fake_render_session_list_image(
        *,
        title,
        sessions,
        total_count,
        hidden_count,
        config,
    ):
        rendered.append((title, [session.session_id for session in sessions]))
        assert total_count == 16
        assert hidden_count == 1
        return b"list-png"

    async def fake_send_chat_image(bot, event, image: bytes) -> None:
        sent_images.append(image)

    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "render_session_list_image",
        fake_render_session_list_image,
    )
    monkeypatch.setattr(
        nonebot_plugin_ruok,
        "send_chat_image",
        fake_send_chat_image,
    )

    event = fake_group_message_event_v11(
        message="/ruok list",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_finished()

    assert rendered == [("活跃 Session", expected_ids)]
    assert sent_images == [b"list-png"]


@pytest.mark.asyncio
async def test_ruok_lookup_user_can_only_view_own_session(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok lookup should hide other users' sessions from normal users."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = ScopedConfig()
    own = create_session(
        tmp_path,
        "music",
        "own issue",
        ReporterInfo(type="user", user_id="12345678", platform="OneBot V11"),
    )
    other = create_session(
        tmp_path,
        "weather",
        "other issue",
        ReporterInfo(type="user", user_id="99999999"),
    )
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", set())

    own_event = fake_group_message_event_v11(
        message=f"/ruok lookup {own.session_id}",
        user_id=12345678,
    )
    denied_event = fake_group_message_event_v11(
        message=f"/ruok lookup {other.session_id}",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, own_event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            own_event,
            _session_lookup_text(own),
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, denied_event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            denied_event,
            f"❌ Session `{other.session_id}` 未找到或无权查看。",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_lookup_superuser_can_view_any_session(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok lookup should allow SUPERUSER to inspect any session."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = ScopedConfig()
    session = create_session(
        tmp_path,
        "weather",
        "other issue",
        ReporterInfo(type="user", user_id="99999999"),
    )
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    event = fake_group_message_event_v11(
        message=f"/ruok lookup {session.session_id}",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            _session_lookup_text(session),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_no_dispatches_rule_notification(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok no should create a session and use the rule notification engine."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig

    calls = []

    async def fake_dispatch(session, config, data_dir):
        calls.append(session.session_id)

    config = ScopedConfig(crisis_mode=True)
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.notifications.dispatch_notification",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.sessions._gen_session_id",
        lambda: "ruok-test0001",
    )
    event = fake_group_message_event_v11(
        message="/ruok no music playback failed",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "📝 已记录 | Session: ruok-test0001\n模块: music\n描述: playback failed",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    assert calls == ["ruok-test0001"]


@pytest.mark.asyncio
async def test_ruok_confirm_accepts_affected_plugins(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok confirm <id> plugin... should persist affected plugins."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = ScopedConfig()
    upsert_module(
        tmp_path,
        ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
    )
    session = create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    event = fake_group_message_event_v11(
        message=f"/ruok confirm {session.session_id} plugin_a plugin_b",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            f"🔴 Session {session.session_id} → 已确认\n影响插件: plugin_a, plugin_b",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    reloaded = get_session(tmp_path, session.session_id)
    assert reloaded is not None
    assert reloaded.status == "unsolved"
    assert reloaded.affected_plugins == ["plugin_a", "plugin_b"]


@pytest.mark.asyncio
async def test_ruok_confirm_without_plugins_does_not_propagate(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok confirm <id> remains valid without plugin propagation."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import get_module, upsert_module
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = ScopedConfig()
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
    session = create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    event = fake_group_message_event_v11(
        message=f"/ruok confirm {session.session_id}",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            f"🔴 Session {session.session_id} → 已确认",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    reloaded = get_session(tmp_path, session.session_id)
    lyrics = get_module(tmp_path, config, "lyrics")
    assert reloaded is not None
    assert reloaded.status == "unsolved"
    assert reloaded.affected_plugins == []
    assert lyrics is not None
    assert lyrics.status == "available"


@pytest.mark.asyncio
async def test_ruok_raise_superuser_creates_internal_session(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok raise should create a RUOK internal-error session for SUPERUSER."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.collectors.sessions import get_session

    config = ScopedConfig()
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})
    monkeypatch.setattr(
        "nonebot_plugin_ruok.collectors.sessions._gen_session_id",
        lambda: "ruok-test0002",
    )
    event = fake_group_message_event_v11(
        message="/ruok raise contract check",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "🧪 已触发 RUOK 测试异常 | Session: ruok-test0002",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    session = get_session(tmp_path, "ruok-test0002")
    assert session is not None
    assert session.module_name == "ruok"
    assert session.source == "automatic"
    assert "manual /ruok raise" in session.description
    assert "contract check" in session.description


@pytest.mark.asyncio
async def test_ruok_raise_rejects_non_superuser(
    app: App,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """/ruok raise should be SUPERUSER-only."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    import nonebot_plugin_ruok
    from nonebot_plugin_ruok.config import ScopedConfig
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    config = ScopedConfig()
    monkeypatch.setattr(nonebot_plugin_ruok, "plugin_config", config)
    monkeypatch.setattr(nonebot_plugin_ruok, "data_dir", tmp_path)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", set())
    event = fake_group_message_event_v11(
        message="/ruok raise should not run",
        user_id=12345678,
    )

    async with app.test_matcher(nonebot_plugin_ruok.ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "❌ 此操作仅 SUPERUSER 可用",
            result=None,
            bot=bot,
        )
        ctx.should_finished()

    assert list_sessions(tmp_path) == []
