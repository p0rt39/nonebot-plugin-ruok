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


def test_webui_platform_marker_uses_bound_platform_for_display(
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
    assert "alice（OneBot V11: 10001）" in response.text
    assert "上报者: webui: 10001" not in response.text


def test_session_detail_renders_automatic_traceback_as_code_block(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    session = create_session(
        tmp_path,
        "ruok",
        "**异常类型**: RuntimeError\n\n```\nTraceback line 1\nRuntimeError: boom\n```",
        ReporterInfo(type="automatic"),
        source="automatic",
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get(f"/ruok/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "异常摘要" in response.text
    assert "Traceback" in response.text
    assert '<pre class="session-traceback"><code>Traceback line 1' in response.text
    assert "RuntimeError: boom" in response.text


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
