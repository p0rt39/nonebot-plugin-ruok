# RUOK 架构参考（描述性）

> 本文是 **描述性参考**，不是权威指令。约束性的“必须遵守”条目请见各级
> [AGENTS.md](../AGENTS.md)。源代码与测试始终是最终事实来源；本文在实现前可作为
> 导览，若与源码冲突以源码为准。

## 项目形态

- 包根：`src/nonebot_plugin_ruok/`
- 插件入口：`src/nonebot_plugin_ruok/__init__.py`（`__plugin_meta__`、config、`/ruok` 命令、startup 挂载）
- 配置模型：`config.py`（`ScopedConfig`，dotenv 前缀 `RUOK__`）
- 领域模型：`protocol.py`
- JSON API：`api.py`（前缀 `/ruok/api`）
- Collector 兼容层：`collector.py`（re-export + `MetricsStore`）
- Collector 实现：`collectors/`
- WebUI 路由/认证/SSE/Jinja：`webui/`
- SSR 模板：`webui/templates/`
- 测试：`tests/`

## 运行时架构

`__init__.py` 负责与 NoneBot 集成：

- 定义 `__plugin_meta__`，读取 `get_plugin_config(Config).ruok`。
- import 期 `require("nonebot_plugin_localstore")` 与 `nonebot_plugin_htmlrender`。
- 注册 `/ruok` 子命令：`no`、`bind`、`reset`、`status`、`list`、`lookup`、
  `confirm`、`solve`、`ignore`、`raise`/`test`。
- 跟踪 bot connect/disconnect；ASGI/FastAPI 驱动上安装 CORS、SessionMiddleware 与全局异常处理。
- startup 时挂载 auth 路由、`/ruok/api/*`、WebUI 路由，启动 `LogMonitor`、初始化通知规则、
  尽力注册 APScheduler summary 任务。

**约束**：startup 必须零配置可用；非 ASGI 驱动跳过 ASGI 挂载；APScheduler、监控、指标、
通知、摘要调度均为 best-effort，失败必须记录日志并且不得拖垮主 Bot。

## 持久化数据

运行数据存放于 `nonebot-plugin-localstore` 的插件数据目录：

| 文件 | 说明 |
|------|------|
| `sessions/{session_id}.json` | 每个 Session 一个文件 |
| `plugin_impacts.json` | 由活跃 Session 重建的插件影响索引（是缓存，非事实源） |
| `modules.json` | `ModuleDefinition` 列表 |
| `notification_rules.json` | `NotificationRule` 列表 |
| `notification_cooldowns.json` | 冷却时间戳与在途声明 |
| `metrics/YYYY-MM-DD.json` | 每日 `MetricPoint` 数组 |
| `webui_users.json` | WebUI 普通用户、密码哈希、绑定键、平台绑定、重置键 |
| `webui_remember_tokens.json` | 可撤销的 30 天记住登录 token |

### 不变量

- 新生成的 Session ID 为 `ruok-{8 位小写 hex}`；读取同时兼容 legacy
  `ruok-[A-Za-z0-9_-]{1,64}`。保留文件名合法性校验与解析路径的容器内检查。
- 链接组为 `ruok-grp-{8 hex}`。
- 内置模块 key=`ruok`、显示名 `RUOK`、默认插件 `nonebot_plugin_ruok`；缺失时由
  `list_modules()` 重建并迁移旧的默认标签/空插件列表，同时保留自定义标签。
- `Session` 是 status 与 plugin impact 的事实源。
- `Session.affected_plugins` 记录已确认的插件级影响。
- 缺失 legacy `affected_plugins` 时必须校验为空列表。
- 自动错误 Session 在 pending/unsolved 期间按 `error_signature` 去重；
  签名是 plugin/exception/message（message 截断 100 字符）的短 MD5，不是时间窗去重键。
  关闭匹配 Session 后，下一次同类错误可再建新 Session。
