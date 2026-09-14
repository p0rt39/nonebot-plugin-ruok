from __future__ import annotations

import secrets
from typing import Annotated
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Callable, Awaitable

from nonebot import logger, require, get_driver, on_command, get_plugin_config
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.drivers import ASGIMixin
from nonebot.adapters import Bot, Event, Message

from .api import create_ruok_router
from .config import Config
from .protocol import Session, ReporterInfo, BotConnectionStatus
from .collector import (
    LogMonitor,
    SessionPluginValidationError,
    get_session,
    list_sessions,
    create_session,
    update_session,
    _handle_ruok_error,
    _connection_history,
    confirm_session_plugins,
)
from .webui.auth import (
    WebUIAuth,
    AuthKeyExpired,
    AuthKeyInvalid,
    AuthKeyAlreadyUsed,
    AuthUserAlreadyBound,
)
from .chat_render import (
    send_chat_image,
    chat_render_enabled,
    render_status_image,
    render_session_list_image,
)
from .webui.router import create_webui_router

require("nonebot_plugin_localstore")
store = __import__("nonebot_plugin_localstore")
require("nonebot_plugin_htmlrender")

__plugin_meta__ = PluginMetadata(
    name="Nonebot, RUOK?",
    description="Bot health monitoring, session tracking, and WebUI",
    usage=(
        "/ruok no <module> <description> — report an issue\n"
        "/ruok bind <auth_key> — bind WebUI account to current platform user\n"
        "/ruok reset — issue a password reset key for bound WebUI user\n"
        "/ruok status — check module health\n"
        "/ruok list — view visible sessions\n"
        "/ruok lookup <session_id> — view visible session details\n"
        "/ruok confirm <session_id> — confirm issue (pending→unsolved)\n"
        "/ruok solve <session_id> — mark as resolved\n"
        "/ruok ignore <session_id> — ignore (false alarm)\n"
        "/ruok raise [message] — create a SUPERUSER-only test internal error\n"
        "Visit /ruok for WebUI dashboard"
    ),
    type="application",
    homepage="https://github.com/p0rt39/nonebot-plugin-ruok",
    config=Config,
    supported_adapters=None,
)

# ────────────────────────────────
# Config
# ────────────────────────────────

plugin_config = get_plugin_config(Config).ruok
data_dir = Path(store.get_plugin_data_dir())

# ────────────────────────────────
# Globals
# ────────────────────────────────

log_monitor = LogMonitor(plugin_config, data_dir)
webui_auth = WebUIAuth(
    plugin_config,
    data_dir,
    superuser_provider=lambda: get_driver().config.superusers,
)

# ────────────────────────────────
# Permission checker for report
# ────────────────────────────────


async def _can_report(bot: Bot, event: Event) -> bool:
    """Check whether a user is allowed to report issues."""
    # crisis_mode: skip all permission checks
    if plugin_config.crisis_mode:
        return True

    user_id = event.get_user_id()

    # SUPERUSERS always allowed
    if user_id in get_driver().config.superusers:
        return True

    # WebUI users bound to this platform user can report from chat.
    if webui_auth.is_user_bound(user_id, bot.type):
        return True

    # Whitelist users
    if user_id in plugin_config.report_whitelist_users:
        return True

    # Group admin / owner
    sender = getattr(event, "sender", None)
    if sender and getattr(sender, "role", None) in ("admin", "owner"):
        return True

    # Whitelist groups
    group_id = getattr(event, "group_id", None)
    if group_id is not None and str(group_id) in plugin_config.report_whitelist_groups:
        return True

    return False


def _is_superuser(event: Event) -> bool:
    """Return whether the current platform user is a NoneBot SUPERUSER."""
    return event.get_user_id() in get_driver().config.superusers


def _is_session_owner(session: Session, bot: Bot, event: Event) -> bool:
    """Return whether a session belongs to the current platform user."""
    return (
        session.reporter.user_id == event.get_user_id()
        and session.reporter.platform == bot.type
    )


def _format_session_list_line(session: Session) -> str:
    """Return one compact chat list line for a session."""
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


# ────────────────────────────────
# RUOK Main Entrypoint
# ────────────────────────────────

ruok_cmd = on_command("ruok", priority=10, block=True)


