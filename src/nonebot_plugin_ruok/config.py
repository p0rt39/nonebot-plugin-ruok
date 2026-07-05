"""RuOK plugin configuration model."""

from pydantic import BaseModel

from .protocol import NotificationRule


class ScopedConfig(BaseModel):
    """RuOK plugin config, scoped under ``ruok__`` in dotenv."""

    # ── Health check ──
    check_timeout: float = 5.0
    ws_deep_check_timeout: float = 3.0
    cache_ttl: float = 10.0
    enable_deep_ws_check: bool = True

    # ── Session ──
    session_enabled: bool = True
    auto_session_enabled: bool = True
    strict_exception_capture: bool = False
    # False: stdlib logging handler only captures framework logs
    #   (uvicorn, starlette, fastapi, asyncio).
    # True: captures ALL stdlib ERROR/CRITICAL from any logger.
    report_whitelist_users: list[str] = []
    report_whitelist_groups: list[str] = []
    crisis_mode: bool = False  # 开启后跳过所有上报权限检查（测试/紧急用）
    notify_superusers: bool = True
    notify_interval_hours: float = 4.0
    summary_interval_hours: float = 4.0

    # ── API / WebUI ──
    cors_origins: list[str] = ["*"]
    api_key: str = ""
    webui_password: str = ""  # 空字符串 = 不启用 WebUI 登录认证
    sse_public: bool = False  # True 时 SSE 端点不需要登录

    # ── Time-series metrics ──
    metrics_retention_days: int = 7

    # ── Notification rules ──
    notification_rules: list[NotificationRule] = []


class Config(BaseModel):
    """Top-level config holder for NoneBot plugin loading."""

    ruok: ScopedConfig = ScopedConfig()
