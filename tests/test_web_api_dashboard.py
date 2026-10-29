from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from webui_test_utils import (
    _client,
    _login_user,
    _login_admin,
    _webui_config,
)


def test_webui_user_dashboard_hides_admin_surfaces(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "/ruok/sessions" not in response.text
    assert "/ruok/modules" not in response.text
    assert "/ruok/notifications" not in response.text
    assert "/ruok/users" in response.text
    assert "dashboard-trends" not in response.text
    assert "dashboard-system" not in response.text
    assert "/ruok bind" in response.text
    assert "我的上报" in response.text


def test_webui_unbound_user_dashboard_does_not_refresh_auth_key(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    registered = WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    first = client.get("/ruok")
    second = client.get("/ruok")
    stored = WebUIAuth(config, tmp_path).get_user("alice")

    assert first.status_code == 200
    assert second.status_code == 200
    assert registered.auth_key in first.text
    assert registered.auth_key in second.text
    assert stored is not None
    assert stored.auth_key_value == registered.auth_key


def test_webui_unbound_user_dashboard_shows_expired_auth_key(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    users_path = tmp_path / "webui_users.json"
    users = json.loads(users_path.read_text("utf-8"))
    users["users"][0]["auth_key_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    users_path.write_text(json.dumps(users), encoding="utf-8")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "当前绑定码已过期" in response.text
    assert "前往用户页" in response.text


def test_webui_user_cannot_access_admin_routes(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    assert client.get("/ruok/modules").status_code == 403
    assert client.get("/ruok/notifications").status_code == 403
    assert client.get("/ruok/sessions").status_code == 403
    assert client.get("/ruok/_partials/dashboard-trends-data").status_code == 403
    assert client.get("/ruok/users").status_code == 200
    assert (
        client.post(
            "/ruok/_actions/module-upsert",
            data={"name": "demo"},
        ).status_code
        == 403
    )


def test_webui_user_must_bind_before_manual_report(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.webui.auth import WebUIAuth

    config = _webui_config()
    WebUIAuth(config, tmp_path).register_user("alice", "secret")
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "music", "description": "boom"},
    )

    assert response.status_code == 403
    assert "绑定平台账号" in response.text


def test_webui_bound_user_report_uses_bound_user_id_and_sees_own_sessions(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.collectors.sessions import list_sessions, create_session

    config = _webui_config()
    auth = WebUIAuth(config, tmp_path)
    result = auth.register_user("alice", "secret")
    auth.bind_auth_key(result.auth_key, "10001", "OneBot V11")
    other = auth.register_user("bob", "secret")
    auth.bind_auth_key(other.auth_key, "10001", "Console")
    create_session(
        tmp_path,
        "music",
        "other user issue",
        ReporterInfo(type="user", user_id="10001", platform="Console"),
    )
    for platform in (None, "webui"):
        create_session(
            tmp_path,
            "music",
            "legacy issue",
            ReporterInfo(type="user", user_id="10001", platform=platform),
        )
    create_session(
        tmp_path,
        "music",
        "own chat issue",
        ReporterInfo(type="user", user_id="10001", platform="OneBot V11"),
    )
    client = _client(config, tmp_path)
    _login_user(client, "alice", "secret")

    response = client.post(
        "/ruok/_actions/session-create",
        data={"module_name": "music", "description": "mine"},
    )

    sessions = list_sessions(tmp_path)
    mine = [session for session in sessions if session.description == "mine"]
    assert response.status_code == 200
    assert len(mine) == 1
    assert mine[0].reporter.user_id == "10001"
    assert mine[0].reporter.platform == "OneBot V11"
    assert "mine" in response.text
    assert "alice（OneBot V11: 10001）" in response.text
    dashboard = client.get("/ruok")
    assert dashboard.status_code == 200
    for page in (response, dashboard):
        assert "own chat issue" in page.text
        assert "mine" in page.text
        assert "other user issue" not in page.text
        assert "legacy issue" not in page.text

    _login_user(client, "bob", "secret")
    other_dashboard = client.get("/ruok")
    assert "other user issue" in other_dashboard.text
    assert "own chat issue" not in other_dashboard.text
    assert "mine" not in other_dashboard.text


def test_dashboard_trends_partial_uses_webui_metrics_context(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import MetricPoint
    from nonebot_plugin_ruok.collector import MetricsStore

    MetricsStore.append(
        tmp_path,
        MetricPoint(
            ts=datetime.now(timezone.utc).isoformat(),
            cpu_percent=12.5,
            memory_percent=34.5,
        ),
    )
    client = _client(_webui_config(api_key="secret"), tmp_path)
    _login_admin(client)

    response = client.get("/ruok?_partial=dashboard-trends")

    assert response.status_code == 200
    assert "fetch('/ruok/api/metrics/history?hours=1')" not in response.text
    # The range is chosen at runtime, so the URL is built from a base path.
    assert "'/ruok/_partials/dashboard-trends-data'" in response.text
    assert "'?hours=' + encodeURIComponent(requestHours)" in response.text
    assert "var initialMetrics =" in response.text
    assert '"cpu_percent": 12.5' in response.text


def test_dashboard_trends_expose_a_selectable_time_range(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert 'data-trend-hours="1"' in response.text
    assert 'data-trend-hours="168"' in response.text
    assert 'role="group"' in response.text
    assert 'aria-label="时间尺度"' in response.text
    assert 'aria-pressed="true"' in response.text
    assert "[data-trend-hours]" in response.text
    # History must survive live SSE samples instead of being trimmed away.
    assert "MAX_LIVE_POINTS" in response.text
    # Loading, empty and failure states are surfaced to the user.
    assert "暂无历史数据" in response.text
    assert "无法加载趋势数据" in response.text
    assert 'data-trend-error role="alert"' in response.text
    assert 'data-trend-status role="status"' in response.text


def test_dashboard_trends_honor_a_requested_range(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import MetricPoint
    from nonebot_plugin_ruok.collector import MetricsStore

    now = datetime.now(timezone.utc)
    MetricsStore.append(
        tmp_path,
        MetricPoint(ts=(now - timedelta(hours=5)).isoformat(), cpu_percent=11.0),
    )
    MetricsStore.append(
        tmp_path,
        MetricPoint(ts=now.isoformat(), cpu_percent=88.0),
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    short = client.get("/ruok?_partial=dashboard-trends&trend_hours=1")
    long = client.get("/ruok?_partial=dashboard-trends&trend_hours=6")

    assert short.status_code == 200
    assert long.status_code == 200
    # The rendered range seeds the initial inline series and the selected button.
    assert "var initialHours = 1;" in short.text
    assert "var initialHours = 6;" in long.text
    assert '"cpu_percent": 88.0' in short.text
    assert '"cpu_percent": 11.0' not in short.text
    assert '"cpu_percent": 11.0' in long.text


def test_dashboard_trends_reject_out_of_range_hours(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    assert (
        client.get("/ruok/_partials/dashboard-trends-data?hours=0").status_code == 422
    )
    assert (
        client.get("/ruok/_partials/dashboard-trends-data?hours=169").status_code == 422
    )
    assert (
        client.get("/ruok?_partial=dashboard-trends&trend_hours=0").status_code == 422
    )


def test_trend_history_is_downsampled_for_long_ranges(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    points = [
        {
            "ts": (now - timedelta(seconds=index * 10)).isoformat(),
            "cpu_percent": float(index % 100),
            "memory_percent": 50.0,
            "sessions_unsolved": 3,
        }
        for index in range(4000)
    ]
    today_file = metrics_dir / f"{now.date().isoformat()}.json"
    today_file.write_text(json.dumps(points), encoding="utf-8")

    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    hourly = client.get("/ruok/_partials/dashboard-trends-data?hours=1")
    weekly = client.get("/ruok/_partials/dashboard-trends-data?hours=168")

    assert hourly.status_code == 200
    assert weekly.status_code == 200
    # A day-plus range must stay chartable rather than returning every sample.
    assert 0 < len(weekly.json()) <= 360
    assert len(hourly.json()) > len(weekly.json())
    assert len({row["ts"] for row in weekly.json()}) == len(weekly.json())
    # Counter fields carry forward instead of being averaged.
    assert all(row["sessions_unsolved"] == 3 for row in weekly.json())


def test_dashboard_trends_data_uses_webui_auth_not_api_key(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import MetricPoint
    from nonebot_plugin_ruok.collector import MetricsStore

    MetricsStore.append(
        tmp_path,
        MetricPoint(
            ts=datetime.now(timezone.utc).isoformat(),
            cpu_percent=22.5,
            memory_percent=44.5,
        ),
    )
    client = _client(_webui_config(api_key="secret"), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/_partials/dashboard-trends-data?hours=1")

    assert response.status_code == 200
    assert response.json()[0]["cpu_percent"] == 22.5


def test_dashboard_trends_do_not_replace_canvas_with_htmx(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "cdn.jsdelivr.net" not in response.text
    assert "unpkg.com" not in response.text
    assert "/ruok/static/vendor/chartjs/chart.umd.min.js" in response.text
    assert '<div id="dashboard-trends">' in response.text
    assert "ruok:metrics" in response.text


def test_dashboard_sse_host_metadata_uses_text_content_rendering(
    tmp_path: Path,
) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "diskPerList.innerHTML" not in response.text
    assert "procPanel.innerHTML" not in response.text
    assert "diskRow.textContent" in response.text
    assert "processRow.textContent" in response.text
    assert "document.createElement('div')" in response.text


def test_dashboard_hero_reports_connection_context_and_reasons(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert 'id="live-status"' in response.text
    assert 'data-state="connecting"' in response.text
    assert 'role="status"' in response.text
    assert 'aria-live="polite"' in response.text
    # The hero states how many connections are online and when it collected.
    assert "连接 0/0 在线" in response.text
    assert "未报告异常原因" in response.text or "status-bar__reasons" in response.text


def test_dashboard_marks_pollable_panels_for_refresh_feedback(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    for panel in ("dashboard-disk", "dashboard-connections", "dashboard-stats"):
        assert f'id="{panel}" class="panel"' in response.text
    assert "htmx:beforeRequest" in response.text
    assert "htmx:afterRequest" in response.text
    assert "panel-note--error" in response.text


def test_dashboard_pauses_stream_when_tab_hidden(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert "visibilitychange" in response.text
    assert "标签页不活跃，实时更新已暂停" in response.text
    assert "ruokTrendRefreshNow" in response.text


def test_dashboard_stats_partial_destroys_replaced_chart(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok?_partial=dashboard-stats")

    assert response.status_code == 200
    # The partial re-renders on every Session change, so it must release the
    # previous Chart.js instance bound to the replaced canvas.
    assert "window.ruokStatsChart.canvas !== ctx" in response.text
    assert ".destroy()" in response.text


def test_theme_prefers_system_scheme_without_a_stored_choice(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    # The pre-paint script must consult the OS setting and keep the
    # color-scheme meta in sync with the resolved theme.
    assert "prefers-color-scheme: dark" in response.text
    assert 'name="color-scheme"' in response.text
    assert ".setAttribute('data-theme', t)" in response.text
    # An explicit choice still wins and is persisted.
    assert "localStorage.setItem('ruok-theme', t)" in response.text


def test_theme_toggle_reports_pressed_state(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    assert 'id="theme-toggle"' in response.text
    assert "aria-pressed" in response.text


def test_metric_tiles_are_keyboard_operable(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok")

    assert response.status_code == 200
    # Tiles expose button semantics and announce their expanded state.
    assert "data-expandable" in response.text
    assert 'role="button"' in response.text
    assert 'aria-expanded="false"' in response.text
    # The inline onclick handler is replaced by the shared keyboard handler.
    assert "onclick=\"this.classList.toggle('expanded')\"" not in response.text
    assert 'aria-label="内存详情"' in response.text
    assert "toggleTile" in response.text
    # role="button" inherits Pico's filled button styling, so the card surface
    # and text colour must be restored explicitly.
    assert '.sys-tile[role="button"]' in response.text
    assert "--ruok-text:" in response.text


def test_decorative_status_dots_are_hidden_from_assistive_tech(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collector import create_session

    config = _webui_config()
    create_session(
        tmp_path,
        module_name="ruok",
        description="a11y check",
        reporter=ReporterInfo(type="user", user_id="1", platform="webui"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    dashboard = client.get("/ruok")
    users = client.get("/ruok/users")

    assert dashboard.status_code == 200
    assert '<span class="status-dot status-unavailable" aria-hidden="true">' in (
        dashboard.text
    )
    # Screen-reader-only text carries the state that the dot only colours.
    assert "visually-hidden" in dashboard.text
    assert users.status_code == 200
