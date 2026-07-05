"""SSR routes for RuOK WebUI — Jinja2 + HTMX + Pico.css."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from jinja2 import Environment, FileSystemLoader

from ..collector import (
    collect_all_statuses,
    delete_module,
    get_linked_sessions,
    get_session,
    get_session_stats,
    link_sessions,
    list_modules,
    list_sessions,
    MetricsStore,
    unlink_session,
    update_session,
    upsert_module,
)
from ..config import ScopedConfig
from ..protocol import ModuleDefinition

from .auth import WebUIAuth
from .sse import event_bus, sse_event_generator

TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def _render(template_name: str, **context) -> HTMLResponse:
    """Render a Jinja2 template directly, bypassing Starlette's TemplateResponse."""
    template = _jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context))


def create_webui_router(config: ScopedConfig, data_dir: Path) -> APIRouter:
    """Build SSR router for the RuOK WebUI."""
    router = APIRouter(tags=["ruok-webui"])
    auth = WebUIAuth(config.webui_password)

    # ── SSE endpoint (before auth — or optional auth) ──

    @router.get("/ruok/sse")
    async def sse_stream():
        """Server-Sent Events stream for real-time dashboard updates."""

        async def collect_fn():
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

    # ── Auth guard dependency ──

    async def _guard(request: Request):
        if not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
        return None

    # ── Pages ─────────────────────

    @router.get("/ruok", response_class=HTMLResponse)
    async def page_dashboard(request: Request, _partial: str = ""):
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
        try:
            status = await collect_all_statuses(config, data_dir)
        except Exception:
            from ..protocol import AggregatedStatus

            status = AggregatedStatus(overall="unavailable")
        modules = list_modules(data_dir, config)
        stats = get_session_stats(data_dir)
        initial_metrics_raw = MetricsStore.query(data_dir, hours=1.0)
        initial_metrics_json = json.dumps([m.model_dump(mode="json") for m in initial_metrics_raw])

        # SSE-triggered partial renders
        if _partial == "dashboard-overview":
            return _render("_dashboard_overview.html.jinja2", status=status)
        if _partial == "dashboard-stats":
            return _render("_dashboard_stats.html.jinja2", stats=stats)

        return _render(
            "dashboard.html.jinja2",
            request=request,
            status=status,
            modules=modules,
            stats=stats,
            initial_metrics_json=initial_metrics_json,
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
    ):
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
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
        return _render(
            "sessions.html.jinja2",
            request=request,
            sessions=sessions,
            stats=stats,
            all_modules=all_modules,
            current_status=status,
            current_search=search,
            current_module=module,
            current_plugin=plugin,
            current_after=after,
            current_before=before,
        )

    @router.get("/ruok/sessions/{session_id}", response_class=HTMLResponse)
    async def page_session_detail(request: Request, session_id: str):
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
        session = get_session(data_dir, session_id)
        if session is None:
            return HTMLResponse("<p>Session not found</p>", status_code=404)
        linked = get_linked_sessions(data_dir, session_id)
        return _render(
            "sessions_detail.html.jinja2",
            request=request,
            session=session,
            linked_sessions=linked,
        )

    @router.get("/ruok/modules", response_class=HTMLResponse)
    async def page_modules(request: Request):
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
        modules = list_modules(data_dir, config)
        return _render("modules.html.jinja2", request=request, modules=modules)

    @router.get("/ruok/notifications", response_class=HTMLResponse)
    async def page_notifications(request: Request):
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)
        rules = config.notification_rules
        return _render(
            "notifications.html.jinja2",
            request=request,
            rules=rules,
        )

    # ── HTMX partials ────────────

    @router.get("/ruok/_partials/modules", response_class=HTMLResponse)
    async def partial_modules(request: Request):
        modules = list_modules(data_dir, config)
        return _render("_modules_table.html.jinja2", request=request, modules=modules)

    @router.get("/ruok/_partials/sessions", response_class=HTMLResponse)
    async def partial_sessions(
        request: Request,
        status: str = "",
        search: str = "",
        module: str = "",
        plugin: str = "",
        after: str = "",
        before: str = "",
    ):
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
        return _render(
            "_sessions_list.html.jinja2",
            request=request,
            sessions=sessions,
            current_status=status,
        )

    # ── HTMX actions ─────────────

    @router.post("/ruok/_actions/confirm/{session_id}")
    async def action_confirm(session_id: str):
        update_session(data_dir, session_id, {"status": "unsolved"})
        return HTMLResponse(status_code=200)

    @router.post("/ruok/_actions/solve/{session_id}")
    async def action_solve(session_id: str):
        update_session(data_dir, session_id, {"status": "solved"})
        return HTMLResponse(status_code=200)

    @router.post("/ruok/_actions/ignore/{session_id}")
    async def action_ignore(session_id: str):
        update_session(data_dir, session_id, {"status": "ignored"})
        return HTMLResponse(status_code=200)

    @router.post("/ruok/_actions/session-note/{session_id}")
    async def action_session_note(session_id: str, developer_notes: str = Form("")):
        update_session(data_dir, session_id, {"developer_notes": developer_notes})
        return HTMLResponse(
            f'<div id="notes-area"><form hx-post="/ruok/_actions/session-note/{session_id}" hx-target="#notes-area" hx-swap="outerHTML"><textarea name="developer_notes" rows="3" style="width:100%;" placeholder="添加备注...">{developer_notes}</textarea><button type="submit">保存备注</button></form><p style="color: var(--pico-ins-color);">✅ 已保存</p></div>'
        )

    @router.post("/ruok/_actions/link/{session_id}")
    async def action_link(session_id: str, other_id: str = Form(...)):
        ok = link_sessions(data_dir, session_id, other_id)
        if not ok:
            return HTMLResponse('<p style="color:var(--pico-del-color);">Session 未找到或 ID 相同</p>', status_code=400)
        linked = get_linked_sessions(data_dir, session_id)
        return _render("_linked_list.html.jinja2", session_id=session_id, linked_sessions=linked)

    @router.post("/ruok/_actions/unlink/{session_id}")
    async def action_unlink(session_id: str):
        unlink_session(data_dir, session_id)
        return HTMLResponse('<p>已解除关联</p>')

    @router.post("/ruok/_actions/link/{session_id}/remove/{other_id}")
    async def action_unlink_other(session_id: str, other_id: str):
        unlink_session(data_dir, other_id)
        linked = get_linked_sessions(data_dir, session_id)
        return _render("_linked_list.html.jinja2", session_id=session_id, linked_sessions=linked)

    @router.post("/ruok/_actions/module-upsert")
    async def action_module_upsert(
        name: str = Form(...),
        display_name: str = Form(""),
        description: str = Form(""),
        plugins: str = Form(""),
        enabled: bool = Form(True),
        request: Request = None,
    ):
        plugins_list = [p.strip() for p in plugins.split(",") if p.strip()]
        definition = ModuleDefinition(
            name=name,
            display_name=display_name or name,
            plugins=plugins_list,
            description=description or None,
            enabled=enabled,
        )
        upsert_module(data_dir, definition)
        modules = list_modules(data_dir, config)
        return _render("_modules_table.html.jinja2", request=request, modules=modules)

    @router.post("/ruok/_actions/module-delete/{name}")
    async def action_module_delete(name: str, request: Request):
        delete_module(data_dir, name)
        modules = list_modules(data_dir, config)
        return _render("_modules_table.html.jinja2", request=request, modules=modules)

    return router
