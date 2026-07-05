"""Session storage, CRUD, linking, statistics, and internal error handler."""

from __future__ import annotations

import json
import hashlib
import secrets
import traceback
from typing import Any
from pathlib import Path
from datetime import datetime, timezone

from nonebot import logger

from ..protocol import (
    Session,
    ReporterInfo,
    SessionStats,
    SessionSource,
)

# ────────────────────────────────
# 1. Low-level helpers
# ────────────────────────────────


def _gen_session_id() -> str:
    return f"ruok-{secrets.token_hex(4)}"


def _make_signature(plugin: str, exc: str, msg: str) -> str:
    raw = f"{plugin}:{exc}:{msg[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ────────────────────────────────
# 2. File-system storage
# ────────────────────────────────


def _sessions_dir(data_dir: Path) -> Path:
    d = data_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(data_dir: Path, session_id: str) -> Path:
    return _sessions_dir(data_dir) / f"{session_id}.json"


def _save_session(data_dir: Path, session: Session) -> None:
    path = _session_path(data_dir, session.session_id)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def _load_session(data_dir: Path, session_id: str) -> Session | None:
    path = _session_path(data_dir, session_id)
    if not path.exists():
        return None
    return Session.model_validate_json(path.read_text(encoding="utf-8"))


def _find_existing_session(data_dir: Path, signature: str) -> Session | None:
    """Find a session with the same signature that is still pending or unsolved."""
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        except Exception as exc:
            _handle_ruok_error(
                exc, f"_find_existing_session 读取文件: {f.name}", data_dir
            )
            continue
        if s.error_signature == signature and s.status in ("pending", "unsolved"):
            return s
    return None


# ────────────────────────────────
# 3. Public CRUD
# ────────────────────────────────


def create_session(
    data_dir: Path,
    module_name: str,
    description: str,
    reporter: ReporterInfo,
    source: SessionSource = "manual",
) -> Session:
    session = Session(
        session_id=_gen_session_id(),
        source=source,
        status="pending",
        module_name=module_name,
        reporter=reporter,
        description=description,
    )
    _save_session(data_dir, session)
    _publish_session_event("created", session)
    return session


def get_session(data_dir: Path, session_id: str) -> Session | None:
    return _load_session(data_dir, session_id)


def list_sessions(
    data_dir: Path,
    status: str | None = None,
    module_name: str | None = None,
    reporter_user_id: str | None = None,
    search: str | None = None,
    first_seen_after: datetime | None = None,
    first_seen_before: datetime | None = None,
    plugin_name: str | None = None,
) -> list[Session]:
    """List sessions with optional advanced filters.

    Args:
        search: Full-text search across session_id, module_name, description,
                reporter user_id, error_signature.
        first_seen_after: Only sessions first seen after this time (UTC).
        first_seen_before: Only sessions first seen before this time.
        plugin_name: Only sessions whose module_name matches a ModuleDefinition
                     that includes this plugin in its ``plugins`` list.
    """
    sessions: list[Session] = []
    statuses = {s.strip() for s in status.split(",")} if status else None

    module_plugin_names: dict[str, set[str]] | None = None
    if plugin_name:
        module_plugin_names = _build_module_plugin_map(data_dir)

    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        except Exception as exc:
            _handle_ruok_error(exc, f"list_sessions 读取文件: {f.name}", data_dir)
            continue
        if statuses and s.status not in statuses:
            continue
        if module_name and s.module_name != module_name:
            continue
        if reporter_user_id and s.reporter.user_id != reporter_user_id:
            continue
        if first_seen_after and s.first_seen_at < first_seen_after:
            continue
        if first_seen_before and s.first_seen_at > first_seen_before:
            continue
        if plugin_name and module_plugin_names:
            allowed_modules = module_plugin_names.get(plugin_name, set())
            if s.module_name not in allowed_modules:
                continue
        if search:
            q = search.lower()
            if not _session_matches_search(s, q):
                continue
        sessions.append(s)
    sessions.sort(key=lambda s: s.last_seen_at, reverse=True)
    return sessions


def update_session(
    data_dir: Path, session_id: str, updates: dict[str, Any]
) -> Session | None:
    session = _load_session(data_dir, session_id)
    if session is None:
        return None

    allowed = {"status", "developer_notes", "last_seen_at", "link_group"}
    old_status = session.status
    for k, v in updates.items():
        if k in allowed:
            setattr(session, k, v)

    if "status" in updates and updates["status"] in ("solved", "ignored"):
        session.resolved_at = datetime.now(timezone.utc)

    _save_session(data_dir, session)
    if "status" in updates and updates["status"] != old_status:
        _publish_session_event("updated", session, old_status=old_status)
    return session


def _publish_session_event(
    action: str, session: Session, old_status: str | None = None
) -> None:
    """Publish a session lifecycle event to the SSE EventBus (best-effort)."""
    try:
        from ..webui.sse import event_bus

        event_bus.publish(
            "session_update",
            {
                "action": action,
                "session_id": session.session_id,
                "module_name": session.module_name,
                "status": session.status,
                "old_status": old_status,
                "last_seen_at": session.last_seen_at.isoformat(),
            },
        )
    except (ImportError, RuntimeError):
        pass  # SSE is best-effort, never crash the collector


# ────────────────────────────────
# 4. Internal error handler — auto-create RuOK sessions
# ────────────────────────────────


