"""Tests for collectors/sessions.py — Session CRUD, linking, statistics.

All imports from nonebot_plugin_ruok are inside test functions
to avoid __init__.py's require() before NoneBot init.
"""

from __future__ import annotations

import os
from typing import Any
from pathlib import Path
from datetime import datetime, timezone, timedelta


class TestGenSessionId:
    def test_generates_unique_ids(self) -> None:
        from nonebot_plugin_ruok.collectors.sessions import _gen_session_id

        ids = {_gen_session_id() for _ in range(50)}
        assert len(ids) == 50
        for sid in ids:
            assert sid.startswith("ruok-")
            assert len(sid) == 13


class TestMakeSignature:
    def test_deterministic(self) -> None:
        from nonebot_plugin_ruok.collectors.sessions import _make_signature

        s1 = _make_signature("plugin_a", "ValueError", "something broke")
        s2 = _make_signature("plugin_a", "ValueError", "something broke")
        assert s1 == s2
        assert len(s1) == 12

    def test_different_inputs(self) -> None:
        from nonebot_plugin_ruok.collectors.sessions import _make_signature

        s1 = _make_signature("plugin_a", "ValueError", "msg a")
        s2 = _make_signature("plugin_a", "ValueError", "msg b")
        assert s1 != s2

    def test_truncates_long_message(self) -> None:
        from nonebot_plugin_ruok.collectors.sessions import _make_signature

        s = _make_signature("p", "e", "x" * 500)
        assert len(s) == 12


