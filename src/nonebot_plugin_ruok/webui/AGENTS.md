# `webui` — Agent 指南

Scope：`src/nonebot_plugin_ruok/webui/`（路由、认证、SSE、Jinja）。先读根 `AGENTS.md` 与包级
`../AGENTS.md`。模板相关约束见 `templates/AGENTS.md`。

## 路由与守卫

- JSON API（`../api.py`）的 API key 与 WebUI 登录认证相互独立；不要混用守卫。
- HTMX 路由在 `/ruok/_partials/*` 与 `/ruok/_actions/*`。改动 WebUI action 要显式验证角色守卫：
  admin-only 依赖 admin 守卫；普通用户账号/报告 action 用 login/bound-user 守卫。
- 对缺失 Session 的 action 返回 404（与详情页同一 fragment），不要抛进全局 500 处理器；
  `session-note`/`unlink` 不得对未触达目标报告成功。
- 模块/通知删除的 `redirect_to` 经 `_safe_redirect_target()`：仅接受本站 `/ruok` 路径，
  其余回退端点默认值（防开放重定向）。
- 模块/通知详情用 `{name:path}` 与 URL 编码支持特殊字符；保留 `_path_quote` 与编码。

## 认证（scope 含 `auth.py`）

- WebUI 认证独立于 API key：内置虚拟 `admin`（密码 `RUOK__WEBUI_ADMIN_PASSWORD`），不入
  `webui_users.json`；普通用户注册后的一次性 `auth_key`（10 分钟）经 `/ruok bind` 绑定。
- 平台身份：绑定、上报、归属、重置与 Session 可见性使用精确 `(platform, user_id)`；
  缺失/空白 platform 按失败关闭；legacy `bound_qq` 仅作迁移展示，不构成活跃授权。
- 已绑定且平台 ID 当前在 `SUPERUSERS` 的普通用户动态成为 WebUI admin；移除后即失效。
- 密码用加盐 PBKDF2-HMAC-SHA256；remember token 存哈希 + credential stamp，30 天过期，
  密码变更即撤销；签名会话 cookie 与 remember token 是两套机制。
- remember token 的恢复校验独立于 `webui_secret_key`（按 token 自身哈希 + credential stamp
  校验，`auth.py` 恢复路径）；仅轮换 `webui_secret_key` **不会**撤销已发放的 remember token，
  也不等于强制全员重新登录。
- `webui_users.json` 与 `webui_remember_tokens.json` 的写遵循进程内锁 + 原子替换。

## SSE / 实时约束

- `EventBus` 进程内、每订阅者 256 项队列，满了即丢；不是持久日志或多 worker 传输。
- SSE 循环用 `ceil` 夹每 tick（<1s 也至少一个 tick）；快速指标在带重叠保护的后台任务采集，
  约每三个 tick 附进程快照；完整状态按自身 TTL 节奏采集。
- SSE 消费端对 `cache_ttl` 做 `max(1, ceil(...))` 下限保护，直接传入的非法非正值也不会造成忙循环
  （`cache_ttl` 的取值校验归属 config，见包级 `../AGENTS.md`）。
- `sse_public=True` 会同时放行 `/ruok/sse` 的**登录与 admin 守卫**（`router.py` 的 SSE 入口在
  `config.sse_public` 为真时跳过 `current_user`/角色检查），会公开实时的主机/进程数据；
  只在你确知后果时才启用该开关。
- 状态变更与自动捕获发布事件；仅备注/链接或同状态影响编辑不一定发布。改动时保留清理与
  线程/事件循环边界。

## 安全边界

- Dashboard 的主机/进程/磁盘元数据经 DOM 节点 + `textContent` 渲染，不得拼进 `innerHTML`。
- 使用共享 Jinja 环境（`autoescape=True`）。
- vendor 静态资源经路径受控路由提供；无 Node/SPA 构建管线。

## 备注

- 仓库历史中存在一个未入库的遗留独立前端（`webui/dist/index.html`），干净克隆里**不存在**；
  它不是现行体系的一部分。改动 WebUI 一律以 `templates/`（SSR/Jinja）为准。
- `static/vendor/` 存放 Pico/htmx/Idiomorph/Chart.js 及其许可证，经路径受控路由提供；无 Node/SPA 构建。

## 描述性参考

模板清单与前端契约见 `../../../docs/webui-frontend.md`；路由总览见 `../../../docs/architecture.md`。
