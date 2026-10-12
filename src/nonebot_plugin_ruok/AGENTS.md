# `nonebot_plugin_ruok` 包 — Agent 指南

Scope：`src/nonebot_plugin_ruok/`（Python 包根）。在进入子目录前请先阅读对应下层 `AGENTS.md`：
`collectors/AGENTS.md`、`webui/AGENTS.md`、`webui/templates/AGENTS.md`。先读根 `AGENTS.md`。

## 子系统职责边界

- `__init__.py`：NoneBot 集成——`__plugin_meta__`、scoped config、`/ruok` 命令、bot 连接跟踪、
  ASGI 挂载与 startup。
- `config.py`：`Config` 与 `ScopedConfig`（dotenv 前缀 `RUOK__`）；配置默认值**必须与 `README.md`
  配置表保持一致**——修改默认值的同一时间更新 README 表格，二者互为锁定。
- `protocol.py`：共享领域 Pydantic 模型（Session、Module、指标、API/报告模型等）。
- `api.py`：`/ruok/api/*` JSON API；API key 校验、输入校验、路由级状态缓存。
- `collector.py`：兼容性 re-export 层 + `MetricsStore` 实现。
- `collectors/`：采集器实现（见 `collectors/AGENTS.md`）。
- `webui/`：WebUI 路由、认证、SSE、Jinja（见 `webui/AGENTS.md`）。
- `chat_render.py`：展示模型、htmlrender 调用、OneBot V11 图片发送，以及供
  `__init__.py` 文本回退消费的错误。

**新 collector 逻辑放 `collectors/` 下**；仅当外部调用方需要某符号时才从
`collectors/__init__.py` 与 `collector.py` re-export。

> **`collector.py` 再导出契约**：`__init__.py`、`api.py`、`webui/router.py`、`webui/sse.py`、
> `collectors/metrics.py` 都按**具名符号**从这里 import（如 `MetricsStore`、
> `confirm_session_plugins`、`_handle_ruok_error` 等）。对 `collector.py`/`collectors/__init__.py`
> 暴露的任何符号做重命名或删除会静坏这些模块，且静态检查未必立刻暴露——改动 re-export 表前先
> 全局检索这些名字的消费方。

## 包级约束

- Startup 必须零配置可用；非 ASGI 驱动跳过 ASGI 挂载。
- APScheduler、监控、通知、指标、摘要调度均为 **best-effort**；失败要记录日志且不得拖垮主 Bot。
- 配置统一走 `ScopedConfig`，dotenv 键保持 `RUOK__...`。
- API key（`/ruok/api/*`）与 WebUI 认证相互独立；改动任一侧时不要串用另一侧的守卫。
- 健康聚合值（`available`/`degraded`/`unavailable`）与单项检查值
  （`healthy`/`degraded`/`unhealthy`/`unknown`）是两套语义，不要混用。
- 聊天图片渲染为 opt-in（`chat_render_mode="image"`，默认 `"text"`），渲染/发送失败必须回退到
  相同的可见文本结果；图片发送当前仅支持 OneBot V11，添加其他适配器时不要扩大授权范围。
  `chat_render.py` 只依赖 `config`/`protocol`/`webui.jinja` 的展示层能力，**不得**从
  `collector.py`/`collectors/` 反向 import——展示层不能倒灌依赖采集层。

## 配置约束（config.py）

- `cache_ttl` 必须为有限值且 `>= 0.5` 秒；`webui_session_ttl` 必须为正整数。
- 空值语义：`webui_admin_password` 为空会禁用 WebUI 普通用户登录入口；`api_key` 为空时受保护的
  `/ruok/api/*` 端点返回 401 而非公开（`/health` 除外）。
- 新增配置带合理的 Pydantic 校验边界；不要给每个设置都强加正数下界（注意保留合法的零值语义）。
- `cors_origins` 默认为 `["*"]`（作用于整个 NoneBot 应用的 CORS）是有意的宽松默认，便于本地与
  Tunnel → 本地 Uvicorn 部署；收紧它属于兼容性变更，需用户在部署层自行处理，不要在代码里静默收窄。

## 入口点约束（__init__.py）

- `/ruok reset` 的一次性重置键在群聊中必须私信投递，绝不能在群聊泄露；`password_reset_platform`
  记录签发平台，不匹配的平台无法兑换。
- `/ruok no` 的权限判断保持既有优先级（crisis → SUPERUSERS → 绑定用户 → 用户白名单 → 群管理/群主 →
  群白名单）；改动时不要调整该顺序。
- JSON API 的客户端输入错误必须保持 400，不得冒泡到应用级 500 处理器（后者会创建 `ruok`
  自监控 Session 并降级内置模块）；新增 `/ruok/api/*` 路由时在模型构造前校验输入。

## JSON API 不变量（api.py）

- 修改 `api.py` 前先核对 `../../docs/architecture.md` 的“API 与 WebUI 路由”一节为**当前源码**语义，
  下列是与正确性强耦合的硬约束：
- `PATCH /sessions/{id}` 只在 `status="unsolved"` 时接受 `affected_plugins`，且必须走
  `confirm_session_plugins(...)`；非法的 status/plugins 组合返回 400 且不改动已存 Session。
- `/metrics/history` 的 `hours` 限定在 `0.5`–`168`。
- `/status` 与 `/health` 共享一份 router-本地 TTL 缓存（含 `/health` 触发的成功采集）；
  API 的 create/update/module 变更只会失效该 **API 侧**缓存，聊天/WebUI/日志引发的变更不会立即失效它——
  不要假设存在单一全局状态快照。
- 保持 `/sessions/stats` 注册在 `/sessions/{session_id}` 之前。
- 任何字段校验/形状调整优先保证向后兼容，否则提供迁移路径（见持久化不变量）。

## 描述性参考

完整架构、持久化数据模型、Session/Module 语义与认证/路由细节见 `../../docs/architecture.md`。
