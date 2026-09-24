from __future__ import annotations

from pathlib import Path

from webui_test_utils import (
    _client,
    _login_admin,
    _webui_config,
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
    assert '<pre class="session-traceback"><code>Traceback line 1' in response.text
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


def test_session_summary_strips_markup_and_truncates() -> None:
    from nonebot_plugin_ruok.webui.jinja import session_summary

    assert session_summary("  用户 **说明** 第一行\n第二行") == "用户 说明 第一行"
    assert session_summary("") == ""
    assert session_summary("```\n日志来源: x\n```") == ""
    long_text = session_summary("x" * 500)
    assert len(long_text) == 160
    assert long_text.endswith("…")
