"""WebUI authentication — simple session-based login.

Protects /ruok SSR pages; does NOT affect /ruok/api/* endpoints.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Form, Request, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from .jinja import render


def verify_password(plain: str, stored_hash: str) -> bool:
    """Compare a plaintext password against configured password data.

    ``RUOK__WEBUI_PASSWORD`` is documented as a plaintext password.  Keep
    accepting SHA-256 hex digests for users who followed the previous
    implementation detail.
    """
    stored = stored_hash.strip()
    if not stored:
        return True  # no password configured = allow all

    if len(stored) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored):
        digest = hashlib.sha256(plain.encode()).hexdigest()
        return secrets.compare_digest(digest, stored.lower())

    return secrets.compare_digest(plain, stored)


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
    def middleware(self) -> type:
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

        @router.get("/ruok/login", response_class=HTMLResponse, response_model=None)
        async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
            # Already logged in?
            if request.session.get(self._session_key):
                return RedirectResponse(url="/ruok", status_code=302)
            return render("login.html.jinja2", request=request)

        @router.post("/ruok/login", response_model=None)
        async def login_action(
            request: Request, password: str = Form(...)
        ) -> HTMLResponse | RedirectResponse:
            if verify_password(password, self._password_hash):
                request.session[self._session_key] = True
                return RedirectResponse(url="/ruok", status_code=302)
            return render("login.html.jinja2", request=request, error="密码错误")

        @router.get("/ruok/logout")
        async def logout(request: Request) -> RedirectResponse:
            if self._enabled:
                request.session.clear()
            return RedirectResponse(
                url="/ruok/login" if self._enabled else "/ruok",
                status_code=302,
            )

        return router
