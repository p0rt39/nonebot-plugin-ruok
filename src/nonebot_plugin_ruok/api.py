"""FastAPI routes for RuOK — health, session, module management."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .collector import (
    collect_all_statuses,
    create_session,
    delete_module,
    get_module,
    get_session,
    link_sessions,
    list_modules,
    list_sessions,
    update_session,
    upsert_module,
)
from .config import ScopedConfig
from .protocol import ModuleDefinition, ReporterInfo

# ────────────────────────────────
# Router factory
# ────────────────────────────────


def create_ruok_router(config: ScopedConfig, data_dir: Path) -> APIRouter:
    """Build the FastAPI router for all ruok endpoints."""
    router = APIRouter(prefix="/ruok/api", tags=["ruok"])

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

    # ── Health ────────────────────

    @router.get("/status")
    async def api_status():
        """Aggregated bot health status."""
        data = _cached("status", lambda: None)
        if data is None:
            data = await collect_all_statuses(config, data_dir)
            _cache["status"] = data
            _cache_time["status"] = time.time()
        return data.model_dump(mode="json")

    @router.get("/health")
    async def api_health():
        """Simple health check for K8s / Docker / Uptime."""
        data = _cached("status", lambda: None)
        if data is None:
            try:
                data = await collect_all_statuses(config, data_dir)
            except Exception:
                return JSONResponse({"status": "unhealthy"}, status_code=503)
        if data.overall == "unavailable":
            return JSONResponse(
                {"status": "unhealthy", "overall": data.overall}, status_code=503
            )
        return {"status": "healthy", "overall": data.overall}

    @router.get("/connections")
    async def api_connections():
        """WS connection status only (lightweight)."""
        # connections are always fresh, no caching
        status = await collect_all_statuses(config, data_dir)
        return [c.model_dump(mode="json") for c in status.connections]

    # ── Sessions ──────────────────

    @router.get("/sessions")
    async def api_list_sessions(
        status: str | None = Query(None),
        module: str | None = Query(None),
        reporter: str | None = Query(None),
    ):
        """List sessions with optional filters."""
        sessions = list_sessions(
            data_dir,
            status=status,
            module_name=module,
            reporter_user_id=reporter,
        )
        return [s.model_dump(mode="json") for s in sessions]

    @router.post("/sessions")
    async def api_create_session(request: Request):
        """Create a session via API (for WebUI)."""
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
        _cache.pop("status", None)  # invalidate cache
        return session.model_dump(mode="json")

    @router.get("/sessions/{session_id}")
    async def api_get_session(session_id: str):
        """Get a single session with all occurrences."""
        session = get_session(data_dir, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.model_dump(mode="json")

    @router.patch("/sessions/{session_id}")
    async def api_update_session(session_id: str, request: Request):
        """Update session status / notes."""
        body = await request.json()
        updates = {k: v for k, v in body.items() if k in ("status", "developer_notes")}
        session = update_session(data_dir, session_id, updates)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _cache.pop("status", None)
        return session.model_dump(mode="json")

    @router.get("/sessions/{session_id}/occurrences")
    async def api_session_occurrences(session_id: str):
        """Get occurrence history for a session."""
        session = get_session(data_dir, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return [o.model_dump(mode="json") for o in session.occurrences]

    @router.post("/sessions/{session_id}/link/{other_id}")
    async def api_link_sessions(session_id: str, other_id: str):
        """Link two sessions together."""
        ok = link_sessions(data_dir, session_id, other_id)
        if not ok:
            raise HTTPException(status_code=404, detail="One or both sessions not found")
        return {"linked": True}

    # ── Modules ───────────────────

    @router.get("/modules")
    async def api_list_modules():
        """List modules with real-time status."""
        return [m.model_dump(mode="json") for m in list_modules(data_dir, config)]

    @router.get("/modules/{name}")
    async def api_get_module(name: str):
        """Get one module."""
        mod = get_module(data_dir, config, name)
        if mod is None:
            raise HTTPException(status_code=404, detail="Module not found")
        return mod.model_dump(mode="json")

    @router.put("/modules/{name}")
    async def api_upsert_module(name: str, request: Request):
        """Create or update a module definition."""
        body = await request.json()
        definition = ModuleDefinition(
            name=name,
            display_name=body.get("display_name", name),
            plugins=body.get("plugins", []),
            description=body.get("description"),
            enabled=body.get("enabled", True),
        )
        upsert_module(data_dir, definition)
        _cache.pop("status", None)
        return definition.model_dump(mode="json")

    @router.delete("/modules/{name}")
    async def api_delete_module(name: str):
        """Delete a module definition."""
        ok = delete_module(data_dir, name)
        if not ok:
            raise HTTPException(status_code=404, detail="Module not found")
        _cache.pop("status", None)
        return {"deleted": True}

    return router