@ruok_cmd.handle()
async def handle_ruok(
    bot: Bot,
    event: Event,
    args: Annotated[Message, CommandArg()],
) -> None:
    text = args.extract_plain_text().strip()
    if not text:
        await ruok_cmd.finish(
            "RUOK — 用法:\n"
            "/ruok no <模块> <描述> — 上报问题\n"
            "/ruok bind <auth_key> — 绑定 WebUI 账户\n"
            "/ruok reset — 重设 WebUI 密码\n"
            "/ruok status — 查看状态\n"
            "/ruok list — 查看可见 session\n"
            "/ruok lookup <id> — 查看可见详情\n"
            "/ruok confirm <id> [插件...] | solve <id> | ignore <id> — 管理\n"
            "/ruok raise [message] — SUPERUSER 测试内部异常"
        )
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "no":
        await _cmd_no(bot, event, rest)
    elif subcmd == "bind":
        await _cmd_bind(bot, event, rest)
    elif subcmd == "reset":
        await _cmd_reset(bot, event)
    elif subcmd == "status":
        await _cmd_status(bot, event)
    elif subcmd == "list":
        await _cmd_list(bot, event)
    elif subcmd == "lookup":
        await _cmd_lookup(bot, event, rest)
    elif subcmd in ("raise", "test"):
        await _cmd_raise(event, rest, subcmd)
    elif subcmd in ("confirm", "solve", "ignore"):
        await _cmd_admin(bot, event, subcmd, rest)
    else:
        await ruok_cmd.finish(
            f"❓ 未知子命令: {subcmd}\n"
            + (
                "可用: no / bind / reset / status / list / lookup / confirm / "
                "solve / ignore / raise"
            )
        )


# ────────────────────────────────
# Subcommand handlers
# ────────────────────────────────


async def _cmd_no(bot: Bot, event: Event, rest: str) -> None:
    """Handle /ruok no <module> <description>"""
    if not plugin_config.session_enabled:
        await ruok_cmd.finish("⚠️ Session 系统未启用，无法上报问题。")
        return

    if not await _can_report(bot, event):
        await ruok_cmd.finish(
            "❌ 你没有权限上报问题。需要："
            "绑定 WebUI 账号 / 群管理员 / 白名单 / SUPERUSER"
        )
        return

    parts = rest.split(maxsplit=1)
    user_input = parts[0].strip() if parts else ""
    description = parts[1].strip() if len(parts) > 1 else ""

    if not user_input:
        await ruok_cmd.finish("用法: /ruok no <模块> <描述>")
        return

    # Resolve user input → module definition
    from .collectors.modules import resolve_module_display

    resolved = resolve_module_display(user_input, data_dir, plugin_config)
    module_name = resolved.name if resolved is not None else user_input

    gid = getattr(event, "group_id", None)
    reporter = ReporterInfo(
        type="user",
        user_id=event.get_user_id(),
        group_id=str(gid) if gid is not None else None,
        platform=bot.type,
    )
    session = create_session(
        data_dir,
        module_name=module_name,
        description=description,
        reporter=reporter,
        source="manual",
    )
    from .collectors.notifications import dispatch_notification

    await dispatch_notification(session, plugin_config, data_dir)
    display = resolved.display_name if resolved is not None else user_input
    await ruok_cmd.finish(
        f"📝 已记录 | Session: {session.session_id}\n"
        f"模块: {display}\n描述: {description}"
    )


async def _cmd_bind(bot: Bot, event: Event, rest: str) -> None:
    """Handle /ruok bind <auth_key>."""
    auth_key = rest.strip()
    if not auth_key:
        await ruok_cmd.finish("用法: /ruok bind <auth_key>")
        return

    try:
        user = webui_auth.bind_auth_key(auth_key, event.get_user_id(), bot.type)
    except AuthKeyExpired:
        await ruok_cmd.finish("❌ auth_key 已过期，请在 WebUI 重新获取绑定码。")
        return
    except AuthKeyAlreadyUsed:
        await ruok_cmd.finish("❌ auth_key 已被使用，请在 WebUI 重新获取绑定码。")
        return
    except AuthUserAlreadyBound as exc:
        await ruok_cmd.finish(f"❌ {exc}")
        return
    except AuthKeyInvalid:
        await ruok_cmd.finish("❌ auth_key 无效，请检查后重试。")
        return

    await ruok_cmd.finish(
        f"✅ WebUI 用户 {user.username} 已绑定平台账号 {user.bound_user_id}"
    )


