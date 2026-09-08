# 扩展指南：给机器人加一个「新频道 + 新功能」

> 本文档的目标：**做扩展时只需要读这一篇**（最多加上目标模块自己的文档），不必把整个项目重新看一遍。

## 0. 先明确三种扩展类型

| 类型 | 特征 | 你要动的文件 |
| --- | --- | --- |
| A. 给现有游戏区加玩法 | 在游戏频道 `1545664527410929745` 内加关键词玩法 | 只动 `roulette/`（见 [roulette.md](roulette.md) 末尾「标准流程」） |
| B. 全新频道 + 全新功能 | 新频道、新玩法、与游戏区无关 | 新建模块 + `bot.py` 三行（本文主要内容） |
| C. 新频道 + 额度类玩法 | 新功能但复用活动额度账本 | B 的基础上复用 `roulette/api.py` 与 `roulette/constants.py` |

## 1. 项目约定（照做就能融入现有结构）

1. **一个功能 = 一个模块文件（或包）+ 一个 `start_xxx(bot)` 入口**。入口里做初始化（建 DB、打印启动日志），并注册自己的监听。
2. **频道 ID 写成模块顶部的常量**，不要散落在函数里。
3. **bot.py 只做「启动编排」**，三步接入（见 bot.md）：
   ```python
   # ① import
   from xxx import start_xxx
   # ② __init__ 里加幂等标志
   self._xxx_started = False
   # ③ on_ready 里启动
   if not self._xxx_started:
       self._xxx_started = True
       start_xxx(self)
   ```
   ⚠️ 斜杠命令是例外：注册要放进 `setup_hook()`（`on_ready` 里的 `tree.sync()` 之前），不是 `on_ready`。
4. **`on_ready` 会因重连多次触发**，幂等标志不能省（重复启动定时循环会成倍执行）。
5. **消息监听（`on_message`）全项目只能有一个**（目前挂在 `roulette/handlers.py`，`@bot.event` 是覆盖语义）。需要监听消息的新功能见 §2 的共存方案；**斜杠命令与定时任务类功能不受此限制**。
6. 注册任何事件监听前先搜一遍现有监听，避免意外覆盖：
   ```bash
   grep -rn "@bot.event" --include="*.py" .
   ```

## 2. 类型 B：全新频道功能的完整模板

以「猜歌台：新频道里发歌名猜歌，猜对加额度」为例（同时演示类型 C 的额度复用）。

### 第 1 步：新建模块 `song_quiz.py`

```python
"""歌猜台：监听频道发言，命中答案发活动额度。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from roulette.api import adjust_quota   # 类型 C：复用额度结算；纯新功能可去掉

if TYPE_CHECKING:
    from bot import SukakaBot

# 本模块的所有配置集中在顶部
SONG_CHANNEL_ID = 1234567890123456789       # ⚠️ 换成你的新频道
ANSWERS = ("稻香", "晴天", "七里香")
REWARD = 10


def start_song_quiz(bot: "SukakaBot") -> None:
    """初始化并注册监听（前提：已按 §2 方案 1 迁移到 commands.Bot）。"""
    # 这里做初始化：建表、读文件、打印启动日志等
    async def on_song_message(message: discord.Message) -> None:
        if message.channel.id != SONG_CHANNEL_ID:
            return
        if message.author.bot:
            return
        if message.content.strip() in ANSWERS:
            # 类型 C：复用共享 HTTP 客户端；没有额度逻辑就改成你自己的结算
            client = bot.game_client   # 需先挂载（见 §3）
            new_quota = await adjust_quota(client, "grant", message.author.name, REWARD)
            if new_quota is not None:
                await message.channel.send(
                    f"🎵 {message.author.mention} 答对了，+{REWARD} 点，当前 {new_quota} 点！"
                )

    bot.add_listener(on_song_message, "on_message")   # ★ 追加监听，不覆盖游戏区
```

（若采用方案 2 转发式，则本模块只暴露 `async def handle_song_quiz(client, message)`，不自己注册监听。）

**多个消息监听如何共存（本指南最重要的一个点）**：

- 本项目用的是纯 `discord.Client`，`@bot.event` 的实现就是 `setattr(self, "on_message", coro)` —— **赋值覆盖语义**，一个事件名只能有一个回调。第二个 `@bot.event on_message` 会顶掉 `roulette/handlers.py` 的，游戏区整体失灵。
- 纯 `Client` **没有** `add_listener`/`listen`（那是 `discord.ext.commands.Bot` 的 API，已实测本项目的 discord.py 2.6.4）。要并存多个消息回调，选其一：
  1. **改用 `commands.Bot`**：`SukakaBot` 改为继承 `discord.ext.commands.Bot`（`commands.Bot` 是 `Client` 的子类，现有代码 `@bot.event`、`bot.tree` 全部兼容，只需在 `__init__` 里把 `super().__init__(intents=intents)` 换成 `super().__init__(command_prefix="!", intents=intents)`），之后各模块用 `bot.add_listener(coro, "on_message")` 追加监听（装饰器写法 `@bot.listen("on_message")`）。`commands.Bot.dispatch` 会先跑 Client 原生 event，再跑所有 extra listeners，互不影响。
  2. **不动 Client 基类**：把新功能写进现有唯一的 `on_message`（即 `roulette/handlers.py`），在函数末尾按频道 ID 转发给新模块：
     ```python
     # handlers.py 的 on_message 开头加：
     if message.channel.id != QUOTA_CHANNEL_ID and message.channel.id != SONG_CHANNEL_ID:
         return
     ...
     # 末尾（return 之前）加：
     if message.channel.id == SONG_CHANNEL_ID:
         await handle_song_quiz(client, message)
         return
     ```
  3. **斜杠命令 / 定时任务类功能**（不依赖消息监听）则完全不受此限制，像 `channel_admin.py`、`carousel.py` 一样做即可。