def _handle_ruok_error(
    exc: BaseException,
    context: str,
    data_dir: Path,
) -> str:
    """Log an unexpected internal error with full traceback and create a
    RuOK self-monitoring session under the built-in ``"ruok"`` module.

    Deduplicates by *error_signature*: if a pending/unsolved session already
    exists for this signature, appends an occurrence instead of creating
    a new session.

    Returns the *session_id* for use in user-facing error messages, or
    ``"N/A"`` when even the session cannot be persisted.
    """
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    logger.error(f"RuOK 内部异常 [{context}]: {type(exc).__name__}: {exc}\n{tb_text}")

    signature = _make_signature("ruok", type(exc).__name__, str(exc)[:100])

    try:
        # ── Deduplicate: if a matching session exists, update it ──
        existing = _find_existing_session(data_dir, signature)
        if existing is not None:
            existing.last_seen_at = datetime.now(timezone.utc)
            existing.description = (
                f"**上下文**: {context}\n"
                f"**异常类型**: {type(exc).__name__}\n"
                f"**异常信息**: {exc}\n\n"
                f"```\n{tb_text}\n```"
            )
            existing.developer_notes = tb_text
            _save_session(data_dir, existing)
            _publish_session_event("updated", existing)
            return existing.session_id

        # ── New session ──
        session = Session(
            session_id=_gen_session_id(),
            source="automatic",
            status="pending",
            module_name="ruok",
            error_signature=signature,
            reporter=ReporterInfo(type="automatic"),
            description=(
                f"**上下文**: {context}\n"
                f"**异常类型**: {type(exc).__name__}\n"
                f"**异常信息**: {exc}\n\n"
                f"```\n{tb_text}\n```"
            ),
            developer_notes=tb_text,
        )
        _save_session(data_dir, session)
    except Exception:
        return "N/A"

    _publish_session_event("created", session)
    return session.session_id


# ────────────────────────────────
# 5. Session linking
# ────────────────────────────────


def link_sessions(data_dir: Path, session_id_a: str, session_id_b: str) -> bool:
    """Link two sessions into the same link_group (group-based, no cross-linking).

    Scenarios:
    - Both have no group → create new group, both join.
    - One has a group → the other joins that group.
    - Different groups → merge all sessions in group B into group A.
    - Same group → no-op (idempotent).
    """
    sa = _load_session(data_dir, session_id_a)
    sb = _load_session(data_dir, session_id_b)
    if sa is None or sb is None:
        return False
    if sa.session_id == sb.session_id:
        return False

    ga = sa.link_group
    gb = sb.link_group

    if ga is None and gb is None:
        gid = f"ruok-grp-{secrets.token_hex(4)}"
        sa.link_group = gid
        sb.link_group = gid
        _save_session(data_dir, sa)
        _save_session(data_dir, sb)
    elif ga and gb is None:
        sb.link_group = ga
        _save_session(data_dir, sb)
    elif ga is None and gb:
        sa.link_group = gb
        _save_session(data_dir, sa)
    elif ga == gb:
        return True
    elif ga is not None and gb is not None:
        _merge_link_groups(data_dir, gb, ga)
    else:
        return False

    return True


def unlink_session(data_dir: Path, session_id: str) -> bool:
    """Remove a session from its link_group."""
    s = _load_session(data_dir, session_id)
    if s is None or s.link_group is None:
        return False
    s.link_group = None
    _save_session(data_dir, s)
    return True


def get_linked_sessions(data_dir: Path, session_id: str) -> list[Session]:
    """Return all sessions in the same link_group (excluding self)."""
    s = _load_session(data_dir, session_id)
    if s is None or s.link_group is None:
        return []
    group = s.link_group
    linked: list[Session] = []
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            other = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        except Exception as exc:
            _handle_ruok_error(exc, f"get_linked_sessions 读取文件: {f.name}", data_dir)
            continue
        if other.link_group == group and other.session_id != session_id:
            linked.append(other)
    linked.sort(key=lambda x: x.last_seen_at, reverse=True)
    return linked


def _merge_link_groups(data_dir: Path, from_group: str, to_group: str) -> None:
    """Move all sessions in *from_group* to *to_group*."""
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            s = Session.model_validate_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        except Exception as exc:
            _handle_ruok_error(exc, f"_merge_link_groups 读取文件: {f.name}", data_dir)
            continue
        if s.link_group == from_group:
            s.link_group = to_group
            _save_session(data_dir, s)


# ────────────────────────────────
# 6. Plugin-module mapping & search
# ────────────────────────────────


def _build_module_plugin_map(data_dir: Path) -> dict[str, set[str]]:
    """Build mapping: plugin_name → set of module_names that reference it."""
    mapping: dict[str, set[str]] = {}
    modules_path = data_dir / "modules.json"
    if not modules_path.exists():
        return mapping
    try:
        modules_data = json.loads(modules_path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return mapping
    for m in modules_data:
        mod_name = m.get("name", "")
        plugins = m.get("plugins", [])
        for p in plugins:
            mapping.setdefault(p, set()).add(mod_name)
    return mapping


def _session_matches_search(s: Session, q: str) -> bool:
    """Check if *q* appears in any searchable field of *s*."""
    if q in s.session_id.lower():
        return True
    if q in s.module_name.lower():
        return True
    if q in s.description.lower():
        return True
    if s.reporter.user_id and q in s.reporter.user_id.lower():
        return True
    if s.error_signature and q in s.error_signature.lower():
        return True
    return False


# ────────────────────────────────
# 7. Session statistics
# ────────────────────────────────


def get_session_stats(data_dir: Path) -> SessionStats:
    """Compute aggregate session statistics."""
    all_sessions = list_sessions(data_dir)
    stats = SessionStats(total=len(all_sessions))
    by_module: dict[str, int] = {}
    for s in all_sessions:
        if s.status == "pending":
            stats.pending += 1
        elif s.status == "unsolved":
            stats.unsolved += 1
        elif s.status == "solved":
            stats.solved += 1
        elif s.status == "ignored":
            stats.ignored += 1
        by_module[s.module_name] = by_module.get(s.module_name, 0) + 1
    stats.by_module = by_module
    return stats