async def _cmd_reset(bot: Bot, event: Event) -> None:
    """Handle /ruok reset."""
    try:
        result = webui_auth.issue_password_reset_key(event.get_user_id(), bot.type)
    except Exception as exc:
        message = str(exc) or "无法生成重置码"
        await ruok_cmd.finish(f"❌ {message}")
        return

    text = (
        f"RUOK WebUI 用户 {result.user.username} 的一次性密码重置码:\n"
        f"{result.reset_key}\n"
        "请在 10 分钟内打开 /ruok/reset-password 完成重设。"
    )
    if getattr(event, "message_type", None) == "group":
        try:
            await bot.call_api(
                "send_private_msg",
                user_id=int(event.get_user_id()),
                message=text,
            )
        except Exception as exc:
            logger.warning(f"RUOK: password reset private message failed: {exc}")
            await ruok_cmd.finish("❌ 私聊发送重置码失败，请先私聊 bot 后重试。")
            return
        await ruok_cmd.finish("✅ 重置码已通过私聊发送，请在 10 分钟内使用。")
        return

    await ruok_cmd.finish(text)


async def _finish_with_optional_image(
    bot: Bot,
    event: Event,
    text: str,
    render_image: Callable[[], Awaitable[bytes]],
) -> None:
    """Try to send a rendered image, then fall back to text."""
    if chat_render_enabled(plugin_config):
        try:
            image = await render_image()
            await send_chat_image(bot, event, image)
        except Exception as exc:
            logger.warning(f"RUOK: chat image output failed, falling back: {exc}")
        else:
            await ruok_cmd.finish()
            return
    await ruok_cmd.finish(text)


async def _cmd_status(bot: Bot, event: Event) -> None:
    """Handle /ruok status"""
    try:
        from .collector import list_modules as lm

        modules = lm(data_dir, plugin_config)
    except (OSError, ValueError, ImportError) as exc:
        logger.warning(f"RUOK: status list_modules failed: {exc}")
        modules = []
    except Exception as exc:
        _handle_ruok_error(exc, "_cmd_status list_modules", data_dir)
        modules = []

    if not modules:
        await ruok_cmd.finish("⚠️ 暂无功能模块定义。在 WebUI 或 .env 中配置模块。")
        return

    lines: list[str] = []
    for m in modules:
        icon = {"available": "🟢", "degraded": "🟡", "unavailable": "🔴"}.get(
            m.status, "⚪"
        )
        lines.append(f"{icon} {m.display_name or m.name} — {m.status}")
    text = "\n".join(lines)
    await _finish_with_optional_image(
        bot,
        event,
        text,
        lambda: render_status_image(modules, plugin_config),
    )


async def _cmd_list(bot: Bot, event: Event) -> None:
    """Handle /ruok list with scoped visibility."""
    if _is_superuser(event):
        sessions = list_sessions(data_dir)
        active = [s for s in sessions if s.status in ("pending", "unsolved")]
        if not active:
            await ruok_cmd.finish("🎉 没有活跃的 session。")
            return

        visible = active[:15]
        lines = [f"共 {len(active)} 个活跃 session:"]
        lines.extend(_format_session_list_line(s) for s in visible)
        if len(active) > 15:
            lines.append(
                f"... 还有 {len(active) - 15} 个，使用 /ruok lookup <id> 查看详情"
            )
        text = "\n".join(lines)
        await _finish_with_optional_image(
            bot,
            event,
            text,
            lambda: render_session_list_image(
                title="活跃 Session",
                sessions=visible,
                total_count=len(active),
                hidden_count=max(len(active) - len(visible), 0),
                config=plugin_config,
            ),
        )
        return

    sessions = list_sessions(
        data_dir,
        reporter_user_id=event.get_user_id(),
        reporter_platform=bot.type,
    )
    visible = sessions[:5]
    if not visible:
        await ruok_cmd.finish("🎉 你还没有上报过 session。")
        return

    lines = [f"你的最近 {len(visible)} 个 session:"]
    lines.extend(_format_session_list_line(s) for s in visible)
    if len(sessions) > 5:
        lines.append(
            f"... 还有 {len(sessions) - 5} 个，使用 /ruok lookup <id> 查看详情"
        )
    text = "\n".join(lines)
    await _finish_with_optional_image(
        bot,
        event,
        text,
        lambda: render_session_list_image(
            title="你的 Session",
            sessions=visible,
            total_count=len(sessions),
            hidden_count=max(len(sessions) - len(visible), 0),
            config=plugin_config,
        ),
    )


