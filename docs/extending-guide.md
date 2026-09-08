# 扩展指南：给机器人加一个「新频道 + 新功能」

> 本文档的目标：**做扩展时只需要读这一篇**（最多加上目标模块自己的文档），不必把整个项目重新看一遍。

## 0. 先明确三种扩展类型

| 类型 | 特征 | 你要动的文件 |
| --- | --- | --- |
| A. 给现有游戏区加玩法 | 在游戏频道 `1545664527410929745` 内加关键词玩法 | 只动 `roulette/`（见 [roulette.md](roulette.md) 末尾「标准流程」） |
| B. 全新频道 + 全新功能 | 新频道、新玩法、与游戏区无关 | 新建模块 + `bot.py` 三行（本文主要内容） |
| C. 新频道 + 额度类玩法 | 新功能但复用活动额度账本 | B 的基础上复用 `roulette/api.py` 与 `roulette/constants.py` |

## 1. 项目约定（照做就能融入现有结构）

1. **一个功能 = 一个模块文件（或包）+ 一个 `start_xxx(bot)` 入口**。入口里做初始化（建 DB、注册消息处理器、打印启动日志）。
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
5. **消息监听（`on_message`）只有 bot.py 一处**（`@bot.event` 是覆盖语义，discord.py 单播）。需要监听消息的新功能在 `start_xxx(bot)` 里调用：
   ```python
   bot.register_message_handler(频道ID, 处理函数)   # bot.py 按频道分发，bot 消息已统一过滤
   ```
   处理函数开头做一次频道过滤即可（见 §2 模板）；**斜杠命令与定时任务类功能不需要这个**。

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


async def handle_song_message(message: discord.Message) -> None:
    """消息处理器：由 bot.py 按频道分发调用（bot 消息已在分发前统一过滤）。"""
    if message.channel.id != SONG_CHANNEL_ID:   # 防御性双检，分发机制已保证频道
        return
    if message.content.strip() in ANSWERS:
        # 类型 C：复用共享 HTTP 客户端；没有额度逻辑就改成你自己的结算
        client = bot.game_client   # 需先挂载（见 §3）
        new_quota = await adjust_quota(client, "grant", message.author.name, REWARD)
        if new_quota is not None:
            await message.channel.send(
                f"🎵 {message.author.mention} 答对了，+{REWARD} 点，当前 {new_quota} 点！"
            )


def start_song_quiz(bot: "SukakaBot") -> None:
    """初始化：建表、注册消息入口、打印启动日志。"""
    # 这里做初始化：建表、读文件等
    bot.register_message_handler(SONG_CHANNEL_ID, handle_song_message)   # ★ 注册到 bot 的分发注册表
    print(f"[SongQuiz] 已启动，监听频道 {SONG_CHANNEL_ID}")
```

**消息监听机制（本指南最重要的一个点）**：

- 全项目**只有 bot.py 一处 `@bot.event on_message`**：discord.py 的 `@bot.event` 是 `setattr(self, "on_message", coro)` **赋值覆盖语义**（纯 `Client` 也没有 `add_listener`，已实测 discord.py 2.6.4）。功能模块注册第二个 `@bot.event on_message` 会顶掉 bot.py 的，全部功能失灵。
- bot.py 持有**消息处理器注册表** `bot.message_handlers: dict[频道ID, list[处理器]]`，唯一入口统一过滤 `author.bot` 后按频道分发（挡住 carousel 轮播与 bot 自己的消息，防回环）：
  ```python
  # bot.py（已存在，不需要改）
  async def on_message(self, message: discord.Message) -> None:
      if message.author.bot:
          return
      for handler in self.message_handlers.get(message.channel.id, ()):
          await handler(message)
  ```
- 新模块只用 `bot.register_message_handler(频道ID, handler)` 注册，与 roulette/mama 完全解耦——互相不知道对方存在，删掉一个模块只需去掉 bot.py 的三行接线。
- **斜杠命令 / 定时任务类功能**（不依赖消息监听）完全不需要上述机制，像 `channel_admin.py`、`carousel.py` 一样做即可。

如果新功能**不需要**监听普通消息（大多数功能如此），优先选定时任务/斜杠命令，省掉全部麻烦。

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
- [ ] 新模块入口为 `start_xxx(bot)`，幂等标志已加进 `SukakaBot.__init__` 与 `on_ready`。
- [ ] 若监听普通消息：用的是 `bot.register_message_handler(频道ID, handler)`，没有注册第二个 `@bot.event on_message`。
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
| 新功能不响应，控制台无报错 | 频道 ID 不对 / 机器人不在该频道可见范围 / 忘了 `register_message_handler` |
| 全部消息功能失灵 | 有模块注册了第二个 `@bot.event on_message`，覆盖了 bot.py 的唯一入口 |
| 重连后定时任务翻倍执行 | 忘加 `_xxx_started` 幂等标志 |
| 收不到任何消息 | 服务器成员网关没开 `message_content` / `members` 特权 Intent（见 [bot.md](bot.md)） |
| 额度调用全失败 | `.env` 缺 `ACTIVITY_QUOTA_API_KEY`，`adjust_quota` 静默返回 None |
| 斜杠命令不出现 | `register_commands` 没在 `setup_hook` 里调用；或全局 `tree.sync()` 最多一小时才生效一次，可先 `/命令` 刷新或稍等 |
| 后台循环悄悄死了 | 循环体内未捕获异常导致任务退出，看控制台最早的 traceback |
