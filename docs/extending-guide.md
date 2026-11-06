# 扩展指南：给机器人加一个「新频道 + 新功能」

> 本文档的目标：**做扩展时只需要读这一篇**（最多加上目标模块自己的文档），不必把整个项目重新看一遍。

## 0. 先明确扩展类型

| 类型 | 特征 | 你要动的文件 |
| --- | --- | --- |
| A. 全新频道 + 全新功能 | 新频道、新玩法 | 新建模块 + `bot.py` 三行（本文主要内容） |

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

## 2. 全新频道功能的完整模板

以「猜歌台：新频道里发歌名猜歌，猜对有奖励」为例。

### 第 1 步：新建模块 `song_quiz.py`

```python
"""歌猜台：监听频道发言，命中答案发奖励。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

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
        # 这里改成你自己的结算/奖励逻辑
        await message.channel.send(
            f"🎵 {message.author.mention} 答对了，+{REWARD} 点！"
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
- 新模块只用 `bot.register_message_handler(频道ID, handler)` 注册，与 mama 等其他模块完全解耦——互相不知道对方存在，删掉一个模块只需去掉 bot.py 的三行接线。
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

无限循环内**所有可预期的异常就地 try/except**（参考 `carousel_loop`），否则一次 HTTP 失败会杀死整个循环。

### 第 3 步：需要本地存储？

照项目惯例（`paths.py` 提供 `data_file`，默认落 `data/` 目录，环境变量可覆盖完整路径）：

```python
from paths import data_file

DB_PATH = data_file("XXX_DB", "xxx.db")

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ...")

_init_db()   # import 时执行，或放 start_xxx() 里
```

量大的状态自行分表设计即可，模式照上面这套（`CREATE TABLE IF NOT EXISTS` + 模块内私有读写函数）。

### 第 4 步：需要斜杠命令？

参考 `channel_admin.register_commands`：写一个 `register_commands(bot)`，内部用 `@bot.tree.command`，然后挂进 `bot.py` 的 `setup_hook()`。注意 `tree.sync()` 在 `on_ready` 里，新命令要在注册之后才会同步上去。

### 第 5 步：接入 bot.py

见 §1 的三行。完成后重启机器人，控制台应打印你的启动日志。

## 3. 检查清单（改完对照）

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

## 4. 排错速查

| 症状 | 最可能原因 |
| --- | --- |
| 新功能不响应，控制台无报错 | 频道 ID 不对 / 机器人不在该频道可见范围 / 忘了 `register_message_handler` |
| 全部消息功能失灵 | 有模块注册了第二个 `@bot.event on_message`，覆盖了 bot.py 的唯一入口 |
| 重连后定时任务翻倍执行 | 忘加 `_xxx_started` 幂等标志 |
| 收不到任何消息 | 服务器成员网关没开 `message_content` / `members` 特权 Intent（见 [bot.md](bot.md)） |
| 斜杠命令不出现 | `register_commands` 没在 `setup_hook` 里调用；或全局 `tree.sync()` 最多一小时才生效一次，可先 `/命令` 刷新或稍等 |
| 后台循环悄悄死了 | 循环体内未捕获异常导致任务退出，看控制台最早的 traceback |