- 不要随意改 JSON 形状；字段变更要么保持向后兼容校验，要么提供迁移路径。
- Session 创建/更新/链接及自动 Session 的 check-and-write 由按数据目录键控的进程级锁保护；
  其余文件使用 per-path 锁 + 同目录临时文件 + `os.replace()` 原子替换。
- 指标 JSON 解析失败时记录并移到 `.corrupt-*` 隔离文件，再写新的当日文件。
- 通知冷却的 check-and-claim 与失败释放使用同一 per-file 进程锁与原子替换；这些锁不跨进程。

锁只覆盖当前进程；WebUI 请求处理与后台任务共享它，但同一数据目录上的多进程并不互斥。

## Session 与 Module 语义

Session 状态：`pending`、`unsolved`、`solved`、`ignored`。

Module 状态在查询时推导：

- 直接 `unsolved` Session → 该模块 `unavailable`。
- 直接 `pending` Session → 该模块 `degraded`。
- Pending 通过当前挂在源模块上的所有插件传播 `degraded`。
- Confirmed 仅通过持久化的 `affected_plugins` 传播 `unavailable`。
- 无插件确认是合法的：Session 变 `unsolved`，但只有源模块直接不可用。
- `unavailable` 优先于 `degraded`。
- 解决/忽略一个 Session 只移除该 Session 的贡献；其他活跃的直接/传播 Session 仍生效。
- `list_module_related_sessions()` 返回直接 Session + 通过共享插件影响该模块的 Session，并去重。

`confirm_session_plugins(data_dir, config, session_id, plugins)` 用于任何设置
`affected_plugins` 的操作：修剪、拆分、去重、按源模块当前插件列表校验，状态置 `unsolved`，
并重建 `plugin_impacts.json`。

`update_session()` 在保存前校验整个候选模型；`solved`/`ignored` 设置 `resolved_at`，
`pending`/`unsolved` 清空。存储层不强制迁移图。
列表按 `(last_seen_at, file mtime_ns, session_id)` 降序。HTTP 日期过滤用
`parse_filter_datetime()`：naive 视为 UTC，带偏移归一到 UTC，非法输入返回 400。搜索包含
持久化的 affected plugins。链接合并组、拒绝自链接、不合并状态。

模块 name 与 display name 是不同字段；聊天解析优先精确 display-name 匹配，再回退内部名。
WebUI 重命名当前是“删除旧定义 + upsert 新定义”，不迁移既有 Session 的 `module_name` 与通知选择器；
删除同样无级联。实现 rename/delete 前要考虑这些引用，尤其是经由“当前模块映射”的 pending 影响传播。

## 认证与权限

WebUI 认证独立于 `/ruok/api/*` 的 API key。

- 内置 WebUI admin 用户名为 `admin`，密码为 `RUOK__WEBUI_ADMIN_PASSWORD`。
- `admin` 是虚拟的，不存于 `webui_users.json`。
- 配置了 admin 密码后，普通用户可通过 WebUI 注册；注册生成 10 分钟一次性 `auth_key`。
- 用户在聊天中发送 `/ruok bind <auth_key>` 完成绑定；绑定后可在 WebUI 提交手动报告。
- `/ruok reset` 为已绑定普通用户签发 10 分钟一次性密码重置键；群聊中重置键必须私信投递，
  绝不能在群聊泄露。`password_reset_platform` 记录签发平台；缺失或不匹配的平台无法兑换。
  绑定/换绑与清除绑定会撤销未使用的重置键。
- 平台用户 ID 当前在 NoneBot `SUPERUSERS` 中的已绑定普通用户，动态成为 WebUI admin；
  从 `SUPERUSERS` 移除后即失去该动态 admin。
