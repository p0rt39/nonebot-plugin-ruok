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


_STATUS_TONES = {
    "pending": "warn",
    "unsolved": "err",
    "solved": "ok",
    "ignored": "muted",
    "available": "ok",
    "healthy": "ok",
    "degraded": "warn",
    "unavailable": "err",
    "unhealthy": "err",
}


def _status_tone(status: object) -> str:
    """Map a Session/module/check status to a badge tone class suffix."""
    return _STATUS_TONES.get(str(status), "muted")


def _path_quote(value: object) -> str:
    """Quote a value for use as a single URL path segment."""
    return quote(str(value), safe="")


def split_session_description(description: str) -> tuple[str, str]:
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


def parse_session_summary(description: str) -> dict[str, str]:
    """Parse RUOK automatic summary markdown into display fields."""
    summary: dict[str, str] = {}
    for line in description.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("**"):
            continue
        label_end = stripped.find("**", 2)
        if label_end == -1:
            continue
        label = stripped[2:label_end].strip()
        value = stripped[label_end + 2 :].lstrip(":：").strip()
        if label and value:
            summary[label] = value
    return summary


_SUMMARY_LIMIT = 160
_LOG_METADATA_PREFIXES = ("日志来源:", "模块映射:", "共享插件模块:")


def session_summary(description: str) -> str:
    """Return a single-line, markup-free summary for Session list rows."""
    prose, code = split_session_description(description)
    fields = parse_session_summary(prose)
    if fields:
        error_type = fields.get("异常类型", "")
        error_message = fields.get("异常信息", "")
        if error_type or error_message:
            text = f"{error_type}: {error_message}" if error_type else error_message
        else:
            text = " · ".join(fields.values())
    else:
        # Log-captured Sessions keep everything inside one fenced block whose
        # leading lines are source metadata.
        candidates = [line.strip() for line in prose.splitlines() if line.strip()]
        if not candidates:
            candidates = [
                line.strip()
                for line in code.splitlines()
                if line.strip() and not line.strip().startswith(_LOG_METADATA_PREFIXES)
            ]
        text = candidates[0] if candidates else ""
        text = text.replace("**", "").replace("`", "")
    text = " ".join(text.split())
    if len(text) > _SUMMARY_LIMIT:
        text = text[: _SUMMARY_LIMIT - 1].rstrip() + "…"
    return text


# ── Singleton environment ─────────────────────────────────────

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
_jinja_env.filters["_fmt_bytes_s"] = _fmt_bytes_s
_jinja_env.filters["_fmt_uptime_s"] = _fmt_uptime_s
_jinja_env.filters["_status_label"] = _status_label
_jinja_env.filters["_status_tone"] = _status_tone
_jinja_env.filters["_path_quote"] = _path_quote
_jinja_env.filters["_session_summary"] = session_summary


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
