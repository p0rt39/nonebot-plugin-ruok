"""Image rendering helpers for RUOK chat commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nonebot import logger
from nonebot.adapters import Bot, Event

from .config import ScopedConfig
from .protocol import Session, ModuleDefinition
from .webui.jinja import render_to_string


class ChatImageRenderError(RuntimeError):
    """Raised when a chat command image cannot be rendered or sent."""


@dataclass(frozen=True)
class ChatStatusItem:
    """Display data for one module in the status image."""

    name: str
    status: str
    label: str
    icon: str
    reasons: list[str]


@dataclass(frozen=True)
class ChatSessionItem:
    """Display data for one session in the list image."""

    session_id: str
    module_name: str
    status: str
    label: str
    icon: str
    time_text: str
    description: str


def chat_render_enabled(config: ScopedConfig) -> bool:
    """Return whether chat image rendering is enabled."""
    return config.chat_render_mode == "image"


def build_status_items(modules: list[ModuleDefinition]) -> list[ChatStatusItem]:
    """Build presentation data for module status rendering."""
    labels = {
        "available": "可用",
        "degraded": "降级",
        "unavailable": "不可用",
    }
    icons = {"available": "🟢", "degraded": "🟡", "unavailable": "🔴"}
    return [
        ChatStatusItem(
            name=module.display_name or module.name,
            status=module.status,
            label=labels.get(module.status, module.status),
            icon=icons.get(module.status, "⚪"),
            reasons=module.status_reasons[:2],
        )
        for module in modules
    ]


def build_session_items(sessions: list[Session]) -> list[ChatSessionItem]:
    """Build presentation data for session list rendering."""
    labels = {
        "pending": "待确认",
        "unsolved": "未解决",
        "solved": "已解决",
        "ignored": "已忽略",
    }
    icons = {
        "pending": "🟡",
        "unsolved": "🔴",
        "solved": "🟢",
        "ignored": "⚪",
    }
    return [
        ChatSessionItem(
            session_id=session.session_id,
            module_name=session.module_name,
            status=session.status,
            label=labels.get(session.status, session.status),
            icon=icons.get(session.status, "❓"),
            time_text=session.last_seen_at.astimezone().strftime("%H:%M"),
            description=session.description[:120],
        )
        for session in sessions
    ]


async def render_status_image(
    modules: list[ModuleDefinition],
    config: ScopedConfig,
) -> bytes:
    """Render /ruok status output as a PNG image."""
    html = render_to_string(
        "chat_status.html.jinja2",
        items=build_status_items(modules),
    )
    return await _render_html(html, config)


async def render_session_list_image(
    *,
    title: str,
    sessions: list[Session],
    total_count: int,
    hidden_count: int,
    config: ScopedConfig,
) -> bytes:
    """Render /ruok list output as a PNG image."""
    html = render_to_string(
        "chat_session_list.html.jinja2",
        title=title,
        items=build_session_items(sessions),
        total_count=total_count,
        hidden_count=hidden_count,
    )
    return await _render_html(html, config)


async def send_chat_image(
    bot: Bot,
    event: Event,
    image: bytes,
) -> None:
    """Send a rendered image through the current adapter."""
    if bot.type != "OneBot V11":
        raise ChatImageRenderError(f"adapter does not support image output: {bot.type}")
    try:
        from nonebot.adapters.onebot.v11 import MessageSegment

        await bot.send(event, MessageSegment.image(image))
    except Exception as exc:
        raise ChatImageRenderError("send image failed") from exc


async def _render_html(html: str, config: ScopedConfig) -> bytes:
    try:
        from nonebot_plugin_htmlrender import render_html

        return await asyncio.wait_for(
            render_html(html, wait=0),
            timeout=config.chat_render_timeout,
        )
    except Exception as exc:
        logger.warning(f"RUOK: chat image render failed: {exc}")
        raise ChatImageRenderError("render image failed") from exc
