# 项目总览

sukaka-discord-bot 是一个单进程 Discord 机器人（Python 3.13 + discord.py 2.x），由 `bot.py` 作为唯一入口，挂载四大互不干扰的功能模块，每个功能各自绑定一个固定频道。

本文档目录为每个模块都提供了独立说明，按需查阅即可，无需通读全部源码：

| 文档 | 内容 | 对应代码 |
| --- | --- | --- |
| [bot.md](bot.md) | 主入口、启动流程、Keepalive 服务器 | `bot.py` |
| [roulette.md](roulette.md) | 游戏区：赌大小、抽卡、银行、红包等全部小游戏 | `roulette/` 包 |
| [carousel.md](carousel.md) | 频道轮播消息 | `carousel.py` |
| [channel-admin.md](channel-admin.md) | 斜杠命令：禁言投票、删除/置顶消息 | `channel_admin.py` |
| [mama.md](mama.md) | 找妈妈：家庭组共享登记（登记/查询按钮 + 弹窗表单） | `mama.py` |
| [family-group-guide.md](family-group-guide.md) | 家庭组教程：Gemini Pro 家庭组共享步骤总结（「家庭组教程」命令的文案来源） | — |
| [extending-guide.md](extending-guide.md) | **扩展指南：新增频道功能该怎么改** | — |

## 快速上手

```bash
# 安装依赖（项目自带 .venv）
pip install -r requirements.txt

# 配置环境变量（项目根目录 .env）
DISCORD_TOKEN=你的机器人Token
ACTIVITY_QUOTA_API_KEY=活动额度API密钥

# 启动
python bot.py
```

## 功能与频道一览

| 功能 | 监听频道 ID | 触发方式 | 所需权限/Intents |
| --- | --- | --- | --- |
| 游戏区（roulette） | `1545664527410929745` | 消息关键词（如「赌大小」「抽卡」） | message_content、members |
| 频道轮播（carousel） | `1455038454772531311` | 定时任务，每分钟 | 无特殊要求 |
| 找妈妈（mama） | `1455038454772531311` | 消息关键词「登记妈妈」「找妈妈」「家庭组教程」 | message_content |
| 管理命令（channel_admin） | `1293095144806940738` | 斜杠命令 `/mute_vote` `/delete_message` `/mark_message` `/unmark_message` | manage_roles、manage_messages |
| Keepalive 服务器 | — | HTTP 0.0.0.0:7861 | — |

## 外部依赖

- **活动额度 API**（`https://catiecli.sukaka.top`）：所有游戏点数的真实账本。机器人不自己记账（银行存款、卡牌、彩票奖池等本地 SQLite 除外），通过 `X-Activity-Quota-Key` 鉴权调用 grant/deduct/query/top 接口。接口详情见 [activity-quota-bot-api.md](activity-quota-bot-api.md)。
- **SQLite 本地库**（项目根目录）：`gacha.db`（卡牌效果/下线状态/身体交换）、`bank.db`（银行存款/抢劫冷却）、`lottery.db`（奖池）、`quota_drops.db`（掉落冷却）、`mama.db`（家庭组登记）、`channel_mutes.db`（频道禁言记录）。
- **数据文件**：`docs/carousel-content.md`（轮播内容，整个文件作为一条消息发送）。

## 项目结构

```
bot.py                  # 主入口：Client 子类、启动编排、Keepalive
carousel.py             # 频道轮播（独立模块）
channel_admin.py        # 斜杠命令注册 + 频道禁言投票（独立模块）
mama.py                 # 找妈妈：家庭组共享登记（独立模块，消息由 handlers.py 转发）
roulette/               # 游戏区包（详见 roulette.md）
├── __init__.py          #   导出 start_roulette
├── handlers.py          #   统一消息入口：关键词分发 + 各游戏冷却字典
├── constants.py         #   全部游戏常量/调参（关键词、概率、冷却、阈值）
├── api.py               #   活动额度 API 封装（query/adjust/query_top）
├── quota_drop.py        #   发言掉落额度
├── dice_game.py / beg.py / duel.py / red_packet.py / rob.py
├── marry.py / curse.py / gacha.py / bank.py / bank_heist.py
├── lottery.py / leaderboard.py / big_red_packet.py
├── packet_base.py       #   通用红包视图（用户红包/大红包/自爆红包共用）
└── utils.py             #   随机分池、人机验证出题
docs/                   # 本文档目录
.env                    # 环境变量（不入 Git）
```

## 设计要点（先读这个再看各模块文档）

1. **单一 `on_message` 监听 + 频道分发注册表**：整个项目只有 `bot.py` 注册了一个 `@bot.event on_message`（discord.py 的 `@bot.event` 是单播覆盖语义）。它统一过滤 bot 自己的消息后，按频道 ID 查 `bot.message_handlers` 注册表，调用各模块在 `start_xxx` 里通过 `bot.register_message_handler(频道ID, 处理器)` 注册的处理器。各消息类功能（roulette、mama）完全解耦，互相不知道对方存在。其他功能要么是斜杠命令（channel_admin），要么是定时任务（carousel、大红包、抢银行邀请）。**不要再注册第二个 `on_message`**。
2. **频道 ID 硬编码**：每个模块的频道 ID 写在各自文件的顶部常量里（roulette 的在 `constants.py`）。
3. **模块自启动**：每个功能模块都暴露一个 `start_xxx(bot)` 函数，由 `bot.py` 的 `on_ready` 各调用一次（带 `_xxx_started` 幂等标志，防止 `on_ready` 重连时重复启动）。
4. **额度结算模式**：先 deduct 再 grant，发放失败时尽量回滚；「销毁」= 只 deduct 不 grant。所有游戏共用一个 `httpx.AsyncClient`。
5. **日志**：全部用 `print()`，无日志框架。
