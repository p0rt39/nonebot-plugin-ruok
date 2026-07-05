<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ nonebot-plugin-ruok ✨
[![LICENSE](https://img.shields.io/github/license/p0rt39/nonebot-plugin-ruok.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-ruok.svg)](https://pypi.python.org/pypi/nonebot-plugin-ruok)
[![python](https://img.shields.io/badge/python-3.10|3.11|3.12|3.13-blue.svg)](https://www.python.org)
[![uv](https://img.shields.io/badge/package%20manager-uv-black?style=flat-square&logo=uv)](https://github.com/astral-sh/uv)
<br/>
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?style=flat-square&logo=ruff)](https://github.com/astral-sh/ruff)
[![prek](https://img.shields.io/badge/hooks-prek-8A2BE2?style=flat-square)](https://github.com/j178/prek)
[![pre-commit](https://results.pre-commit.ci/badge/github/p0rt39/nonebot-plugin-ruok/master.svg)](https://results.pre-commit.ci/latest/github/p0rt39/nonebot-plugin-ruok/master)

</div>

## 📖 介绍

RuOK 是一个 NoneBot2 **健康监控 + 事件追踪 + WebUI 面板**插件，零侵入设计，无需其他插件配合：

- 🔍 **硬件 & 连接监控**：CPU / 内存 / 磁盘 / 网络 + WS 连接状态 + e2e 延迟
- 📋 **插件内省**：自动扫描所有已加载插件的 Matcher、元信息、加载状态
- 🚨 **日志错误自动捕获**：loguru sink 全局拦截 ERROR，自动创建追踪 Session
- 📝 **手动问题上报**：用户通过 `/ruok no <模块> <描述>` 主动报告，自动捕获上报者信息
- 🔄 **Session 状态机**：`pending(🟡)→unsolved(🔴)→solved(🟢)`，支持忽略误报
- 🌐 **HTTP API + WebUI**：`/ruok/api/*` JSON 接口 + `/ruok` 可视化面板，运行在同一端口

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-ruok --upgrade
使用 **pypi** 源安装

    nb plugin install nonebot-plugin-ruok --upgrade -i "https://pypi.org/simple"
使用**清华源**安装

    nb plugin install nonebot-plugin-ruok --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"


</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details open>
<summary>uv</summary>

    uv add nonebot-plugin-ruok
安装仓库 master 分支

    uv add git+https://github.com/p0rt39/nonebot-plugin-ruok@master
</details>

<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-ruok
安装仓库 master 分支

    pdm add git+https://github.com/p0rt39/nonebot-plugin-ruok@master
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-ruok
安装仓库 master 分支

    poetry add git+https://github.com/p0rt39/nonebot-plugin-ruok@master
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_ruok"]

</details>

<details>
<summary>使用 nbr 安装(使用 uv 管理依赖可用)</summary>

[nbr](https://github.com/fllesser/nbr) 是一个基于 uv 的 nb-cli，可以方便地管理 nonebot2

    nbr plugin install nonebot-plugin-ruok
使用 **pypi** 源安装

    nbr plugin install nonebot-plugin-ruok -i "https://pypi.org/simple"
使用**清华源**安装

    nbr plugin install nonebot-plugin-ruok -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>

## ⚙️ 配置

在 nonebot2 项目的 `.env` 文件中，所有配置项使用 `RUOK__` 前缀。**插件零配置即可加载**，所有配置均有默认值。

### 健康检查

| 配置项 | 类型 | 默认值 | 说明 |
| :----- | :--: | :----: | :--- |
| `RUOK__CHECK_TIMEOUT` | `float` | `5.0` | 健康检查超时时间（秒） |
| `RUOK__WS_DEEP_CHECK_TIMEOUT` | `float` | `3.0` | WS 深度检查超时（秒），`bot.get_status()` 最大等待 |
| `RUOK__CACHE_TTL` | `float` | `10.0` | API 缓存有效期（秒），减少重复采集 |
| `RUOK__ENABLE_DEEP_WS_CHECK` | `bool` | `True` | 是否执行 e2e 延迟检测（`get_status()`） |

### Session 追踪

| 配置项 | 类型 | 默认值 | 说明 |
| :----- | :--: | :----: | :--- |
| `RUOK__SESSION_ENABLED` | `bool` | `True` | 是否启用 Session 系统 |
| `RUOK__AUTO_SESSION_ENABLED` | `bool` | `True` | 是否自动捕获 ERROR 日志生成 Session |
| `RUOK__CRISIS_MODE` | `bool` | `False` | 紧急模式：跳过所有上报权限检查（测试/紧急用） |
| `RUOK__NOTIFY_SUPERUSERS` | `bool` | `True` | 新 Session 是否私聊通知 SUPERUSERS |
| `RUOK__NOTIFY_INTERVAL_HOURS` | `float` | `4.0` | 同一 Session 再次通知的最小间隔（小时） |
| `RUOK__SUMMARY_INTERVAL_HOURS` | `float` | `4.0` | 定时汇总未解决 Session 的间隔（小时） |

### 权限控制

| 配置项 | 类型 | 默认值 | 说明 |
| :----- | :--: | :----: | :--- |
| `RUOK__REPORT_WHITELIST_USERS` | `list[str]` | `[]` | 允许上报问题的用户白名单（QQ号字符串） |
| `RUOK__REPORT_WHITELIST_GROUPS` | `list[str]` | `[]` | 允许上报问题的群白名单（群号字符串） |

> **上报权限优先级**：SUPERUSERS > 白名单用户 > 群管理员/群主 > 白名单群。`crisis_mode=True` 时全部跳过。

### API / WebUI

| 配置项 | 类型 | 默认值 | 说明 |
| :----- | :--: | :----: | :--- |
| `RUOK__CORS_ORIGINS` | `list[str]` | `["*"]` | CORS 允许的源列表 |
| `RUOK__API_KEY` | `str` | `""` | API 密钥（为空则不校验） |

### 配置示例

```dotenv
# .env
RUOK__CHECK_TIMEOUT=10.0
RUOK__CACHE_TTL=30.0
RUOK__ENABLE_DEEP_WS_CHECK=true
RUOK__AUTO_SESSION_ENABLED=true
RUOK__NOTIFY_SUPERUSERS=true
RUOK__REPORT_WHITELIST_USERS=["123456789", "987654321"]
RUOK__REPORT_WHITELIST_GROUPS=["1000000"]
```

## 🎉 使用

### 聊天指令

| 指令 | 权限 | 范围 | 说明 |
| :--- | :--- | :--- | :--- |
| `/ruok` | 所有人 | 群聊/私聊 | 显示帮助信息 |
| `/ruok status` | 所有人 | 群聊/私聊 | 查看所有模块实时状态（🟢🟡🔴） |
| `/ruok no <模块> <描述>` | 白名单/群管/SUPERUSER | 群聊/私聊 | 手动上报问题，创建 Session |
| `/ruok list` | 所有人 | 群聊/私聊 | 列出活跃 Session（最多 10 条） |
| `/ruok lookup <id>` | 所有人 | 群聊/私聊 | 查看 Session 详情（含重复次数、关联） |
| `/ruok confirm <id>` | SUPERUSER | 群聊/私聊 | 确认问题 → `pending→unsolved` |
| `/ruok solve <id>` | SUPERUSER | 群聊/私聊 | 标记已解决 → `solved` |
| `/ruok ignore <id>` | SUPERUSER | 群聊/私聊 | 忽略误报 → `ignored` |

### HTTP API

所有接口前缀 `/ruok/api`，返回 JSON。

| 端点 | 方法 | 说明 |
| :--- | :--: | :--- |
| `/ruok/api/status` | GET | 聚合健康状态（硬件+连接+插件+模块） |
| `/ruok/api/health` | GET | 简单探针，200 或 503（K8s / Docker / Uptime） |
| `/ruok/api/connections` | GET | 仅 WS 连接状态（轻量） |
| `/ruok/api/sessions` | GET | Session 列表，支持 `?status=&module=&reporter=` 过滤 |
| `/ruok/api/sessions` | POST | 创建 Session（WebUI 用） |
| `/ruok/api/sessions/{id}` | GET | Session 详情 + 全部 Occurrence |
| `/ruok/api/sessions/{id}` | PATCH | 更新 status / developer_notes |
| `/ruok/api/modules` | GET | 模块列表（含实时派生状态） |
| `/ruok/api/modules/{name}` | GET / PUT / DELETE | 模块 CRUD |

### WebUI

启动机器人后，浏览器访问 `http://<host>:<port>/ruok` 即可打开可视化面板。

### 🎨 效果图

> 待补充
