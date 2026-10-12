# `webui/templates` — Agent 指南

Scope：`src/nonebot_plugin_ruok/webui/templates/`（SSR Jinja 模板）。先读根 `AGENTS.md`、包级
`../../AGENTS.md` 与 `../AGENTS.md`（webui）。

## 一致性约束

- 列表与详情 partial 的卡片/操作按钮行为保持一致；Session 插件影响编辑需同时更新
  `_session_card.html.jinja2`（被 `_sessions_list.html.jinja2` 引入的行模板）与
  `_session_detail_content.html.jinja2`。
- 使用共享 Jinja 环境（`autoescape=True`）；状态徽章用 `_status_tone`，列表摘要用 `_session_summary`
  （均在 `../jinja.py`）。

## `status-table` / 响应式契约

- 状态表用 `status-table` / `status-table-scroll`；每个表体单元格必须带 `data-label`。
- 600px 以下堆叠为“标签-值”卡片；600px 以上保留真实 `<table>` 与表头。
  不要对手机尺寸重引入 40rem 最小宽度；横向兜底滚动容器须 `max-width: 100%` 且不传给 `body`。

## 实时更新 / 图表边界

- Dashboard 的可轮询区域标记 `.panel`；实时指示器为 `#live-status`。标签页隐藏时关闭 EventSource、
  返回时重连一次——每个可见性周期不得新建第二个 EventSource 或 Chart 实例。
- 图表原地更新，不要用 HTMX 替换 canvas。
- 动态主机/进程/磁盘值经 DOM 节点 + `textContent` 写入，不拼 `innerHTML`。

## 表单与可达性契约

- 表单用共享 `[data-form-status]` 区域（`role="status"` + `aria-live="polite"`）；
  `.session-row` 内的表单有意排除以保住 Session 行网格，新增行内 action 时保留该排除。
- 可展开磁贴用 `data-expandable` + `role="button"` + `aria-expanded` + 共享 click/keydown；
  `.sys-tile[role="button"]` 需配合恢复卡片表面/文字/内边距的样式。
- 装饰性 `.status-dot` 带 `aria-hidden="true"`；其含义必须同时以文字存在（无文字 chip 用
  `.visually-hidden`）。
- 主题解析跟随 `localStorage['ruok-theme']`，否则 `prefers-color-scheme`；`--ruok-text` 必须随
  每个主题与 `--ruok-surface` 配套定义。

## Session 详情折叠

- Traceback 渲染在默认展开的 `details.detail-fold`，`data-copy-target` 指包裹元素 id
  （`session-traceback-text`）；>600 字符的描述同样折叠。
- 共享剪贴板处理优先 `navigator.clipboard`，失败回退选区 + `execCommand`。
- `.detail-fold` 不得再画第二个展开箭头（Pico 已在 `summary` 提供）。

## 描述性参考

完整模板清单与契约细节见 `docs/webui-frontend.md`。
