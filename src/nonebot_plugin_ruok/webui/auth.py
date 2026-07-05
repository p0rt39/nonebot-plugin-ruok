"""WebUI authentication — simple session-based login.

Protects /ruok SSR pages; does NOT affect /ruok/api/* endpoints.
"""
from __future__ import annotations

import hashlib

from fastapi import Form, Request, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from .jinja import render


def verify_password(plain: str, stored_hash: str) -> bool:
    """Compare a plaintext password against its SHA-256 hex digest."""
    if not stored_hash:
        return True  # no password configured = allow all
    return hashlib.sha256(plain.encode()).hexdigest() == stored_hash


def hash_password(plain: str) -> str:
    """Return SHA-256 hex digest of *plain*."""
    return hashlib.sha256(plain.encode()).hexdigest()


class WebUIAuth:
    """Encapsulates WebUI auth logic and router."""

    def __init__(self, webui_password: str) -> None:
        self._password_hash = webui_password
        self._enabled = bool(webui_password)
        self._session_key = "ruok_authed"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def middleware(self):
        """Starlette SessionMiddleware class (lazy import)."""
        from starlette.middleware.sessions import (
            SessionMiddleware,
        )

        return SessionMiddleware

    async def require_login(self, request: Request) -> bool:
        """Dependency: returns True if authenticated (or auth disabled)."""
        if not self._enabled:
            return True
        return request.session.get(self._session_key, False)

    def create_router(self) -> APIRouter:
        """Build login/logout routes."""
        router = APIRouter(tags=["ruok-auth"])

        @router.get("/ruok/login", response_class=HTMLResponse)
        async def login_page(request: Request):
            # Already logged in?
            if request.session.get(self._session_key):
                return RedirectResponse(url="/ruok", status_code=302)
            return render("login.html.jinja2", request=request)

        @router.post("/ruok/login")
        async def login_action(request: Request, password: str = Form(...)):
            if verify_password(password, self._password_hash):
                request.session[self._session_key] = True
                return RedirectResponse(url="/ruok", status_code=302)
            return render(
                "login.html.jinja2", request=request, error="密码错误"
            )

        @router.get("/ruok/logout")
        async def logout(request: Request):
            if self._enabled:
                request.session.clear()
            return RedirectResponse(
                url="/ruok/login" if self._enabled else "/ruok",
                status_code=302,
            )

        return router