如果新功能**不需要**监听普通消息（大多数功能如此），优先选 3，省掉全部麻烦。

### 第 2 步：需要后台定时任务？

参考 `carousel.py` 的形态，不需要事件监听：

```python
def start_xxx(bot: "SukakaBot") -> None:
    async def xxx_loop() -> None:
        while True:
            await asyncio.sleep(60)
            channel = bot.get_channel(XXX_CHANNEL_ID)
            if channel:
                await channel.send("tick")
    asyncio.create_task(xxx_loop(), name="xxx-loop")
```

无限循环内**所有可预期的异常就地 try/except**（参考 `carousel_loop`、`big_red_packet_loop`），否则一次 HTTP 失败会杀死整个循环。

### 第 3 步：需要本地存储？

照抄 roulette 的惯例：

```python
DB_PATH = Path(os.getenv("XXX_DB", "xxx.db"))

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ...")

_init_db()   # import 时执行，或放 start_xxx() 里
```

量大的状态直接看齐 `bank.py`；「唯一持有者」「下线」类状态优先塞进 `gacha.py` 的现成表。

### 第 4 步：需要斜杠命令？

参考 `channel_admin.register_commands`：写一个 `register_commands(bot)`，内部用 `@bot.tree.command`，然后挂进 `bot.py` 的 `setup_hook()`。注意 `tree.sync()` 在 `on_ready` 里，新命令要在注册之后才会同步上去。

### 第 5 步：接入 bot.py

见 §1 的三行。完成后重启机器人，控制台应打印你的启动日志。

## 3. 类型 C 补充：复用活动额度体系

- **额度 API 封装**：`roulette/api.py` 的 `query_quota / adjust_quota / query_top_quota`，鉴权与地址已处理，新模块直接 import。
- **HTTP 客户端**：`start_roulette()` 内创建的 `httpx.AsyncClient` 是闭包变量，**外部拿不到**。两个选择：
  1. 在 `roulette/handlers.py` 的 `start_roulette()` 里把它挂到 bot 上（一行：`bot.game_client = client`），新模块直接用 `bot.game_client`；
  2. 新模块自建 `httpx.AsyncClient(timeout=15)`（独立、简单，量小时完全可行）。
- **用户标识用 `user.name`**（Discord 用户名），与账本对齐，不要用 `display_name`/昵称。
- **结算惯例**：先 deduct 后 grant；失败回滚；None 一律当「API 失败」处理。

## 4. 检查清单（改完对照）

- [ ] 新频道 ID 已在模块顶部定义为常量，且机器人已在目标服务器中（否则 `get_channel` 为 None）。
- [ ] 新监听回调开头两行：频道过滤 + `author.bot` 过滤。
- [ ] 新模块入口为 `start_xxx(bot)`，幂等标志已加进 `SukakaBot.__init__` 与 `on_ready`。
- [ ] 若监听普通消息：确认没有注册第二个 `@bot.event on_message`（覆盖游戏区），共存方案见 §2。
- [ ] 后台循环：`asyncio.create_task(..., name="...")` + 异常就地处理，循环体不会因单次失败退出。
- [ ] 斜杠命令在 `setup_hook()` 注册。
- [ ] 需要的 Intents 已在 `SukakaBot.__init__` 声明（默认集已含 guilds/members/message_content）。
- [ ] 数据库/文件写入考虑了首启动（`CREATE TABLE IF NOT EXISTS` / 文件不存在时跳过）。
- [ ] 机器人需要的额外权限（manage_messages 等）已在服务器授予。
- [ ] 启动验证：控制台出现你的启动日志；目标频道内功能实际触发一次。
- [ ] 文档：在本目录新增 `xxx.md`，并更新 [overview.md](overview.md) 的两个表格。

## 5. 排错速查

| 症状 | 最可能原因 |
| --- | --- |
| 新功能不响应，控制台无报错 | 频道 ID 不对 / 机器人不在该频道可见范围 / 频道过滤写错 |
| 游戏区整体失灵 | 有模块注册了第二个 `@bot.event on_message`，覆盖了 handlers.py 的（见 §2 共存方案） |
| 重连后定时任务翻倍执行 | 忘加 `_xxx_started` 幂等标志 |
| 收不到任何消息 | 服务器成员网关没开 `message_content` / `members` 特权 Intent（见 [bot.md](bot.md)） |
| 额度调用全失败 | `.env` 缺 `ACTIVITY_QUOTA_API_KEY`，`adjust_quota` 静默返回 None |
| 斜杠命令不出现 | `register_commands` 没在 `setup_hook` 里调用；或全局 `tree.sync()` 最多一小时才生效一次，可先 `/命令` 刷新或稍等 |
| 后台循环悄悄死了 | 循环体内未捕获异常导致任务退出，看控制台最早的 traceback |
