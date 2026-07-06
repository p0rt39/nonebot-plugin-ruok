"""Shared Jinja2 environment for RUOK WebUI.

All template rendering goes through this single instance to ensure
consistent filters, autoescape, and template caching.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader
from fastapi.responses import HTMLResponse

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


def _status_label(status: object) -> str:
    """Return a localized label for internal status values."""
    value = str(status)
    labels = {
        "pending": "待确认",
        "unsolved": "未解决",
        "solved": "已解决",
        "ignored": "已忽略",
        "available": "可用",
        "degraded": "降级",
        "unavailable": "不可用",
        "healthy": "健康",
    }
    return labels.get(value, value)


def _path_quote(value: object) -> str:
    """Quote a value for use as a single URL path segment."""
    return quote(str(value), safe="")


# ── Singleton environment ─────────────────────────────────────

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
_jinja_env.filters["_fmt_bytes_s"] = _fmt_bytes_s
_jinja_env.filters["_fmt_uptime_s"] = _fmt_uptime_s
_jinja_env.filters["_status_label"] = _status_label
_jinja_env.filters["_path_quote"] = _path_quote


def render(template_name: str, **context) -> HTMLResponse:
    """Render a Jinja2 template directly, bypassing Starlette's TemplateResponse.

    Special context keys:
        headers: dict[str,str] — extra HTTP response headers (e.g. HX-Trigger)
    """
    headers = context.pop("headers", None)
    template = _jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context), headers=headers)


def render_to_string(template_name: str, **context) -> str:
    """Render a Jinja2 template to a plain string."""
    template = _jinja_env.get_template(template_name)
    return template.render(**context)
