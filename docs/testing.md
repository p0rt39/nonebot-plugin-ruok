# 测试参考（描述性）

> 本文是测试布局与工具链的描述性参考。约束性条目归口于
> [../tests/AGENTS.md](../tests/AGENTS.md) 与根 [AGENTS.md](../AGENTS.md)。

## 工具链

项目使用 `uv`、`pytest`、`nonebug`、`ruff`、Pyright/BasedPyright、`prek` 与 `typos`。
`prek` 读取 `.pre-commit-config.yaml`；不要假设 `pre-commit` 可执行文件已安装。
Pyright/BasedPyright 与 prek 不是声明的 dev 依赖；CI 对齐的类型检查命令用 `uvx` 自带工具本身。
但它们只解析到项目已同步的环境，因此所有命令都要求先 `uv sync --all-groups`。

常用命令：

```powershell
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uvx basedpyright==1.40.0   # 先同步环境再跑；uvx 自带工具，但仍依赖 .venv 解析导入
uv run pytest
uv run poe test
prek run --all-files
```

聚焦测试：

```powershell
uv run pytest tests/test_sessions.py
uv run pytest tests/test_modules.py
uv run pytest tests/test_collector.py
uv run pytest tests/plugin_test.py
uv run pytest tests/test_chat_render.py
uv run pytest tests/test_web_api.py
uv run pytest tests/test_web_api_auth.py
uv run pytest tests/test_web_api_dashboard.py
uv run pytest tests/test_web_api_sessions.py
uv run pytest tests/test_web_api_modules.py
uv run pytest tests/test_web_api_notifications.py
uv run pytest tests/test_web_api_users.py
```

## 测试布局

- `plugin_test.py`：NoneBot 命令行为与 OneBot V11 交互测试。
- `test_chat_render.py`：图片模板字段与 HTML 转义；命令测试 mock 渲染/发送，
  因此该套件不验证真实浏览器后端。
- `test_collector.py`：指标、tracker、进程/系统采集。
- `test_sessions.py`：Session CRUD、过滤、链接、统计、插件影响索引。
- `test_modules.py`：模块 CRUD 与直接/传播状态推导。
- `test_web_api.py`：通用 API key 与路由回归测试。
- `test_web_api_auth.py`：login/register/bind/remember/reset/auth 守卫。
- `test_web_api_dashboard.py`：admin/user 仪表盘与趋势端点。
- `test_web_api_sessions.py`：Session WebUI/API 行为。
- `test_web_api_modules.py`：模块 WebUI 行为。
- `test_web_api_notifications.py`：通知规则 WebUI 行为。
- `test_web_api_users.py`：用户管理与自助。
- `webui_test_utils.py`：FastAPI/TestClient 辅助。
- `test_config.py`：配置模型与校验边界。
- `test_test_isolation.py`：测试隔离（`LOCALSTORE_PLUGIN_DATA_DIR` 等）保障。
- `fake.py`：假事件构造；`conftest.py`：NoneBot 初始化与夹具。

## 约定与注意

- 测试通过 `tests/conftest.py` 初始化 NoneBot，并从 `pyproject.toml` 加载插件。
- `tests/conftest.py` 在 NoneBot 初始化前把 `LOCALSTORE_PLUGIN_DATA_DIR` 指向一次性临时目录，
  使套件永远不读写仓库 `data/` 树。否则会因 `.env.test` 的 `LOCALSTORE_USE_CWD=true` 让遗留的
  本地 Session 与模块状态决定结果（曾因此出现 4 个陈旧自动 Session 导致 3 个 `/ruok status`
  断言失败）。修改 fixture 时保持该隔离。
- 从 `nonebot_plugin_ruok` 的测试 import 常放在测试函数内部，以避免在 NoneBot 初始化前 import 插件。
  （隔离成立的根因是上一项中 `LOCALSTORE_PLUGIN_DATA_DIR` 先行设置，并非下述 `.env` 选择顺序。）
- `tests/conftest.py` 优先用 `.env.dev`，否则 `.env.test`，并把异步测试放在 session 级事件循环上。
  用 `tmp_path` 加 `webui_test_utils._client()` 获得隔离的 API/auth 存储。连接开发 Bot 配置前先
  检查环境选择。
- `uv run poe test` 以 xdist（`-n auto`）跑 pytest，产出 XML 覆盖率与 JUnit 报告；
  覆盖率门槛 >= 70%。裸 `uv run pytest` 在未启用覆盖率时不强制该门槛。
  CI 在 Linux 的 Python 3.10–3.14 上跑测试；其类型检查任务在 `uv sync --locked` 后用
  Python 3.12 + `uvx basedpyright==1.40.0`。
- 构建用 `uv build`；改动包数据/依赖时检查 Jinja 模板、vendor 静态资源与许可证是否随包分发。
  保持 `uv.lock` 与项目元数据同步。
