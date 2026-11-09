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

### 作用域

- **贡献者（无 write 权限）**：工作到「创建 PR」为止。PR 上的 CI 使用只读 token，只做检查，
  不能合并、打标签或发布；本地检查命令与 PR 检查一致，可直接在 fork 中运行。
- **维护者（`owner`/`collaborator`，仓库 write 权限）**：负责合并、打标签与发布。下文「发布」
  小节的命令只适用于这一范围；没有 write 权限时 `gh workflow run` 与推送 `v*` 标签都会被拒绝。
- 发布作业经 `release` environment 运行，该 environment 目前没有 required reviewers：
  拥有 write 权限即等于拥有发布到 PyPI 的能力。

### CI 与合并

- CI 只在 `pull_request` 上运行（`.github/workflows/ci.yml`）。master 的 push 与 bump 提交
  不再触发测试矩阵；合并前的 PR 检查就是对合并结果的检查。
- 这条等价关系要求合并时 PR 与 master 同步。当前 master 未启用分支保护，合并前需由维护者
  保证：`gh pr update-branch <n>`（或本地 rebase 后推送）。
- 矩阵为 Linux 的 Python 3.10 / 3.12 / 3.14，另有 `Prek` 与 `BasedPyright` 两个 job。

### 发布

发布统一走 `.github/workflows/release.yml`，两种入口（均需 write 权限）：

- `gh workflow run release.yml -f bump=auto`（日常路径）：workflow 自行 bump、提交、打标签、
  构建、发布到 PyPI，并创建 GitHub Release；
- 手动推送 `v*` 标签（旧的本地 bump 流程仍然可用）。

```powershell
gh workflow run release.yml -f bump=auto                        # 日常发布
gh workflow run release.yml -f bump=patch -f dry_run=true       # 演练：不推送、不发布、不建 Release
gh workflow run release.yml -f tag=v0.4.3                       # 补发失败的那次发布，不会再 bump
```

- bump 级别：`auto` 读取上一个标签之后合并的 PR 标签，出现 `breaking-change` 取 major，
  否则出现 `enhancement` 取 minor，其余取 patch；也可用 `-f bump=patch` 等直接指定。
- 标签版本必须与 `pyproject.toml` 的 `version` 一致，workflow 在发布前校验。
- workflow 用 `GITHUB_TOKEN` 推送 bump 提交与标签；这类推送不会触发新的 workflow run。
- release-drafter 会占用下一个版本名的草稿；发布步骤按 id 删除同名**草稿**，正式 Release 不受影响。
- 重复触发有守卫：自上一个标签以来没有新提交时，workflow 以 `Nothing to release` 结束，
  后续步骤全部跳过，不会产出空版本；`-f tag=...` 的补发入口不受该守卫影响。
- 重试安全：`uv publish --check-url` 跳过已存在的文件，已存在的 Release 走 `gh release upload --clobber`。
- 改动 `.github/workflows/**` 后先跑 `-f dry_run=true` 演练，再正式发布。
- bump 由自动化完成，普通开发/文档工作不要手动调用 bump 工具。
- 构建后端为 `uv_build`；改动包数据/依赖时用 `uv build` 并检查模板、vendor 静态资源
  与许可证是否随包分发。
