"""Tests for collectors/modules.py — Module CRUD and status derivation.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""

from __future__ import annotations

import json
from pathlib import Path


class TestListModules:
    def test_builtin_ruok_module(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import list_modules

        config = ScopedConfig()
        modules = list_modules(tmp_path, config)
        assert len(modules) >= 1
        ruok_mod = next((m for m in modules if m.name == "ruok"), None)
        assert ruok_mod is not None
        assert ruok_mod.display_name == "RUOK"
        assert "nonebot_plugin_ruok" in ruok_mod.plugins

    def test_persisted_on_disk(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import list_modules

        config = ScopedConfig()
        list_modules(tmp_path, config)
        assert (tmp_path / "modules.json").exists()

    def test_ignores_legacy_enabled_field(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import get_module

        (tmp_path / "modules.json").write_text(
            json.dumps(
                [
                    {
                        "name": "legacy",
                        "display_name": "Legacy",
                        "plugins": [],
                        "enabled": False,
                    }
                ]
            ),
            encoding="utf-8",
        )

        mod = get_module(tmp_path, ScopedConfig(), "legacy")
        assert mod is not None
        assert mod.name == "legacy"

    def test_ignores_invalid_persisted_module_rows(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import list_modules

        (tmp_path / "modules.json").write_text(
            json.dumps(
                [
                    {"name": "valid", "plugins": ["plugin_a"]},
                    {"name": "invalid", "plugins": "not-a-list"},
                ]
            ),
            encoding="utf-8",
        )

        modules = list_modules(tmp_path, ScopedConfig())

        assert [module.name for module in modules] == ["ruok", "valid"]


class TestUpsertModule:
    def test_recovers_from_invalid_json(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module

        (tmp_path / "modules.json").write_text("{", encoding="utf-8")

        upsert_module(tmp_path, ModuleDefinition(name="weather"))

        stored = json.loads((tmp_path / "modules.json").read_text("utf-8"))
        assert [module["name"] for module in stored] == ["weather"]

    def test_create_new(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            list_modules,
            upsert_module,
        )

        config = ScopedConfig()
        definition = ModuleDefinition(
            name="weather",
            display_name="天气",
            plugins=["nonebot_plugin_weather"],
            description="天气查询模块",
        )
        upsert_module(tmp_path, definition)
        modules = list_modules(tmp_path, config)
        assert any(m.name == "weather" for m in modules)

    def test_update_existing(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )

        config = ScopedConfig()
        definition = ModuleDefinition(
            name="music",
            display_name="音乐",
            plugins=["nonebot_plugin_music"],
        )
        upsert_module(tmp_path, definition)

        updated = ModuleDefinition(
            name="music",
            display_name="网易云音乐",
            plugins=["nonebot_plugin_ncm"],
            description="updated",
        )
        upsert_module(tmp_path, updated)

        mod = get_module(tmp_path, config, "music")
        assert mod is not None
        assert mod.display_name == "网易云音乐"
        assert mod.description == "updated"


class TestDeleteModule:
    def test_delete_existing(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            list_modules,
            delete_module,
            upsert_module,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(
                name="test-del",
                display_name="ToDelete",
            ),
        )
        assert delete_module(tmp_path, "test-del") is True
        modules = list_modules(tmp_path, config)
        assert not any(m.name == "test-del" for m in modules)

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.modules import delete_module

        assert delete_module(tmp_path, "does-not-exist") is False


class TestGetModule:
    def test_get_existing(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(
                name="test-get",
                display_name="GetTest",
            ),
        )
        mod = get_module(tmp_path, config, "test-get")
        assert mod is not None
        assert mod.name == "test-get"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import get_module

        assert get_module(tmp_path, ScopedConfig(), "nope") is None


class TestDeriveModuleStatus:
    def test_available_no_sessions(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.modules import (
            derive_module_status,
        )

        assert derive_module_status(tmp_path, "any-module") == "available"

    def test_degraded_with_pending(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.modules import (
            derive_module_status,
        )
        from nonebot_plugin_ruok.collectors.sessions import create_session

        reporter = ReporterInfo(type="user")
        create_session(tmp_path, "mod-x", "issue", reporter)
        assert derive_module_status(tmp_path, "mod-x") == "degraded"

    def test_unavailable_with_unsolved(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.modules import (
            derive_module_status,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        s = create_session(tmp_path, "mod-y", "critical", reporter)
        update_session(tmp_path, s.session_id, {"status": "unsolved"})
        assert derive_module_status(tmp_path, "mod-y") == "unavailable"

    def test_ignored_not_counted(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.modules import (
            derive_module_status,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        s = create_session(tmp_path, "mod-z", "false alarm", reporter)
        update_session(tmp_path, s.session_id, {"status": "ignored"})
        assert derive_module_status(tmp_path, "mod-z") == "available"

    def test_pending_session_degrades_modules_sharing_plugins(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )
        from nonebot_plugin_ruok.collectors.sessions import create_session

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
        )
        upsert_module(
            tmp_path,
            ModuleDefinition(name="playlist", plugins=["plugin_b"]),
        )
        create_session(tmp_path, "music", "pending issue", ReporterInfo(type="user"))

        related = get_module(tmp_path, config, "playlist")

        assert related is not None
        assert related.status == "degraded"
        assert any("plugin_b" in reason for reason in related.status_reasons)

    def test_confirm_selected_plugin_unavailable_only_shared_modules(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
        )
        upsert_module(
            tmp_path,
            ModuleDefinition(name="lyrics", plugins=["plugin_a"]),
        )
        upsert_module(
            tmp_path,
            ModuleDefinition(name="playlist", plugins=["plugin_b"]),
        )
        session = create_session(
            tmp_path,
            "music",
            "confirmed issue",
            ReporterInfo(type="user"),
        )
        confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_a"])

        lyrics = get_module(tmp_path, config, "lyrics")
        playlist = get_module(tmp_path, config, "playlist")

        assert lyrics is not None
        assert playlist is not None
        assert lyrics.status == "unavailable"
        assert playlist.status == "available"

    def test_confirm_without_plugin_does_not_propagate(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
        session = create_session(
            tmp_path,
            "music",
            "local only",
            ReporterInfo(type="user"),
        )
        confirm_session_plugins(tmp_path, config, session.session_id, [])

        music = get_module(tmp_path, config, "music")
        lyrics = get_module(tmp_path, config, "lyrics")

        assert music is not None
        assert lyrics is not None
        assert music.status == "unavailable"
        assert lyrics.status == "available"

    def test_confirm_can_replace_existing_plugin_impact(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            create_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
        )
        upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
        upsert_module(
            tmp_path,
            ModuleDefinition(name="playlist", plugins=["plugin_b"]),
        )
        session = create_session(
            tmp_path,
            "music",
            "plugin impact changed",
            ReporterInfo(type="user"),
        )
        confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_a"])

        confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_b"])

        reloaded = get_session(tmp_path, session.session_id)
        lyrics = get_module(tmp_path, config, "lyrics")
        playlist = get_module(tmp_path, config, "playlist")
        assert reloaded is not None
        assert reloaded.affected_plugins == ["plugin_b"]
        assert lyrics is not None
        assert lyrics.status == "available"
        assert playlist is not None
        assert playlist.status == "unavailable"

    def test_solving_one_session_keeps_other_plugin_impact(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            get_module,
            upsert_module,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
        first = create_session(tmp_path, "music", "first", ReporterInfo(type="user"))
        second = create_session(tmp_path, "music", "second", ReporterInfo(type="user"))
        confirm_session_plugins(tmp_path, config, first.session_id, ["plugin_a"])
        confirm_session_plugins(tmp_path, config, second.session_id, ["plugin_a"])

        update_session(tmp_path, first.session_id, {"status": "solved"})
        lyrics = get_module(tmp_path, config, "lyrics")

        assert lyrics is not None
        assert lyrics.status == "unavailable"

    def test_direct_unsolved_takes_priority_over_plugin_pending(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            upsert_module,
            derive_module_status_with_reasons,
        )
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
        create_session(tmp_path, "music", "pending shared", ReporterInfo(type="user"))
        direct = create_session(
            tmp_path,
            "lyrics",
            "direct confirmed",
            ReporterInfo(type="user"),
        )
        update_session(tmp_path, direct.session_id, {"status": "unsolved"})

        status, reasons = derive_module_status_with_reasons(tmp_path, "lyrics")

        assert status == "unavailable"
        assert reasons == ["存在已确认未解决的本模块 Session"]

    def test_status_derivation_ignores_stale_plugin_impacts_file(
        self,
        tmp_path: Path,
    ) -> None:
        import json

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import get_module, upsert_module

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        (tmp_path / "plugin_impacts.json").write_text(
            json.dumps(
                {
                    "plugin_a": {
                        "pending": ["ruok-stale"],
                        "unsolved": ["ruok-stale"],
                    }
                }
            ),
            encoding="utf-8",
        )

        module = get_module(tmp_path, config, "music")

        assert module is not None
        assert module.status == "available"
        assert module.status_reasons == []

    def test_module_related_sessions_are_deduplicated_when_direct_and_propagated(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            upsert_module,
            list_module_related_sessions,
        )
        from nonebot_plugin_ruok.collectors.sessions import create_session

        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        session = create_session(
            tmp_path,
            "music",
            "direct and plugin-related",
            ReporterInfo(type="user"),
        )

        related = list_module_related_sessions(tmp_path, "music")

        assert [s.session_id for s in related] == [session.session_id]


class TestResolveModuleDisplay:
    def test_exact_name(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            upsert_module,
            resolve_module_display,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(
                name="weather",
                display_name="天气查询",
            ),
        )
        resolved = resolve_module_display("weather", tmp_path, config)
        assert resolved is not None
        assert resolved.name == "weather"
        assert resolved.display_name == "天气查询"

    def test_display_name_match(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import (
            upsert_module,
            resolve_module_display,
        )

        config = ScopedConfig()
        upsert_module(
            tmp_path,
            ModuleDefinition(
                name="music",
                display_name="网易云音乐",
            ),
        )
        resolved = resolve_module_display("网易云音乐", tmp_path, config)
        assert resolved is not None
        assert resolved.name == "music"

    def test_no_match(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors.modules import (
            resolve_module_display,
        )

        resolved = resolve_module_display("unknown", tmp_path, ScopedConfig())
        assert resolved is None
