from __future__ import annotations

import secrets
from typing import Annotated
from pathlib import Path
from datetime import datetime, timezone

from nonebot import logger, require, get_driver, on_command, get_plugin_config
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.drivers import ASGIMixin
from nonebot.adapters import Bot, Event, Message

from .api import create_ruok_router
from .config import Config
from .protocol import ReporterInfo, BotConnectionStatus
from .collector import (
    LogMonitor,
    get_session,
    list_sessions,
    create_session,
    update_session,
    _handle_ruok_error,
    _connection_history,
)
from .webui.auth import (
    WebUIAuth,
    AuthKeyExpired,
    AuthKeyInvalid,
    AuthKeyAlreadyUsed,
    AuthUserAlreadyBound,
)
from .webui.router import create_webui_router

require("nonebot_plugin_localstore")
store = __import__("nonebot_plugin_localstore")

__plugin_meta__ = PluginMetadata(
    name="RuOK",
    description="Bot health monitoring, session tracking, and WebUI",
    usage=(
        "/ruok no <module> <description> — report an issue\n"
        "/ruok bind <auth_key> — bind WebUI account to current platform user\n"
        "/ruok reset — issue a password reset key for bound WebUI user\n"
        "/ruok status — check module health\n"
        "/ruok lookup <session_id> — view session details\n"
        "/ruok confirm <session_id> — confirm issue (pending→unsolved)\n"
        "/ruok solve <session_id> — mark as resolved\n"
        "/ruok ignore <session_id> — ignore (false alarm)\n"
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


async def _can_report(event: Event) -> bool:
    """Check whether a user is allowed to report issues."""
    # crisis_mode: skip all permission checks
    if plugin_config.crisis_mode:
        return True

    user_id = event.get_user_id()

    # SUPERUSERS always allowed
    if user_id in get_driver().config.superusers:
        return True

    # WebUI users bound to this platform user can report from chat.
    if webui_auth.is_user_bound(user_id):
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


# ────────────────────────────────
# RuOK Main Entrypoint
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
            "RuOK — 用法:\n"
            "/ruok no <模块> <描述> — 上报问题\n"
            "/ruok bind <auth_key> — 绑定 WebUI 账户\n"
            "/ruok reset — 重设 WebUI 密码\n"
            "/ruok status — 查看状态\n"
            "/ruok list — 查看所有 session\n"
            "/ruok lookup <id> — 查看详情\n"
            "/ruok confirm <id> | solve <id> | ignore <id> — 管理"
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
        await _cmd_status()
    elif subcmd == "list":
        await _cmd_list()
    elif subcmd == "lookup":
        await _cmd_lookup(rest)
    elif subcmd in ("confirm", "solve", "ignore"):
        await _cmd_admin(bot, event, subcmd, rest)
    else:
        await ruok_cmd.finish(
            f"❓ 未知子命令: {subcmd}\n"
            + "可用: no / bind / reset / status / lookup / confirm / solve / ignore"
        )


# ────────────────────────────────
# Subcommand handlers
# ────────────────────────────────


async def _cmd_no(bot: Bot, event: Event, rest: str) -> None:
    """Handle /ruok no <module> <description>"""
    if not plugin_config.session_enabled:
        await ruok_cmd.finish("⚠️ Session 系统未启用，无法上报问题。")
        return

    if not await _can_report(event):
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
    if plugin_config.notify_superusers:
        from .collector import _notify_new_session

        await _notify_new_session(session, plugin_config, data_dir)
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
        f"RuOK WebUI 用户 {result.user.username} 的一次性密码重置码:\n"
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
            logger.warning(f"RuOK: password reset private message failed: {exc}")
            await ruok_cmd.finish("❌ 私聊发送重置码失败，请先私聊 bot 后重试。")
            return
        await ruok_cmd.finish("✅ 重置码已通过私聊发送，请在 10 分钟内使用。")
        return

    await ruok_cmd.finish(text)


async def _cmd_status() -> None:
    """Handle /ruok status"""
    try:
        from .collector import list_modules as lm

        modules = lm(data_dir, plugin_config)
    except (OSError, ValueError, ImportError) as exc:
        logger.warning(f"RuOK: status list_modules failed: {exc}")
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
    await ruok_cmd.finish("\n".join(lines))


async def _cmd_list() -> None:
    """Handle /ruok list — show all active sessions"""
    sessions = list_sessions(data_dir)
    active = [s for s in sessions if s.status in ("pending", "unsolved")]
    if not active:
        await ruok_cmd.finish("🎉 没有活跃的 session。")
        return

    lines = [f"共 {len(active)} 个活跃 session:"]
    for s in active[:10]:  # cap at 10 for chat
        icon = {"pending": "🟡", "unsolved": "🔴"}.get(s.status, "⚪")
        lines.append(
            f"{icon} {s.session_id} | {s.module_name} | "
            f"{s.status} | {s.first_seen_at.astimezone().strftime('%H:%M')}"
        )
    if len(active) > 10:
        lines.append(f"... 还有 {len(active) - 10} 个，使用 /ruok lookup <id> 查看详情")
    await ruok_cmd.finish("\n".join(lines))


async def _cmd_lookup(rest: str) -> None:
    """Handle /ruok lookup <session_id>"""
    sid = rest.strip()
    if not sid:
        await ruok_cmd.finish("用法: /ruok lookup <session_id>")
        return

    session = get_session(data_dir, sid)
    if session is None:
        await ruok_cmd.finish(f"❌ Session `{sid}` 未找到。")
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
    if linked:
        lines.append(f"关联 ({len(linked)}): {', '.join(s.session_id for s in linked)}")
    await ruok_cmd.finish("\n".join(lines))


async def _cmd_admin(bot: Bot, event: Event, action: str, rest: str) -> None:
    """Handle /ruok confirm|solve|ignore <session_id> (SUPERUSER only)"""
    user_id = event.get_user_id()
    if user_id not in get_driver().config.superusers:
        await ruok_cmd.finish("❌ 此操作仅 SUPERUSER 可用")
        return

    sid = rest.strip()
    if not sid:
        await ruok_cmd.finish(f"用法: /ruok {action} <session_id>")
        return

    status_map = {"confirm": "unsolved", "solve": "solved", "ignore": "ignored"}
    new_status = status_map[action]

    session = update_session(data_dir, sid, {"status": new_status})
    if session is None:
        await ruok_cmd.finish(f"❌ Session `{sid}` 未找到。")
        return

    icon = {"unsolved": "🔴", "solved": "🟢", "ignored": "⚪"}[new_status]
    labels = {"unsolved": "已确认", "solved": "已解决", "ignored": "已忽略(误报)"}
    await ruok_cmd.finish(f"{icon} Session {sid} → {labels[new_status]}")


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
            logger.info("RuOK CORS middleware registered")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RuOK CORS setup failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "CORS middleware setup", data_dir)

        # SessionMiddleware — always added for request.session support.
        # Auth gating (enabled/disabled) is handled at route level via require_login().
        try:
            from starlette.middleware.sessions import SessionMiddleware

            secret = plugin_config.webui_secret_key or secrets.token_hex(32)
            if not plugin_config.webui_secret_key:
                logger.warning(
                    "RuOK: webui_secret_key not set — sessions invalidate on restart. "
                    "Set RUOK__WEBUI_SECRET_KEY in .env for persistence."
                )
            app.add_middleware(SessionMiddleware, secret_key=secret, max_age=None)
            logger.info("RuOK SessionMiddleware registered")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RuOK SessionMiddleware setup failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "SessionMiddleware setup", data_dir)

        # ── Global ASGI exception handler ──
        # Catches any unhandled exception that escapes route-level try/except
        # (e.g. response header encoding errors, middleware failures).
        # Creates a RuOK session so the error is tracked and visible in WebUI.
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

        logger.info("RuOK global exception handler registered")
    else:
        logger.warning(
            "RuOK: driver.server_app is not a FastAPI instance, skipping middleware"
        )


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    _connection_history[bot.self_id] = BotConnectionStatus(
        self_id=bot.self_id,
        adapter=bot.type,
        connected=True,
        connected_at=datetime.now(timezone.utc),
    )
    logger.info(f"RuOK: bot {bot.self_id} connected ({bot.type})")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    entry = _connection_history.get(bot.self_id)
    if entry:
        entry.connected = False
        entry.disconnected_at = datetime.now(timezone.utc)
    logger.info(f"RuOK: bot {bot.self_id} disconnected")


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
            logger.info("RuOK Auth routes mounted")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RuOK Auth mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "Auth routes mount", data_dir)

        try:
            router = create_ruok_router(plugin_config, data_dir)
            app.include_router(router)
            logger.info("RuOK API mounted at /ruok/api/*")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RuOK API mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "API routes mount", data_dir)

        try:
            webui_router = create_webui_router(plugin_config, data_dir, webui_auth)
            app.include_router(webui_router)
            logger.info("RuOK WebUI SSR mounted at /ruok")
        except (ImportError, RuntimeError) as exc:
            logger.warning(f"RuOK WebUI mount failed: {exc}")
        except Exception as exc:
            _handle_ruok_error(exc, "WebUI routes mount", data_dir)
    else:
        logger.info("RuOK: non-ASGI driver, skipping API/WebUI mount")

    # Start LogMonitor (works regardless of driver type)
    log_monitor.start()

    # Ensure notification rules are initialized on first run
    from .collectors.notifications import _load_rules

    _load_rules(data_dir, plugin_config)

    # Register summary notification job (best-effort, APScheduler optional)
    try:
        from .collectors.notifications import register_summary_job

        register_summary_job(plugin_config, data_dir)
    except Exception as exc:
        logger.warning(f"RuOK: summary job registration failed: {exc}")

    logger.info("RuOK plugin started")


@driver.on_shutdown
async def _shutdown() -> None:
    log_monitor.stop()
    logger.info("RuOK plugin stopped")
