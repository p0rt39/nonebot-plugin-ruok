"""SSR routes for RuOK WebUI — Jinja2 + HTMX + Pico.css."""

from __future__ import annotations

import html
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from collections.abc import Callable

from fastapi import Form, Query, Depends, Request, APIRouter, HTTPException
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from .sse import event_bus, sse_event_generator
from .auth import (
    WebUIAuth,
    StoredWebUIUser,
    CurrentWebUIUser,
    UserManagementError,
    UserRegistrationError,
)
from .jinja import render
from ..config import ScopedConfig
from ..protocol import (
    NetworkRate,
    ReporterInfo,
    ModuleDefinition,
    NotificationRule,
    FastMetricsSnapshot,
)
from ..collector import (
    MetricsStore,
    get_module,
    get_session,
    list_modules,
    delete_module,
    link_sessions,
    list_sessions,
    upsert_module,
    create_session,
    unlink_session,
    update_session,
    _network_tracker,
    get_session_stats,
    _handle_ruok_error,
    get_linked_sessions,
    collect_all_statuses,
    collect_fast_metrics,
    _collect_plugin_inventory,
)


def _query_metrics_history(data_dir: Path, hours: float = 1.0) -> list[dict[str, Any]]:
    """Return serialized historical metrics for WebUI chart rendering."""
    return [
        point.model_dump(mode="json")
        for point in MetricsStore.query(data_dir, hours=hours)
    ]


def _plugin_names() -> list[str]:
    """Return loaded plugin names for module form suggestions."""
    return [plugin.name for plugin in _collect_plugin_inventory(skip_ruok=False)]


def _extra_module_plugins(
    module: ModuleDefinition | None, plugin_names: list[str]
) -> list[str]:
    """Return configured module plugins that are not in the loaded plugin list."""
    if module is None:
        return []
    loaded = set(plugin_names)
    return [plugin for plugin in module.plugins if plugin not in loaded]


def _parse_form_values(selected: list[str], raw: str) -> list[str]:
    """Parse selected and manually typed form values, preserving order."""
    parsed: list[str] = []
    for item in [*selected, raw]:
        for plugin in item.replace("\n", ",").split(","):
            name = plugin.strip()
            if name and name not in parsed:
                parsed.append(name)
    return parsed


def _parse_module_plugins(selected: list[str], raw: str) -> list[str]:
    """Parse selected and manually typed plugin names."""
    return _parse_form_values(selected, raw)


def _extra_notification_modules(
    rule: NotificationRule | None, module_names: list[str]
) -> list[str]:
    """Return rule module names that are not in the module definition list."""
    if rule is None:
        return []
    known = set(module_names)
    return [module for module in rule.on_module if module not in known]


def _module_detail_url(name: str) -> str:
    """Build a WebUI module detail URL for arbitrary module names."""
    return f"/ruok/modules/{quote(name, safe='')}"


def _notification_detail_url(name: str) -> str:
    """Build a WebUI notification detail URL for arbitrary rule names."""
    return f"/ruok/notifications/{quote(name, safe='')}"


def _account_reporter_id(user: CurrentWebUIUser) -> str | None:
    """Return the reporter user id used for the current WebUI account."""
    if user.admin_source == "builtin":
        return "webui-admin"
    return user.bound_user_id


def _account_reporter_platform(user: CurrentWebUIUser) -> str:
    """Return the reporter platform marker used for WebUI manual reports."""
    return "webui"


def _reporter_identity(user_id: str, platform: str | None) -> str:
    """Format a platform identity for display."""
    if platform:
        return f"{platform}: {user_id}"
    return user_id


def _find_bound_reporter_user(
    reporter: ReporterInfo,
    users: list[StoredWebUIUser],
) -> StoredWebUIUser | None:
    """Find the WebUI user currently bound to a session reporter identity."""
    if not reporter.user_id:
        return None
    for user in users:
        if user.bound_user_id != reporter.user_id:
            continue
        if reporter.platform in (None, "webui") or user.bound_platform is None:
            return user
        if user.bound_platform == reporter.platform:
            return user
    return None


def _reporter_display_factory(
    users: list[StoredWebUIUser],
) -> Callable[[ReporterInfo], str]:
    """Build a per-render reporter display formatter from current bindings."""

    def _reporter_display(reporter: ReporterInfo) -> str:
        if not reporter.user_id:
            return ""

        bound_user = _find_bound_reporter_user(reporter, users)
        if bound_user is None:
            return _reporter_identity(reporter.user_id, reporter.platform)

        bound_user_id = bound_user.bound_user_id or reporter.user_id
        bound_platform = bound_user.bound_platform or reporter.platform
        return (
            f"{bound_user.username}"
            f"（{_reporter_identity(bound_user_id, bound_platform)}）"
        )

    return _reporter_display


