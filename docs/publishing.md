# 仓库发布约束（提炼）

> 本文提炼与本仓库直接相关的 NoneBot 商店发布/合规约束，供各级 AGENTS.md 引用。
> 基础教程性内容（Matcher、依赖注入、消息处理等的完整讲解）保留在本地归档
> `.llm/reference/Nonebot_for_LLM.md`，本文件只承载约束，不复述教程。

## 商店审核红线（违反将被自动拒绝）

1. **命名**
   - 项目名（PyPI/GitHub）：`nonebot-plugin-ruok`（词间 `-`）。
   - 模块名（Python import）：`nonebot_plugin_ruok`（词间 `_`）。

2. **依赖管理**
   - 版本用宽松区间 `>=X.Y,<Z.0`（或 `~=`），不要 `==` 锁死。
   - 必须依赖 `nonebot2`；不要添加 V1 的 `nonebot`。
   - 代码里 import 的第三方库都必须列在 `pyproject.toml` 的 `dependencies`。
   - 直接使用的适配器要显式声明（本仓库以 `nonebot-adapter-onebot` 作为可选 extra）。

3. **本地文件存储**
   - 运行期数据读写必须通过 `nonebot-plugin-localstore`。
   - 不用 `open()`、`Path(__file__).parent`、`os.getcwd()` 等直接定位/读写插件数据。

4. **`__plugin_meta__` 完整**
   - 定义在 `__init__.py` 最外层，提供 `name`、`description`、`usage`、`type`
     （`"application"`）、`homepage`、`config`、`supported_adapters`。

5. **零配置可加载**
   - 无预配置时插件能被 import 且不报错；缺配置时优雅降级而非崩溃。

6. **禁同步阻塞**
   - 异步路径使用 `httpx.AsyncClient` 等异步替代品；长时间同步操作会阻塞整个事件循环。

7. **跨插件依赖用 `require()`**
   - import 可选插件前先 `require("nonebot_plugin_xxx")`。

8. **README 安装方式**
   - 必须展示 `nb plugin install nonebot-plugin-ruok`；不要以旧式 `bot.py` 配置方式作为安装路径。

## 版本与发布

- 版本由 `pyproject.toml` 的 `version` 与 bump 配置维护；`v*` 标签触发
  `.github/workflows/release.yml`（标签需与项目版本一致，锁定依赖与覆盖率测试通过
  后才构建、发布并创建 GitHub Release）。
- 版本 bump 工具会自动提交与打标签；普通开发/文档工作不要手动调用它。
- 构建后端为 `uv_build`；改动包数据/依赖时用 `uv build` 并检查模板、vendor 静态资源
  与许可证是否随包分发。
