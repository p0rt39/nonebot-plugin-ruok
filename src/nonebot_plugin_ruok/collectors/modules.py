"""Module definition CRUD and real-time status derivation."""

from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from nonebot import logger

from ..config import ScopedConfig
from .storage import locked_file, atomic_write_text
from .sessions import (
    list_sessions,
    _data_dir_lock,
    build_plugin_impacts,
    rebuild_plugin_impacts,
)
from ..protocol import Session, ModuleStatus, ModuleDefinition

# ────────────────────────────────
# 1. Storage helpers
# ────────────────────────────────


def _modules_path(data_dir: Path) -> Path:
    return data_dir / "modules.json"


def _path_write_json(path: Path, data: list[dict[str, Any]]) -> None:
    """Write JSON data to path, creating parent directories as needed."""
    with locked_file(path):
        atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def _load_module_records(path: Path) -> list[ModuleDefinition]:
    """Load valid module definitions without letting one bad row abort the list."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"RUOK: failed to load module definitions: {exc}")
        return []
    if not isinstance(raw, list):
        logger.warning("RUOK: modules file must contain a JSON array")
        return []

    modules: list[ModuleDefinition] = []
    for row in raw:
        try:
            modules.append(ModuleDefinition.model_validate(row))
        except (TypeError, ValueError) as exc:
            logger.warning(f"RUOK: ignored invalid module definition: {exc}")
    return modules


def _builtin_module() -> ModuleDefinition:
    """Return the built-in RUOK self-monitoring module."""
    return ModuleDefinition(
        name="ruok",
        display_name="RUOK",
        description="RUOK 插件自身 — 监控系统健康状态",
        plugins=["nonebot_plugin_ruok"],
    )


# ────────────────────────────────
# 2. Module CRUD
# ────────────────────────────────


def list_modules(data_dir: Path, config: ScopedConfig) -> list[ModuleDefinition]:
    """Return all defined modules with real-time derived status.

    Always includes a built-in «ruok» module representing RUOK itself.
    On first run, this module is persisted to modules.json.
    """
    with _data_dir_lock(data_dir):
        path = _modules_path(data_dir)
        modules = _load_module_records(path)

        has_builtin = any(m.name == "ruok" for m in modules)
        if not has_builtin:
            modules.insert(0, _builtin_module())
            _path_write_json(path, [m.model_dump() for m in modules])
        else:
            # Migration: keep old persisted built-in defaults aligned without
            # overwriting user-customized labels.
            changed = False
            for m in modules:
                if m.name != "ruok":
                    continue
                if not m.plugins:
                    m.plugins = ["nonebot_plugin_ruok"]
                    changed = True
                if m.display_name in {"RuOK", "ruok"}:
                    m.display_name = "RUOK"
                    changed = True
                if m.description == "RuOK 插件自身 — 监控系统健康状态":
                    m.description = "RUOK 插件自身 — 监控系统健康状态"
                    changed = True
                break
            if changed:
                _path_write_json(path, [mod.model_dump() for mod in modules])

        rebuild_plugin_impacts(data_dir)
        for mod in modules:
            mod.status, mod.status_reasons = derive_module_status_with_reasons(
                data_dir, mod.name
            )
    return modules


def get_module(
    data_dir: Path, config: ScopedConfig, name: str
) -> ModuleDefinition | None:
    for m in list_modules(data_dir, config):
        if m.name == name:
            return m
    return None


def upsert_module(data_dir: Path, definition: ModuleDefinition) -> ModuleDefinition:
    path = _modules_path(data_dir)
    with _data_dir_lock(data_dir), locked_file(path):
        modules = [module.model_dump() for module in _load_module_records(path)]

        existing_idx = next(
            (i for i, m in enumerate(modules) if m["name"] == definition.name),
            None,
        )
        data = definition.model_dump()
        if existing_idx is not None:
            modules[existing_idx] = data
        else:
            modules.append(data)

        _path_write_json(path, modules)
    return definition


def delete_module(data_dir: Path, name: str) -> bool:
    path = _modules_path(data_dir)
    if not path.exists():
        return False
    with _data_dir_lock(data_dir), locked_file(path):
        modules = [module.model_dump() for module in _load_module_records(path)]
        new_modules = [m for m in modules if m["name"] != name]
        if len(new_modules) == len(modules):
            return False
        _path_write_json(path, new_modules)
    return True


# ────────────────────────────────
# 3. Module status derivation
# ────────────────────────────────


def derive_module_status(data_dir: Path, module_name: str) -> ModuleStatus:
    """Real-time module status from direct sessions and plugin impacts."""
    status, _reasons = derive_module_status_with_reasons(data_dir, module_name)
    return status


def derive_module_status_with_reasons(
    data_dir: Path,
    module_name: str,
) -> tuple[ModuleStatus, list[str]]:
    """Return module status and human-readable derivation reasons."""
    direct_sessions = list_sessions(data_dir, module_name=module_name)
    reasons: list[str] = []
    if any(session.status == "unsolved" for session in direct_sessions):
        reasons.append("存在已确认未解决的本模块 Session")
        return "unavailable", reasons

    modules_by_name = _load_module_definitions(data_dir)
    module = modules_by_name.get(module_name)
    module_plugins = module.plugins if module else []
    impacts = build_plugin_impacts(data_dir)
    for plugin_name in module_plugins:
        unsolved_ids = impacts.get(plugin_name, {}).get("unsolved", [])
        if unsolved_ids:
            reasons.append(
                f"关联插件 {plugin_name} 存在已确认未解决 Session: "
                + ", ".join(unsolved_ids)
            )
            return "unavailable", reasons

    if any(session.status == "pending" for session in direct_sessions):
        reasons.append("存在待确认的本模块 Session")
        return "degraded", reasons

    for plugin_name in module_plugins:
        pending_ids = impacts.get(plugin_name, {}).get("pending", [])
        if pending_ids:
            reasons.append(
                f"关联插件 {plugin_name} 存在待确认 Session: " + ", ".join(pending_ids)
            )
            return "degraded", reasons

    return "available", reasons


def list_module_related_sessions(data_dir: Path, module_name: str) -> list[Session]:
    """Return direct and plugin-propagated sessions related to a module."""
    sessions = list_sessions(data_dir)
    modules_by_name = _load_module_definitions(data_dir)
    module = modules_by_name.get(module_name)
    module_plugins = set(module.plugins if module else [])
    related: list[Session] = []
    seen: set[str] = set()
    for session in sessions:
        if session.module_name == module_name:
            related.append(session)
            seen.add(session.session_id)
            continue
        if session.status == "pending":
            source_module = modules_by_name.get(session.module_name)
            source_plugins = set(source_module.plugins if source_module else [])
            if module_plugins.intersection(source_plugins):
                related.append(session)
                seen.add(session.session_id)
            continue
        if session.status == "unsolved" and module_plugins.intersection(
            session.affected_plugins
        ):
            if session.session_id not in seen:
                related.append(session)
                seen.add(session.session_id)
    return related


def _load_module_definitions(data_dir: Path) -> dict[str, ModuleDefinition]:
    """Load persisted module definitions without status derivation."""
    path = _modules_path(data_dir)
    modules = _load_module_records(path)
    if not any(module.name == "ruok" for module in modules):
        modules.insert(0, _builtin_module())
    return {module.name: module for module in modules}


# ────────────────────────────────
# 4. display_name resolution
# ────────────────────────────────


def resolve_module_display(
    user_input: str,
    data_dir: Path,
    config: ScopedConfig,
) -> ModuleDefinition | None:
    """Resolve user input to a ModuleDefinition.

    Priority: display_name match → name match → None.
    """
    modules = list_modules(data_dir, config)
    for m in modules:
        if m.display_name and m.display_name.strip() == user_input:
            return m
    for m in modules:
        if m.name == user_input:
            return m
    return None
