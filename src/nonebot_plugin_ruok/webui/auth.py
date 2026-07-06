"""Session-based WebUI authentication and platform user binding support.

Protects /ruok SSR pages; does NOT affect /ruok/api/* endpoints.
"""

from __future__ import annotations

import json
import hashlib
import secrets
from typing import Literal
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from collections.abc import Callable, Iterable

from fastapi import Form, Request, APIRouter
from pydantic import Field, BaseModel
from fastapi.responses import HTMLResponse, RedirectResponse

from .jinja import render
from ..config import ScopedConfig

UserRole = Literal["admin", "user"]
AdminSource = Literal["builtin", "superuser"]

ADMIN_USERNAME = "admin"
AUTH_KEY_TTL = timedelta(minutes=10)
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 390_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_password(plain: str) -> str:
    """Hash a user password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored_hash: str) -> bool:
    """Verify a plaintext password against the stored PBKDF2 hash."""
    try:
        scheme, iterations_raw, salt_hex, digest_hex = stored_hash.split("$", 3)
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False

    if scheme != PASSWORD_SCHEME or iterations <= 0:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt,
        iterations,
    )
    return secrets.compare_digest(digest, expected)


def _hash_auth_key(auth_key: str) -> str:
    return hashlib.sha256(auth_key.encode("utf-8")).hexdigest()


class StoredWebUIUser(BaseModel):
    """Persistent WebUI user record."""

    username: str
    password_hash: str
    role: Literal["user"] = "user"
    bound_user_id: str | None = None
    bound_platform: str | None = None
    auth_key_hash: str | None = None
    auth_key_expires_at: datetime | None = None
    used_auth_key_hashes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


@dataclass(frozen=True)
class CurrentWebUIUser:
    """Current authenticated WebUI identity."""

    username: str
    role: UserRole
    bound_user_id: str | None = None
    bound_platform: str | None = None
    auth_key_expires_at: datetime | None = None
    admin_source: AdminSource | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_bound(self) -> bool:
        return bool(self.bound_user_id)


@dataclass(frozen=True)
class RegistrationResult:
    """Result returned after creating a normal WebUI account."""

    user: StoredWebUIUser
    auth_key: str


class AuthKeyError(ValueError):
    """Base class for auth_key binding failures."""


class AuthKeyInvalid(AuthKeyError):
    """Raised when an auth_key cannot be found."""


class AuthKeyExpired(AuthKeyError):
    """Raised when an auth_key is found but expired."""


class AuthKeyAlreadyUsed(AuthKeyError):
    """Raised when an auth_key has already been consumed."""


class AuthUserAlreadyBound(AuthKeyError):
    """Raised when a platform user is already bound to another account."""


class UserRegistrationError(ValueError):
    """Raised when a WebUI registration request is invalid."""


class UserManagementError(ValueError):
    """Raised when a WebUI user management request is invalid."""


class WebUIAuth:
    """Encapsulates WebUI login, registration, and platform binding logic."""

    def __init__(
        self,
        config: ScopedConfig,
        data_dir: Path,
        superuser_provider: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._config = config
        self._users_path = data_dir / "webui_users.json"
        self._session_username_key = "ruok_username"
        self._session_role_key = "ruok_role"
        self._superuser_provider = superuser_provider or (lambda: ())

    @property
    def enabled(self) -> bool:
        """WebUI auth is always enforced for SSR pages."""
        return True

    @property
    def admin_configured(self) -> bool:
        return bool(self._config.webui_admin_password)

    @property
    def middleware(self) -> type:
        """Starlette SessionMiddleware class (lazy import)."""
        from starlette.middleware.sessions import (
            SessionMiddleware,
        )

        return SessionMiddleware

    def current_user(self, request: Request) -> CurrentWebUIUser | None:
        """Return the authenticated WebUI user stored in the session."""
        username = request.session.get(self._session_username_key)
        role = request.session.get(self._session_role_key)
        if not isinstance(username, str) or role not in ("admin", "user"):
            return None

        if username.casefold() == ADMIN_USERNAME:
            if role != "admin" or not self.admin_configured:
                request.session.clear()
                return None
            return CurrentWebUIUser(
                username=ADMIN_USERNAME,
                role="admin",
                admin_source="builtin",
            )

        user = self.get_user(username)
        if user is None:
            request.session.clear()
            return None
        return self._current_from_stored(user)

    async def require_login(self, request: Request) -> bool:
        """Compatibility helper for older guard code."""
        return self.current_user(request) is not None

    def require_admin(self, request: Request) -> CurrentWebUIUser | None:
        """Return current user only when it is the built-in admin."""
        user = self.current_user(request)
        if user is None or not user.is_admin:
            return None
        return user

    def require_bound_user(self, request: Request) -> CurrentWebUIUser | None:
        """Return current normal user only when it has a bound platform user id."""
        user = self.current_user(request)
        if user is None or user.is_admin or not user.is_bound:
            return None
        return user

    def get_user(self, username: str) -> StoredWebUIUser | None:
        return self._load_users().get(self._user_key(username))

    def list_users(self) -> list[StoredWebUIUser]:
        users = self._load_users()
        return sorted(users.values(), key=lambda user: user.username.casefold())

    def is_superuser_account(self, user: StoredWebUIUser) -> bool:
        """Return True when a stored user is bound to a NoneBot SUPERUSER id."""
        return bool(user.bound_user_id and user.bound_user_id in self._superusers())

    def authenticate(
        self,
        username: str,
        password: str,
    ) -> CurrentWebUIUser | None:
        """Authenticate either the virtual admin or a stored normal user."""
        normalized = username.strip()
        if not normalized or not password or not self.admin_configured:
            return None

        if normalized.casefold() == ADMIN_USERNAME:
            if secrets.compare_digest(password, self._config.webui_admin_password):
                return CurrentWebUIUser(
                    username=ADMIN_USERNAME,
                    role="admin",
                    admin_source="builtin",
                )
            return None

        user = self.get_user(normalized)
        if user is None or not verify_password(password, user.password_hash):
            return None
        return self._current_from_stored(user)

    def login_session(self, request: Request, user: CurrentWebUIUser) -> None:
        """Persist a successful WebUI login in the Starlette session."""
        request.session.clear()
        request.session[self._session_username_key] = user.username
        request.session[self._session_role_key] = user.role

    def register_user(self, username: str, password: str) -> RegistrationResult:
        """Create a normal user and return the one-time binding key."""
        normalized = username.strip()
        if not self.admin_configured:
            raise UserRegistrationError("WebUI 管理员密码尚未配置")
        if not normalized:
            raise UserRegistrationError("用户名不能为空")
        if normalized.casefold() == ADMIN_USERNAME:
            raise UserRegistrationError("admin 是内置管理员账户，不能注册")
        if not password:
            raise UserRegistrationError("密码不能为空")

        users = self._load_users()
        key = self._user_key(normalized)
        if key in users:
            raise UserRegistrationError("用户名已存在")

        auth_key = secrets.token_urlsafe(24)
        user = StoredWebUIUser(
            username=normalized,
            password_hash=hash_password(password),
            auth_key_hash=_hash_auth_key(auth_key),
            auth_key_expires_at=_utc_now() + AUTH_KEY_TTL,
        )
        users[key] = user
        self._save_users(users)
        return RegistrationResult(user=user, auth_key=auth_key)

    def issue_auth_key(
        self,
        username: str,
        *,
        allow_bound: bool = False,
    ) -> RegistrationResult:
        """Generate a fresh one-time auth_key for a normal user."""
        users = self._load_users()
        user = users.get(self._user_key(username))
        if user is None:
            raise UserRegistrationError("用户不存在")
        if user.bound_user_id and not allow_bound:
            raise UserRegistrationError("用户已绑定平台账号")

        auth_key = secrets.token_urlsafe(24)
        user.auth_key_hash = _hash_auth_key(auth_key)
        user.auth_key_expires_at = _utc_now() + AUTH_KEY_TTL
        users[self._user_key(user.username)] = user
        self._save_users(users)
        return RegistrationResult(user=user, auth_key=auth_key)

    def bind_auth_key(
        self,
        auth_key: str,
        user_id: str,
        platform: str | None = None,
    ) -> StoredWebUIUser:
        """Bind a one-time auth_key to the user_id from the current event."""
        normalized_key = auth_key.strip()
        normalized_user_id = user_id.strip()
        normalized_platform = platform.strip() if platform else None
        if not normalized_key or not normalized_user_id:
            raise AuthKeyInvalid("auth_key 无效")

        key_hash = _hash_auth_key(normalized_key)
        users = self._load_users()
        matched_expired: StoredWebUIUser | None = None
        matched_used = False

        for user in users.values():
            if key_hash in user.used_auth_key_hashes:
                matched_used = True
                continue
            if user.auth_key_hash != key_hash:
                continue
            expires_at = user.auth_key_expires_at
            if expires_at is None or _aware_utc(expires_at) < _utc_now():
                matched_expired = user
                continue
            conflict = self._binding_conflict(
                users,
                normalized_user_id,
                normalized_platform,
                exclude_username=user.username,
            )
            if conflict is not None:
                raise AuthUserAlreadyBound(
                    f"平台账号已绑定 WebUI 用户 {conflict.username}"
                )
            user.bound_user_id = normalized_user_id
            user.bound_platform = normalized_platform
            user.auth_key_hash = None
            user.auth_key_expires_at = None
            user.used_auth_key_hashes.append(key_hash)
            users[self._user_key(user.username)] = user
            self._save_users(users)
            return user

        if matched_expired is not None:
            raise AuthKeyExpired("auth_key 已过期")
        if matched_used:
            raise AuthKeyAlreadyUsed("auth_key 已使用")
        raise AuthKeyInvalid("auth_key 无效")

    def is_user_bound(self, user_id: str, platform: str | None = None) -> bool:
        """Return True if any normal WebUI account is bound to this platform user."""
        normalized_user_id = user_id.strip()
        normalized_platform = platform.strip() if platform else None
        if not normalized_user_id:
            return False
        return any(
            user.bound_user_id == normalized_user_id
            and (
                normalized_platform is None
                or user.bound_platform is None
                or user.bound_platform == normalized_platform
            )
            for user in self._load_users().values()
        )

    def verify_user_password(self, username: str, password: str) -> bool:
        """Verify the password for a stored normal user."""
        user = self.get_user(username)
        return user is not None and verify_password(password, user.password_hash)

    def update_user(
        self,
        username: str,
        *,
        new_username: str | None = None,
        new_password: str | None = None,
    ) -> StoredWebUIUser:
        """Rename and/or reset the password for a stored normal user."""
        users = self._load_users()
        old_key = self._user_key(username)
        user = users.get(old_key)
        if user is None:
            raise UserManagementError("用户不存在")

        normalized_name = (new_username or user.username).strip()
        if not normalized_name:
            raise UserManagementError("用户名不能为空")
        if normalized_name.casefold() == ADMIN_USERNAME:
            raise UserManagementError("admin 是内置管理员账户，不能作为普通用户名")

        new_key = self._user_key(normalized_name)
        if new_key != old_key and new_key in users:
            raise UserManagementError("用户名已存在")

        users.pop(old_key)
        user.username = normalized_name
        if new_password is not None:
            if not new_password:
                raise UserManagementError("密码不能为空")
            user.password_hash = hash_password(new_password)
        users[new_key] = user
        self._save_users(users)
        return user

    def change_user_password(
        self,
        username: str,
        current_password: str,
        new_password: str,
    ) -> StoredWebUIUser:
        """Change a stored user's password after verifying the current password."""
        if not self.verify_user_password(username, current_password):
            raise UserManagementError("当前密码错误")
        return self.update_user(username, new_password=new_password)

    def clear_binding(self, username: str) -> StoredWebUIUser:
        """Remove a stored user's platform binding."""
        users = self._load_users()
        key = self._user_key(username)
        user = users.get(key)
        if user is None:
            raise UserManagementError("用户不存在")
        user.bound_user_id = None
        user.bound_platform = None
        users[key] = user
        self._save_users(users)
        return user

    def delete_user(self, username: str) -> None:
        """Delete a stored normal user."""
        users = self._load_users()
        key = self._user_key(username)
        if key not in users:
            raise UserManagementError("用户不存在")
        users.pop(key)
        self._save_users(users)

    def create_router(self) -> APIRouter:
        """Build login, registration, and logout routes."""
        router = APIRouter(tags=["ruok-auth"])

        @router.get("/ruok/login", response_class=HTMLResponse, response_model=None)
        async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
            if self.current_user(request) is not None:
                return RedirectResponse(url="/ruok", status_code=302)
            return render(
                "login.html.jinja2",
                request=request,
                admin_configured=self.admin_configured,
            )

        @router.post("/ruok/login", response_model=None)
        async def login_action(
            request: Request,
            username: str = Form(...),
            password: str = Form(...),
        ) -> HTMLResponse | RedirectResponse:
            if not self.admin_configured:
                return render(
                    "login.html.jinja2",
                    request=request,
                    admin_configured=False,
                    error="未配置 RUOK__WEBUI_ADMIN_PASSWORD，WebUI 登录不可用",
                )

            user = self.authenticate(username, password)
            if user is None:
                return render(
                    "login.html.jinja2",
                    request=request,
                    admin_configured=True,
                    error="用户名或密码错误",
                    username=username.strip(),
                )

            self.login_session(request, user)
            return RedirectResponse(url="/ruok", status_code=302)

        @router.get("/ruok/register", response_class=HTMLResponse, response_model=None)
        async def register_page(request: Request) -> HTMLResponse:
            return render(
                "register.html.jinja2",
                request=request,
                admin_configured=self.admin_configured,
            )

        @router.post("/ruok/register", response_class=HTMLResponse, response_model=None)
        async def register_action(
            request: Request,
            username: str = Form(...),
            password: str = Form(...),
            password_confirm: str = Form(""),
        ) -> HTMLResponse:
            if password_confirm and password_confirm != password:
                return render(
                    "register.html.jinja2",
                    request=request,
                    admin_configured=self.admin_configured,
                    error="两次输入的密码不一致",
                    username=username.strip(),
                )

            try:
                result = self.register_user(username, password)
            except UserRegistrationError as exc:
                return render(
                    "register.html.jinja2",
                    request=request,
                    admin_configured=self.admin_configured,
                    error=str(exc),
                    username=username.strip(),
                )

            self.login_session(
                request,
                CurrentWebUIUser(username=result.user.username, role="user"),
            )
            return render(
                "register.html.jinja2",
                request=request,
                admin_configured=self.admin_configured,
                success=True,
                username=result.user.username,
                auth_key=result.auth_key,
                auth_key_expires_at=result.user.auth_key_expires_at,
            )

        @router.get("/ruok/logout")
        async def logout(request: Request) -> RedirectResponse:
            request.session.clear()
            return RedirectResponse(url="/ruok/login", status_code=302)

        return router

    @staticmethod
    def _user_key(username: str) -> str:
        return username.strip().casefold()

    def _superusers(self) -> set[str]:
        try:
            return {str(user_id) for user_id in self._superuser_provider()}
        except Exception:
            return set()

    def _current_from_stored(self, user: StoredWebUIUser) -> CurrentWebUIUser:
        admin_source: AdminSource | None = (
            "superuser" if self.is_superuser_account(user) else None
        )
        return CurrentWebUIUser(
            username=user.username,
            role="admin" if admin_source else "user",
            bound_user_id=user.bound_user_id,
            bound_platform=user.bound_platform,
            auth_key_expires_at=user.auth_key_expires_at,
            admin_source=admin_source,
        )

    def _binding_conflict(
        self,
        users: dict[str, StoredWebUIUser],
        user_id: str,
        platform: str | None,
        *,
        exclude_username: str,
    ) -> StoredWebUIUser | None:
        exclude_key = self._user_key(exclude_username)
        for key, user in users.items():
            if key == exclude_key or user.bound_user_id != user_id:
                continue
            if platform is None or user.bound_platform is None:
                return user
            if user.bound_platform == platform:
                return user
        return None

    def _load_users(self) -> dict[str, StoredWebUIUser]:
        if not self._users_path.exists():
            return {}

        try:
            raw = json.loads(self._users_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

        rows = raw.get("users", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return {}

        users: dict[str, StoredWebUIUser] = {}
        for row in rows:
            if isinstance(row, dict) and "bound_qq" in row:
                row = row.copy()
                row.setdefault("bound_user_id", row.pop("bound_qq"))
                row.setdefault("bound_platform", None)
            try:
                user = StoredWebUIUser.model_validate(row)
            except ValueError:
                continue
            users[self._user_key(user.username)] = user
        return users

    def _save_users(self, users: dict[str, StoredWebUIUser]) -> None:
        self._users_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [
                user.model_dump(mode="json")
                for user in sorted(
                    users.values(),
                    key=lambda item: item.username.casefold(),
                )
            ]
        }
        self._users_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
