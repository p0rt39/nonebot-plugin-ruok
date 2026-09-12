from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "admin-secret"


def _client(
    config,
    data_dir: Path,
    *,
    superusers: Iterable[str] = (),
) -> TestClient:
    from starlette.middleware.sessions import SessionMiddleware

    from nonebot_plugin_ruok.api import create_ruok_router
    from nonebot_plugin_ruok.webui.auth import WebUIAuth
    from nonebot_plugin_ruok.webui.router import create_webui_router

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret",
        max_age=config.webui_session_ttl,
        same_site="lax",
    )
    auth = WebUIAuth(config, data_dir, superuser_provider=lambda: superusers)
    app.include_router(auth.create_router())
    app.include_router(create_ruok_router(config, data_dir))
    app.include_router(create_webui_router(config, data_dir, auth))
    return TestClient(app)


def _webui_config(**kwargs):
    from nonebot_plugin_ruok.config import ScopedConfig

    return ScopedConfig(webui_admin_password=ADMIN_PASSWORD, **kwargs)


def _login_admin(client: TestClient, password: str = ADMIN_PASSWORD) -> None:
    response = client.post(
        "/ruok/login",
        data={"username": "admin", "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def _login_user(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/ruok/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ruok"


def _save_notification_rules(config, data_dir: Path, rules) -> None:
    from nonebot_plugin_ruok.collectors.notifications import _save_rules

    _save_rules(data_dir, rules)


def _load_notification_rules(config, data_dir: Path):
    from nonebot_plugin_ruok.collectors.notifications import _load_rules

    return _load_rules(data_dir)


def _upsert_module(data_dir: Path, module) -> None:
    from nonebot_plugin_ruok.collectors.modules import upsert_module

    upsert_module(data_dir, module)


def _module_names(config, data_dir: Path) -> list[str]:
    from nonebot_plugin_ruok.collectors.modules import list_modules

    return [module.name for module in list_modules(data_dir, config)]
