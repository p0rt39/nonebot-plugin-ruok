# `nonebot_plugin_ruok` 包 — Agent 指南

Scope：`src/nonebot_plugin_ruok/`（Python 包根）。在进入子目录前请先阅读对应下层 `AGENTS.md`：
`collectors/AGENTS.md`、`webui/AGENTS.md`、`webui/templates/AGENTS.md`。先读根 `AGENTS.md`。

## 子系统职责边界

- `__init__.py`：NoneBot 集成——`__plugin_meta__`、scoped config、`/ruok` 命令、bot 连接跟踪、
  ASGI 挂载与 startup。
- `config.py`：`Config` 与 `ScopedConfig`（dotenv 前缀 `RUOK__`）；配置默认值须与 README 表格保持一致。
- `protocol.py`：共享领域 Pydantic 模型（Session、Module、指标、API/报告模型等）。
- `api.py`：`/ruok/api/*` JSON API；API key 校验、输入校验、路由级状态缓存。
- `collector.py`：兼容性 re-export 层 + `MetricsStore` 实现。
- `collectors/`：采集器实现（见 `collectors/AGENTS.md`）。
- `webui/`：WebUI 路由、认证、SSE、Jinja（见 `webui/AGENTS.md`）。
- `chat_render.py`：展示模型、htmlrender 调用、OneBot V11 图片发送，以及供
  `__init__.py` 文本回退消费的错误。

**新 collector 逻辑放 `collectors/` 下**；仅当外部调用方需要某符号时才从
`collectors/__init__.py` 与 `collector.py` re-export。

## 包级约束

- Startup 必须零配置可用；非 ASGI 驱动跳过 ASGI 挂载。
- APScheduler、监控、通知、指标、摘要调度均为 **best-effort**；失败要记录日志且不得拖垮主 Bot。
- 配置统一走 `ScopedConfig`，dotenv 键保持 `RUOK__...`。
- API key（`/ruok/api/*`）与 WebUI 认证相互独立；改动任一侧时不要串用另一侧的守卫。
- 健康聚合值（`available`/`degraded`/`unavailable`）与单项检查值
  （`healthy`/`degraded`/`unhealthy`/`unknown`）是两套语义，不要混用。
- 聊天图片渲染为 opt-in（`chat_render_mode="image"`，默认 `"text"`），渲染/发送失败必须回退到
  相同的可见文本结果；图片发送当前仅支持 OneBot V11，添加其他适配器时不要扩大授权范围。

## 描述性参考

完整架构、持久化数据模型、Session/Module 语义与认证/路由细节见仓库根的
`docs/architecture.md`（相对包根为 `../../../docs/architecture.md`）。
