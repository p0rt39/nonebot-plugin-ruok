from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(config, data_dir: Path) -> TestClient:
    from starlette.middleware.sessions import SessionMiddleware

    from nonebot_plugin_ruok.api import create_ruok_router
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.webui.router import create_webui_router

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(WebUIAuth(config.webui_password).create_router())
    app.include_router(create_ruok_router(config, data_dir))
    app.include_router(create_webui_router(config, data_dir))
    return TestClient(app)


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    assert client.get("/ruok/api/sessions").status_code == 401
    header_response = client.get(
        "/ruok/api/sessions",
        headers={"X-RuOK-API-Key": "secret"},
    )
    bearer_response = client.get(
        "/ruok/api/sessions",
        headers={"Authorization": "Bearer secret"},
    )
    assert header_response.status_code == 200
    assert bearer_response.status_code == 200


def test_sessions_stats_route_is_not_shadowed(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)
    response = client.get("/ruok/api/sessions/stats")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_webui_plaintext_password_login(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(webui_password="mysecret"), tmp_path)

    response = client.post(
        "/ruok/login",
        data={"password": "mysecret"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def test_webui_protected_action_requires_login(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(webui_password="mysecret"), tmp_path)
    response = client.post(
        "/ruok/_actions/module-upsert",
        data={"name": "demo"},
    )

    assert response.status_code == 401


def test_dashboard_trends_partial_uses_webui_metrics_context(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig
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
    client = _client(ScopedConfig(api_key="secret"), tmp_path)

    response = client.get("/ruok?_partial=dashboard-trends")

    assert response.status_code == 200
    assert "fetch('/ruok/api/metrics/history?hours=1')" not in response.text
    assert "var metrics =" in response.text
    assert '"cpu_percent": 12.5' in response.text


def test_dashboard_trends_polling_preserves_container(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.config import ScopedConfig

    client = _client(ScopedConfig(), tmp_path)

    response = client.get("/ruok")

    assert response.status_code == 200
    assert 'id="dashboard-trends"' in response.text
    assert 'hx-swap="innerHTML"' in response.text