async def _cmd_lookup(bot: Bot, event: Event, rest: str) -> None:
    """Handle /ruok lookup <session_id>"""
    sid = rest.strip()
    if not sid:
        await ruok_cmd.finish("用法: /ruok lookup <session_id>")
        return

    is_superuser = _is_superuser(event)
    session = get_session(data_dir, sid)
    if session is None:
        message = (
            f"❌ Session `{sid}` 未找到。"
            if is_superuser
            else f"❌ Session `{sid}` 未找到或无权查看。"
        )
        await ruok_cmd.finish(message)
        return
    if not is_superuser and not _is_session_owner(session, bot, event):
        await ruok_cmd.finish(f"❌ Session `{sid}` 未找到或无权查看。")
        return

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
        f"创建: {session.first_seen_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"最近: {session.last_seen_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    from .collector import get_linked_sessions

    linked = get_linked_sessions(data_dir, session.session_id)
    if not is_superuser:
        linked = [s for s in linked if _is_session_owner(s, bot, event)]
    if linked:
        lines.append(f"关联 ({len(linked)}): {', '.join(s.session_id for s in linked)}")
    await ruok_cmd.finish("\n".join(lines))


async def _cmd_raise(event: Event, rest: str, action: str) -> None:
    """Handle /ruok raise|test [message] (SUPERUSER only)."""
    if not _is_superuser(event):
        await ruok_cmd.finish("❌ 此操作仅 SUPERUSER 可用")
        return

    message = rest.strip() or "manual RUOK test exception"
    try:
        raise RuntimeError(message)
    except RuntimeError as exc:
        sid = _handle_ruok_error(exc, f"manual /ruok {action}", data_dir)

    await ruok_cmd.finish(f"🧪 已触发 RUOK 测试异常 | Session: {sid}")


async def _cmd_admin(bot: Bot, event: Event, action: str, rest: str) -> None:
    """Handle /ruok confirm|solve|ignore <session_id> (SUPERUSER only)"""
    user_id = event.get_user_id()
    if user_id not in get_driver().config.superusers:
        await ruok_cmd.finish("❌ 此操作仅 SUPERUSER 可用")
        return

    sid = rest.strip()
    if not sid:
        if action == "confirm":
            await ruok_cmd.finish(f"用法: /ruok {action} <session_id> [插件...]")
            return
        await ruok_cmd.finish(f"用法: /ruok {action} <session_id>")
        return

    status_map = {"confirm": "unsolved", "solve": "solved", "ignore": "ignored"}
    new_status = status_map[action]

    if action == "confirm":
        session_id, plugins = _parse_confirm_args(rest)
        try:
            session = confirm_session_plugins(
                data_dir,
                plugin_config,
                session_id,
                plugins,
            )
        except SessionPluginValidationError as exc:
            await ruok_cmd.finish(f"❌ {exc}")
            return
        sid = session_id
    else:
        session = update_session(data_dir, sid, {"status": new_status})
    if session is None:
        await ruok_cmd.finish(f"❌ Session `{sid}` 未找到。")
        return

    icon = {"unsolved": "🔴", "solved": "🟢", "ignored": "⚪"}[new_status]
    labels = {"unsolved": "已确认", "solved": "已解决", "ignored": "已忽略(误报)"}
    plugin_text = (
        f"\n影响插件: {', '.join(session.affected_plugins)}"
        if action == "confirm" and session.affected_plugins
        else ""
    )
    await ruok_cmd.finish(f"{icon} Session {sid} → {labels[new_status]}{plugin_text}")


def _parse_confirm_args(rest: str) -> tuple[str, list[str]]:
    """Parse /ruok confirm <session_id> [plugin ...]."""
    session_id, _, raw_plugins = rest.strip().partition(" ")
    plugins = [
        item
        for token in raw_plugins.split()
        for item in token.split(",")
        if item.strip()
    ]
    return session_id, plugins


# ────────────────────────────────
# WS connection tracking hooks
# ────────────────────────────────

driver = get_driver()

# CORS + SessionMiddleware — at module level before uvicorn starts
if isinstance(driver, ASGIMixin):
    from fastapi import FastAPI

    app = driver.server_app
    if isinstance(app, FastAPI):
        # CORS
        try:
            from fastapi.middleware.cors import CORSMiddleware

            app.add_middleware(
                CORSMiddleware,
                allow_origins=plugin_config.cors_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            logger.info("RUOK CORS middleware registered")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RUOK CORS setup failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "CORS middleware setup", data_dir)

        # SessionMiddleware — always added for request.session support.
        # Auth gating (enabled/disabled) is handled at route level via require_login().
        try:
            from starlette.middleware.sessions import SessionMiddleware

            secret = plugin_config.webui_secret_key or secrets.token_hex(32)
            if not plugin_config.webui_secret_key:
                logger.warning(
                    "RUOK: webui_secret_key not set — sessions invalidate on restart. "
                    "Set RUOK__WEBUI_SECRET_KEY in .env for persistence."
                )
            app.add_middleware(
                SessionMiddleware,
                secret_key=secret,
                max_age=plugin_config.webui_session_ttl,
                same_site="lax",
            )
            logger.info("RUOK SessionMiddleware registered")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RUOK SessionMiddleware setup failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "SessionMiddleware setup", data_dir)

        # ── Global ASGI exception handler ──
        # Catches any unhandled exception that escapes route-level try/except
        # (e.g. response header encoding errors, middleware failures).
        # Creates a RUOK session so the error is tracked and visible in WebUI.
        from fastapi.responses import JSONResponse as _JSONResponse
        from starlette.requests import Request as StarletteRequest

        @app.exception_handler(Exception)
        async def _ruok_global_exception_handler(
            request: StarletteRequest, exc: Exception
        ) -> _JSONResponse:
            sid = _handle_ruok_error(
                exc,
                f"ASGI {request.method} {request.url.path}",
                data_dir,
            )
            return _JSONResponse(
                {
                    "detail": (
                        f"Internal error [{type(exc).__name__}] — Session: {sid}"
                    ),
                },
                status_code=500,
            )

        logger.info("RUOK global exception handler registered")
    else:
        logger.warning(
            "RUOK: driver.server_app is not a FastAPI instance, skipping middleware"
        )


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    _connection_history[bot.self_id] = BotConnectionStatus(
        self_id=bot.self_id,
        adapter=bot.type,
        connected=True,
        connected_at=datetime.now(timezone.utc),
    )
    logger.info(f"RUOK: bot {bot.self_id} connected ({bot.type})")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    entry = _connection_history.get(bot.self_id)
    if entry:
        entry.connected = False
        entry.disconnected_at = datetime.now(timezone.utc)
    logger.info(f"RUOK: bot {bot.self_id} disconnected")


