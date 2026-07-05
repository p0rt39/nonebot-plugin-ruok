"""Shared Jinja2 environment for RuOK WebUI.

All template rendering goes through this single instance to ensure
consistent filters, autoescape, and template caching.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"

# ── Custom filters ────────────────────────────────────────────


def _fmt_bytes_s(n: float) -> str:
    """Format bytes as human-readable string."""
    if n < 1024:
        return f"{n:.0f} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _fmt_uptime_s(seconds: int) -> str:
    """Format uptime seconds as human-readable string."""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


# ── Singleton environment ─────────────────────────────────────

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
_jinja_env.filters["_fmt_bytes_s"] = _fmt_bytes_s
_jinja_env.filters["_fmt_uptime_s"] = _fmt_uptime_s


def render(template_name: str, **context) -> HTMLResponse:
    """Render a Jinja2 template directly, bypassing Starlette's TemplateResponse.

    Special context keys:
        headers: dict[str,str] — extra HTTP response headers (e.g. HX-Trigger)
    """
    headers = context.pop("headers", None)
    template = _jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context), headers=headers)
