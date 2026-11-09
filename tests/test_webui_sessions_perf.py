"""Regression tests for the Session read paths.

Opening ``/ruok/sessions`` with many stored Sessions used to re-read the whole
``sessions/`` directory once per module and once per rendered row, which blocked
the event loop.  These tests pin the observable contract: one request validates
each Session file once, read paths do not rewrite derived caches, and the
blocking work runs outside the event loop.
"""

from __future__ import annotations

import json
import time
import asyncio
from pathlib import Path

import httpx
from webui_test_utils import (
    ADMIN_PASSWORD,
    _app,
    _client,
    _login_admin,
    _webui_config,
    _count_session_validations,
)

ModuleNames = list[str]


def _seed_sessions(
    data_dir: Path,
    count: int,
    modules: ModuleNames,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import create_session

    for index, module_name in enumerate(modules):
        upsert_module(
            data_dir,
            ModuleDefinition(name=module_name, plugins=[f"plugin-{index}"]),
        )
    for index in range(count):
        create_session(
            data_dir,
            modules[index % len(modules)],
            f"issue {index}",
            ReporterInfo(type="user", user_id=f"u{index}"),
        )


def test_sessions_page_reads_each_session_file_once(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_sessions(tmp_path, 12, ["music", "news", "tools"])
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert calls["count"] == 12


def test_sessions_partial_reads_each_session_file_once(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_sessions(tmp_path, 9, ["music", "news"])
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok/_partials/sessions")

    assert response.status_code == 200
    assert calls["count"] == 9


def test_sessions_page_with_plugin_filter_stays_single_pass(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_sessions(tmp_path, 10, ["music", "news"])
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok/sessions", params={"plugin": "plugin-0"})

    assert response.status_code == 200
    assert calls["count"] == 10


def test_module_detail_reads_each_session_file_once(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_sessions(tmp_path, 8, ["music"])
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok/modules/music")

    assert response.status_code == 200
    assert calls["count"] == 8


def test_dashboard_reads_each_session_file_once(tmp_path: Path, monkeypatch) -> None:
    _seed_sessions(tmp_path, 6, ["music", "news"])
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    calls = _count_session_validations(monkeypatch)
    response = client.get("/ruok")

    assert response.status_code == 200
    assert calls["count"] == 6


def test_sessions_page_does_not_rewrite_plugin_impacts(tmp_path: Path) -> None:
    _seed_sessions(tmp_path, 4, ["music"])
    impacts_path = tmp_path / "plugin_impacts.json"
    sentinel = json.dumps({"sentinel": {"pending": [], "unsolved": []}})
    impacts_path.write_text(sentinel, encoding="utf-8")
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert impacts_path.read_text(encoding="utf-8") == sentinel


def test_sessions_page_plugin_options_match_module_definitions(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo, ModuleDefinition
    from nonebot_plugin_ruok.collectors.modules import upsert_module
    from nonebot_plugin_ruok.collectors.sessions import create_session

    upsert_module(
        tmp_path,
        ModuleDefinition(name="music", plugins=["plugin-music", "plugin-shared"]),
    )
    create_session(
        tmp_path,
        "music",
        "player crashed",
        ReporterInfo(type="user", user_id="u1"),
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert 'name="affected_plugins"' in response.text
    assert 'value="plugin-music"' in response.text
    assert 'value="plugin-shared"' in response.text


def test_sessions_page_renders_without_modules_file(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collectors.sessions import create_session

    create_session(
        tmp_path,
        "unconfigured-module",
        "no module definition",
        ReporterInfo(type="user", user_id="u1"),
    )
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    assert "unconfigured-module" in response.text


async def test_sessions_page_does_not_block_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    """A slow directory read must not stop the loop from running other tasks."""
    from nonebot_plugin_ruok.webui import router as router_module

    _seed_sessions(tmp_path, 3, ["music"])
    app = _app(_webui_config(), tmp_path)
    transport = httpx.ASGITransport(app=app)

    original_load_sessions = router_module.load_sessions

    def slow_load_sessions(data_dir: Path):
        time.sleep(0.2)
        return original_load_sessions(data_dir)

    monkeypatch.setattr(router_module, "load_sessions", slow_load_sessions)

    ticks = 0
    running = True

    async def probe() -> None:
        nonlocal ticks
        while running:
            await asyncio.sleep(0.01)
            ticks += 1

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/ruok/login",
            data={"username": "admin", "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code == 302

        probe_task = asyncio.create_task(probe())
        try:
            response = await client.get("/ruok/sessions")
        finally:
            running = False
            await probe_task

    assert response.status_code == 200
    # Without the thread offload the probe cannot run during the 200 ms read.
    assert ticks >= 5
