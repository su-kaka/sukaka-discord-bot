# 项目总览

sukaka-discord-bot 是一个单进程 Discord 机器人（Python 3.13 + discord.py 2.x），由 `bot.py` 作为唯一入口，挂载三个互不干扰的功能模块，每个功能各自绑定一个固定频道。

本文档目录为每个模块都提供了独立说明，按需查阅即可，无需通读全部源码：

| 文档 | 内容 | 对应代码 |
| --- | --- | --- |
| [bot.md](bot.md) | 主入口、启动流程 | `bot.py` |
| [carousel.md](carousel.md) | 频道轮播消息 | `carousel.py` |
| [channel-admin.md](channel-admin.md) | 斜杠命令：禁言投票、删除/置顶消息 | `channel_admin.py` |
| [mama.md](mama.md) | 找妈妈：家庭组共享登记（全局斜杠命令，ephemeral 仅本人可见） | `mama.py` |
| [family-group-guide.md](family-group-guide.md) | 家庭组教程：Gemini Pro 家庭组共享步骤总结（`/家庭组教程` 命令的文案来源） | — |
| [extending-guide.md](extending-guide.md) | **扩展指南：新增频道功能该怎么改** | — |

## 快速上手

```bash
# 安装依赖（项目自带 .venv）
pip install -r requirements.txt

# 配置环境变量（项目根目录 .env）
DISCORD_TOKEN=你的机器人Token

# 启动
python bot.py
```

## 功能与频道一览

| 功能 | 监听频道 ID | 触发方式 | 所需权限/Intents |
| --- | --- | --- | --- |
| 频道轮播（carousel） | `1455038454772531311` | 定时任务，每分钟 | 无特殊要求 |
| 找妈妈（mama） | `1455038454772531311` | 斜杠命令 `/登记妈妈` `/找妈妈` `/家庭组教程`（仅限该频道，全部 ephemeral 仅发起者可见）；管理命令 `/拉黑` `/解除拉黑` `/拉黑列表`（不限频道，仅限 `BLOCK_COMMAND_USER_IDS`） | 无特殊要求（斜杠命令） |
| 管理命令（channel_admin） | `1293095144806940738` | 斜杠命令 `/mute_vote` `/delete_message` `/mark_message` `/unmark_message` | manage_roles、manage_messages |

## 外部依赖

- **SQLite 本地库**（默认集中存放在 `data/` 目录，`DATA_DIR` 环境变量可整体改址，见 `paths.py`）：`mama.db`（家庭组登记）、`channel_mutes.db`（频道禁言记录）。
- **数据文件**：`docs/carousel-content.md`（轮播内容，整个文件作为一条消息发送）。

## 项目结构

```
bot.py                  # 主入口：Client 子类、启动编排
carousel.py             # 频道轮播（独立模块）
channel_admin.py        # 斜杠命令注册 + 频道禁言投票（独立模块）
mama.py                 # 找妈妈：家庭组共享登记（斜杠命令模块，与 channel_admin 同形态）
paths.py                # 数据目录统一解析（默认 ./data，DATA_DIR 可改址）
docs/                   # 本文档目录
.env                    # 环境变量（不入 Git）
```

## 设计要点（先读这个再看各模块文档）

1. **单一 `on_message` 监听 + 频道分发注册表**：整个项目只有 `bot.py` 注册了一个 `@bot.event on_message`（discord.py 的 `@bot.event` 是单播覆盖语义）。它统一过滤 bot 自己的消息后，按频道 ID 查 `bot.message_handlers` 注册表，调用各模块在 `start_xxx` 里通过 `bot.register_message_handler(频道ID, 处理器)` 注册的处理器。目前没有消息类功能：carousel 用定时任务，其余为斜杠命令。**不要再注册第二个 `on_message`**。
2. **频道 ID 硬编码**：每个模块的频道 ID 写在各自文件的顶部常量里。
3. **模块自启动**：每个功能模块都暴露一个 `start_xxx(bot)` 函数，由 `bot.py` 的 `on_ready` 各调用一次（带 `_xxx_started` 幂等标志，防止 `on_ready` 重连时重复启动）。
4. **日志**：全部用 `print()`，无日志框架。
