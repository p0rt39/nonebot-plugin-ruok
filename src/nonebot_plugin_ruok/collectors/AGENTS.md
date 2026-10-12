# `collectors` — Agent 指南

Scope：`src/nonebot_plugin_ruok/collectors/`。先读根 `AGENTS.md` 与包级 `../AGENTS.md`。

## 各 collector 职责

- `sessions.py`：文件支持的 Session CRUD、过滤、链接、`confirm_session_plugins`、
  插件影响索引重建、统计、内部“异常转 Session”处理、SSE 事件发布。
- `modules.py`：模块 CRUD、内置模块迁移、显示名解析、直接与传播的模块状态推导。
- `metrics.py`：psutil 系统检查、bot 信息、连接检查、插件清单、聚合状态与历史点生成。
- `monitor.py`：捕获 loguru 与选定 stdlib 的 ERROR/CRITICAL 并创建自动 Session。
- `notifications.py`：规则、冷却、bot DM、webhook、可选的 APScheduler 摘要通知。
  新 Session 的即时通知由 `NotificationRule` 与 `notification_enabled` 负责；摘要调度独立。
- `trackers.py`：网络与磁盘 I/O 的增量速率辅助。
- `storage.py`：共享文件锁与原子写辅助（per-path 锁、同目录临时文件、`os.replace()`）。

## 持久化不变量（不可违反）

运行数据存放于 localstore 插件数据目录：

- `sessions/{session_id}.json`、`plugin_impacts.json`、`modules.json`、`notification_rules.json`、
  `notification_cooldowns.json`、`metrics/YYYY-MM-DD.json`。
- Session ID：新值为 `ruok-{8 位小写 hex}`；读取兼容 legacy `ruok-[A-Za-z0-9_-]{1,64}`。
  保留文件名合法性与解析路径容器内校验。链接组为 `ruok-grp-{8 hex}`。
- 内置模块：`name="ruok"`、display `RUOK`、默认插件 `nonebot_plugin_ruok`；缺失时由
  `list_modules()` 重建并迁移旧默认值。
- `Session` 是状态与插件影响的事实源；`plugin_impacts.json` 是可重建缓存，不作事实源。
- `affected_plugins` 记录已确认的插件影响；缺失 legacy 值须校验为空列表。
- 自动错误 Session 按 `error_signature`（plugin/exception/message，message 截断 100 字符的短 MD5）
  在 pending/unsolved 期间去重；关闭匹配 Session 后允许下一次再建。
- 不随意改 JSON 形状；字段变更保持向后兼容校验或提供迁移路径。
- Session 创建/更新/链接及自动 Session 的 check-and-write 由按数据目录键控的进程级锁保护；
  其余文件用 per-path 锁 + 同目录临时文件 + `os.replace()`。
- 指标 JSON 解析失败时记录并移到 `.corrupt-*` 隔离，再写当日新文件。
- 通知冷却的 check-and-claim 与失败释放用同一 per-file 进程锁与原子替换；锁不跨进程/多 worker。

## 语义约束

- Module 状态在查询时推导：直接 `unsolved` → `unavailable`；直接 `pending` → `degraded`；
  pending 经当前插件传播 `degraded`；confirmed 仅经持久化 `affected_plugins` 传播 `unavailable`；
  `unavailable` 优先；`list_module_related_sessions()` 合并直接与经共享插件影响的 Session 并去重。
- 设置 `affected_plugins` 必须走 `confirm_session_plugins(...)`。
- 通知规则按状态与直接模块名匹配；冷却键为 `rule.name:session.module_name`；
  dispatch 先原子声明再发送，全通道失败才释放声明。
- 指标/通知/自动采集保持 best-effort，不得拖垮主插件。

## 描述性参考

完整的存储布局与状态语义推导见 `docs/architecture.md`。