- 用户名按 `strip().casefold()` 归一化以判重；密码使用加盐 PBKDF2-HMAC-SHA256；
  remember token 存哈希 + credential stamp，30 天过期，密码变更后撤销。
  待消费的绑定键保留可显示值到被消费/过期；重置键只存哈希。
  Legacy `bound_qq` 以“无平台”的 `bound_user_id` 加载，仅作迁移用途；在用户通过
  `/ruok bind <auth_key>` 重新绑定当前平台前不构成活跃绑定。
- 签名会话 cookie 与持久化 remember token 是两套机制。remember-token 恢复独立校验其哈希与
  credential stamp，与 `webui_secret_key` 无关；仅轮换会话密钥并不等于撤销 remember token。
  README 的“重启即失效”表述比当前实现更宽泛。

`/ruok no` 的报告权限优先级：

1. `crisis_mode=True`
2. NoneBot `SUPERUSERS`
3. 已绑定 WebUI 用户
4. `RUOK__REPORT_WHITELIST_USERS`
5. 群管理员或群主
6. `RUOK__REPORT_WHITELIST_GROUPS`

身份与兼容策略：

- 基于绑定的上报、归属、密码重置与 WebUI Session 可见性使用同一精确 `(platform, user_id)` 身份。
  聊天侧传 `Bot.type`；WebUI 上报用绑定平台；列表过滤与 owner 校验比较两字段。
  新绑定要求非空 platform；缺失/空白 platform 的查询按失败关闭。
  `list_sessions` 保持既有位置参数顺序，`reporter_platform` 追加在末尾。
- 无平台的 legacy `bound_qq` 行与绑定仍可读取以便迁移展示，但不参与平台授权、密码重置、
  动态 admin 或 Session 可见性。用户需在 WebUI 生成新绑定键并在当前平台完成 `/ruok bind`；
  新绑定替换 legacy 身份。legacy 账号忘记密码需管理员恢复。
- 无平台或带历史 `webui` 标记的 Session 记录会被保留，但不按 user ID 归并到聊天绑定；
  只有管理员可查看这些二义记录，直到归属被独立确认。即使只有一个匹配，也不要从当前绑定推断迁移。
- 显式 `SUPERUSERS` 与上报白名单保留既有 ID 授权语义。动态 WebUI admin 先要求完整绑定，
  再应用配置的 SUPERUSER ID 授权；legacy 绑定不能晋升。

聊天 Session 可见性：

- `/ruok status` 公开。
- `/ruok list` 给普通用户展示其最近 5 条 Session（不论状态），按
  `(Session.reporter.platform, Session.reporter.user_id)` 与 `(Bot.type, Event.get_user_id())` 匹配。
- `/ruok list` 给 SUPERUSER 展示最近 15 条活跃 Session（`pending`/`unsolved`）。
- `/ruok lookup <id>` 普通用户仅能看自己的 Session，且不应泄露他人 Session 是否存在。
- `/ruok lookup <id>` SUPERUSER 可看任意 Session。
- `/ruok raise [message]` 与 `/ruok test [message]` 是 SUPERUSER 专用的受控故障注入命令，
  通过 `_handle_ruok_error()` 创建 RUOK 自监控 Session。

主要受测适配器是 OneBot V11；绑定记录的是通用 `Event.get_user_id()` 与 `Bot.type`，
上报与归属同时比较两者。除非确有必要，避免使用 OneBot 专属 API。

## API 与 WebUI 路由

JSON API 前缀：`/ruok/api`

- 健康：`/status`、`/health`、`/connections`
- Sessions：`/sessions`、`/sessions/stats`、`/sessions/{id}`、`/sessions/{id}/link/{other}`、
  `/sessions/{id}/linked`
- Modules：`/modules`、`/modules/{name}`
- Metrics：`/metrics/history`

除了最简 `/health` 探针外，所有 JSON API 端点都要求 `RUOK__API_KEY`，并接受
`X-ruok-API-Key` 或 `Authorization: Bearer ...`。配置的 key 为空时受保护端点返回 401 而非公开；
`/health` 无 key 仍可用作存活探针。API key 校验独立于 WebUI 认证，且不影响内部 collector 调用
或 WebUI 路由。

