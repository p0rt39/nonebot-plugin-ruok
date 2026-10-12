# WebUI 前端契约（描述性）

> 本文是 WebUI 前端的描述性参考。约束性条目归口于
> [../src/nonebot_plugin_ruok/webui/templates/AGENTS.md](../src/nonebot_plugin_ruok/webui/templates/AGENTS.md)
> 与 [../src/nonebot_plugin_ruok/webui/AGENTS.md](../src/nonebot_plugin_ruok/webui/AGENTS.md)。
> 模板源码始终是最终事实来源。

## 模板清单

重要 SSR 模板（`src/nonebot_plugin_ruok/webui/templates/`）：

- `base.html.jinja2`：布局、导航、共享 CSS/JS、toast 处理。
- `_auth_shell.html.jinja2`、`login.html.jinja2`、`register.html.jinja2`、
  `reset_password.html.jinja2`：认证相关页面。
- `dashboard.html.jinja2`、`dashboard_user.html.jinja2`：管理员/普通用户仪表盘。
- `_dashboard_*.html.jinja2`：仪表盘卡片、系统指标、图表。
- `sessions.html.jinja2`、`sessions_detail.html.jinja2`、`_sessions_list.html.jinja2`、
  `_session_card.html.jinja2`、`_linked_list.html.jinja2`：Session 列表/详情/操作。
- `modules.html.jinja2`、`modules_detail.html.jinja2`、`_module_form.html.jinja2`、
  `_modules_table.html.jinja2`：模块列表/详情/编辑。
- `notifications.html.jinja2`、`notifications_detail.html.jinja2`、
  `_notification_form.html.jinja2`、`_notifications_list.html.jinja2`：通知规则管理。
- `users.html.jinja2`、`_users_panel.html.jinja2`、`_user_sessions.html.jinja2`：用户管理与自助。

约束：列表与详情 partial 的卡片/操作按钮行为要一致；Session 插件影响编辑需同时更新
`_session_card.html.jinja2`（`_sessions_list.html.jinja2` 引入的单一行模板）与
`_session_detail_content.html.jinja2`。

## 样式与主题

扁平 design token 在 `base.html.jinja2`：`--ruok-*` 颜色，带暗色覆盖。状态徽章用
`_status_tone` 过滤器（`badge-ok/warn/err/muted`）；列表摘要用 `webui/jinja.py` 的
`_session_summary` 过滤器。

Session 列表在路由层分页（`page`，`page_size` 10–100，默认 20）；`/ruok/_partials/sessions`
返回 `HX-Push-Url`，使过滤器与页码在刷新/后退后保留。通知规则表单以复选框提交
`on_status`/`channels`；路由仍兼容旧式逗号字符串。Dashboard SSE 的控制台追踪默认关闭，
仅当 `localStorage['ruok-debug'] === '1'` 时开启。HTMX 请求失败（4xx/5xx）不替换正文，
因此通过 `htmx:responseError` 弹出错误 toast。

## 响应式 + 实时更新契约

模块表（`_modules_table.html.jinja2`）、通知规则表（`_notifications_list.html.jinja2`）与
`modules_detail.html.jinja2` 通过共享的 `status-table` / `status-table-scroll` 契约渲染状态表。每个表体单元格带 `data-label`；
`base.html.jinja2` 在 600px 以下把这些行堆叠成“标签-值”卡片，600px 以上保留真实 `<table>`
与表头行。新增的 host-status 表也要放进 `status-table-scroll` 并给每个单元格 `data-label`；
不要在手机尺寸恢复旧的 40rem 最小宽度。

Dashboard 的可轮询区域标记为 `.panel`，共享脚本在刷新失败时插入 `role="alert"` 的过期数据提示，
恢复后自动清除。实时指示器 `#live-status` 报告 connected/reconnecting/paused/failed；标签页隐藏时
关闭 EventSource，返回时重连一次。因此每个可见性周期都不得新增第二个 EventSource 或 Chart 实例。
SSE `status`/`tick` 载荷带 `collection_ok`、`collection_error`、`collected_at`；采集失败时保留
上一次的 `overall` 与时间戳，客户端不得用失败的载荷推进“collected at”显示。

## 表单反馈契约

`base.html.jinja2` 为每个表单提供共享的 `[data-form-status]` 区域（`role="status"`，
`aria-live="polite"`）。模板未声明时脚本会在提交时创建；FastAPI 422 的 `detail` 载荷经
`describeValidationError` 翻译后再展示；已就地渲染错误的表单抑制错误 toast。
`.session-row` 内的表单被有意排除，以保持紧凑的 Session 列表网格；新增行内 action 时保留该排除。

## 主题与键盘可达性

首屏前脚本按 `localStorage['ruok-theme']`（若已设置）否则按 `prefers-color-scheme` 解析主题，
并保持 `color-scheme` meta 同步；仅在无显式选择时跟踪 OS 变化。`--ruok-text` 是
`--ruok-surface` 的配套前景 token，新增主题时必须同步重定义。可展开的指标磁贴用
`data-expandable` + `role="button"` + `aria-expanded` + 共享 click/keydown 处理，不用内联 `onclick`；
因为 Pico 会把 `[role="button"]` 渲染成实心按钮，`.sys-tile[role="button"]` 规则负责恢复卡片
表面色、文字色与内边距，重设磁贴样式时保持同步。装饰性 `.status-dot` 元素带 `aria-hidden="true"`，
其传达的含义必须同时以文字存在（无文字状态的 chip 用 `.visually-hidden`）。

## Session 详情折叠

`_session_detail_content.html.jinja2` 将 Traceback 渲染在默认展开的
`details.detail-fold` 中，配 `data-copy-target` 按钮，其值为包裹元素 id
（`session-traceback-text`）。超过 600 字符的描述同样折叠，较短描述保持内联。
`base.html.jinja2` 的共享剪贴板处理优先用 `navigator.clipboard`，失败时回退到选区 +
`execCommand`。Pico 在 `summary` 上绘制展开箭头，因此 `.detail-fold` 相关规则不得再添加第二个标记。
回归覆盖在 `tests/test_web_api_sessions.py`。

## SSE / 图表 / 静态资源边界

Dashboard 的动态值经由 SSE 到达；磁盘与进程元数据通过 DOM 节点和 `textContent` 渲染，编辑这些路径时
保持该边界。`cache_ttl` 在到达 SSE 循环前被校验为至少 `0.5` 秒的有限值；直接调用 SSE 者也被
用 `ceil` 夹到至少一个 tick，非法的非正值不会造成忙循环。

SSE 循环用 `ceil` 把 TTL 向上取整，故低于 1 秒的值仍每次 tick 发出。它约每秒发一个 `tick`，
在带重叠保护的后台任务里采集快速指标，并约每三个 tick 附带一次进程快照。完整状态按自身 TTL
节奏采集。`EventBus` 是进程内的，每个订阅者 256 项队列；队列满即丢弃事件，Session 事件在 tick 时
排空。它不是持久事件日志，也不是多 worker 传输。状态变更与自动捕获会发布事件；仅备注/链接的写入
与同状态的影响编辑不一定发布。改动时保留清理逻辑与线程/事件循环边界。

使用共享 Jinja 环境（`autoescape=True`）。Pico CSS 2.1.1、htmx 2.0.8、Idiomorph 0.7.4、
Chart.js 4.5.1 vendor 在 `webui/static/vendor/` 下并附带许可证，经路径受控路由提供。当前没有
Node/SPA 构建管线。图表原地更新，不要用 HTMX 替换 canvas 元素；改动列表/详情 action 时在真实浏览器
中验证 DOM 替换行为。