class TestCreateAndGetSession:
    def test_concurrent_creation_keeps_sessions_and_impacts(
        self, tmp_path: Path
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
            build_plugin_impacts,
        )

        upsert_module(
            tmp_path,
            ModuleDefinition(name="concurrent", plugins=["plugin_concurrent"]),
        )

        def create(index: int):
            return create_session(
                tmp_path,
                "concurrent",
                f"issue {index}",
                ReporterInfo(type="user", user_id=str(index)),
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            sessions = list(executor.map(create, range(24)))

        assert len(list_sessions(tmp_path, module_name="concurrent")) == 24
        impacts = build_plugin_impacts(tmp_path)
        assert set(impacts["plugin_concurrent"]["pending"]) == {
            session.session_id for session in sessions
        }

    def test_create_and_retrieve(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            create_session,
        )

        reporter = ReporterInfo(type="user", user_id="123", platform="test")
        session = create_session(
            data_dir=tmp_path,
            module_name="test-module",
            description="test description",
            reporter=reporter,
            source="manual",
        )
        assert session.session_id.startswith("ruok-")
        assert session.status == "pending"
        assert session.module_name == "test-module"

        fpath = tmp_path / "sessions" / f"{session.session_id}.json"
        assert fpath.exists()

        retrieved = get_session(tmp_path, session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import get_session

        assert get_session(tmp_path, "ruok-nonexist") is None

    def test_rejects_path_traversal_session_id(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            _session_path,
            update_session,
        )

        outside = tmp_path / "outside.json"
        outside.write_text('{"sentinel": true}', encoding="utf-8")

        malicious_id = "../outside"
        try:
            _session_path(tmp_path, malicious_id)
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal session ID was accepted")

        assert get_session(tmp_path, malicious_id) is None
        assert update_session(tmp_path, malicious_id, {"status": "solved"}) is None
        assert outside.read_text(encoding="utf-8") == '{"sentinel": true}'

    def test_create_automatic(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import create_session

        reporter = ReporterInfo(type="automatic")
        session = create_session(
            data_dir=tmp_path,
            module_name="auto-module",
            description="auto error",
            reporter=reporter,
            source="automatic",
        )
        assert session.source == "automatic"


class TestListSessions:
    def test_list_empty(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import list_sessions

        assert list_sessions(tmp_path) == []

    def test_list_all(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user", user_id="u1")
        create_session(tmp_path, "mod-a", "desc 1", reporter)
        s2 = create_session(tmp_path, "mod-b", "desc 2", reporter)
        result = list_sessions(tmp_path)
        assert len(result) == 2
        assert result[0].session_id == s2.session_id

    def test_list_all_stable_when_timestamps_match(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            list_sessions,
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user", user_id="u1")
        s1 = create_session(tmp_path, "mod-a", "desc 1", reporter)
        s2 = create_session(tmp_path, "mod-b", "desc 2", reporter)
        same_time = datetime.now(timezone.utc)
        update_session(tmp_path, s1.session_id, {"last_seen_at": same_time})
        update_session(tmp_path, s2.session_id, {"last_seen_at": same_time})
        assert get_session(tmp_path, s2.session_id) is not None

        first_result = list_sessions(tmp_path)
        second_result = list_sessions(tmp_path)

        assert len(first_result) == 2
        assert [s.session_id for s in first_result] == [
            s.session_id for s in second_result
        ]

    def test_filter_by_status(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user", user_id="u1")
        s1 = create_session(tmp_path, "mod-a", "pend", reporter)
        s2 = create_session(tmp_path, "mod-b", "solv", reporter)
        update_session(tmp_path, s2.session_id, {"status": "solved"})

        pending = list_sessions(tmp_path, status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == s1.session_id

        multi = list_sessions(tmp_path, status="pending,solved")
        assert len(multi) == 2

    def test_filter_by_module(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user", user_id="u1")
        create_session(tmp_path, "mod-a", "desc", reporter)
        create_session(tmp_path, "mod-b", "desc", reporter)
        result = list_sessions(tmp_path, module_name="mod-a")
        assert len(result) == 1

    def test_filter_by_reporter(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        r1 = ReporterInfo(type="user", user_id="uid-1")
        r2 = ReporterInfo(type="user", user_id="uid-2")
        create_session(tmp_path, "mod", "desc", r1)
        create_session(tmp_path, "mod", "desc", r2)
        result = list_sessions(tmp_path, reporter_user_id="uid-1")
        assert len(result) == 1

    def test_filter_by_reporter_identity(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        create_session(
            tmp_path,
            "mod",
            "onebot",
            ReporterInfo(type="user", user_id="same", platform="OneBot V11"),
        )
        create_session(
            tmp_path,
            "mod",
            "console",
            ReporterInfo(type="user", user_id="same", platform="Console"),
        )
        result = list_sessions(
            tmp_path,
            reporter_user_id="same",
            reporter_platform="Console",
        )
        assert [session.description for session in result] == ["console"]

    def test_search(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user", user_id="uid-x")
        create_session(tmp_path, "weather", "sunny day error", reporter)
        create_session(tmp_path, "music", "playback failed", reporter)
        result = list_sessions(tmp_path, search="sunny")
        assert len(result) == 1
        assert result[0].module_name == "weather"

    def test_search_and_plugin_filter_include_persisted_affected_plugins(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        upsert_module(tmp_path, ModuleDefinition(name="other", plugins=["plugin_b"]))
        session = create_session(
            tmp_path,
            "music",
            "confirmed plugin issue",
            ReporterInfo(type="user"),
        )
        confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_a"])
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=[]))

        by_plugin = list_sessions(tmp_path, plugin_name="plugin_a")
        by_search = list_sessions(tmp_path, search="plugin_a")

        assert [s.session_id for s in by_plugin] == [session.session_id]
        assert [s.session_id for s in by_search] == [session.session_id]

    def test_time_range(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            create_session,
        )

        now = datetime.now(timezone.utc)
        reporter = ReporterInfo(type="user", user_id="u1")
        create_session(tmp_path, "mod", "old", reporter)

        result = list_sessions(tmp_path, first_seen_after=now - timedelta(hours=1))
        assert len(result) >= 1

        result = list_sessions(tmp_path, first_seen_after=now + timedelta(hours=1))
        assert len(result) == 0


class TestUpdateSession:
    def test_update_status(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        s = create_session(tmp_path, "mod", "desc", reporter)
        updated = update_session(tmp_path, s.session_id, {"status": "unsolved"})
        assert updated is not None
        assert updated.status == "unsolved"
        reloaded = get_session(tmp_path, s.session_id)
        assert reloaded is not None
        assert reloaded.status == "unsolved"

    def test_resolved_at_on_solve(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        s = create_session(tmp_path, "mod", "desc", reporter)
        assert s.resolved_at is None
        updated = update_session(tmp_path, s.session_id, {"status": "solved"})
        assert updated is not None
        assert updated.resolved_at is not None

    def test_reopening_session_clears_resolved_at(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        session = create_session(
            tmp_path,
            "mod",
            "desc",
            ReporterInfo(type="user"),
        )
        solved = update_session(tmp_path, session.session_id, {"status": "solved"})
        reopened = update_session(
            tmp_path,
            session.session_id,
            {"status": "unsolved"},
        )

        assert solved is not None
        assert solved.resolved_at is not None
        assert reopened is not None
        assert reopened.resolved_at is None

    def test_update_notes(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        s = create_session(tmp_path, "mod", "desc", reporter)
        updated = update_session(tmp_path, s.session_id, {"developer_notes": "fixed"})
        assert updated is not None
        assert updated.developer_notes == "fixed"

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import update_session

        assert update_session(tmp_path, "ruok-nope", {"status": "solved"}) is None

    def test_update_rejects_invalid_status_without_corrupting_file(
        self,
        tmp_path: Path,
    ) -> None:
        import pytest

        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            SessionUpdateValidationError,
            get_session,
            create_session,
            update_session,
        )

        session = create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))

        with pytest.raises(SessionUpdateValidationError):
            update_session(tmp_path, session.session_id, {"status": "invalid"})

        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        assert reloaded.status == "pending"

    def test_old_session_json_defaults_affected_plugins(self, tmp_path: Path) -> None:
        import json
        from datetime import datetime, timezone

        from nonebot_plugin_ruok.collectors.sessions import get_session

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        (session_dir / "ruok-legacy.json").write_text(
            json.dumps(
                {
                    "session_id": "ruok-legacy",
                    "source": "manual",
                    "status": "pending",
                    "module_name": "music",
                    "reporter": {"type": "user"},
                    "description": "legacy",
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
            ),
            encoding="utf-8",
        )

        session = get_session(tmp_path, "ruok-legacy")

        assert session is not None
        assert session.affected_plugins == []

    def test_confirm_session_plugins_rejects_invalid_plugin(
        self,
        tmp_path: Path,
    ) -> None:
        import pytest

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.sessions import (
            SessionPluginValidationError,
            get_session,
            create_session,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        session = create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))

        with pytest.raises(SessionPluginValidationError):
            confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_b"])

        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        assert reloaded.status == "pending"
        assert reloaded.affected_plugins == []

    def test_confirm_session_plugins_normalizes_names(
        self,
        tmp_path: Path,
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
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
        session = create_session(tmp_path, "music", "issue", ReporterInfo(type="user"))

        confirm_session_plugins(
            tmp_path,
            config,
            session.session_id,
            [" plugin_a, plugin_b ", "plugin_a", "\nplugin_b\n", ""],
        )

        reloaded = get_session(tmp_path, session.session_id)
        assert reloaded is not None
        assert reloaded.status == "unsolved"
        assert reloaded.affected_plugins == ["plugin_a", "plugin_b"]

    def test_plugin_impacts_rebuilds_from_active_sessions(
        self,
        tmp_path: Path,
    ) -> None:
        import json

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            build_plugin_impacts,
            rebuild_plugin_impacts,
            confirm_session_plugins,
        )

        config = ScopedConfig()
        upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
        pending = create_session(
            tmp_path,
            "music",
            "pending",
            ReporterInfo(type="user"),
        )
        confirmed = create_session(
            tmp_path,
            "music",
            "confirmed",
            ReporterInfo(type="user"),
        )
        confirm_session_plugins(tmp_path, config, confirmed.session_id, ["plugin_a"])

        impacts = build_plugin_impacts(tmp_path)
        persisted = rebuild_plugin_impacts(tmp_path)
        disk = json.loads((tmp_path / "plugin_impacts.json").read_text("utf-8"))

        assert impacts["plugin_a"]["pending"] == [pending.session_id]
        assert confirmed.session_id in impacts["plugin_a"]["unsolved"]
        assert persisted == impacts
        assert disk == impacts


class TestLinkSessions:
    def test_link_two(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            link_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user")
        s1 = create_session(tmp_path, "mod", "first", reporter)
        s2 = create_session(tmp_path, "mod", "second", reporter)
        assert link_sessions(tmp_path, s1.session_id, s2.session_id) is True

        r1 = get_session(tmp_path, s1.session_id)
        r2 = get_session(tmp_path, s2.session_id)
        assert r1 is not None
        assert r2 is not None
        assert r1.link_group is not None
        assert r1.link_group == r2.link_group
        assert r1.link_group.startswith("ruok-grp-")

    def test_link_nonexistent(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            link_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user")
        s1 = create_session(tmp_path, "mod", "desc", reporter)
        assert link_sessions(tmp_path, s1.session_id, "ruok-nope") is False

    def test_unlink(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            link_sessions,
            create_session,
            unlink_session,
        )

        reporter = ReporterInfo(type="user")
        s1 = create_session(tmp_path, "mod", "first", reporter)
        s2 = create_session(tmp_path, "mod", "second", reporter)
        link_sessions(tmp_path, s1.session_id, s2.session_id)
        assert unlink_session(tmp_path, s1.session_id) is True
        r1 = get_session(tmp_path, s1.session_id)
        assert r1 is not None
        assert r1.link_group is None

    def test_get_linked(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            link_sessions,
            create_session,
            get_linked_sessions,
        )

        reporter = ReporterInfo(type="user")
        s1 = create_session(tmp_path, "mod", "first", reporter)
        s2 = create_session(tmp_path, "mod", "second", reporter)
        s3 = create_session(tmp_path, "mod", "third", reporter)
        link_sessions(tmp_path, s1.session_id, s2.session_id)

        linked = get_linked_sessions(tmp_path, s1.session_id)
        ids = {s.session_id for s in linked}
        assert len(linked) == 1  # excludes self
        assert s2.session_id in ids
        assert s3.session_id not in ids

    def test_merging_groups_moves_every_member(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            link_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user")
        sessions = [
            create_session(tmp_path, "mod", f"issue {index}", reporter)
            for index in range(4)
        ]
        first, second, third, fourth = sessions
        link_sessions(tmp_path, first.session_id, second.session_id)
        link_sessions(tmp_path, third.session_id, fourth.session_id)

        assert link_sessions(tmp_path, second.session_id, third.session_id) is True

        groups = []
        for session in sessions:
            stored = get_session(tmp_path, session.session_id)
            assert stored is not None
            groups.append(stored.link_group)
        assert len(set(groups)) == 1
        assert groups[0] is not None

    def test_confirm_allows_plugins_from_modules_sharing_a_log_source(
        self, tmp_path: Path
    ) -> None:
        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
        from nonebot_plugin_ruok.collectors.modules import upsert_module
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            confirm_session_plugins,
        )

        upsert_module(
            tmp_path, ModuleDefinition(name="music", plugins=["shared", "plugin-m"])
        )
        upsert_module(
            tmp_path, ModuleDefinition(name="lyrics", plugins=["shared", "plugin-l"])
        )
        session = create_session(
            tmp_path, "shared", "ambiguous log source", ReporterInfo(type="automatic")
        )

        confirmed = confirm_session_plugins(
            tmp_path, ScopedConfig(), session.session_id, ["plugin-m", "plugin-l"]
        )

        assert confirmed is not None
        assert confirmed.status == "unsolved"
        assert confirmed.affected_plugins == ["plugin-m", "plugin-l"]


class TestSessionStats:
    def test_empty(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import get_session_stats

        stats = get_session_stats(tmp_path)
        assert stats.total == 0

    def test_aggregation(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            create_session,
            update_session,
            get_session_stats,
        )

        reporter = ReporterInfo(type="user")
        create_session(tmp_path, "mod-a", "p1", reporter)
        s2 = create_session(tmp_path, "mod-a", "p2", reporter)
        create_session(tmp_path, "mod-b", "u1", reporter)
        update_session(tmp_path, s2.session_id, {"status": "solved"})

        stats = get_session_stats(tmp_path)
        assert stats.total == 3
        assert stats.pending == 2
        assert stats.solved == 1


class TestHandleRuokError:
    def test_creates_session(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            _handle_ruok_error,
        )

        try:
            raise ValueError("internal test error")
        except ValueError as exc:
            sid = _handle_ruok_error(exc, "test_ctx", tmp_path)

        assert sid.startswith("ruok-")
        sessions = list_sessions(tmp_path, module_name="ruok")
        assert len(sessions) == 1
        assert "test_ctx" in sessions[0].description

    def test_deduplicates(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            _handle_ruok_error,
        )

        try:
            raise RuntimeError("dedup test")
        except RuntimeError as exc:
            sid1 = _handle_ruok_error(exc, "first", tmp_path)

        try:
            raise RuntimeError("dedup test")
        except RuntimeError as exc:
            sid2 = _handle_ruok_error(exc, "second", tmp_path)

        assert sid1 == sid2
        sessions = list_sessions(tmp_path, module_name="ruok")
        assert len(sessions) == 1

    def test_deduplicates_concurrent_errors(self, tmp_path: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            _handle_ruok_error,
        )

        def capture(_: int) -> str:
            try:
                raise RuntimeError("concurrent dedup test")
            except RuntimeError as exc:
                return _handle_ruok_error(exc, "concurrent", tmp_path)

        with ThreadPoolExecutor(max_workers=8) as executor:
            session_ids = list(executor.map(capture, range(32)))

        sessions = list_sessions(tmp_path, module_name="ruok")
        assert len(sessions) == 1
        assert set(session_ids) == {sessions[0].session_id}


def _fail_on_read(self: Path, *args: Any, **kwargs: Any) -> str:
    raise AssertionError(f"unexpected file read: {self}")


class TestBulkUpdateSessions:
    """One bulk pass must cost one lock and one impact rebuild."""

    @staticmethod
    def _create(tmp_path: Path, count: int, *, module: str = "music") -> list[str]:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import create_session

        return [
            create_session(
                tmp_path,
                module,
                f"bulk issue {index}",
                ReporterInfo(type="user", user_id="u1"),
                source="manual",
            ).session_id
            for index in range(count)
        ]

    def test_applies_target_status_to_open_sessions(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            bulk_update_sessions,
        )

        session_ids = self._create(tmp_path, 3)
        result = bulk_update_sessions(tmp_path, session_ids, status="solved")

        assert set(result.changed) == set(session_ids)
        assert result.unchanged == ()
        assert result.skipped == ()
        statuses = {s.session_id: s for s in list_sessions(tmp_path)}
        for session_id in session_ids:
            assert statuses[session_id].status == "solved"
            assert statuses[session_id].resolved_at is not None

    def test_leaves_closed_sessions_untouched(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            get_session,
            update_session,
            bulk_update_sessions,
        )

        pending_id, ignored_id = self._create(tmp_path, 2)
        update_session(tmp_path, ignored_id, {"status": "ignored"})
        before = (tmp_path / "sessions" / f"{ignored_id}.json").read_bytes()

        result = bulk_update_sessions(
            tmp_path, [pending_id, ignored_id], status="solved"
        )

        assert result.changed == (pending_id,)
        assert ignored_id in result.skipped
        assert (tmp_path / "sessions" / f"{ignored_id}.json").read_bytes() == before
        reloaded = get_session(tmp_path, ignored_id)
        assert reloaded is not None
        assert reloaded.status == "ignored"

    def test_unchanged_rows_are_not_rewritten(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            update_session,
            bulk_update_sessions,
        )

        (session_id,) = self._create(tmp_path, 1)
        update_session(tmp_path, session_id, {"status": "solved"})
        path = tmp_path / "sessions" / f"{session_id}.json"
        before_mtime = path.stat().st_mtime_ns

        # ``solved`` is terminal, so a second solve pass skips rather than
        # rewrites; an ignored row re-ignored is reported as skipped too.
        result = bulk_update_sessions(tmp_path, [session_id], status="solved")

        assert result.changed == ()
        assert path.stat().st_mtime_ns == before_mtime

    def test_reports_missing_and_invalid_ids(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            bulk_update_sessions,
        )

        (session_id,) = self._create(tmp_path, 1)
        result = bulk_update_sessions(
            tmp_path,
            ["ruok-missing1", "../escape", session_id, session_id],
            status="ignored",
        )

        assert result.changed == (session_id,)
        assert "ruok-missing1" in result.skipped
        assert "../escape" in result.skipped
        # A duplicated id is processed once.
        assert list(result.changed).count(session_id) == 1
        assert len(list_sessions(tmp_path)) == 1

    def test_rebuilds_plugin_impacts_once(self, tmp_path: Path, monkeypatch) -> None:
        from nonebot_plugin_ruok.collectors import sessions as sessions_module

        session_ids = self._create(tmp_path, 5)
        rebuilds = {"count": 0}
        original = sessions_module.rebuild_plugin_impacts

        def counting_rebuild(*args: Any, **kwargs: Any) -> Any:
            rebuilds["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(sessions_module, "rebuild_plugin_impacts", counting_rebuild)

        sessions_module.bulk_update_sessions(tmp_path, session_ids, status="ignored")

        assert rebuilds["count"] == 1

    def test_publishes_one_aggregated_event(self, tmp_path: Path, monkeypatch) -> None:
        from nonebot_plugin_ruok.collectors import sessions as sessions_module

        session_ids = self._create(tmp_path, 4)
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            sessions_module, "_publish_bulk_event", lambda *a, **k: events.append({})
        )

        result = sessions_module.bulk_update_sessions(
            tmp_path, session_ids, status="unsolved"
        )

        assert len(result.changed) == 4
        assert len(events) == 1

    def test_rejects_non_maintenance_target_status(self, tmp_path: Path) -> None:
        import pytest

        from nonebot_plugin_ruok.collectors.sessions import (
            SessionUpdateValidationError,
            bulk_update_sessions,
        )

        with pytest.raises(SessionUpdateValidationError):
            bulk_update_sessions(tmp_path, ["ruok-any"], status="pending")

    def test_no_op_batch_does_not_rebuild(self, tmp_path: Path, monkeypatch) -> None:
        from nonebot_plugin_ruok.collectors import sessions as sessions_module

        rebuilds = {"count": 0}
        monkeypatch.setattr(
            sessions_module,
            "rebuild_plugin_impacts",
            lambda *a, **k: rebuilds.__setitem__("count", rebuilds["count"] + 1),
        )

        result = sessions_module.bulk_update_sessions(
            tmp_path, ["ruok-missing1"], status="ignored"
        )

        assert result.changed == ()
        assert rebuilds["count"] == 0


class TestStalePending:
    """Staleness uses ``last_seen_at`` and closes pending rows only."""

    @staticmethod
    def _age(tmp_path: Path, session_id: str, *, hours: float) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            _load_session,
            _save_session,
        )

        session = _load_session(tmp_path, session_id)
        assert session is not None
        session.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        _save_session(tmp_path, session)

    @staticmethod
    def _create(tmp_path: Path, count: int) -> list[str]:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import create_session

        return [
            create_session(
                tmp_path, "music", f"stale {index}", ReporterInfo(type="user")
            ).session_id
            for index in range(count)
        ]

    def test_counts_and_resolves_only_stale_pending(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            update_session,
            stale_pending_ids,
            count_stale_pending,
            bulk_resolve_stale_pending,
        )

        session_ids = self._create(tmp_path, 4)
        stale, fresh, confirmed, fresh_confirmed = session_ids
        for session_id in (stale, confirmed):
            self._age(tmp_path, session_id, hours=48)
        update_session(tmp_path, confirmed, {"status": "unsolved"})

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert count_stale_pending(tmp_path, cutoff) == 1
        assert stale_pending_ids(tmp_path, cutoff) == [stale]

        result = bulk_resolve_stale_pending(tmp_path, cutoff, status="ignored")

        assert result.changed == (stale,)
        statuses = {s.session_id: s.status for s in list_sessions(tmp_path)}
        assert statuses[stale] == "ignored"
        assert statuses[fresh] == "pending"
        assert statuses[confirmed] == "unsolved"
        assert statuses[fresh_confirmed] == "pending"

    def test_uses_last_seen_not_first_seen(self, tmp_path: Path) -> None:
        """A recurring old Session stays young while it keeps reappearing."""
        from nonebot_plugin_ruok.collectors.sessions import (
            _load_session,
            _save_session,
            count_stale_pending,
        )

        (session_id,) = self._create(tmp_path, 1)
        session = _load_session(tmp_path, session_id)
        assert session is not None
        session.first_seen_at = datetime.now(timezone.utc) - timedelta(days=30)
        session.last_seen_at = datetime.now(timezone.utc)
        _save_session(tmp_path, session)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        assert count_stale_pending(tmp_path, cutoff) == 0

    def test_count_reuses_snapshot_without_rescanning(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from nonebot_plugin_ruok.collectors.sessions import (
            load_sessions,
            count_stale_pending,
        )

        self._create(tmp_path, 2)
        snapshot = load_sessions(tmp_path)
        monkeypatch.setattr(Path, "read_text", _fail_on_read)

        cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        assert count_stale_pending(tmp_path, cutoff, snapshot=snapshot) == 2

    def test_job_is_skipped_when_not_enabled(self, tmp_path: Path, monkeypatch) -> None:
        import nonebot_plugin_apscheduler as aps

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors import sessions as sessions_module

        scheduled: list[dict[str, Any]] = []
        monkeypatch.setattr(
            aps.scheduler,
            "scheduled_job",
            lambda *a, **k: lambda fn: scheduled.append(k) and fn,
        )

        # Default: threshold 0 disables it.
        sessions_module.register_stale_cleanup_job(ScopedConfig(), tmp_path)
        # Threshold set but auto-resolve still off.
        sessions_module.register_stale_cleanup_job(
            ScopedConfig(stale_pending_after_hours=24.0), tmp_path
        )
        assert scheduled == []

    def test_job_interval_is_bounded_when_enabled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import nonebot_plugin_apscheduler as aps

        from nonebot_plugin_ruok.config import ScopedConfig
        from nonebot_plugin_ruok.collectors import sessions as sessions_module

        scheduled: list[dict[str, Any]] = []
        monkeypatch.setattr(
            aps.scheduler,
            "scheduled_job",
            lambda *a, **k: lambda fn: scheduled.append(k) and fn,
        )
        config = ScopedConfig(
            stale_pending_after_hours=0.01,
            stale_pending_auto_resolve=True,
        )

        sessions_module.register_stale_cleanup_job(config, tmp_path)

        assert len(scheduled) == 1
        # Sub-quarter-hour thresholds still sweep at a sane floor.
        assert scheduled[0]["hours"] == 0.25


class TestSessionSnapshot:
    """The read snapshot must behave exactly like a fresh directory scan."""

    def test_snapshot_matches_directory_scan(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            load_sessions,
            create_session,
        )

        reporter = ReporterInfo(type="user")
        for index in range(3):
            create_session(tmp_path, "mod", f"desc {index}", reporter)

        snapshot = load_sessions(tmp_path)
        from_snapshot = [
            s.session_id for s in list_sessions(tmp_path, snapshot=snapshot)
        ]
        from_disk = [s.session_id for s in list_sessions(tmp_path)]

        assert len(snapshot) == 3
        assert from_snapshot == from_disk

    def test_snapshot_avoids_extra_file_reads(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            load_sessions,
            create_session,
            get_session_stats,
            build_plugin_impacts,
        )

        reporter = ReporterInfo(type="user")
        create_session(tmp_path, "mod", "one", reporter)
        create_session(tmp_path, "mod", "two", reporter)
        snapshot = load_sessions(tmp_path)

        monkeypatch.setattr(Path, "read_text", _fail_on_read)

        assert len(list_sessions(tmp_path, snapshot=snapshot)) == 2
        assert get_session_stats(tmp_path, snapshot=snapshot).total == 2
        assert isinstance(build_plugin_impacts(tmp_path, snapshot=snapshot), dict)

    def test_empty_snapshot_is_reused(self, tmp_path: Path, monkeypatch) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            SessionSnapshot,
            list_sessions,
            create_session,
        )

        create_session(tmp_path, "mod", "one", ReporterInfo(type="user"))
        empty = SessionSnapshot(entries=())

        monkeypatch.setattr(Path, "read_text", _fail_on_read)

        # An empty snapshot is still a snapshot: it must not trigger a rescan.
        assert list_sessions(tmp_path, snapshot=empty) == []

    def test_preserves_mtime_ordering(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            list_sessions,
            load_sessions,
            create_session,
            update_session,
        )

        reporter = ReporterInfo(type="user")
        first = create_session(tmp_path, "mod", "first", reporter)
        second = create_session(tmp_path, "mod", "second", reporter)
        same_moment = datetime.now(timezone.utc)
        update_session(tmp_path, first.session_id, {"last_seen_at": same_moment})
        update_session(tmp_path, second.session_id, {"last_seen_at": same_moment})

        sessions_dir = tmp_path / "sessions"
        older: tuple[int, int] = (1_000_000_000, 1_000_000_000)
        newer: tuple[int, int] = (2_000_000_000, 2_000_000_000)
        os.utime(sessions_dir / f"{first.session_id}.json", ns=older)
        os.utime(sessions_dir / f"{second.session_id}.json", ns=newer)

        from_disk = [s.session_id for s in list_sessions(tmp_path)]
        snapshot = load_sessions(tmp_path)
        from_snapshot = [
            s.session_id for s in list_sessions(tmp_path, snapshot=snapshot)
        ]

        assert from_disk == [second.session_id, first.session_id]
        assert from_snapshot == from_disk

    def test_skips_corrupt_files_and_reads_legacy_ids(self, tmp_path: Path) -> None:
        from nonebot_plugin_ruok.protocol import Session, ReporterInfo
        from nonebot_plugin_ruok.collectors.sessions import (
            load_sessions,
            create_session,
        )

        created = create_session(tmp_path, "mod", "current", ReporterInfo(type="user"))
        sessions_dir = tmp_path / "sessions"
        (sessions_dir / "ruok-broken.json").write_text("{not json", encoding="utf-8")
        legacy = Session(
            session_id="ruok-legacy_1",
            source="manual",
            status="pending",
            module_name="legacy",
        )
        (sessions_dir / "ruok-legacy_1.json").write_text(
            legacy.model_dump_json(), encoding="utf-8"
        )

        ids = {session.session_id for session in load_sessions(tmp_path).sessions}

        assert ids == {created.session_id, "ruok-legacy_1"}