WebUI 页面：

- `/ruok`：dashboard。admin 得完整面板；普通用户得受限面板（模块健康、绑定状态、手动报告、自己的 Session）。
- `/ruok/users`：admin 用户管理与普通用户自助账号页。
- `/ruok/sessions`、`/ruok/sessions/{id}`：admin Session 管理。
- `/ruok/modules`、`/ruok/modules/{name}`：admin 模块管理。
- `/ruok/notifications`、`/ruok/notifications/{name}`：admin 通知规则。
- `/ruok/login`、`/ruok/register`、`/ruok/logout`、`/ruok/reset-password`。
- `/ruok/sse`：admin 专用 WebUI SSE，除非 `RUOK__SSE_PUBLIC=True`。

HTMX 路由在 `/ruok/_partials/*` 与 `/ruok/_actions/*` 下。改动 WebUI action 时要显式验证角色守卫；
admin-only action 依赖 admin 守卫，普通用户账号/报告 action 用对应的 login/bound-user 守卫。

对缺失 Session 的 Session action 返回 404（与详情页同一 fragment），而不是抛给全局处理器；
`session-note`/`unlink` 对未触达目标不得报告成功。模块与通知删除的 `redirect_to` 经
`_safe_redirect_target()`：只接受本站 `/ruok` 路径，其余回退到端点默认值。

保持 `/sessions/stats` 注册在 `/sessions/{session_id}` 之前。JSON API 的 PATCH 仅在
`status="unsolved"` 时接受 `affected_plugins`，随后调用确认 helper；非法的 status/plugin 更新返回 400
且不改动已存 Session。metrics history 的 `hours` 取 0.5 到 168。

JSON API 的客户端输入错误一律是 400 而不是 500：`_read_json_body()` 拒绝畸形 JSON 与非对象 body，
`api_create_session`、`api_update_session`、`api_upsert_module` 在构造模型前校验
`source`、`reporter_type`、`module_name`、`status`、`developer_notes`、`affected_plugins`、
`plugins`、`display_name`、`description`。这一点重要，因为应用级异常处理器会返回 500 并调用
`_handle_ruok_error`，未校验的 payload 会创建 `ruok` 自监控 Session 并把内置模块降级。

`/status` 与 `/health` 共享一个 router 本地 TTL 缓存，包括由 `/health` 发起的成功采集。
`/health` 对不可用或采集失败返回 503，对 available/degraded 返回 200。
`/connections`、WebUI 与 SSE 各自完整采集。API 的 create/update/module 变更会使该 API 缓存失效；
聊天/WebUI/日志变更不会立即使其失效。不要假设存在单一全局状态快照。

HTML GET 守卫基于 `Accept: text/html` 重定向浏览器；非 HTML 请求/action 一般收到 401/403。
WebUI 图表使用 admin 守卫的 `/ruok/_partials/dashboard-trends-data`，与 API key 凭据无关。
模块/通知的 WebUI 详情使用 `{name:path}` 与编码后的 query/form 值以支持特殊字符；保留 `_path_quote`
与 URL 编码。

## NoneBot 与发布约束

适用本仓库的通用 NoneBot 约束（完整清单见 [publishing.md](./publishing.md)）：

- 保持项目/包名为 `nonebot-plugin-ruok` 与 `nonebot_plugin_ruok`。
- 依赖范围用宽松区间（`>=x,<y`），不要在项目依赖里精确锁死。
- 运行期 import 要在 `pyproject.toml` 声明。
- 需要可选插件时先 `require(...)` 再 import（如 `require("nonebot_plugin_localstore")`）。
- APScheduler 集成必须保持 best-effort。
- 异步路径用异步 HTTP 客户端；不要在异步 handler 里加同步网络调用。
- 保留 `__init__.py` 中的 `PluginMetadata`。
- README 必须继续包含 `nb plugin install` 用法。
