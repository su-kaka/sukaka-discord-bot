# AGENTS.md — AI 协作规范

## 项目简介

单进程 Discord 机器人（Python 3.13 + discord.py 2.x），`bot.py` 为唯一入口，模块各自绑定固定频道。详见 [docs/overview.md](docs/overview.md) 及各模块文档。

## Python 环境（重要）

项目自带虚拟环境 `.venv`（Windows，Python 3.13）。**所有 Python 命令一律使用：**

```powershell
& .venv\Scripts\python.exe <args>
```

- 运行机器人：`& .venv\Scripts\python.exe bot.py`
- 语法检查：`& .venv\Scripts\python.exe -m py_compile <file.py>`
- 安装依赖：`uv sync`（venv 由 uv 创建，**没有 pip**，`-m pip` 不可用）
- 短代码片段：`& .venv\Scripts\python.exe -c "<code>"`

不要用裸 `python` / `pip`——系统 PATH 里的解释器可能不是本项目环境。

## 常用命令

| 任务 | 命令 |
| --- | --- |
| 运行 | `& .venv\Scripts\python.exe bot.py` |
| 语法检查 | `& .venv\Scripts\python.exe -m py_compile bot.py` |
| 依赖安装 | `uv sync` |

项目由 **uv** 管理（`uv.lock` + `pyproject.toml`），`.venv` 内无 pip。项目无测试套件、无 linter 配置；改动后至少跑 `py_compile` 验证语法。运行机器人需要 `.env` 中的 `DISCORD_TOKEN`（见 `.env.example`）。

## 代码约定

- 日志统一 `print()`，不用日志框架；异常就地 `try/except`，不允许异常杀死无限循环任务。
- 只允许 `bot.py` 注册 `on_message`；消息类功能通过 `bot.register_message_handler(频道ID, 处理器)` 接入。
- 新功能模块暴露 `start_xxx(bot)` 函数，由 `bot.py` 的 `on_ready` 启动（带幂等标志）。
- 频道 ID 硬编码在各模块文件顶部常量（roulette 的在 `roulette/constants.py`）。
- 本地 SQLite 数据库统一经 `paths.py` 的 `data_file()` 解析（默认 `data/` 目录），不要在模块里手写路径。
- Python 版本下限见 `pyproject.toml`（>=3.13），可用 3.13+ 语法。

## 文档

改动模块行为后，同步更新 `docs/` 下对应文档（模块文档、`docs/overview.md`）；README 中的环境变量/行为说明也要保持一致。
