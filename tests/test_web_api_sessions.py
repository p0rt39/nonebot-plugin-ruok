from __future__ import annotations

from pathlib import Path

from webui_test_utils import (
    _client,
    _login_admin,
    _webui_config,
    _count_session_validations,
)


def test_session_detail_renders_manual_description_separately(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    session = create_session(
        tmp_path,
        "music",
        "用户描述第一行\n用户描述第二行",
        ReporterInfo(type="user", user_id="u1"),
        source="manual",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "用户提交说明" in response.text
    assert 'class="session-description-block"' in response.text
    assert "用户描述第一行" in response.text
    assert "<strong>描述</strong>" not in response.text


def test_webui_session_buttons_and_status_labels_are_consistent(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(
        tmp_path,
        "music",
        "pending issue",
        ReporterInfo(type="user", user_id="u1"),
        source="manual",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "待确认" in response.text
    assert "未解决" in response.text
    assert "Pending" not in response.text
    assert "Unsolved" not in response.text
    assert 'class="ruok-button-row--grid"' in response.text
    assert 'class="secondary outline ruok-button-danger"' in response.text


def test_webui_sessions_display_bound_username_and_platform_id(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    session = create_session(
        tmp_path,
        "music",
        "bound user issue",
        ReporterInfo(type="user", user_id="10001", platform="OneBot V11"),
        source="manual",
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    list_response = client.get("/ruok/sessions")
    detail_response = client.get(f"/ruok/sessions/{session.session_id}")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert "alice（OneBot V11: 10001）" in list_response.text
    assert "alice（OneBot V11: 10001）" in detail_response.text
    assert "<td>OneBot V11</td>" in detail_response.text


def test_webui_sessions_fallback_reporter_display_includes_platform(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(
        tmp_path,
        "music",
        "unbound user issue",
        ReporterInfo(type="user", user_id="10001", platform="OneBot V11"),
        source="manual",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "上报者: OneBot V11: 10001" in response.text


def test_unscoped_webui_platform_marker_is_not_mapped_to_a_binding(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    create_session(
        tmp_path,
        "music",
        "webui user issue",
        ReporterInfo(type="user", user_id="10001", platform="webui"),
        source="manual",
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "上报者: webui: 10001" in response.text
    assert "alice（OneBot V11: 10001）" not in response.text


def test_session_detail_renders_automatic_traceback_as_code_block(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    session = create_session(
        tmp_path,
        "ruok",
        "**上下文**: manual /ruok raise\n"
        "**异常类型**: RuntimeError\n"
        "**异常信息**: boom\n\n"
        "```\nTraceback line 1\nRuntimeError: boom\n```",
        ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "异常摘要" in response.text
    assert 'class="session-summary-grid"' in response.text
    assert "<span>上下文</span>" in response.text
    assert "<strong>manual /ruok raise</strong>" in response.text
    assert "<span>异常类型</span>" in response.text
    assert "<strong>RuntimeError</strong>" in response.text
    assert "<span>异常信息</span>" in response.text
    assert "<strong>boom</strong>" in response.text
    assert "Traceback" in response.text
    # The traceback renders inside a fold whose id the copy control targets.
    assert 'id="session-traceback-text"' in response.text
    assert 'data-copy-target="session-traceback-text"' in response.text
    assert ">Traceback line 1" in response.text
    assert "RuntimeError: boom" in response.text
    assert '<pre class="session-description-block">' not in response.text


def test_webui_confirm_form_supports_multiple_affected_plugins(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = _webui_config()
    upsert_module(
        tmp_path,
        ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
    )
    session = create_session(
        tmp_path,
        "music",
        "plugin issue",
        ReporterInfo(type="user"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    page_response = client.get("/ruok/sessions")
    action_response = client.post(
        f"/ruok/_actions/confirm/{session.session_id}",
        data={"affected_plugins": ["plugin_a", "plugin_b"]},
    )
    reloaded = get_session(tmp_path, session.session_id)

    assert page_response.status_code == 200
    assert 'name="affected_plugins" value="plugin_a"' in page_response.text
    assert 'name="affected_plugins" value="plugin_b"' in page_response.text
    assert action_response.status_code == 200
    assert reloaded is not None
    assert reloaded.status == "unsolved"
    assert reloaded.affected_plugins == ["plugin_a", "plugin_b"]
    assert "影响插件: plugin_a, plugin_b" in action_response.text


def test_webui_confirm_without_plugins_does_not_propagate(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import get_module, upsert_module
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = _webui_config()
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
    session = create_session(
        tmp_path,
        "music",
        "local issue",
        ReporterInfo(type="user"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(f"/ruok/_actions/confirm/{session.session_id}", data={})
    reloaded = get_session(tmp_path, session.session_id)
    lyrics = get_module(tmp_path, config, "lyrics")

    assert response.status_code == 200
    assert reloaded is not None
    assert reloaded.status == "unsolved"
    assert reloaded.affected_plugins == []
    assert lyrics is not None
    assert lyrics.status == "available"


def test_webui_unsolved_session_can_update_affected_plugins(
    tmp_path: Path,
) -> None:
    import re

    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import get_module, upsert_module
    from nonebot_plugin_ruok.collectors.sessions import (
        get_session,
        create_session,
        confirm_session_plugins,
    )

    config = _webui_config()
    upsert_module(
        tmp_path,
        ModuleDefinition(name="music", plugins=["plugin_a", "plugin_b"]),
    )
    upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
    upsert_module(tmp_path, ModuleDefinition(name="playlist", plugins=["plugin_b"]))
    session = create_session(
        tmp_path,
        "music",
        "plugin impact changed",
        ReporterInfo(type="user"),
    )
    confirm_session_plugins(tmp_path, config, session.session_id, ["plugin_a"])
    client = _client(config, tmp_path)
    _login_admin(client)

    page_response = client.get("/ruok/sessions")
    action_response = client.post(
        f"/ruok/_actions/confirm/{session.session_id}",
        data={"affected_plugins": ["plugin_b"]},
    )
    reloaded = get_session(tmp_path, session.session_id)
    lyrics = get_module(tmp_path, config, "lyrics")
    playlist = get_module(tmp_path, config, "playlist")

    assert page_response.status_code == 200
    assert "🧩 修改插件" in page_response.text
    assert re.search(
        r'name="affected_plugins" value="plugin_a"\s+checked',
        page_response.text,
    )
    assert action_response.status_code == 200
    assert "影响插件: plugin_b" in action_response.text
    assert reloaded is not None
    assert reloaded.affected_plugins == ["plugin_b"]
    assert lyrics is not None
    assert lyrics.status == "available"
    assert playlist is not None
    assert playlist.status == "unavailable"


def test_module_detail_includes_plugin_propagated_sessions(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = _webui_config()
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    upsert_module(tmp_path, ModuleDefinition(name="lyrics", plugins=["plugin_a"]))
    create_session(
        tmp_path,
        "music",
        "propagated pending",
        ReporterInfo(type="user"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules/lyrics")

    assert response.status_code == 200
    assert "propagated pending" in response.text


def test_api_patch_session_accepts_affected_plugins(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = _webui_config(api_key="secret")
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    session = create_session(tmp_path, "music", "api issue", ReporterInfo(type="user"))
    client = _client(config, tmp_path)

    response = client.patch(
        f"/ruok/api/sessions/{session.session_id}",
        headers={"X-RUOK-API-Key": "secret"},
        json={"status": "unsolved", "affected_plugins": ["plugin_a"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unsolved"
    assert response.json()["affected_plugins"] == ["plugin_a"]


def test_api_patch_session_rejects_invalid_affected_plugin(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import create_session

    config = _webui_config(api_key="secret")
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    session = create_session(tmp_path, "music", "api issue", ReporterInfo(type="user"))
    client = _client(config, tmp_path)

    response = client.patch(
        f"/ruok/api/sessions/{session.session_id}",
        headers={"X-RUOK-API-Key": "secret"},
        json={"status": "unsolved", "affected_plugins": ["plugin_b"]},
    )

    assert response.status_code == 400
    assert "插件不属于该 Session 原模块" in response.text


def test_api_patch_session_rejects_invalid_status_without_corrupting_session(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = _webui_config(api_key="secret")
    session = create_session(tmp_path, "music", "api issue", ReporterInfo(type="user"))
    client = _client(config, tmp_path)

    response = client.patch(
        f"/ruok/api/sessions/{session.session_id}",
        headers={"X-RUOK-API-Key": "secret"},
        json={"status": "invalid"},
    )
    reloaded = get_session(tmp_path, session.session_id)

    assert response.status_code == 400
    assert reloaded is not None
    assert reloaded.status == "pending"


def test_api_patch_session_rejects_affected_plugins_without_confirm_status(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import get_session, create_session

    config = _webui_config(api_key="secret")
    upsert_module(tmp_path, ModuleDefinition(name="music", plugins=["plugin_a"]))
    session = create_session(tmp_path, "music", "api issue", ReporterInfo(type="user"))
    client = _client(config, tmp_path)

    response = client.patch(
        f"/ruok/api/sessions/{session.session_id}",
        headers={"X-RUOK-API-Key": "secret"},
        json={"developer_notes": "note", "affected_plugins": ["plugin_a"]},
    )
    reloaded = get_session(tmp_path, session.session_id)

    assert response.status_code == 400
    assert "affected_plugins can only be set when confirming a session" in response.text
    assert reloaded is not None
    assert reloaded.status == "pending"
    assert reloaded.affected_plugins == []


def _create_many_sessions(data_dir: Path, count: int) -> list[str]:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    return [
        create_session(
            data_dir, "music", f"issue {index}", ReporterInfo(type="user")
        ).session_id
        for index in range(count)
    ]


def test_api_sessions_reads_each_session_file_once(tmp_path: Path, monkeypatch) -> None:
    _create_many_sessions(tmp_path, 6)
    client = _client(_webui_config(api_key="secret"), tmp_path)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok/api/sessions", headers={"x-ruok-api-key": "secret"})

    assert response.status_code == 200
    assert len(response.json()) == 6
    assert calls["count"] == 6


def test_api_session_stats_reads_each_session_file_once(
    tmp_path: Path, monkeypatch
) -> None:
    _create_many_sessions(tmp_path, 4)
    client = _client(_webui_config(api_key="secret"), tmp_path)

    calls = _count_session_validations(monkeypatch)
    response = client.get(
        "/ruok/api/sessions/stats", headers={"x-ruok-api-key": "secret"}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 4
    assert calls["count"] == 4


def test_webui_sessions_list_paginates_and_pushes_url(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 25)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    first = client.get("/ruok/sessions")
    second = client.get("/ruok/_partials/sessions", params={"page": 2})

    assert first.status_code == 200
    assert first.text.count('class="session-row"') == 20
    assert "第 1–20 条 / 共 25 条" in first.text
    assert 'hx-get="/ruok/_partials/sessions?page=2"' in first.text
    assert second.status_code == 200
    assert second.text.count('class="session-row"') == 5
    assert "第 21–25 条 / 共 25 条" in second.text
    assert second.headers["HX-Push-Url"] == "/ruok/sessions?page=2"
    assert "<!DOCTYPE html>" not in second.text


def test_webui_sessions_pagination_clamps_input(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 25)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    past_end = client.get("/ruok/_partials/sessions", params={"page": 99})
    before_start = client.get("/ruok/_partials/sessions", params={"page": 0})
    tiny = client.get("/ruok/_partials/sessions", params={"page_size": 1})
    huge = client.get("/ruok/_partials/sessions", params={"page_size": 1000})

    assert "第 21–25 条 / 共 25 条" in past_end.text
    assert past_end.headers["HX-Push-Url"] == "/ruok/sessions?page=2"
    assert "第 1–20 条 / 共 25 条" in before_start.text
    assert before_start.headers["HX-Push-Url"] == "/ruok/sessions"
    assert tiny.text.count('class="session-row"') == 10
    assert tiny.headers["HX-Push-Url"] == "/ruok/sessions?page_size=10"
    assert huge.text.count('class="session-row"') == 25
    assert "Session 分页" not in huge.text


def test_webui_sessions_pagination_preserves_filters(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import update_session

    session_ids = _create_many_sessions(tmp_path, 25)
    for session_id in session_ids[:3]:
        update_session(tmp_path, session_id, {"status": "solved"})
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/_partials/sessions", params={"status": "pending", "search": "issue"}
    )

    assert response.status_code == 200
    assert "第 1–20 条 / 共 22 条" in response.text
    assert "status=pending&amp;search=issue&amp;page=2" in response.text
    assert response.headers["HX-Push-Url"] == (
        "/ruok/sessions?status=pending&search=issue"
    )
    assert 'data-status-filter="pending"\n' in response.text
    assert 'class="status-chip is-active"' in response.text


def test_webui_sessions_list_shows_markup_free_summaries(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(
        tmp_path,
        "ruok",
        "**上下文**: action_solve\n"
        "**异常类型**: RuntimeError\n"
        "**异常信息**: boom\n\n"
        "```\nTraceback line 1\n```",
        ReporterInfo(type="automatic"),
        source="automatic",
    )
    create_session(
        tmp_path,
        "music",
        "```\n日志来源: nonebot_plugin_music\n模块映射: music\n"
        "Player crashed\nTraceback line\n```",
        ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "RuntimeError: boom" in response.text
    assert "Player crashed" in response.text
    assert "**异常类型**" not in response.text
    assert "日志来源" not in response.text
    assert "```" not in response.text


def test_webui_session_badges_use_status_tones(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import update_session

    unsolved_id, ignored_id = _create_many_sessions(tmp_path, 2)
    update_session(tmp_path, unsolved_id, {"status": "unsolved"})
    update_session(tmp_path, ignored_id, {"status": "ignored"})
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert '<span class="badge badge-err">未解决</span>' in response.text
    assert '<span class="badge badge-muted">已忽略</span>' in response.text
    assert 'class="badge badge-error"' not in response.text


def test_webui_ignore_from_list_requires_confirmation(tmp_path: Path) -> None:
    (session_id,) = _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert f'hx-post="/ruok/_actions/ignore/{session_id}"' in response.text
    assert f"确定将 {session_id} 标记为误报并忽略？" in response.text


def test_webui_detail_actions_return_detail_fragment(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import get_session

    confirm_id, solve_id, ignore_id = _create_many_sessions(tmp_path, 3)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    page = client.get(f"/ruok/sessions/{solve_id}")
    responses = {
        "unsolved": client.post(f"/ruok/_actions/confirm/{confirm_id}?view=detail"),
        "solved": client.post(f"/ruok/_actions/solve/{solve_id}?view=detail"),
        "ignored": client.post(f"/ruok/_actions/ignore/{ignore_id}?view=detail"),
    }

    assert page.status_code == 200
    assert f"/ruok/_actions/solve/{solve_id}?view=detail" in page.text
    assert "hx-on::after-request" not in page.text
    for status, response in responses.items():
        assert response.status_code == 200
        assert response.text.count('id="session-detail-content"') == 1
        assert "时间线" in response.text
        assert 'id="notes-area"' in response.text
        assert 'id="linked-container"' in response.text
        assert 'class="session-row"' not in response.text
        assert "<!DOCTYPE html>" not in response.text
        assert response.headers["HX-Trigger"]
    for session_id, status in (
        (confirm_id, "unsolved"),
        (solve_id, "solved"),
        (ignore_id, "ignored"),
    ):
        reloaded = get_session(tmp_path, session_id)
        assert reloaded is not None
        assert reloaded.status == status
    assert "?view=detail" in responses["unsolved"].text
    assert "?view=detail" not in responses["solved"].text


def test_webui_list_actions_still_return_session_row(tmp_path: Path) -> None:
    (session_id,) = _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(f"/ruok/_actions/solve/{session_id}")

    assert response.status_code == 200
    assert f'id="session-{session_id}" class="session-row"' in response.text
    assert 'id="session-detail-content"' not in response.text
    assert '<span class="badge badge-ok">已解决</span>' in response.text


def test_bulk_panel_requires_an_explicit_action(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 2)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert 'hx-post="/ruok/_actions/sessions-bulk"' in response.text
    assert 'hx-target="#session-content"' in response.text
    # No action is preselected and the submit button starts disabled.
    assert 'value="ignore" data-bulk-action checked' not in response.text
    assert "data-bulk-submit disabled" in response.text
    assert response.text.count('name="action"') == 3


def test_bulk_panel_reports_the_filtered_status_spread(tmp_path: Path) -> None:
    """The panel describes the filtered set, never the global totals."""
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import (
        create_session,
        update_session,
    )

    _create_many_sessions(tmp_path, 2)
    solved = create_session(tmp_path, "music", "closed", ReporterInfo(type="user"))
    update_session(tmp_path, solved.session_id, {"status": "solved"})
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions", params={"status": "pending"})

    assert response.status_code == 200
    panel = response.text.split('class="bulk-panel__hint"', 1)[1].split("</p>", 1)[0]
    # The filtered-out solved row must not inflate the panel counts.
    assert "2</strong> 条" in panel
    assert "待确认\n            2" in panel
    assert "已解决\n            0" in panel


def test_sessions_list_uses_readable_chip_and_stacked_bulk_labels(
    tmp_path: Path,
) -> None:
    """Chips sit on a button reset, so they need the surface foreground."""
    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "color: var(--ruok-text);" in response.text
    # Pico's inverse button colour made the chip text unreadable on the surface.
    chip_rule = response.text.split(".status-chip {", 1)[1].split("}", 1)[0]
    assert "var(--pico-color)" not in chip_rule
    # Mobile: label and explanation stack so neither wraps mid-word.
    assert ".bulk-actions__label" in response.text
    assert "flex-basis: 100%;" in response.text


def test_bulk_action_ignores_the_whole_filtered_set(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import (
        list_sessions,
        create_session,
        update_session,
    )

    pending_ids = _create_many_sessions(tmp_path, 3)
    solved = create_session(
        tmp_path, "music", "already done", ReporterInfo(type="user")
    )
    update_session(tmp_path, solved.session_id, {"status": "solved"})
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/sessions-bulk",
        data={"action": "ignore", "status": "pending"},
    )

    assert response.status_code == 200
    # The container is the swap target, so the body is its inner content.
    assert 'id="session-content"' not in response.text
    assert "已批量" in response.text
    assert "3 changed" in response.headers["HX-Trigger"]
    statuses = {s.session_id: s.status for s in list_sessions(tmp_path)}
    assert all(statuses[session_id] == "ignored" for session_id in pending_ids)
    assert statuses[solved.session_id] == "solved"


def test_bulk_action_processes_beyond_the_visible_page(tmp_path: Path) -> None:
    """A bulk pass acts on the filter, not on the 20 rows currently rendered."""
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    session_ids = _create_many_sessions(tmp_path, 25)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/sessions-bulk",
        data={"action": "ignore", "page_size": "10"},
    )

    assert response.status_code == 200
    statuses = {s.session_id: s.status for s in list_sessions(tmp_path)}
    assert all(statuses[session_id] == "ignored" for session_id in session_ids)


def test_bulk_action_requires_an_action(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    (session_id,) = _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post("/ruok/_actions/sessions-bulk", data={})

    assert response.status_code == 400
    assert list_sessions(tmp_path)[0].session_id == session_id
    assert list_sessions(tmp_path)[0].status == "pending"


def test_bulk_action_rejects_unknown_action(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post("/ruok/_actions/sessions-bulk", data={"action": "delete"})

    assert response.status_code == 400


def test_bulk_action_with_no_matches_is_not_an_error(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 2)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/sessions-bulk",
        data={"action": "ignore", "search": "no-such-text"},
    )

    assert response.status_code == 200
    assert "没有匹配" in response.text
    assert 'class="bulk-notice"' in response.text


def test_bulk_action_rejects_oversized_batches(tmp_path: Path, monkeypatch) -> None:
    from nonebot_plugin_ruok.webui import router as router_module

    _create_many_sessions(tmp_path, 4)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)
    monkeypatch.setattr(router_module, "BULK_MAX_SESSIONS", 2)

    response = client.post("/ruok/_actions/sessions-bulk", data={"action": "ignore"})

    assert response.status_code == 400
    assert "超过单次上限" in response.text


def test_bulk_action_requires_session_system(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    (session_id,) = _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(session_enabled=False), tmp_path)
    _login_admin(client)

    response = client.post("/ruok/_actions/sessions-bulk", data={"action": "ignore"})

    assert response.status_code == 403
    assert list_sessions(tmp_path)[0].session_id == session_id


def test_bulk_action_requires_authentication(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)

    response = client.post("/ruok/_actions/sessions-bulk", data={"action": "ignore"})

    assert response.status_code == 401
    assert list_sessions(tmp_path)[0].status == "pending"


def test_bulk_action_rejects_non_admin_users(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    _create_many_sessions(tmp_path, 1)
    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    login = client.post(
        "/ruok/login",
        data={"username": "alice", "password": "secret"},
        follow_redirects=False,
    )
    assert login.status_code == 302

    response = client.post("/ruok/_actions/sessions-bulk", data={"action": "ignore"})

    assert response.status_code == 403
    assert list_sessions(tmp_path)[0].status == "pending"


def test_bulk_action_rejects_invalid_datetime_filter(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/sessions-bulk",
        data={"action": "ignore", "after": "not-a-date"},
    )

    assert response.status_code == 400
    assert list_sessions(tmp_path)[0].status == "pending"


def test_stale_action_requires_the_threshold(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.post("/ruok/_actions/sessions-stale", data={})

    assert response.status_code == 400
    assert "STALE_PENDING_AFTER_HOURS" in response.text


def test_stale_panel_and_action_handle_only_stale_rows(tmp_path: Path) -> None:
    from datetime import datetime, timezone, timedelta

    from nonebot_plugin_ruok.collectors.sessions import (
        _load_session,
        _save_session,
        list_sessions,
    )

    stale_id, fresh_id = _create_many_sessions(tmp_path, 2)
    stale = _load_session(tmp_path, stale_id)
    assert stale is not None
    stale.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=48)
    _save_session(tmp_path, stale)

    config = _webui_config(stale_pending_after_hours=24.0)
    client = _client(config, tmp_path)
    _login_admin(client)

    page = client.get("/ruok/sessions")
    assert page.status_code == 200
    assert 'hx-post="/ruok/_actions/sessions-stale"' in page.text
    assert "陈旧待确认 Session（1 条）" in page.text

    response = client.post("/ruok/_actions/sessions-stale", data={})

    assert response.status_code == 200
    assert "已处理 1 个陈旧待确认 Session" in response.text
    statuses = {s.session_id: s.status for s in list_sessions(tmp_path)}
    assert statuses[stale_id] == "ignored"
    assert statuses[fresh_id] == "pending"


def test_stale_panel_hidden_when_threshold_is_zero(tmp_path: Path) -> None:
    _create_many_sessions(tmp_path, 1)
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert 'hx-post="/ruok/_actions/sessions-stale"' not in response.text


def test_session_summary_strips_markup_and_truncates() -> None:
    from nonebot_plugin_ruok.webui.jinja import session_summary

    assert session_summary("  用户 **说明** 第一行\n第二行") == "用户 说明 第一行"
    assert session_summary("") == ""
    assert session_summary("```\n日志来源: x\n```") == ""
    long_text = session_summary("x" * 500)
    assert len(long_text) == 160
    assert long_text.endswith("…")


def test_webui_session_actions_return_404_for_missing_session(tmp_path: Path) -> None:
    """Missing Session is a client error, not a 500 with a self-monitoring Session."""
    from nonebot_plugin_ruok.collectors.sessions import list_sessions

    client = _client(_webui_config(), tmp_path)
    _login_admin(client)
    missing = "ruok-deadbeef"

    responses = {
        "confirm": client.post(f"/ruok/_actions/confirm/{missing}"),
        "solve": client.post(f"/ruok/_actions/solve/{missing}"),
        "ignore": client.post(f"/ruok/_actions/ignore/{missing}"),
        "note": client.post(
            f"/ruok/_actions/session-note/{missing}",
            data={"developer_notes": "note"},
        ),
        "unlink": client.post(f"/ruok/_actions/unlink/{missing}"),
        "unlink-other": client.post(f"/ruok/_actions/link/{missing}/remove/{missing}"),
    }

    for name, response in responses.items():
        assert response.status_code == 404, name
        assert "Session not found" in response.text, name
        assert "已保存" not in response.text, name
        assert "已解除关联" not in response.text, name
    assert list_sessions(tmp_path) == []


def test_webui_session_note_and_unlink_still_succeed_for_existing_session(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.collectors.sessions import get_session, link_sessions

    (session_id,) = _create_many_sessions(tmp_path, 1)
    (other_id,) = _create_many_sessions(tmp_path, 1)
    assert link_sessions(tmp_path, session_id, other_id)

    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    note = client.post(
        f"/ruok/_actions/session-note/{session_id}",
        data={"developer_notes": "kept"},
    )
    unlink = client.post(f"/ruok/_actions/unlink/{session_id}")

    reloaded = get_session(tmp_path, session_id)
    assert note.status_code == 200
    assert "已保存" in note.text
    assert unlink.status_code == 200
    assert "已解除关联" in unlink.text
    assert reloaded is not None
    assert reloaded.developer_notes == "kept"
    assert reloaded.link_group is None


def test_session_detail_folds_long_traceback_with_copy_control(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collector import create_session

    config = _webui_config()
    traceback_text = "\n".join(f"line {i}" for i in range(80))
    session = create_session(
        tmp_path,
        module_name="ruok",
        description="**异常类型**: RuntimeError\n\n```\n" + traceback_text + "\n```\n",
        reporter=ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert 'class="detail-fold" open' in response.text
    assert 'data-copy-target="session-traceback-text"' in response.text
    assert 'id="session-traceback-text"' in response.text
    assert "共 80 行" in response.text
    assert "复制" in response.text
    # The fold must not duplicate Pico's disclosure marker.
    assert "summary::before" not in response.text


def test_session_detail_keeps_short_description_inline(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collector import create_session

    config = _webui_config()
    session = create_session(
        tmp_path,
        module_name="ruok",
        description="短说明",
        reporter=ReporterInfo(type="user", user_id="1", platform="webui"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    # A short description stays visible rather than hiding behind a fold.
    assert '<pre class="session-description-block">短说明</pre>' in response.text


def test_detail_actions_still_return_the_detail_fragment(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collector import create_session

    config = _webui_config()
    session = create_session(
        tmp_path,
        module_name="ruok",
        description="**异常类型**: RuntimeError\n\n```\nboom\n```\n",
        reporter=ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    page = client.get(f"/ruok/sessions/{session.session_id}")
    response = client.post(
        f"/ruok/_actions/solve/{session.session_id}?view=detail",
    )

    assert page.status_code == 200
    assert response.status_code == 200
    assert response.text.count('id="session-detail-content"') == 1
    assert 'class="timeline"' in response.text
    assert 'id="notes-area"' in response.text
    assert 'id="linked-container"' in response.text


def test_session_detail_page_returns_404_for_unknown_session(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions/ruok-deadbeef")

    assert response.status_code == 404
    assert "Session not found" in response.text
