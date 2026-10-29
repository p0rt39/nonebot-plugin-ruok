# RUOK — Agent 指南（根）

`nonebot-plugin-ruok` 是一个 NoneBot2 插件，提供健康监控、异常 Session 追踪、
插件/模块影响传播、通知规则、FastAPI JSON API、SSR WebUI 与基于角色的 WebUI 账号。

本文件只包含**全仓库通用**的关键指令，以及**阅读各级子目录特化 AGENTS.md 的要求**。

## Scoped 阅读要求（强制）

在修改任何文件**之前**，先阅读与该目录对应的各级 `AGENTS.md`（从本根文件向下逐级深入）：

- 包级约束：`src/nonebot_plugin_ruok/AGENTS.md`
- 采集器与持久化：`src/nonebot_plugin_ruok/collectors/AGENTS.md`
- WebUI 路由/认证/SSE：`src/nonebot_plugin_ruok/webui/AGENTS.md`
- SSR 模板与前端契约：`src/nonebot_plugin_ruok/webui/templates/AGENTS.md`
- 测试：`tests/AGENTS.md`

深层 `AGENTS.md` 只补充其所在子树的定向约束，不重复上层内容；若上下层表述冲突，以更具体（更深）者为准，
但不得违反本根文件的安全、兼容与发布底线。`docs/` 下是描述性参考（非权威指令），正文以链接进入，
约束仍以各级 `AGENTS.md` 为准。

## 开发工作流（L1nk Workflow）

实现类改动遵循以下顺序，除非用户明确收窄任务范围：

```text
modify -> ruff -> pyright -> focused pytest -> (full pytest) -> [本地] 同步测试环境 -> git commit -s
```

- 依次执行 `uv run ruff check .`、`uv run ruff format --check .`，再做类型检查：本地用 `uv run pyright`；
   CI 对齐用 `uvx basedpyright==1.40.0`。修复全部报告项。
- 先跑变更区域的聚焦 pytest；改动涉及共享行为、API 契约、WebUI 路由、认证、存储或 Session 状态语义时跑全量 pytest。
- 用户环境包含一个本地测试 Bot；涉及运行时验证时按用户说明**本地**同步并人工验收（具体路径/方式属本地环境，不在本仓库指令中固化）。
- 完成后本地 `git commit -s`；除非用户明确要求，不要 push 到远端。
- 仅文档改动也至少跑 `ruff` 与 `pyright` 这两个能捕捉仓库级问题的低成本检查，并按用户要求决定是否提交。

## 编码规范

- 优先在拥有该行为的子系统内做小范围、定向的修改。
- 严格遵守 Pylance 与 Ruff；把静态告警、lint、import 顺序、格式与异步风格问题当作必须处理的工作。
- 用既有模型（`protocol.py`/`config.py`/`webui/auth.py` 等处的 Pydantic 模型），不要临时拼凑 dict 契约。
- 持久化时间戳使用时区感知的 UTC datetime。
- 不要阻塞事件循环；psutil 等阻塞工作继续用 `asyncio.to_thread` 等方式。
- 面向外部的辅助子系统（指标、通知、SSE、摘要调度）按 best-effort 处理，不应让主插件崩溃。
- 若为保住运行行为而捕获宽泛异常，意外错误要视情况走 `_handle_ruok_error`；日志 sink 不得递归上报自身异常。
  预期的校验/未找到错误应返回客户端错误而非产生自监控 Session。
- 注意 `matcher.finish()` 会抛 NoneBot matcher 异常。
- 新的用户可见配置放 `ScopedConfig`，保持 dotenv 键为 `RUOK__...`。
- 对不清楚或说明不足的事项向用户提问澄清——尤其是行为、兼容性、数据安全、测试范围、发布流程
  或不受版本控制的文件。
- 不要随意删除不受版本控制的文件；删除任何未跟踪、被忽略、生成物或本地环境文件前，需向用户明确二次确认。

## 发布与合规约束

详见 `docs/publishing.md`；要点：

- 保持项目/模块命名 `nonebot-plugin-ruok` / `nonebot_plugin_ruok`。
- 依赖用宽松区间；运行期 import 须在 `pyproject.toml` 声明；适配器按需显式声明。
- 运行期数据读写经 `nonebot-plugin-localstore`。
- 插件须零配置可加载；缺少配置时优雅降级。
- 异步路径禁同步阻塞；跨插件依赖先 `require()`。
- 保留 `__init__.py` 的 `PluginMetadata`；README 继续提供 `nb plugin install`。
- CI 与发布：`prek` 与 `typos` 在 pre-commit/CI 中执行，勿跳过；打 `v*` 标签触发 release，
  标签版本必须与 `pyproject.toml` 的 version 一致，版本号工具会自动提交并打标签。

## 全局不变量

仅列跨子树的底线；具体约束见对应 scoped AGENTS.md：

- 持久化数据保持向后兼容：不随意改 JSON 形状，不做破坏性格式变更（细节见
  `src/nonebot_plugin_ruok/collectors/AGENTS.md`）。
- 运行期数据由 `nonebot-plugin-localstore` 定位，文件写采用原子替换原则。
- 两条认证边界（JSON API 的 API key 与 WebUI 登录）相互独立。

## 描述性参考（docs/）

- `docs/architecture.md`：项目结构、运行时架构、持久化、Session/Module 语义、认证与 API 路由总览。
- `docs/webui-frontend.md`：模板清单与响应式/实时/无障碍前端契约。
- `docs/testing.md`：测试布局、命令、coverage 与 conftest 约定。
- `docs/publishing.md`：面向 NoneBot 商店的发布合规约束。

权威指令在各级 `AGENTS.md`；`docs/` 为描述性参考。
