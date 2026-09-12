"""RUOK plugin configuration model."""

from typing import Literal

from pydantic import Field, BaseModel, FiniteFloat


class ScopedConfig(BaseModel):
    """RUOK plugin config, scoped under ``RUOK__`` in dotenv."""

    # ── Health check ──
    check_timeout: float = 5.0
    ws_deep_check_timeout: float = 3.0
    # The SSE loop ticks once per second and uses this value for its full
    # status collection cadence. Keep it finite and at least half a second;
    # the SSE consumer rounds sub-second values up to one tick.
    cache_ttl: FiniteFloat = Field(default=10.0, ge=0.5)
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

    # ── Chat rendering ──
    chat_render_mode: Literal["text", "image"] = "text"
    chat_render_timeout: float = 8.0

    # ── Notification rules ──
    notification_enabled: bool = True


class Config(BaseModel):
    """Top-level config holder for NoneBot plugin loading."""

    ruok: ScopedConfig = ScopedConfig()
