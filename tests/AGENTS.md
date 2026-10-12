# `tests` — Agent 指南

Scope：`tests/`。先读根 `AGENTS.md`。

## 隔离与环境

- `conftest.py` 初始化 NoneBot 并从 `pyproject.toml` 加载插件；在 NoneBot 初始化前把
  `LOCALSTORE_PLUGIN_DATA_DIR` 指向一次性临时目录，使套件**不读写仓库 `data/` 树**。
  修改 fixture 时必须保持该隔离（否则会因 `.env.test` 的 `LOCALSTORE_USE_CWD=true` 让遗留本地
  Session 决定结果）。
- `conftest.py` 的环境选择顺序是 **`.env.dev` 优先、否则 `.env.test`**——这是隔离成立的前提，
  改动 fixture/conftest 时不得颠倒该顺序。异步测试在 session 级事件循环上。
  从 `nonebot_plugin_ruok` 的 import 常放测试函数内部，避免在 NoneBot 初始化前 import 插件。
- 用 `tmp_path` + `webui_test_utils._client()` 获得隔离的 API/auth 存储。

## 命令

聚焦测试与全量命令见 `../docs/testing.md`。要点：

- `uv run pytest` 跑全量；`uv run poe test` 以 xdist 并产出覆盖率/JUnit（coverage 门槛 >= 70%）。
- 裸 `uv run pytest` 未启用覆盖率时不强制该门槛。
- CI 在 Linux Python 3.10–3.14 上跑测试；类型任务用 Python 3.12 + `uvx basedpyright==1.40.0`。

## 变更约束

- 改动涉及 Session 状态语义、模块状态传播、持久化形状、API 契约、WebUI 路由/守卫、认证或
  通知冷却/持久化时，**新增或更新相应回归测试**并跑全量 pytest。
- 测试命名与现有文件对齐（`test_<subsystem>.py`、`test_web_api_<surface>.py`）。

## 描述性参考

测试文件布局说明与聚焦命令清单见 `../docs/testing.md`。
