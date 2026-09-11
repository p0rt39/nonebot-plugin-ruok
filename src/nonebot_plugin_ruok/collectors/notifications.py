"""Notification engine — rule evaluation, cooldown, multi-channel dispatch."""

from __future__ import annotations

import json
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta

import httpx
from nonebot import logger, get_driver

from ..config import ScopedConfig
from .storage import locked_file, atomic_write_text
from ..protocol import Session, NotificationRule

# ────────────────────────────────
# 0. Time formatting helper (UTC → local)
# ────────────────────────────────


def _fmt_time(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Convert a UTC datetime to local time and format it."""
    return dt.astimezone().strftime(fmt)


# ────────────────────────────────
# 1. Rule loading / default
# ────────────────────────────────


def _default_rule() -> NotificationRule:
    return NotificationRule(
        name="superusers",
        enabled=True,
        on_status=["pending", "unsolved"],
        on_module=[],
        cooldown_minutes=60.0,
        channels=["bot_dm"],
        webhook_url=None,
    )


def _load_rules(data_dir: Path) -> list[NotificationRule]:
    """Load persisted notification rules, creating a default rule if needed."""
    rules_file = data_dir / "notification_rules.json"
    file_rules: list[dict[str, Any]] = []
    if rules_file.exists():
        try:
            file_rules = json.loads(rules_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            file_rules = []

    rules: list[NotificationRule] = []
    if not isinstance(file_rules, list):
        logger.warning("RUOK: notification rules file must contain a JSON array")
    else:
        for rule_data in file_rules:
            try:
                rules.append(NotificationRule.model_validate(rule_data))
            except (TypeError, ValueError) as exc:
                logger.warning(f"RUOK: ignored invalid notification rule: {exc}")

    # Auto-create default rule if none exist
    if not rules:
        default = _default_rule()
        rules.append(default)
        _save_rules(data_dir, rules)

    return rules


def _save_rules(data_dir: Path, rules: list[NotificationRule]) -> None:
    rules_file = data_dir / "notification_rules.json"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(
        json.dumps(
            [r.model_dump(mode="json") for r in rules],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ────────────────────────────────
# 2. Rule evaluation
# ────────────────────────────────


def evaluate_rules(
    session: Session,
    rules: list[NotificationRule],
) -> list[NotificationRule]:
    """Return rules that match the given session."""
    matching: list[NotificationRule] = []
    for rule in rules:
        if not rule.enabled:
            continue
        if session.status not in rule.on_status:
            continue
        if rule.on_module and session.module_name not in rule.on_module:
            continue
        matching.append(rule)
    return matching


# ────────────────────────────────
# 3. Cooldown
# ────────────────────────────────


def _cooldowns_path(data_dir: Path) -> Path:
    return data_dir / "notification_cooldowns.json"


def _load_cooldowns(data_dir: Path) -> dict[str, str]:
    path = _cooldowns_path(data_dir)
    if not path.exists():
        return {}
    try:
        cooldowns = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return cooldowns if isinstance(cooldowns, dict) else {}


def _save_cooldowns(data_dir: Path, cooldowns: dict[str, str]) -> None:
    path = _cooldowns_path(data_dir)
    atomic_write_text(path, json.dumps(cooldowns, indent=2, ensure_ascii=False))


def _is_cooling_down(
    rule_name: str, cooldown_minutes: float, cooldowns: dict[str, str]
) -> bool:
    last_str = cooldowns.get(rule_name)
    if not last_str:
        return False
    try:
        last = datetime.fromisoformat(last_str)
    except (TypeError, ValueError):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    else:
        last = last.astimezone(timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(minutes=cooldown_minutes)


def _claim_cooldown(
    rule_name: str,
    cooldown_minutes: float,
    data_dir: Path,
) -> tuple[bool, str | None]:
    """Atomically check and reserve a rule/module cooldown slot.

    The returned timestamp identifies this reservation.  It is used to avoid
    removing a newer reservation when a failed sender releases its own claim.
    A non-positive cooldown keeps the historical "disabled" semantics and
    does not persist a reservation.
    """
    if cooldown_minutes <= 0:
        return True, None

    path = _cooldowns_path(data_dir)
    with locked_file(path):
        cooldowns = _load_cooldowns(data_dir)
        if _is_cooling_down(rule_name, cooldown_minutes, cooldowns):
            return False, None

        claimed_at = datetime.now(timezone.utc).isoformat()
        cooldowns[rule_name] = claimed_at
        try:
            _save_cooldowns(data_dir, cooldowns)
        except OSError as exc:
            logger.warning(f"RUOK: cannot claim notification cooldown: {exc}")
            return False, None
        return True, claimed_at


def _release_cooldown(
    rule_name: str,
    claimed_at: str,
    data_dir: Path,
) -> None:
    """Release a failed notification's reservation if it is still current."""
    path = _cooldowns_path(data_dir)
    with locked_file(path):
        cooldowns = _load_cooldowns(data_dir)
        if cooldowns.get(rule_name) != claimed_at:
            return
        cooldowns.pop(rule_name, None)
        if cooldowns:
            try:
                _save_cooldowns(data_dir, cooldowns)
            except OSError as exc:
                logger.warning(f"RUOK: cannot release notification cooldown: {exc}")
        else:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning(f"RUOK: cannot remove notification cooldown: {exc}")


# ────────────────────────────────
# 4. Channel senders
# ────────────────────────────────


async def _send_bot_dm(session: Session) -> bool:
    """Send private-chat notification to all SUPERUSERS."""
    try:
        import nonebot

        bots = nonebot.get_bots()
        if not bots:
            return False
        bot = next(iter(bots.values()))
        superusers = get_driver().config.superusers
        if not superusers:
            return False

        text = f"🔔 新的异常事件\nSession: {session.session_id}\n来源: {session.source}"
        if session.reporter.user_id:
            text += f"\n用户: {session.reporter.user_id}"
        if session.reporter.group_id:
            text += f"\n群号: {session.reporter.group_id}"
        if session.reporter.platform:
            text += f"\n平台: {session.reporter.platform}"
        text += (
            f"\n模块: {session.module_name}\n"
            f"描述: {session.description[:200]}\n"
            f"时间: {_fmt_time(session.first_seen_at)}"
        )
        sent = False
        for uid in superusers:
            try:
                await bot.send_private_msg(user_id=int(uid), message=text)
            except Exception as exc:
                logger.warning(f"RUOK: failed to notify superuser {uid}: {exc}")
            else:
                sent = True
        return sent
    except Exception as exc:
        logger.warning(f"RUOK: bot DM notification failed: {exc}")
        return False


async def _send_webhook(rule: NotificationRule, session: Session) -> bool:
    """POST session info to a webhook URL."""
    if not rule.webhook_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rule.webhook_url,
                json={
                    "event": "ruok_session",
                    "session_id": session.session_id,
                    "module": session.module_name,
                    "status": session.status,
                    "description": session.description[:500],
                    "source": session.source,
                    "timestamp": session.first_seen_at.isoformat(),
                },
            )
            if resp.is_error:
                logger.warning(
                    f"RUOK: webhook {rule.webhook_url} returned {resp.status_code}"
                )
                return False
            return True
    except Exception as exc:
        logger.warning(f"RUOK: webhook {rule.webhook_url} failed: {exc}")
        return False


