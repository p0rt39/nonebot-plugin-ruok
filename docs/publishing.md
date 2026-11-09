# 仓库发布约束（提炼）

> 本文提炼与本仓库直接相关的 NoneBot 商店发布/合规约束，供各级 AGENTS.md 引用。
> 本文件只承载约束，不复述教程性内容（Matcher、依赖注入、消息处理等请见官方文档）。

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

- CI 只在 `pull_request` 上运行（`.github/workflows/ci.yml`）。master 的 push 与
  bump 提交不再触发测试矩阵；合并前的 PR 检查就是对合并结果的检查。
- 发布统一走 `.github/workflows/release.yml`，两种入口：
  - `gh workflow run release.yml -f bump=auto`（日常路径）：workflow 自行 bump、
    提交、打标签、构建、发布并创建 GitHub Release；
  - 手动推送 `v*` 标签（旧的本地 bump 流程仍然可用）。
- 演练用 `gh workflow run release.yml -f bump=patch -f dry_run=true`：bump、跑测试、
  构建都会执行，但不推送、不发布、不建 Release。
- 发布中途失败（如 PyPI 已上传、建 Release 失败）时，用
  `gh workflow run release.yml -f tag=v0.4.3` 重跑该标签，不会再 bump 一次；
  `uv publish --check-url` 会跳过已存在的文件。
- bump 级别：`auto` 读取上一个标签之后合并的 PR 标签，出现 `breaking-change` 取
  major，否则出现 `enhancement` 取 minor，其余取 patch；也可用 `-f bump=patch` 等直接指定。
- 标签版本必须与 `pyproject.toml` 的 `version` 一致，workflow 在发布前校验；bump 由
  自动化完成，普通开发/文档工作不要手动调用 bump 工具。
- workflow 用 `GITHUB_TOKEN` 推送 bump 提交与标签，因此 master 若启用保护规则，需要给
  GitHub Actions 留 bypass，否则推送会被拒绝。
- 构建后端为 `uv_build`；改动包数据/依赖时用 `uv build` 并检查模板、vendor 静态资源
  与许可证是否随包分发。
