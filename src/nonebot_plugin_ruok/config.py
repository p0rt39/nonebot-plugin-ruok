"""RUOK plugin configuration model."""

from pydantic import BaseModel

from .protocol import NotificationRule


class ScopedConfig(BaseModel):
    """RUOK plugin config, scoped under ``RUOK__`` in dotenv."""

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
    summary_interval_hours: float = 4.0

    # ── API / WebUI ──
    cors_origins: list[str] = ["*"]
    api_key: str = ""
    webui_admin_password: str = ""  # WebUI 内置 admin 账户密码
    webui_secret_key: str = ""  # SessionMiddleware 密钥（空=每次重启随机生成）
    sse_public: bool = False  # True 时 SSE 端点不需要登录

    # ── Time-series metrics ──
    metrics_retention_days: int = 7

    # ── Notification rules ──
    notification_enabled: bool = True
    notification_rules: list[NotificationRule] = []


class Config(BaseModel):
    """Top-level config holder for NoneBot plugin loading."""

    ruok: ScopedConfig = ScopedConfig()
