"""Module definition CRUD and real-time status derivation."""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from ..config import ScopedConfig
from .sessions import list_sessions
from ..protocol import ModuleStatus, ModuleDefinition

# ────────────────────────────────
# 1. Storage helpers
# ────────────────────────────────


def _modules_path(data_dir: Path) -> Path:
    return data_dir / "modules.json"


def _path_write_json(path: Path, data: list[dict[str, Any]]) -> None:
    """Write JSON data to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _builtin_module() -> ModuleDefinition:
    """Return the built-in RuOK self-monitoring module."""
    return ModuleDefinition(
        name="ruok",
        display_name="RuOK",
        description="RuOK 插件自身 — 监控系统健康状态",
        plugins=[],
        enabled=True,
    )


# ────────────────────────────────
# 2. Module CRUD
# ────────────────────────────────


def list_modules(
    data_dir: Path, config: ScopedConfig
) -> list[ModuleDefinition]:
    """Return all defined modules with real-time derived status.

    Always includes a built-in «ruok» module representing RuOK itself.
    On first run, this module is persisted to modules.json.
    """
    path = _modules_path(data_dir)
    modules: list[ModuleDefinition] = []
    if path.exists():
        try:
            modules = [
                ModuleDefinition.model_validate(m)
                for m in json.loads(path.read_text("utf-8"))
            ]
        except (json.JSONDecodeError, TypeError):
            modules = []

    has_builtin = any(m.name == "ruok" for m in modules)
    if not has_builtin:
        modules.insert(0, _builtin_module())
        _path_write_json(path, [m.model_dump() for m in modules])

    for mod in modules:
        mod.status = derive_module_status(data_dir, mod.name)
    return modules


def get_module(
    data_dir: Path, config: ScopedConfig, name: str
) -> ModuleDefinition | None:
    for m in list_modules(data_dir, config):
        if m.name == name:
            return m
    return None


def upsert_module(
    data_dir: Path, definition: ModuleDefinition
) -> ModuleDefinition:
    path = _modules_path(data_dir)
    modules: list[dict[str, Any]] = []
    if path.exists():
        modules = json.loads(path.read_text("utf-8"))

    existing_idx = next(
        (i for i, m in enumerate(modules) if m["name"] == definition.name),
        None,
    )
    data = definition.model_dump()
    if existing_idx is not None:
        modules[existing_idx] = data
    else:
        modules.append(data)

    path.write_text(
        json.dumps(modules, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return definition


def delete_module(data_dir: Path, name: str) -> bool:
    path = _modules_path(data_dir)
    if not path.exists():
        return False
    modules: list[dict[str, Any]] = json.loads(path.read_text("utf-8"))
    new_modules = [m for m in modules if m["name"] != name]
    if len(new_modules) == len(modules):
        return False
    path.write_text(
        json.dumps(new_modules, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return True


# ────────────────────────────────
# 3. Module status derivation
# ────────────────────────────────


def derive_module_status(data_dir: Path, module_name: str) -> ModuleStatus:
    """Real-time module status from its sessions."""
    sessions = list_sessions(data_dir, module_name=module_name)
    if any(s.status == "unsolved" for s in sessions):
        return "unavailable"
    if any(s.status == "pending" for s in sessions):
        return "degraded"
    return "available"


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
