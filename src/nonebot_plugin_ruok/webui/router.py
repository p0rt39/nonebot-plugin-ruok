"""SSR routes for RuOK WebUI — Jinja2 + HTMX + Pico.css."""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import Form, Depends, Request, APIRouter
from nonebot import logger
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from .sse import event_bus, sse_event_generator
from .auth import WebUIAuth
from .jinja import render
from ..config import ScopedConfig
from ..protocol import (
    NetworkRate,
    ReporterInfo,
    ModuleDefinition,
    FastMetricsSnapshot,
)
from ..collector import (
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
)


def create_webui_router(config: ScopedConfig, data_dir: Path) -> APIRouter:
    """Build SSR router for the RuOK WebUI."""
    router = APIRouter(tags=["ruok-webui"])
    auth = WebUIAuth(config.webui_password)

    # ── SSE endpoint ──

    @router.get("/ruok/sse")
    async def sse_stream(request: Request):
        """Server-Sent Events stream for real-time dashboard updates.

        Respects WebUI auth unless config.sse_public is True.
        """
        if auth.enabled and not config.sse_public:
            if not await auth.require_login(request):
                return JSONResponse(
                    {"detail": "Unauthorized"}, status_code=401
                )

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

    async def _webui_guard(request: Request):
        """FastAPI dependency: redirect to login if not authenticated."""
        if auth.enabled and not await auth.require_login(request):
            return RedirectResponse(url="/ruok/login", status_code=302)

    # ── Pages ─────────────────────

    @router.get("/ruok", response_class=HTMLResponse)
    async def page_dashboard(request: Request, _partial: str = "",
                             _guard_ok=Depends(_webui_guard)):
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
            return render("_dashboard_trends.html.jinja2")

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
            status=status,
            modules=modules,
            stats=stats,
            metrics=metrics_snap,
            net_up=net_rate.bytes_sent_per_sec,
            net_down=net_rate.bytes_recv_per_sec,
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
        _guard_ok=Depends(_webui_guard),
    ):
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
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_sessions", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    @router.get("/ruok/sessions/{session_id}", response_class=HTMLResponse)
    async def page_session_detail(request: Request, session_id: str,
                                 _guard_ok=Depends(_webui_guard)):
        try:
            session = get_session(data_dir, session_id)
            if session is None:
                return HTMLResponse("<p>Session not found</p>", status_code=404)
            linked = get_linked_sessions(data_dir, session_id)
            return render(
                "sessions_detail.html.jinja2",
                request=request,
                session=session,
                linked_sessions=linked,
            )
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"page_session_detail {session_id}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    @router.get("/ruok/modules", response_class=HTMLResponse)
    async def page_modules(request: Request, _guard_ok=Depends(_webui_guard)):
        try:
            modules = list_modules(data_dir, config)
            return render("modules.html.jinja2", request=request, modules=modules)
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_modules", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    @router.get("/ruok/notifications", response_class=HTMLResponse)
    async def page_notifications(request: Request, _guard_ok=Depends(_webui_guard)):
        try:
            rules = _load_notification_rules()
            return render(
                "notifications.html.jinja2",
                request=request,
                rules=rules,
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "page_notifications", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    # ── HTMX partials ────────────

    @router.get("/ruok/_partials/modules", response_class=HTMLResponse)
    async def partial_modules(request: Request):
        try:
            modules = list_modules(data_dir, config)
            return render(
                "_modules_table.html.jinja2", request=request, modules=modules
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "partial_modules", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

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
            return render(
                "_sessions_list.html.jinja2",
                request=request,
                sessions=sessions,
                current_status=status,
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "partial_sessions", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 加载失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    # ── HTMX actions ─────────────

    @router.post("/ruok/_actions/session-create")
    async def action_session_create(
        request: Request,
        module_name: str = Form(""),
        module_name_custom: str = Form(""),
        description: str = Form(""),
    ):
        """Create a session from the WebUI manual report form."""
        name = module_name_custom.strip() or module_name.strip()
        if not name:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">❌ 请选择或输入模块名</p>',
                status_code=400,
            )
        try:
            reporter = ReporterInfo(type="user", user_id="webui")
            create_session(
                data_dir,
                module_name=name,
                description=description.strip() or "(无描述)",
                reporter=reporter,
                source="manual",
            )
            # Refresh session list
            sessions = list_sessions(data_dir)
            stats = get_session_stats(data_dir)
            all_modules = list_modules(data_dir, config)
            return render(
                "sessions.html.jinja2",
                request=request,
                sessions=sessions,
                stats=stats,
                all_modules=all_modules,
                current_status="",
                current_search="",
                current_module="",
                current_plugin="",
                current_after="",
                current_before="",
                headers={
                    "HX-Trigger": (
                        '{"toast":"Session created","toastType":"success"}'
                    )
                },
            )
        except Exception as exc:
            sid = _handle_ruok_error(exc, "action_session_create", data_dir)
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 创建失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )

    @router.post("/ruok/_actions/confirm/{session_id}")
    async def action_confirm(session_id: str):
        try:
            update_session(data_dir, session_id, {"status": "unsolved"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_confirm {session_id}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            headers={
                "HX-Trigger": (
                    '{"toast":"Session confirmed","toastType":"success"}'
                )
            },
        )

    @router.post("/ruok/_actions/solve/{session_id}")
    async def action_solve(session_id: str):
        try:
            update_session(data_dir, session_id, {"status": "solved"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_solve {session_id}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            headers={
                "HX-Trigger": (
                    '{"toast":"Session resolved","toastType":"success"}'
                )
            },
        )

    @router.post("/ruok/_actions/ignore/{session_id}")
    async def action_ignore(session_id: str):
        try:
            update_session(data_dir, session_id, {"status": "ignored"})
            s = get_session(data_dir, session_id)
            if s is None:
                raise ValueError("Session not found after update")
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_ignore {session_id}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 操作失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render(
            "_session_card.html.jinja2",
            s=s,
            headers={
                "HX-Trigger": (
                    '{"toast":"Session ignored","toastType":"info"}'
                )
            },
        )

    @router.post("/ruok/_actions/session-note/{session_id}")
    async def action_session_note(
        session_id: str, developer_notes: str = Form("")
    ):
        try:
            update_session(
                data_dir, session_id, {"developer_notes": developer_notes}
            )
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_session_note {session_id}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return HTMLResponse(
            f'<div id="notes-area">'
            f'<form hx-post="/ruok/_actions/session-note/{session_id}" '
            f'hx-target="#notes-area" hx-swap="outerHTML">'
            f'<textarea name="developer_notes" rows="3" '
            f'style="width:100%;" placeholder="添加备注...">'
            f'{developer_notes}</textarea>'
            f'<button type="submit">保存备注</button></form>'
            f'<p style="color: var(--pico-ins-color);">✅ 已保存</p></div>'
        )

    @router.post("/ruok/_actions/link/{session_id}")
    async def action_link(
        session_id: str, other_id: str = Form(...)
    ):
        ok = link_sessions(data_dir, session_id, other_id)
        if not ok:
            return HTMLResponse(
                '<p style="color:var(--pico-del-color);">'
                'Session 未找到或 ID 相同</p>',
                status_code=400,
            )
        linked = get_linked_sessions(data_dir, session_id)
        return render(
            "_linked_list.html.jinja2",
            session_id=session_id,
            linked_sessions=linked,
        )

    @router.post("/ruok/_actions/unlink/{session_id}")
    async def action_unlink(session_id: str):
        unlink_session(data_dir, session_id)
        return HTMLResponse("<p>已解除关联</p>")

    @router.post("/ruok/_actions/link/{session_id}/remove/{other_id}")
    async def action_unlink_other(session_id: str, other_id: str):
        unlink_session(data_dir, other_id)
        linked = get_linked_sessions(data_dir, session_id)
        return render(
            "_linked_list.html.jinja2",
            session_id=session_id,
            linked_sessions=linked,
        )

    @router.post("/ruok/_actions/module-upsert")
    async def action_module_upsert(
        request: Request,
        name: str = Form(...),
        display_name: str = Form(""),
        description: str = Form(""),
        plugins: str = Form(""),
        enabled: bool = Form(True),
    ):
        plugins_list = [p.strip() for p in plugins.split(",") if p.strip()]
        definition = ModuleDefinition(
            name=name,
            display_name=display_name or name,
            plugins=plugins_list,
            description=description or None,
            enabled=enabled,
        )
        try:
            upsert_module(data_dir, definition)
            modules = list_modules(data_dir, config)
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_module_upsert {name}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render("_modules_table.html.jinja2", request=request, modules=modules)

    @router.post("/ruok/_actions/module-delete/{name}")
    async def action_module_delete(name: str, request: Request):
        try:
            delete_module(data_dir, name)
            modules = list_modules(data_dir, config)
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, f"action_module_delete {name}", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 删除失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render("_modules_table.html.jinja2", request=request, modules=modules)

    # ── Notification rules CRUD ──

    def _load_notification_rules() -> list:
        """Load notification rules from data file, merging config defaults."""
        from ..protocol import NotificationRule

        file_rules: list[dict] = []
        rules_file = data_dir / "notification_rules.json"
        if rules_file.exists():
            try:
                file_rules = json.loads(rules_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning(
                    f"RuOK WebUI: load notification rules failed: {exc}"
                )
                file_rules = []
            except Exception as exc:
                _handle_ruok_error(
                    exc, "_load_notification_rules", data_dir
                )
                file_rules = []
        # Merge: file rules override config rules by name
        config_rules = [r.model_dump(mode="json") for r in config.notification_rules]
        merged: dict[str, dict] = {r["name"]: r for r in config_rules}
        for r in file_rules:
            merged[r["name"]] = r
        return [NotificationRule(**r) for r in merged.values()]

    def _save_notification_rules(rules: list) -> None:
        """Persist notification rules to data file."""

        rules_file = data_dir / "notification_rules.json"
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        rules_file.write_text(
            json.dumps([r.model_dump(mode="json") for r in rules],
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @router.post("/ruok/_actions/notification-upsert")
    async def action_notification_upsert(
        request: Request,
        name: str = Form(...),
        enabled: bool = Form(True),
        on_status: str = Form("pending,unsolved"),
        on_module: str = Form(""),
        cooldown_minutes: float = Form(60.0),
        channels: str = Form("bot_dm"),
        webhook_url: str = Form(""),
    ):
        """Create or update a notification rule."""
        from ..protocol import NotificationRule

        try:
            rules = _load_notification_rules()
            rule = NotificationRule(
                name=name,
                enabled=enabled,
                on_status=[s.strip() for s in on_status.split(",") if s.strip()],
                on_module=[m.strip() for m in on_module.split(",") if m.strip()],
                cooldown_minutes=cooldown_minutes,
                channels=[c.strip() for c in channels.split(",") if c.strip()],
                webhook_url=webhook_url or None,
            )
            # Upsert: replace if exists
            existing = [r for r in rules if r.name == name]
            if existing:
                rules = [r for r in rules if r.name != name]
            rules.append(rule)
            _save_notification_rules(rules)
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, "action_notification_upsert", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 保存失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render(
            "notifications.html.jinja2",
            request=request,
            rules=rules,
            headers={"HX-Trigger": '{"toast":"Rule saved","toastType":"success"}'},
        )

    @router.post("/ruok/_actions/notification-delete/{name}")
    async def action_notification_delete(request: Request, name: str):
        """Delete a notification rule by name."""
        try:
            rules = [r for r in _load_notification_rules() if r.name != name]
            _save_notification_rules(rules)
        except Exception as exc:
            sid = _handle_ruok_error(
                exc, "action_notification_delete", data_dir
            )
            return HTMLResponse(
                f'<p style="color:var(--pico-del-color);">'
                f'❌ 删除失败 [{type(exc).__name__}] → Session: {sid}</p>',
                status_code=500,
            )
        return render(
            "notifications.html.jinja2",
            request=request,
            rules=rules,
            headers={"HX-Trigger": '{"toast":"Rule deleted","toastType":"info"}'},
        )

    return router