# ────────────────────────────────
# Startup — mount API + LogMonitor
# ────────────────────────────────


@driver.on_startup
async def _startup() -> None:
    if isinstance(driver, ASGIMixin):
        app = driver.server_app

        # Auth router (login/register/logout)
        try:
            auth_router = webui_auth.create_router()
            app.include_router(auth_router)
            logger.info("RUOK Auth routes mounted")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RUOK Auth mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "Auth routes mount", data_dir)

        try:
            router = create_ruok_router(plugin_config, data_dir)
            app.include_router(router)
            logger.info("RUOK API mounted at /ruok/api/*")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RUOK API mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "API routes mount", data_dir)

        try:
            webui_router = create_webui_router(plugin_config, data_dir, webui_auth)
            app.include_router(webui_router)
            logger.info("RUOK WebUI SSR mounted at /ruok")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RUOK WebUI mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "WebUI routes mount", data_dir)
    else:
        logger.info("RUOK: non-ASGI driver, skipping API/WebUI mount")

    # Start LogMonitor (works regardless of driver type)
    try:
        log_monitor.start()
    except Exception as exc:
        # Monitoring is best-effort and must never prevent the bot from
        # completing startup.
        logger.warning(f"RUOK: LogMonitor startup failed: {exc}")

    # Ensure notification rules are initialized on first run
    from .collectors.notifications import _load_rules

    try:
        _load_rules(data_dir)
    except Exception as exc:
        logger.warning(f"RUOK: notification rule initialization failed: {exc}")

    # Register summary notification job (best-effort, APScheduler optional)
    try:
        from .collectors.notifications import register_summary_job

        register_summary_job(plugin_config, data_dir)
    except Exception as exc:
        logger.warning(f"RUOK: summary job registration failed: {exc}")

    logger.info("RUOK plugin started")


@driver.on_shutdown
async def _shutdown() -> None:
    try:
        log_monitor.stop()
    except Exception as exc:
        logger.warning(f"RUOK: LogMonitor shutdown failed: {exc}")
    logger.info("RUOK plugin stopped")