# ────────────────────────────────
# 5. Main dispatch entry point
# ────────────────────────────────


async def dispatch_notification(
    session: Session,
    config: ScopedConfig,
    data_dir: Path,
) -> None:
    """Evaluate rules and send notifications for a new/updated session.

    Must be called from an async context.
    """
    if not config.notification_enabled:
        return

    rules = _load_rules(data_dir)
    matching = evaluate_rules(session, rules)
    if not matching:
        return

    tasks_by_rule: list[tuple[str, str | None, list[asyncio.Task[bool]]]] = []
    for rule in matching:
        cooldown_key = f"{rule.name}:{session.module_name}"
        claimed, claimed_at = _claim_cooldown(
            cooldown_key,
            rule.cooldown_minutes,
            data_dir,
        )
        if not claimed:
            continue

        tasks: list[asyncio.Task[bool]] = []
        for channel in rule.channels:
            if channel == "bot_dm":
                tasks.append(asyncio.create_task(_send_bot_dm(session)))
            elif channel == "webhook":
                tasks.append(asyncio.create_task(_send_webhook(rule, session)))
            else:
                logger.warning(f"RUOK: unknown notification channel: {channel}")
        if tasks:
            tasks_by_rule.append((cooldown_key, claimed_at, tasks))
        elif claimed_at is not None:
            _release_cooldown(cooldown_key, claimed_at, data_dir)

    for cooldown_key, claimed_at, tasks in tasks_by_rule:
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            if claimed_at is not None:
                _release_cooldown(cooldown_key, claimed_at, data_dir)
            raise
        if claimed_at is not None and not any(result is True for result in results):
            _release_cooldown(cooldown_key, claimed_at, data_dir)


# ────────────────────────────────
# 6. Summary notification (APScheduler)
# ────────────────────────────────


async def _send_summary(config: ScopedConfig, data_dir: Path) -> None:
    """Send a summary of unresolved sessions to superusers."""
    import nonebot

    try:
        from .sessions import list_sessions

        sessions = list_sessions(data_dir)
        pending = [s for s in sessions if s.status == "pending"]
        unsolved = [s for s in sessions if s.status == "unsolved"]
        if not pending and not unsolved:
            return

        bots = nonebot.get_bots()
        if not bots:
            return
        bot = next(iter(bots.values()))
        superusers = get_driver().config.superusers
        if not superusers:
            return

        text = (
            f"📊 RUOK 定时汇总\nPending: {len(pending)} | Unsolved: {len(unsolved)}\n"
        )
        if pending:
            text += "\n— Pending —\n"
            for s in pending[:5]:
                text += (
                    f"  {s.session_id} | {s.module_name} | "
                    f"{_fmt_time(s.first_seen_at, '%H:%M')}\n"
                )
            if len(pending) > 5:
                text += f"  ... 还有 {len(pending) - 5} 个\n"
        if unsolved:
            text += "\n— Unsolved —\n"
            for s in unsolved[:5]:
                text += (
                    f"  {s.session_id} | {s.module_name} | "
                    f"{_fmt_time(s.first_seen_at, '%H:%M')}\n"
                )
            if len(unsolved) > 5:
                text += f"  ... 还有 {len(unsolved) - 5} 个\n"

        for uid in superusers:
            try:
                await bot.send_private_msg(user_id=int(uid), message=text)
            except Exception as exc:
                logger.warning(f"RUOK: summary notify failed for {uid}: {exc}")
    except Exception as exc:
        logger.warning(f"RUOK: summary notification failed: {exc}")


def register_summary_job(config: ScopedConfig, data_dir: Path) -> None:
    """Register a periodic summary job via APScheduler, if available."""
    try:
        from nonebot import require

        require("nonebot_plugin_apscheduler")
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.info("RUOK: APScheduler not available, summary notifications disabled")
        return

    hours = config.summary_interval_hours or 4.0

    @scheduler.scheduled_job("interval", hours=hours, misfire_grace_time=300)
    async def _ruok_summary_job() -> None:
        await _send_summary(config, data_dir)

    logger.info(f"RUOK: summary notification job registered (every {hours}h)")