def _error_html(message: str, status_code: int = 400) -> HTMLResponse:
    """Return a compact Pico-styled error fragment for HTMX actions."""
    return HTMLResponse(
        f'<p style="color:var(--pico-del-color);">❌ {html.escape(message)}</p>',
        status_code=status_code,
    )


def _filter_users(users, search: str):
    """Filter stored WebUI users by username or bound platform identity."""
    query = search.strip().casefold()
    if not query:
        return users

    def _matches(user) -> bool:
        values = (
            user.username,
            user.bound_user_id or "",
            user.bound_platform or "",
        )
        return any(query in value.casefold() for value in values)

    return [user for user in users if _matches(user)]


def _split_session_description(description: str) -> tuple[str, str]:
    """Split a session description into prose and fenced code content."""
    marker = "```"
    start = description.find(marker)
    if start == -1:
        return description.strip(), ""

    end = description.find(marker, start + len(marker))
    if end == -1:
        return description.strip(), ""

    prose = description[:start].strip()
    code = description[start + len(marker) : end].strip()
    return prose, code


def create_webui_router(
    config: ScopedConfig,
    data_dir: Path,
    auth: WebUIAuth,
) -> APIRouter:
    """Build SSR router for the RuOK WebUI."""
    router = APIRouter(tags=["ruok-webui"])

    # ── SSE endpoint ──

    @router.get("/ruok/sse", response_model=None)
    async def sse_stream(request: Request) -> StreamingResponse | JSONResponse:
        """Server-Sent Events stream for real-time dashboard updates.

        Respects WebUI auth unless config.sse_public is True.
        """
        if not config.sse_public:
            user = auth.current_user(request)
            if user is None:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if not user.is_admin:
                return JSONResponse({"detail": "Admin required"}, status_code=403)

        async def collect_fn() -> Any:
            return await collect_all_statuses(config, data_dir)

        return StreamingResponse(
            sse_event_generator(config.cache_ttl, collect_fn, event_bus),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Auth guard dependencies ──

    async def _login_guard(request: Request) -> CurrentWebUIUser:
        """FastAPI dependency: redirect to login if not authenticated."""
        user = auth.current_user(request)
        if user is None:
            accept = request.headers.get("accept", "")
            if "text/html" in accept and request.method == "GET":
                raise HTTPException(
                    status_code=302,
                    headers={"Location": "/ruok/login"},
                    detail="Login required",
                )
            raise HTTPException(status_code=401, detail="Login required")
        return user

    async def _admin_guard(
        request: Request,
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> CurrentWebUIUser:
        """FastAPI dependency: require the built-in WebUI admin account."""
        if user.is_admin:
            return user
        accept = request.headers.get("accept", "")
        if "text/html" in accept and request.method == "GET":
            raise HTTPException(
                status_code=302,
                headers={"Location": "/ruok"},
                detail="Admin required",
            )
        raise HTTPException(status_code=403, detail="Admin required")

    # ── Pages ─────────────────────

    @router.get("/ruok", response_class=HTMLResponse)
    async def page_dashboard(
        request: Request,
        _partial: str = "",
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        try:
            status = await collect_all_statuses(config, data_dir)
        except (asyncio.TimeoutError, RuntimeError, OSError):
            from ..protocol import AggregatedStatus

            status = AggregatedStatus(overall="unavailable")
        except Exception as exc:
            _handle_ruok_error(exc, "page_dashboard 状态采集", data_dir)
            from ..protocol import AggregatedStatus

            status = AggregatedStatus(overall="unavailable")
        modules = list_modules(data_dir, config)
        stats = get_session_stats(data_dir)

        # Hero banner partial (SSE-driven refresh)
        if _partial == "dashboard-hero":
            return render("_dashboard_hero.html.jinja2", status=status)

        if not user.is_admin:
            if _partial:
                raise HTTPException(status_code=403, detail="Admin required")
            stored_user = auth.get_user(user.username)
            auth_key = auth.active_auth_key(stored_user)
            auth_key_expired = auth.auth_key_expired(stored_user)
            auth_key_expires_at = (
                stored_user.auth_key_expires_at if stored_user else None
            )
            if not user.bound_user_id:
                if auth_key is None and not auth_key_expired:
                    issued = auth.issue_auth_key(user.username)
                    auth_key = issued.auth_key
                    auth_key_expires_at = issued.user.auth_key_expires_at
            return render(
                "dashboard_user.html.jinja2",
                request=request,
                current_user=user,
                status=status,
                modules=modules,
                sessions=(
                    list_sessions(data_dir, reporter_user_id=user.bound_user_id)
                    if user.bound_user_id
                    else []
                ),
                all_modules=modules,
                auth_key=auth_key,
                auth_key_expired=auth_key_expired,
                auth_key_expires_at=auth_key_expires_at,
                reporter_display=_reporter_display_factory(auth.list_users()),
            )

        # SSE/polling partial renders (other panels)
        if _partial == "dashboard-metrics":
            return render("_dashboard_metrics.html.jinja2", status=status)
        if _partial == "dashboard-disk":
            return render("_dashboard_disk.html.jinja2", status=status)
        if _partial == "dashboard-connections":
            return render("_dashboard_connections.html.jinja2", status=status)
        if _partial == "dashboard-stats":
            return render("_dashboard_stats.html.jinja2", stats=stats)
        if _partial == "dashboard-trends":
            return render(
                "_dashboard_trends.html.jinja2",
                metrics_history=_query_metrics_history(data_dir),
            )

        # Initial data for full page render
        try:
            metrics_snap = await collect_fast_metrics()
            net_rate = _network_tracker.get_rate()
        except (OSError, RuntimeError):
            metrics_snap = FastMetricsSnapshot()
            net_rate = NetworkRate()
        except Exception as exc:
            _handle_ruok_error(exc, "page_dashboard 快速指标采集", data_dir)
            metrics_snap = FastMetricsSnapshot()
            net_rate = NetworkRate()
        return render(
            "dashboard.html.jinja2",
            request=request,
            current_user=user,
            status=status,
            modules=modules,
            stats=stats,
            metrics=metrics_snap,
            metrics_history=_query_metrics_history(data_dir),
            net_up=net_rate.bytes_sent_per_sec,
            net_down=net_rate.bytes_recv_per_sec,
        )

    @router.get("/ruok/users", response_class=HTMLResponse)
    async def page_users(
        request: Request,
        search: str = "",
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        try:
            stored_user = (
                None if user.username == "admin" else auth.get_user(user.username)
            )
            users = _filter_users(auth.list_users(), search) if user.is_admin else []
            template = (
                "_users_panel.html.jinja2"
                if request.headers.get("hx-request") == "true"
                else "users.html.jinja2"
            )
            return render(
                template,
                request=request,
                current_user=user,
                users=users,
                user_search=search,
                stored_user=stored_user,
                auth=auth,
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_users", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/sessions", response_class=HTMLResponse)
    async def page_sessions(
        request: Request,
        status: str = "",
        search: str = "",
        module: str = "",
        plugin: str = "",
        after: str = "",
        before: str = "",
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            first_seen_after = datetime.fromisoformat(after) if after else None
            first_seen_before = datetime.fromisoformat(before) if before else None
            sessions = list_sessions(
                data_dir,
                status=status if status else None,
                module_name=module if module else None,
                search=search if search else None,
                first_seen_after=first_seen_after,
                first_seen_before=first_seen_before,
                plugin_name=plugin if plugin else None,
            )
            stats = get_session_stats(data_dir)
            all_modules = list_modules(data_dir, config)
            return render(
                "sessions.html.jinja2",
                request=request,
                current_user=user,
                sessions=sessions,
                stats=stats,
                all_modules=all_modules,
                current_status=status,
                current_search=search,
                current_module=module,
                current_plugin=plugin,
                current_after=after,
                current_before=before,
                reporter_display=_reporter_display_factory(auth.list_users()),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_sessions", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/sessions/{session_id}", response_class=HTMLResponse)
    async def page_session_detail(
        request: Request,
        session_id: str,
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            session = get_session(data_dir, session_id)
            if session is None:
                return HTMLResponse("<p>Session not found</p>", status_code=404)
            linked = get_linked_sessions(data_dir, session_id)
            session_description, session_traceback = _split_session_description(
                session.description
            )
            return render(
                "sessions_detail.html.jinja2",
                request=request,
                current_user=user,
                session=session,
                session_description=session_description,
                session_traceback=session_traceback,
                linked_sessions=linked,
                reporter_display=_reporter_display_factory(auth.list_users()),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"page_session_detail {session_id}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/modules", response_class=HTMLResponse)
    async def page_modules(
        request: Request,
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            modules = list_modules(data_dir, config)
            # Gather loaded plugin names for datalist suggestions
            plugin_names = _plugin_names()
            return render(
                "modules.html.jinja2",
                request=request,
                current_user=user,
                modules=modules,
                all_plugins=plugin_names,
                extra_plugins=[],
                module=None,
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_modules", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/modules/{name:path}", response_class=HTMLResponse)
    async def page_module_detail(
        request: Request,
        name: str,
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            mod = get_module(data_dir, config, name)
            if mod is None:
                return HTMLResponse("<p>Module not found</p>", status_code=404)
            # Sessions for this module
            sessions = list_sessions(data_dir, module_name=name)
            # Plugin health for associated plugins
            all_plugins = _collect_plugin_inventory(skip_ruok=False)
            plugin_names = [plugin.name for plugin in all_plugins]
            linked_plugins = [p for p in all_plugins if p.name in mod.plugins]
            return render(
                "modules_detail.html.jinja2",
                request=request,
                current_user=user,
                module=mod,
                sessions=sessions,
                linked_plugins=linked_plugins,
                all_plugins=plugin_names,
                extra_plugins=_extra_module_plugins(mod, plugin_names),
                reporter_display=_reporter_display_factory(auth.list_users()),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"page_module_detail {name}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/notifications", response_class=HTMLResponse)
    async def page_notifications(
        request: Request,
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            rules = _load_notification_rules()
            modules = list_modules(data_dir, config)
            return render(
                "notifications.html.jinja2",
                request=request,
                current_user=user,
                rules=rules,
                rule=None,
                all_modules=modules,
                extra_modules=[],
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_notifications", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/notifications/{name:path}", response_class=HTMLResponse)
    async def page_notification_detail(
        request: Request,
        name: str,
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            rules = _load_notification_rules()
            rule = next((rule for rule in rules if rule.name == name), None)
            if rule is None:
                return HTMLResponse("<p>Rule not found</p>", status_code=404)
            modules = list_modules(data_dir, config)
            return render(
                "notifications_detail.html.jinja2",
                request=request,
                current_user=user,
                rule=rule,
                all_modules=modules,
                extra_modules=_extra_notification_modules(
                    rule,
                    [module.name for module in modules],
                ),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"page_notification_detail {name}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    # ── HTMX partials ────────────

    @router.get("/ruok/_partials/modules", response_class=HTMLResponse)
    async def partial_modules(
        request: Request,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            modules = list_modules(data_dir, config)
            return render(
                "_modules_table.html.jinja2", request=request, modules=modules
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "partial_modules", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    @router.get("/ruok/_partials/dashboard-trends-data")
    async def partial_dashboard_trends_data(
        hours: float = Query(1.0, ge=0.5, le=168.0),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> list[dict[str, Any]]:
        """JSON data for dashboard trend charts.

        This route is WebUI-authenticated but independent from the public API key
        because it is consumed by the already-authenticated SSR UI.
        """
        return _query_metrics_history(data_dir, hours=hours)

    @router.get("/ruok/_partials/sessions", response_class=HTMLResponse)
    async def partial_sessions(
        request: Request,
        status: str = "",
        search: str = "",
        module: str = "",
        plugin: str = "",
        after: str = "",
        before: str = "",
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            first_seen_after = datetime.fromisoformat(after) if after else None
            first_seen_before = datetime.fromisoformat(before) if before else None
            sessions = list_sessions(
                data_dir,
                status=status if status else None,
                module_name=module if module else None,
                search=search if search else None,
                first_seen_after=first_seen_after,
                first_seen_before=first_seen_before,
                plugin_name=plugin if plugin else None,
            )
            stats = get_session_stats(data_dir)
            return render(
                "_session_container.html.jinja2",
                sessions=sessions,
                stats=stats,
                reporter_display=_reporter_display_factory(auth.list_users()),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "partial_sessions", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    # ── HTMX actions ─────────────

    @router.post("/ruok/_actions/session-create")
    async def action_session_create(
        request: Request,
        module_name: str = Form(""),
        module_name_custom: str = Form(""),
        description: str = Form(""),
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        """Create a session from the WebUI manual report form."""
        if not config.session_enabled:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">'
                "❌ Session 系统未启用，无法创建</p>",
                status_code=403,
            )

        name = module_name_custom.strip() or module_name.strip()
        if not name:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">❌ 请选择或输入模块名</p>',
                status_code=400,
            )
        if not user.is_admin and not user.bound_user_id:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">'
                "❌ 请先通过 /ruok bind <auth_key> 绑定平台账号后再提交上报</p>",
                status_code=403,
            )
        try:
            reporter = ReporterInfo(
                type="user",
                user_id=_account_reporter_id(user),
                platform=_account_reporter_platform(user),
            )
            session = create_session(
                data_dir,
                module_name=name,
                description=description.strip() or "(无描述)",
                reporter=reporter,
                source="manual",
            )
            # Dispatch notifications for the new session
            from ..collectors.notifications import dispatch_notification

            await dispatch_notification(session, config, data_dir)
            if not user.is_admin:
                return render(
                    "_user_sessions.html.jinja2",
                    sessions=list_sessions(
                        data_dir,
                        reporter_user_id=user.bound_user_id,
                    ),
                    reporter_display=_reporter_display_factory(auth.list_users()),
                    headers={
                        "HX-Trigger": (
                            '{"toast":"Session created","toastType":"success"}'
                        )
                    },
                )
            # Refresh session list
            sessions = list_sessions(data_dir)
            stats = get_session_stats(data_dir)
            # Return session-content partial (stats + count + list)
            return render(
                "_session_container.html.jinja2",
                sessions=sessions,
                stats=stats,
                reporter_display=_reporter_display_factory(auth.list_users()),
                headers={
                    "HX-Trigger": ('{"toast":"Session created","toastType":"success"}')
                },
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_session_create", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 创建失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    def _render_users_panel(
        request: Request,
        user: CurrentWebUIUser,
        *,
        search: str = "",
        toast: str = "",
        toast_type: str = "success",
    ) -> HTMLResponse:
        stored_user = None if user.username == "admin" else auth.get_user(user.username)
        headers = (
            {"HX-Trigger": f'{{"toast":"{toast}","toastType":"{toast_type}"}}'}
            if toast
            else None
        )
        return render(
            "_users_panel.html.jinja2",
            request=request,
            current_user=user,
            users=_filter_users(auth.list_users(), search) if user.is_admin else [],
            user_search=search,
            stored_user=stored_user,
            auth=auth,
            headers=headers,
        )

    @router.post("/ruok/_actions/user-create")
    async def action_user_create(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        search: str = Form(""),
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            result = auth.register_user(username, password)
        except UserManagementError as exc:
            return _error_html(str(exc))
        except UserRegistrationError as exc:
            return _error_html(str(exc))
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_user_create", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 创建失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return render(
            "_users_panel.html.jinja2",
            request=request,
            current_user=user,
            users=_filter_users(auth.list_users(), search),
            user_search=search,
            stored_user=None,
            auth=auth,
            issued_auth_key=result.auth_key,
            issued_auth_key_user=result.user.username,
            issued_auth_key_expires_at=result.user.auth_key_expires_at,
            headers={"HX-Trigger": '{"toast":"User created","toastType":"success"}'},
        )

    @router.post("/ruok/_actions/user-update")
    async def action_user_update(
        request: Request,
        username: str = Form(...),
        new_username: str = Form(""),
        new_password: str = Form(""),
        search: str = Form(""),
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            auth.update_user(
                username,
                new_username=new_username or None,
                new_password=new_password or None,
            )
            return _render_users_panel(
                request,
                user,
                search=search,
                toast="User saved",
            )
        except UserManagementError as exc:
            return _error_html(str(exc))

    @router.post("/ruok/_actions/user-auth-key")
    async def action_user_auth_key(
        request: Request,
        username: str = Form(...),
        search: str = Form(""),
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            result = auth.issue_auth_key(username, allow_bound=True)
        except UserManagementError as exc:
            return _error_html(str(exc))
        except UserRegistrationError as exc:
            return _error_html(str(exc))
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_user_auth_key", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 生成失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return render(
            "_users_panel.html.jinja2",
            request=request,
            current_user=user,
            users=_filter_users(auth.list_users(), search),
            user_search=search,
            stored_user=None,
            auth=auth,
            issued_auth_key=result.auth_key,
            issued_auth_key_user=result.user.username,
            issued_auth_key_expires_at=result.user.auth_key_expires_at,
            headers={"HX-Trigger": '{"toast":"Auth key issued","toastType":"success"}'},
        )

    @router.post("/ruok/_actions/user-clear-binding")
    async def action_user_clear_binding(
        request: Request,
        username: str = Form(...),
        search: str = Form(""),
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            auth.clear_binding(username)
            return _render_users_panel(
                request,
                user,
                search=search,
                toast="Binding cleared",
                toast_type="info",
            )
        except UserManagementError as exc:
            return _error_html(str(exc))

    @router.post("/ruok/_actions/user-delete")
    async def action_user_delete(
        request: Request,
        username: str = Form(...),
        search: str = Form(""),
        user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            auth.delete_user(username)
            return _render_users_panel(
                request,
                user,
                search=search,
                toast="User deleted",
                toast_type="info",
            )
        except UserManagementError as exc:
            return _error_html(str(exc))

    @router.post("/ruok/_actions/account-password")
    async def action_account_password(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        new_password_confirm: str = Form(""),
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        if user.username == "admin":
            return _error_html("内置 admin 密码请通过 .env 修改")
        if new_password_confirm and new_password != new_password_confirm:
            return _error_html("两次输入的密码不一致")
        try:
            auth.change_user_password(user.username, current_password, new_password)
            return _render_users_panel(
                request,
                auth.current_user(request) or user,
                toast="Password changed",
            )
        except UserManagementError as exc:
            return _error_html(str(exc))

    @router.post("/ruok/_actions/account-rebind-key")
    async def action_account_rebind_key(
        request: Request,
        current_password: str = Form(...),
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        if user.username == "admin":
            return _error_html("内置 admin 账户不支持平台绑定")
        if not auth.verify_user_password(user.username, current_password):
            return _error_html("当前密码错误")
        try:
            result = auth.issue_auth_key(user.username, allow_bound=True)
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_account_rebind_key", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 生成失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        refreshed = auth.current_user(request) or user
        return render(
            "_users_panel.html.jinja2",
            request=request,
            current_user=refreshed,
            users=auth.list_users() if refreshed.is_admin else [],
            stored_user=auth.get_user(refreshed.username),
            auth=auth,
            issued_auth_key=result.auth_key,
            issued_auth_key_user=result.user.username,
            issued_auth_key_expires_at=result.user.auth_key_expires_at,
            headers={"HX-Trigger": '{"toast":"Auth key issued","toastType":"success"}'},
        )

    @router.post("/ruok/_actions/account-auth-key")
    async def action_account_auth_key(
        request: Request,
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse:
        if user.username == "admin":
            return _error_html("内置 admin 账户不支持平台绑定")
        if user.bound_user_id:
            return _error_html("已绑定账号请使用换绑流程")
        try:
            result = auth.issue_auth_key(user.username, allow_bound=False)
        except UserRegistrationError as exc:
            return _error_html(str(exc))
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_account_auth_key", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 生成失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        refreshed = auth.current_user(request) or user
        return render(
            "_users_panel.html.jinja2",
            request=request,
            current_user=refreshed,
            users=[],
            stored_user=auth.get_user(refreshed.username),
            auth=auth,
            issued_auth_key=result.auth_key,
            issued_auth_key_user=result.user.username,
            issued_auth_key_expires_at=result.user.auth_key_expires_at,
            headers={"HX-Trigger": '{"toast":"Auth key issued","toastType":"success"}'},
        )

    @router.post("/ruok/_actions/account-delete", response_model=None)
    async def action_account_delete(
        request: Request,
        current_password: str = Form(...),
        confirm_username: str = Form(...),
        user: CurrentWebUIUser = Depends(_login_guard),
    ) -> HTMLResponse | JSONResponse:
        if user.username == "admin":
            return _error_html("内置 admin 不能注销")
        if confirm_username.strip() != user.username:
            return _error_html("用户名确认不匹配")
        if not auth.verify_user_password(user.username, current_password):
            return _error_html("当前密码错误")
        try:
            auth.delete_user(user.username)
            request.session.clear()
        except UserManagementError as exc:
            return _error_html(str(exc))
        return JSONResponse(
            {"ok": True},
            headers={
                "HX-Redirect": "/ruok/login",
                "HX-Trigger": '{"toast":"Account deleted","toastType":"info"}',
            },
        )

    @router.post("/ruok/_actions/confirm/{session_id}")
    async def action_confirm(
        session_id: str,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        try:
            update_session(data_dir, session_id, {"status": "unsolved"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_confirm {session_id}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            reporter_display=_reporter_display_factory(auth.list_users()),
            headers={
                "HX-Trigger": ('{"toast":"Session confirmed","toastType":"success"}')
            },
        )

    @router.post("/ruok/_actions/solve/{session_id}")
    async def action_solve(
        session_id: str,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        try:
            update_session(data_dir, session_id, {"status": "solved"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_solve {session_id}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            reporter_display=_reporter_display_factory(auth.list_users()),
            headers={
                "HX-Trigger": ('{"toast":"Session resolved","toastType":"success"}')
            },
        )

    @router.post("/ruok/_actions/ignore/{session_id}")
    async def action_ignore(
        session_id: str,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        try:
            update_session(data_dir, session_id, {"status": "ignored"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_ignore {session_id}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            reporter_display=_reporter_display_factory(auth.list_users()),
            headers={"HX-Trigger": ('{"toast":"Session ignored","toastType":"info"}')},
        )

    @router.post("/ruok/_actions/session-note/{session_id}")
    async def action_session_note(
        session_id: str,
        developer_notes: str = Form(""),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        try:
            update_session(data_dir, session_id, {"developer_notes": developer_notes})
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_session_note {session_id}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        return HTMLResponse(
            f'<div id="notes-area">'
            f'<form hx-post="/ruok/_actions/session-note/{session_id}" '
            f'hx-target="#notes-area" hx-swap="outerHTML">'
            f'<textarea name="developer_notes" rows="3" '
            f'style="width:100%;" placeholder="添加备注...">'
            f"{html.escape(developer_notes)}</textarea>"
            f'<button type="submit">保存备注</button></form>'
            f'<p style="color: var(--pico-ins-color);">✅ 已保存</p></div>'
        )

    @router.post("/ruok/_actions/link/{session_id}")
    async def action_link(
        session_id: str,
        other_id: str = Form(...),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        ok = link_sessions(data_dir, session_id, other_id)
        if not ok:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">Session 未找到或 ID 相同</p>',
                status_code=400,
            )
        linked = get_linked_sessions(data_dir, session_id)
        return render(
            "_linked_list.html.jinja2",
            session_id=session_id,
            linked_sessions=linked,
        )

    @router.post("/ruok/_actions/unlink/{session_id}")
    async def action_unlink(
        session_id: str,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        unlink_session(data_dir, session_id)
        return HTMLResponse("<p>已解除关联</p>")

    @router.post("/ruok/_actions/link/{session_id}/remove/{other_id}")
    async def action_unlink_other(
        session_id: str,
        other_id: str,
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        if not config.session_enabled:
            return HTMLResponse("Session system disabled", status_code=403)
        unlink_session(data_dir, other_id)
        linked = get_linked_sessions(data_dir, session_id)
        return render(
            "_linked_list.html.jinja2",
            session_id=session_id,
            linked_sessions=linked,
        )

    @router.post("/ruok/_actions/module-upsert", response_model=None)
    async def action_module_upsert(
        request: Request,
        name: str = Form(...),
        original_name: str = Form(""),
        display_name: str = Form(""),
        description: str = Form(""),
        plugins: str = Form(""),
        selected_plugins: list[str] = Form(default_factory=list),
        return_to_detail: bool = Form(False),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse | JSONResponse:
        try:
            normalized_name = name.strip()
            normalized_original = original_name.strip()
            if not normalized_name:
                return HTMLResponse(
                    '<p style="color:var(--pico-del-color);">❌ 模块名不能为空</p>',
                    status_code=400,
                )

            modules_before = list_modules(data_dir, config)
            if normalized_original and normalized_original != normalized_name:
                if any(m.name == normalized_name for m in modules_before):
                    return HTMLResponse(
                        f'<p style="color:var(--pico-del-color);">'
                        f'❌ 模块 "{normalized_name}" 已存在</p>',
                        status_code=400,
                    )

            new_display = display_name.strip() or normalized_name
            replace_name = normalized_original or normalized_name
            for module in modules_before:
                if module.name == replace_name:
                    continue
                if module.display_name == new_display:
                    return HTMLResponse(
                        f'<p style="color:var(--pico-del-color);">'
                        f'❌ 显示名 "{new_display}" 已被模块 '
                        f'"{module.display_name or module.name}" 使用</p>',
                        status_code=400,
                    )

            plugins_list = _parse_module_plugins(selected_plugins, plugins)
            definition = ModuleDefinition(
                name=normalized_name,
                display_name=new_display,
                plugins=plugins_list,
                description=description.strip() or None,
            )
            if normalized_original and normalized_original != normalized_name:
                delete_module(data_dir, normalized_original)
            upsert_module(data_dir, definition)
            modules = list_modules(data_dir, config)
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_module_upsert {name}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        if return_to_detail:
            return JSONResponse(
                {"ok": True},
                headers={
                    "HX-Redirect": _module_detail_url(normalized_name),
                    "HX-Trigger": '{"toast":"Module saved","toastType":"success"}',
                },
            )
        return render(
            "_modules_table.html.jinja2",
            request=request,
            modules=modules,
            headers={
                "HX-Trigger": (
                    '{"toast":"Module saved","toastType":"success",'
                    '"refresh":"#module-form-panel",'
                    '"refreshUrl":"/ruok/_actions/module-edit-form"}'
                )
            },
        )

    @router.post("/ruok/_actions/module-delete", response_model=None)
    async def action_module_delete(
        request: Request,
        name: str = Form(...),
        redirect_to: str = Form("/ruok/modules"),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse | JSONResponse:
        try:
            delete_module(data_dir, name.strip())
            modules = list_modules(data_dir, config)
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_module_delete {name}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 删除失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        if redirect_to:
            return JSONResponse(
                {"ok": True},
                headers={
                    "HX-Redirect": redirect_to,
                    "HX-Trigger": '{"toast":"Module deleted","toastType":"info"}',
                },
            )
        return render("_modules_table.html.jinja2", request=request, modules=modules)

    @router.get("/ruok/_actions/module-edit-form")
    async def action_module_edit_form(
        name: str = "",
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        try:
            plugin_names = _plugin_names()
            if not name:
                return render(
                    "_module_form.html.jinja2",
                    module=None,
                    all_plugins=plugin_names,
                    extra_plugins=[],
                )
            mod = get_module(data_dir, config, name)
            if mod is None:
                return HTMLResponse("<p>Module not found</p>", status_code=404)
            return render(
                "_module_form.html.jinja2",
                module=mod,
                all_plugins=plugin_names,
                extra_plugins=_extra_module_plugins(mod, plugin_names),
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, f"action_module_edit_form {name}", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    # ── Notification rules CRUD ──

    def _load_notification_rules() -> list[NotificationRule]:
        """Load notification rules — delegates to collectors/notifications.py."""
        from ..collectors.notifications import _load_rules

        return _load_rules(data_dir, config)

    def _save_notification_rules(rules: list[NotificationRule]) -> None:
        """Persist notification rules — delegates to collectors/notifications.py."""
        from ..collectors.notifications import _save_rules

        _save_rules(data_dir, rules)

    @router.post("/ruok/_actions/notification-upsert", response_model=None)
    async def action_notification_upsert(
        request: Request,
        name: str = Form(...),
        original_name: str = Form(""),
        enabled: bool = Form(False),
        on_status: str = Form("pending,unsolved"),
        on_module: str = Form(""),
        selected_modules: list[str] = Form(default_factory=list),
        cooldown_minutes: float = Form(60.0),
        channels: str = Form("bot_dm"),
        webhook_url: str = Form(""),
        return_to_detail: bool = Form(False),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse | JSONResponse:
        """Create or update a notification rule."""
        try:
            rules = _load_notification_rules()
            normalized_name = name.strip()
            normalized_original = original_name.strip()
            if not normalized_name:
                return HTMLResponse(
                    '<p style="color:var(--pico-del-color);">❌ 规则名称不能为空</p>',
                    status_code=400,
                )
            if normalized_original and normalized_original != normalized_name:
                if any(r.name == normalized_name for r in rules):
                    return HTMLResponse(
                        f'<p style="color:var(--pico-del-color);">'
                        f'❌ 规则 "{normalized_name}" 已存在</p>',
                        status_code=400,
                    )
            rule = NotificationRule(
                name=normalized_name,
                enabled=enabled,
                on_status=[s.strip() for s in on_status.split(",") if s.strip()],
                on_module=_parse_form_values(selected_modules, on_module),
                cooldown_minutes=cooldown_minutes,
                channels=[c.strip() for c in channels.split(",") if c.strip()],
                webhook_url=webhook_url or None,
            )
            replace_name = normalized_original or normalized_name
            rules = [r for r in rules if r.name != replace_name]
            if replace_name != normalized_name:
                rules = [r for r in rules if r.name != normalized_name]
            rules.append(rule)
            _save_notification_rules(rules)
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_notification_upsert", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        if return_to_detail:
            return JSONResponse(
                {"ok": True},
                headers={
                    "HX-Redirect": _notification_detail_url(normalized_name),
                    "HX-Trigger": '{"toast":"Rule saved","toastType":"success"}',
                },
            )
        return render(
            "_notifications_list.html.jinja2",
            rules=rules,
            headers={
                "HX-Trigger": (
                    '{"toast":"Rule saved","toastType":"success",'
                    '"refresh":"#notification-form-panel",'
                    '"refreshUrl":"/ruok/_actions/notification-edit-form"}'
                )
            },
        )

    @router.post("/ruok/_actions/notification-delete", response_model=None)
    async def action_notification_delete(
        request: Request,
        name: str = Form(...),
        redirect_to: str = Form(""),
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse | JSONResponse:
        """Delete a notification rule by name."""
        try:
            delete_name = name.strip()
            rules = [r for r in _load_notification_rules() if r.name != delete_name]
            _save_notification_rules(rules)
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_notification_delete", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 删除失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )
        if redirect_to:
            return JSONResponse(
                {"ok": True},
                headers={
                    "HX-Redirect": redirect_to,
                    "HX-Trigger": '{"toast":"Rule deleted","toastType":"info"}',
                },
            )
        return render(
            "_notifications_list.html.jinja2",
            rules=rules,
            headers={"HX-Trigger": '{"toast":"Rule deleted","toastType":"info"}'},
        )

    @router.get("/ruok/_actions/notification-edit-form")
    async def action_notification_edit_form(
        name: str = "",
        _user: CurrentWebUIUser = Depends(_admin_guard),
    ) -> HTMLResponse:
        """Return the notification form, optionally pre-filled for editing."""
        try:
            modules = list_modules(data_dir, config)
            if not name:
                return render(
                    "_notification_form.html.jinja2",
                    rule=None,
                    all_modules=modules,
                    extra_modules=[],
                )
            rules = _load_notification_rules()
            rule = next((r for r in rules if r.name == name), None)
            if rule is None:
                return HTMLResponse("Rule not found", status_code=404)
            return render(
                "_notification_form.html.jinja2",
                rule=rule,
                all_modules=modules,
                extra_modules=_extra_notification_modules(
                    rule,
                    [module.name for module in modules],
                ),
            )
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_notification_edit_form {name}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f"❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>",
                status_code=500,
            )

    return router
