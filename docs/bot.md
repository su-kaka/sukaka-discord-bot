# bot.py — 主入口

`bot.py` 是整个机器人的唯一启动文件（`python bot.py`），职责只有三件事：加载环境变量、启动 Keepalive HTTP 服务器、把四大功能模块挂到机器人上。它本身不含任何业务逻辑。

## 文件结构

```
bot.py（约 110 行）
├── load_dotenv()            # 加载 .env
├── KeepAliveHandler          # HTTP 处理器：返回 "Bot is running." 页面
├── start_keepalive_server()  # 后台线程跑 ThreadingHTTPServer
├── class SukakaBot(discord.Client)
│   ├── __init__()           # Intents + 各模块的状态容器
│   ├── setup_hook()          # 注册斜杠命令
│   └── on_ready()            # 幂等地启动各功能模块
└── main()                    # 读 DISCORD_TOKEN → bot.run()
```

## 启动流程

```python
main()
 ├── start_keepalive_server()        # 守护线程，HTTP 0.0.0.0:7861，供存活探针访问
 └── bot = SukakaBot(); bot.run(token)
      ├── setup_hook()（登录前）
      │    └── register_commands(bot)     # channel_admin 的 4 个斜杠命令
      └── on_ready()（登录后，可能因重连多次触发）
           ├── await self.tree.sync()          # 同步斜杠命令到 Discord（仅首次）
           ├── 恢复未到期的频道禁言            # start_channel_mute_restores（读 channel_mutes.db）
           ├── start_carousel(self)             # 轮播任务
           └── start_roulette(self)             # 游戏区（含发言掉落、后台任务）
```

### `on_ready` 的幂等保护

Discord 重连时 `on_ready` 会被再次调用。每个功能都对应一个 `self._xxx_started` 布尔标志（`_synced`、`_channel_mutes_started`、`_carousel_started`、`_roulette_started`），保证任务只启动一次。**新增启动逻辑时必须沿用这个模式**，否则重连后会重复开任务（例如大红包循环会翻倍发红包）。

## SukakaBot 的状态容器

`__init__` 里挂在 `self` 上的状态，就是各模块的「公共黑板」：

| 属性 | 归属模块 | 用途 |
| --- | --- | --- |
| `self.tree` | discord.py | 斜杠命令树 |
| `self.mute_votes` / `self.active_vote_by_target` | channel_admin | 投票状态（内存态，重启丢失） |
| `self._carousel_task` | carousel | 轮播任务句柄 |

roulette 模块没有往 bot 上挂状态——它的冷却字典等全部闭包在 `start_roulette()` 内部。channel_admin 的频道禁言记录也不再挂状态：持久层是模块私有的 SQLite 函数（`channel_mutes.db`），恢复任务注册表与写锁是模块级私有变量。

## Intents 配置

```python
intents = discord.Intents.default()
intents.guilds = True        # 频道/服务器结构
intents.members = True       # 成员列表（排行榜成员查找、fetch_member 兜底）——需在开发者后台开启 Privileged
intents.message_content = True   # 读取消息内容（游戏关键词识别必需）——需在开发者后台开启 Privileged
```

`members` 和 `message_content` 属于特权 Intent，除了代码里声明，还必须在 [Discord 开发者后台](https://discord.com/developers/applications) → Bot → Privileged Gateway Intents 中开启，否则启动报错或收不到消息。

## Keepalive 服务器

- `ThreadingHTTPServer` 监听 `0.0.0.0:7861`，跑在名为 `keepalive-http` 的守护线程里。
- 任意 GET 返回一个写着 "Bot is running." 的 HTML 页面，`log_message` 被覆写为静默（不打访问日志）。
- 用途：部署平台（如 Hugging Face Space）的健康检查。若不需要可直接去掉 `start_keepalive_server()` 调用。

## 环境变量

| 变量 | 用途 | 缺失后果 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 机器人登录凭证 | 启动直接 `RuntimeError` |
| `ACTIVITY_QUOTA_API_KEY` | 活动额度 API 鉴权 | 启动正常，但所有额度查询/发放静默失败（游戏不可用） |
| `ACTIVITY_QUOTA_API_BASE` | 覆盖 API 地址（默认 `https://catiecli.sukaka.top`） | 用默认值 |
| `MUTE_WHITELIST` | 管理命令的用户 ID 白名单（逗号分隔） | 无人可用管理命令 |

## 消息分发机制（bot.py 持有唯一的 on_message）

`SukakaBot` 上有一个**消息处理器注册表**，各功能模块在 `start_xxx(bot)` 里向 bot 注册自己监听的频道：

```python
# bot.py 的核心三件
MessageHandler = Callable[[discord.Message], Awaitable[None]]

self.message_handlers: dict[int, list[MessageHandler]] = defaultdict(list)  # 频道 ID -> 处理器列表

def register_message_handler(self, channel_id: int, handler: MessageHandler) -> None: ...

async def on_message(self, message: discord.Message) -> None:
    """唯一的消息入口：按频道分发给注册了该频道的模块。"""
    if message.author.bot:      # 挡住 carousel 轮播与 bot 自己的消息，防回环
        return
    for handler in self.message_handlers.get(message.channel.id, ()):
        await handler(message)
```

- **`@bot.event` 是覆盖语义**（discord.py 单播），所以全项目只允许这一处 `on_message`；功能模块一律通过 `register_message_handler` 注册，bot 消息在分发前统一过滤。
- 一个频道可以注册多个处理器（按注册顺序依次执行）；没有注册处理器的频道消息直接丢弃。
- `roulette`（游戏频道 `1545664527410929745`）与 `mama`（找妈妈频道 `1455038454772531311`）都走这套机制。channel_admin 用斜杠命令、carousel 用定时任务，不参与消息分发。

## 新增功能模块时的改动点

在本文件中只需三步（详细流程见 [extending-guide.md](extending-guide.md)）：

1. `from xxx import start_xxx` —— 新模块必须暴露 `start_xxx(bot)` 入口；
2. `__init__` 加 `self._xxx_started = False`；
3. `on_ready` 加：

```python
if not self._xxx_started:
    self._xxx_started = True
    start_xxx(self)
```

新模块若要监听消息，在自己的 `start_xxx(bot)` 里调用 `bot.register_message_handler(频道ID, handler)`——不要注册第二个 `@bot.event on_message`。

注意：如果新模块也用斜杠命令，注册放在 `setup_hook()`（调用模块的 `register_commands`），不要放 `on_ready`。
