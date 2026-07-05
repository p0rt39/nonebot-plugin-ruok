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
    _connection_history,
)
from .webui.router import create_webui_router

require("nonebot_plugin_localstore")
store = __import__("nonebot_plugin_localstore")

__plugin_meta__ = PluginMetadata(
    name="RuOK",
    description="Bot health monitoring, session tracking, and WebUI",
    usage=(
        "/ruok no <module> <description> — report an issue\n"
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
):
    text = args.extract_plain_text().strip()
    if not text:
        await ruok_cmd.finish(
            "RuOK — 用法:\n"
            "/ruok no <模块> <描述> — 上报问题\n"
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
    elif subcmd == "status":
        await _cmd_status()
    elif subcmd == "list":
        await _cmd_list()
    elif subcmd == "lookup":
        await _cmd_lookup(rest)
    elif subcmd in ("confirm", "solve", "ignore"):
        await _cmd_admin(bot, event, subcmd, rest)
    else:
        await ruok_cmd.finish(f"❓ 未知子命令: {subcmd}\n"+
                              "可用: no / status / lookup / confirm / solve / ignore")


# ────────────────────────────────
# Subcommand handlers
# ────────────────────────────────


async def _cmd_no(bot: Bot, event: Event, rest: str) -> None:
    """Handle /ruok no <module> <description>"""
    if not await _can_report(event):
        await ruok_cmd.finish(
            "❌ 你没有权限上报问题。需要：群管理员 / 白名单 / SUPERUSER"
            )
        return

    parts = rest.split(maxsplit=1)
    module_name = parts[0].strip() if parts else ""
    description = parts[1].strip() if len(parts) > 1 else ""

    if not module_name:
        await ruok_cmd.finish("用法: /ruok no <模块> <描述>")
        return

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

        _notify_new_session(session)
    await ruok_cmd.finish(
        f"📝 已记录 | Session: {session.session_id}\n"
        f"模块: {module_name}\n描述: {description}"
    )


async def _cmd_status() -> None:
    """Handle /ruok status"""
    try:
        from .collector import list_modules as lm

        modules = lm(data_dir, plugin_config)
    except Exception:
        modules = []

    if not modules:
        await ruok_cmd.finish("⚠️ 暂无功能模块定义。在 WebUI 或 .env 中配置模块。")
        return

    lines: list[str] = []
    for m in modules:
        icon = {"available": "🟢",
                "degraded": "🟡",
                "unavailable": "🔴"
                }.get(m.status, "⚪")
        lines.append(f"{icon} {m.name} — {m.status}")
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
            f"{s.status} | {s.first_seen_at.strftime('%H:%M')}"
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

    status_icon = {"pending": "🟡",
                   "unsolved": "🔴",
                   "solved": "🟢",
                   "ignored": "⚪"}.get(
        session.status, "❓"
    )
    lines = [
        f"{status_icon} Session: {session.session_id}",
        f"状态: {session.status} | 来源: {session.source}",
        f"模块: {session.module_name}",
        f"描述: {session.description[:200]}",
        f"创建: {session.first_seen_at} | 最近: {session.last_seen_at}",
        f"重复次数: {len(session.occurrences)}",
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
        except Exception as exc:
            logger.warning(f"RuOK CORS setup failed: {exc}")

        # SessionMiddleware — always added for request.session support.
        # Auth gating (enabled/disabled) is handled at route level via require_login().
        try:
            from starlette.middleware.sessions import SessionMiddleware

            app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32))
            logger.info("RuOK SessionMiddleware registered")
        except Exception as exc:
            logger.warning(f"RuOK SessionMiddleware setup failed: {exc}")
    else:
        logger.warning("RuOK: driver.server_app is not a FastAPI instance, " \
        "skipping middleware")


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    _connection_history[bot.self_id] = BotConnectionStatus(
        self_id=bot.self_id,
        adapter=bot.type,
        connected=True,
        connected_at=datetime.now(timezone.utc),
    )
    logger.info(f"RuOK: bot {bot.self_id} connected ({bot.type})")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot):
    entry = _connection_history.get(bot.self_id)
    if entry:
        entry.connected = False
        entry.disconnected_at = datetime.now(timezone.utc)
    logger.info(f"RuOK: bot {bot.self_id} disconnected")


# ────────────────────────────────
# Startup — mount API + LogMonitor
# ────────────────────────────────


@driver.on_startup
async def _startup():
    if isinstance(driver, ASGIMixin):
        app = driver.server_app

        # Auth router (login/logout)
        try:
            from .webui.auth import WebUIAuth

            auth = WebUIAuth(plugin_config.webui_password)
            auth_router = auth.create_router()
            app.include_router(auth_router)
            logger.info("RuOK Auth routes mounted")
        except Exception as exc:
            logger.warning(f"RuOK Auth mount failed: {exc}")

        try:
            router = create_ruok_router(plugin_config, data_dir)
            app.include_router(router)
            logger.info("RuOK API mounted at /ruok/api/*")
        except Exception as exc:
            logger.warning(f"RuOK API mount failed: {exc}")

        try:
            webui_router = create_webui_router(plugin_config, data_dir)
            app.include_router(webui_router)
            logger.info("RuOK WebUI SSR mounted at /ruok")
        except Exception as exc:
            logger.warning(f"RuOK WebUI mount failed: {exc}")
    else:
        logger.info("RuOK: non-ASGI driver, skipping API/WebUI mount")

    # Start LogMonitor (works regardless of driver type)
    log_monitor.start()

    logger.info("RuOK plugin started")


@driver.on_shutdown
async def _shutdown():
    log_monitor.stop()
    logger.info("RuOK plugin stopped")
