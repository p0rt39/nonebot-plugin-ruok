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
