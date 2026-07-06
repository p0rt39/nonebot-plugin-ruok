"""Tests for /ruok bot commands using nonebug + OneBot V11 fake events."""

import pytest
from fake import fake_group_message_event_v11
from nonebug import App


@pytest.mark.asyncio
async def test_ruok_matcher_loads(app: App) -> None:
    """Verify ruok_cmd matcher can be imported."""
    try:
        from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("nonebot_plugin_ruok.ruok_cmd not found")
    assert ruok_cmd is not None


@pytest.mark.asyncio
async def test_ruok_no_args_shows_usage(app: App) -> None:
    """/ruok (no args) should show usage help."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="/ruok")
    try:
        from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("nonebot_plugin_ruok.ruok_cmd not found")

    async with app.test_matcher(ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "RuOK — 用法:\n"
            "/ruok no <模块> <描述> — 上报问题\n"
            "/ruok status — 查看状态\n"
            "/ruok list — 查看所有 session\n"
            "/ruok lookup <id> — 查看详情\n"
            "/ruok confirm <id> | solve <id> | ignore <id> — 管理",
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ruok_status_builtin_module(app: App) -> None:
    """/ruok status should show built-in RuOK module."""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="/ruok status")
    try:
        from nonebot_plugin_ruok import ruok_cmd  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("nonebot_plugin_ruok.ruok_cmd not found")

    async with app.test_matcher(ruok_cmd) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_pass_rule()
        ctx.should_pass_permission()
        ctx.should_call_send(
            event,
            "🟢 RuOK — available",
            result=None,
            bot=bot,
        )
        ctx.should_finished()
