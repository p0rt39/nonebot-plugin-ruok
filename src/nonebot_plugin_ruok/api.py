"""FastAPI routes for RUOK — health, session, module management."""

from __future__ import annotations

import time
import asyncio
import secrets
from typing import Any
from pathlib import Path

from fastapi import Query, Header, Depends, Request, APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .config import ScopedConfig
from .protocol import ReporterInfo, ModuleDefinition
from .collector import (
    MetricsStore,
    SessionPluginValidationError,
    SessionUpdateValidationError,
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
    get_session_stats,
    _handle_ruok_error,
    get_linked_sessions,
    collect_all_statuses,
    dispatch_notification,
    parse_filter_datetime,
    confirm_session_plugins,
)

# ────────────────────────────────
# Router factory
# ────────────────────────────────


def create_ruok_router(config: ScopedConfig, data_dir: Path) -> APIRouter:
    """Build the FastAPI router for all RUOK endpoints."""
    router = APIRouter(prefix="/ruok/api", tags=["RUOK"])

    # Simple cache
    _cache: dict[str, Any] = {}
    _cache_time: dict[str, float] = {}

    def _cached(key: str, factory: Any) -> Any:
        now = time.time()
        if key in _cache and now - _cache_time.get(key, 0) < config.cache_ttl:
            return _cache[key]
        result = factory()
        _cache[key] = result
        _cache_time[key] = now
        return result

    def _require_api_key(
        x_ruok_api_key: str | None = Header(None),
        authorization: str | None = Header(None),
    ) -> None:
        if not config.api_key:
            return

        token = x_ruok_api_key or ""
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() == "bearer":
                token = value

        if not secrets.compare_digest(token, config.api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    # ── Health ────────────────────

    @router.get("/status")
    async def api_status(_: None = Depends(_require_api_key)) -> dict[str, Any]:
        """Aggregated bot health status."""
        data = _cached("status", lambda: None)
        if data is None:
            data = await collect_all_statuses(config, data_dir)
            _cache["status"] = data
            _cache_time["status"] = time.time()
        return data.model_dump(mode="json")

    @router.get("/health", response_model=None)
    async def api_health(
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any] | JSONResponse:
        """Simple health check for K8s / Docker / Uptime."""
        data = _cached("status", lambda: None)
        if data is None:
            try:
                data = await collect_all_statuses(config, data_dir)
            except (asyncio.TimeoutError, RuntimeError, OSError):
                return JSONResponse({"status": "unhealthy"}, status_code=503)
            except Exception as exc:
                _handle_ruok_error(exc, "api_health", data_dir)
                return JSONResponse({"status": "unhealthy"}, status_code=503)
        if data.overall == "unavailable":
            return JSONResponse(
                {"status": "unhealthy", "overall": data.overall}, status_code=503
            )
        return {"status": "healthy", "overall": data.overall}

    @router.get("/connections")
    async def api_connections(
        _: None = Depends(_require_api_key),
    ) -> list[dict[str, Any]]:
        """WS connection status only (lightweight)."""
        # connections are always fresh, no caching
        status = await collect_all_statuses(config, data_dir)
        return [c.model_dump(mode="json") for c in status.connections]

    # ── Sessions ──────────────────

    @router.get("/sessions")
    async def api_list_sessions(
        _: None = Depends(_require_api_key),
        status: str | None = Query(None),
        module: str | None = Query(None),
        reporter: str | None = Query(None),
        search: str | None = Query(None),
        after: str | None = Query(
            None, description="ISO datetime, e.g. 2026-07-01T00:00:00"
        ),
        before: str | None = Query(None, description="ISO datetime"),
        plugin: str | None = Query(
            None, description="Filter by plugin name from ModuleDefinitions"
        ),
    ) -> list[dict[str, Any]]:
        """List sessions with optional filters."""
        try:
            first_seen_after = parse_filter_datetime(after) if after else None
            first_seen_before = parse_filter_datetime(before) if before else None
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid ISO datetime filter",
            ) from exc
        sessions = list_sessions(
            data_dir,
            status=status,
            module_name=module,
            reporter_user_id=reporter,
            search=search,
            first_seen_after=first_seen_after,
            first_seen_before=first_seen_before,
            plugin_name=plugin,
        )
        return [s.model_dump(mode="json") for s in sessions]

    @router.post("/sessions")
    async def api_create_session(
        request: Request,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Create a session via API (for WebUI)."""
        if not config.session_enabled:
            raise HTTPException(status_code=403, detail="Session system disabled")

        body = await request.json()
        module_name = body.get("module_name", "")
        description = body.get("description", "")
        reporter = ReporterInfo(
            type=body.get("reporter_type", "user"),
            user_id=body.get("user_id"),
            group_id=body.get("group_id"),
            platform=body.get("platform"),
        )
        session = create_session(
            data_dir,
            module_name=module_name,
            description=description,
            reporter=reporter,
            source=body.get("source", "manual"),
        )
        await dispatch_notification(session, config, data_dir)
        _cache.pop("status", None)  # invalidate cache
        return session.model_dump(mode="json")

    @router.get("/sessions/stats")
    async def api_session_stats(_: None = Depends(_require_api_key)) -> dict[str, Any]:
        """Return aggregate session statistics."""
        stats = get_session_stats(data_dir)
        return stats.model_dump(mode="json")

    @router.get("/sessions/{session_id}")
    async def api_get_session(
        session_id: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Get a single session with all occurrences."""
        session = get_session(data_dir, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.model_dump(mode="json")

    @router.patch("/sessions/{session_id}")
    async def api_update_session(
        session_id: str,
        request: Request,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Update session status / notes."""
        if not config.session_enabled:
            raise HTTPException(status_code=403, detail="Session system disabled")

        body = await request.json()
        if body.get("status") == "unsolved" and "affected_plugins" in body:
            try:
                session = confirm_session_plugins(
                    data_dir,
                    config,
                    session_id,
                    body.get("affected_plugins", []),
                )
            except SessionPluginValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            if "affected_plugins" in body:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "affected_plugins can only be set when confirming a session"
                    ),
                )
            updates = {
                k: v for k, v in body.items() if k in ("status", "developer_notes")
            }
            try:
                session = update_session(data_dir, session_id, updates)
            except SessionUpdateValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _cache.pop("status", None)
        return session.model_dump(mode="json")

    @router.post("/sessions/{session_id}/link/{other_id}")
    async def api_link_sessions(
        session_id: str,
        other_id: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Link two sessions into the same link_group."""
        if not config.session_enabled:
            raise HTTPException(status_code=403, detail="Session system disabled")

        ok = link_sessions(data_dir, session_id, other_id)
        if not ok:
            raise HTTPException(
                status_code=404, detail="One or both sessions not found"
            )
        s = get_session(data_dir, session_id)
        return {"linked": True, "link_group": s.link_group if s else None}

    @router.delete("/sessions/{session_id}/link")
    async def api_unlink_session(
        session_id: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Remove a session from its link_group."""
        if not config.session_enabled:
            raise HTTPException(status_code=403, detail="Session system disabled")

        ok = unlink_session(data_dir, session_id)
        if not ok:
            raise HTTPException(
                status_code=404, detail="Session not found or not linked"
            )
        return {"unlinked": True}

    @router.get("/sessions/{session_id}/linked")
    async def api_get_linked_sessions(
        session_id: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Get all sessions in the same link_group."""
        session = get_session(data_dir, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        linked = get_linked_sessions(data_dir, session_id)
        return {
            "link_group": session.link_group,
            "linked": [s.model_dump(mode="json") for s in linked],
        }

    # ── Modules ───────────────────

    @router.get("/modules")
    async def api_list_modules(
        _: None = Depends(_require_api_key),
    ) -> list[dict[str, Any]]:
        """List modules with real-time status."""
        return [m.model_dump(mode="json") for m in list_modules(data_dir, config)]

    @router.get("/modules/{name}")
    async def api_get_module(
        name: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Get one module."""
        mod = get_module(data_dir, config, name)
        if mod is None:
            raise HTTPException(status_code=404, detail="Module not found")
        return mod.model_dump(mode="json")

    @router.put("/modules/{name}")
    async def api_upsert_module(
        name: str,
        request: Request,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Create or update a module definition."""
        body = await request.json()
        definition = ModuleDefinition(
            name=name,
            display_name=body.get("display_name", name),
            plugins=body.get("plugins", []),
            description=body.get("description"),
        )
        upsert_module(data_dir, definition)
        _cache.pop("status", None)
        return definition.model_dump(mode="json")

    @router.delete("/modules/{name}")
    async def api_delete_module(
        name: str,
        _: None = Depends(_require_api_key),
    ) -> dict[str, Any]:
        """Delete a module definition."""
        ok = delete_module(data_dir, name)
        if not ok:
            raise HTTPException(status_code=404, detail="Module not found")
        _cache.pop("status", None)
        return {"deleted": True}

    # ── Metrics history ───────────

    @router.get("/metrics/history")
    async def api_metrics_history(
        _: None = Depends(_require_api_key),
        hours: float = Query(24.0, ge=0.5, le=168.0),
    ) -> list[dict[str, Any]]:
        """Return time-series metrics for the last *hours* hours."""
        points = MetricsStore.query(data_dir, hours=hours)
        return [p.model_dump(mode="json") for p in points]

    return router
