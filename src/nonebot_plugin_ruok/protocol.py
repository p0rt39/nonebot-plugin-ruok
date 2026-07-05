"""Data models for RuOK plugin — health, session, module definitions."""

from __future__ import annotations

from typing import Any, Literal
from datetime import datetime, timezone

from pydantic import Field, BaseModel

# ────────────────────────────────
# 1. Health check models
# ────────────────────────────────

HealthStatus = Literal["healthy", "unhealthy", "degraded", "unknown"]
ModuleStatus = Literal["available", "degraded", "unavailable"]


class CheckResult(BaseModel):
    """A single sub-check result."""

    name: str
    status: HealthStatus
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None


class StatusResult(BaseModel):
    """Plugin / component-level status report."""

    plugin_name: str
    plugin_type: str  # "builtin" | "application" | "library"
    status: HealthStatus
    checks: list[CheckResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BotConnectionStatus(BaseModel):
    """WebSocket connection tracking for one bot account."""

    self_id: str
    adapter: str
    connected: bool
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    ws_closed: bool | None = None  # OneBot-specific
    latency_ms: float | None = None  # bot.get_status() e2e latency
    error: str | None = None


class PluginHealthInfo(BaseModel):
    """Five-layer plugin health model (L1-L5)."""

    name: str
    loaded: bool  # L1
    metadata: dict[str, Any] | None = None  # L2
    matchers: list[dict[str, Any]] = Field(default_factory=list)  # L3
    health_hint: str = ""  # "load_failed" | "no_matchers" | "matchers_ok"
    deep_check: dict[str, Any] | None = None  # L4 (opt-in)
    log_errors: int = 0  # L5: recent error count


class AggregatedStatus(BaseModel):
    """Root API response."""

    overall: ModuleStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    bot: StatusResult | None = None
    connections: list[BotConnectionStatus] = Field(default_factory=list)
    plugins: list[PluginHealthInfo] = Field(default_factory=list)


# ────────────────────────────────
# 2. Session models
# ────────────────────────────────

SessionSource = Literal["automatic", "manual"]
SessionStatus = Literal["pending", "unsolved", "solved", "ignored"]
ReporterType = Literal["automatic", "user"]


class ReporterInfo(BaseModel):
    """Who / what created this session."""

    type: ReporterType
    user_id: str | None = None
    group_id: str | None = None
    platform: str | None = None


class Session(BaseModel):
    """An abnormal event record — independent, linkable."""

    session_id: str  # "ruok-{8 hex}"
    source: SessionSource
    status: SessionStatus = "pending"
    module_name: str
    error_signature: str | None = None  # dedup key (automatic only)

    reporter: ReporterInfo = Field(
        default_factory=lambda: ReporterInfo(type="automatic")
    )
    description: str = ""

    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    link_group: str | None = None  # "ruok-grp-{8 hex}" — group-based linking
    developer_notes: str | None = None


# ────────────────────────────────
# 3. Module definition
# ────────────────────────────────


class ModuleDefinition(BaseModel):
    """A user-facing feature module that maps to plugins."""

    name: str  # "网易云"
    display_name: str = ""
    plugins: list[str] = Field(default_factory=list)  # ["nonebot_plugin_ncm"]
    description: str | None = None
    enabled: bool = True
    status: ModuleStatus = "available"  # derived at query time


# ────────────────────────────────
# 4. Time-series metrics
# ────────────────────────────────


class MetricPoint(BaseModel):
    """A single time-series data point for dashboard charts."""

    ts: str  # ISO 8601
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    sessions_total: int = 0
    sessions_pending: int = 0
    sessions_unsolved: int = 0
    connections_total: int = 0
    connections_online: int = 0


class SessionStats(BaseModel):
    """Aggregated session statistics."""

    total: int = 0
    pending: int = 0
    unsolved: int = 0
    solved: int = 0
    ignored: int = 0
    by_module: dict[str, int] = Field(default_factory=dict)


# ────────────────────────────────
# 5. Notification rules
# ────────────────────────────────


class NotificationRule(BaseModel):
    """A notification rule definition."""

    name: str
    enabled: bool = True
    on_status: list[str] = Field(default_factory=lambda: ["pending", "unsolved"])
    on_module: list[str] = Field(default_factory=list)  # empty = all
    cooldown_minutes: float = 60.0
    channels: list[str] = Field(default_factory=lambda: ["bot_dm"])  # bot_dm, webhook
    webhook_url: str | None = None


# ────────────────────────────────
# 6. Fast metrics (for ~3s SSE gauges)
# ────────────────────────────────


class FastMetricsSnapshot(BaseModel):
    """Lightweight snapshot for real-time system gauges (~3s refresh)."""

    cpu_percent: float = 0.0
    cpu_per_core: list[float] = Field(default_factory=list)
    memory_percent: float = 0.0
    memory_used: int = 0
    memory_total: int = 0
    swap_percent: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    load_1m: float | None = None
    load_5m: float | None = None
    load_15m: float | None = None
    process_count: int = 0
    uptime_seconds: int = 0
    timestamp: str = ""
    # ── New fields for unified system overview ──
    boot_time_epoch: float = 0.0
    bot_process_create_time: float = 0.0
    bot_rss_bytes: int = 0
    cpu_temp: float | None = None


class NetworkRate(BaseModel):
    """Per-second network transfer rate (computed from delta)."""

    bytes_sent_per_sec: float = 0.0
    bytes_recv_per_sec: float = 0.0
    packets_sent_per_sec: float = 0.0
    packets_recv_per_sec: float = 0.0


class DiskIORate(BaseModel):
    """Per-second disk I/O rate (computed from delta)."""

    read_bytes_per_sec: float = 0.0
    write_bytes_per_sec: float = 0.0
    read_count_per_sec: float = 0.0
    write_count_per_sec: float = 0.0


# ────────────────────────────────
# 7. Process info
# ────────────────────────────────


class ProcessInfo(BaseModel):
    """Lightweight info about a single process."""

    name: str = ""
    pid: int = 0
    cpu_percent: float = 0.0
    mem_rss: int = 0


class ProcessSnapshot(BaseModel):
    """Bot process details + top system processes."""

    bot_rss: int = 0
    bot_vms: int = 0
    bot_threads: int = 0
    bot_cpu_percent: float = 0.0
    top_processes: list[ProcessInfo] = Field(default_factory=list)
