# roulette/ — 游戏区模块

以「活动额度」为货币的小游戏合集，全部集中在游戏频道（`QUOTA_CHANNEL_ID = 1545664527410929745`）内，通过**消息关键词**触发。统一入口是 `roulette/handlers.py` 的 `start_roulette(bot)`。

## 一句话理解架构

```
start_roulette(bot)
 ├── 创建共享 httpx.AsyncClient（活动额度 API 用）
 ├── 启动 3 个后台任务：大红包循环、抢银行邀请循环、身体交换恢复
 ├── start_quota_drop()（发言掉落初始化）
 └── @bot.event on_message     ← 全项目唯一的消息监听
      └── 按消息文本前缀/全等匹配关键词 → 调用各游戏的 handle_xxx()
```

## 模块清单

| 文件 | 行数约 | 职责 |
| --- | --- | --- |
| `__init__.py` | 10 | 导出 `start_roulette` |
| `handlers.py` | 520 | 消息入口：关键词分发、梭哈/红包/乞讨/庄家的内联逻辑、各冷却字典 |
| `constants.py` | 145 | **所有可调参数**：关键词、点数、概率、冷却、阈值、DB 文件名 |
| `api.py` | 71 | 活动额度 API 封装：`query_quota` / `adjust_quota` / `query_top_quota` |
| `quota_drop.py` | 176 | 发言掉落：30% 掉 0 点、10% 扣减事件、SQLite 原子冷却、通知合并发送 |
| `dice_game.py` | 140 | 赌大小：与庄家（玩家或机器人）各押 5 点 roll 点比大小 |
| `beg.py` | 96 | 乞讨：按钮施舍，乞讨者 +5、施舍者 -7 |
| `duel.py` | 193 | 决斗：押额度较少方全部，赢家得 80% |
| `red_packet.py` | 40 | 用户红包视图（继承 PacketView） |
| `packet_base.py` | 293 | 通用红包视图：人机验证、分配、结算（user/big/selfdestruct 三类型共用） |
| `big_red_packet.py` | 60 | 机器人大红包：每 6 分钟 500 点奖池 |
| `rob.py` | 181 | 抢劫：50% 成功率，卡牌效果多 |
| `marry.py` | 140 | 结婚：双方额度合并扣 10% 后平分 |
| `curse.py` | 88 | 诅咒：10 点使目标下次抢劫/决斗/梭哈必输 |
| `gacha.py` | 1006 | 抽卡：卡池定义、效果存取、下线/身体交换/诱惑等特殊玩法 |
| `bank.py` | 411 | 地精银行：存款、安保、贷款 |
| `bank_heist.py` | 371 | 抢银行：三人组队选装备 |
| `lottery.py` | 107 | 彩票：10 点一张，5% 赢奖池 |
| `leaderboard.py` | 49 | 排行榜（蛇符咒持有者隐身） |
| `utils.py` | 53 | `split_random`（随机分池）、`make_arithmetic_question`（人机验证出题） |

## 消息分发（handlers.py）

`on_message` 开头两行先做过滤：**非游戏频道的消息直接 return；bot 自己的消息 return**。之后按顺序匹配关键词，命中即处理并 return。维护要点：

1. **关键词匹配是顺序敏感的**：`content.startswith(...)` 类（结婚/诅咒/决斗/抢劫/诱惑/你的名字）和 `content == ...` 类（其余）混排，新增关键词注意别被已有的前缀规则截胡。
2. **冷却字典全部闭包在 `start_roulette()` 里**（`beg_cooldowns`、`duel_cooldowns` 等 dict[int, float]，存 `time.monotonic()` 到期时间），通过 `on_finish` 回调传给各 View 在结束时写入。重启即清零，属可接受设计。
3. **被诅咒用户集合 `cursed_users`** 也是闭包状态，传给决斗/抢劫/梭哈逻辑。
4. **下线状态**：用户使用「下线」卡后，其**下一次任意发言**解除下线（发提示消息），且**那次发言不触发掉落**（`skip_drop` 标志）。命令匹配在其之后，所以下线者发「赌大小」会先解除下线再正常执行命令。
5. 所有命令都 return，掉落检查 `if not skip_drop` 放在**最后**——即执行了游戏命令的发言不再触发掉落。

## 额度结算约定（全包统一）

- 统一走 `api.py`：`query_quota(client, username)` 返回 `Optional[int]`（失败 None）；`adjust_quota(client, "grant"|"deduct", username, amount)` 返回扣/发后的当前额度。
- **先扣后发**；发放失败要尽量回滚（参考 `beg.py`、`rob.py`）。
- 「销毁」= 只 deduct 不 grant；「机器人回收」同理。
- 用户标识用 `message.author.name`（**Discord 用户名**，非昵称），与额度 API 账本对齐。
- API 失败统一回复用户「查询/扣除额度失败，请稍后再试」。

## 卡牌系统（gacha.py）

- 卡池 `CARD_POOL`：`(key → 名称, 描述, 权重)`。50% 概率直接空白；抽到卡牌后效果写入 SQLite `gacha_effects` 表（`discord_id, card_key, remaining`）。
- 效果存取三件套，被其他游戏模块大量调用：
  - `consume_effect(user_id, key) -> bool`：读出即消费（一次性卡）；
  - `has_effect(user_id, key) -> bool`：只查不消费；
  - `is_offline(user_id) -> bool` / `clear_offline`：下线状态。
- 唯一道具（蛇符咒、会员卡）用单行表 `snake_charm_holder` / `membership_card_holder` 存持有者。
- 身体交换（你的名字卡）存 `body_swaps` 表，`restore_body_swaps` 后台任务在 5 分钟后换回。
- **循环依赖规避惯例**：`packet_base.py`、`quota_drop.py` 等在函数体内延迟 `from roulette.gacha import ...`，因为 gacha 又 import 了 packet_base。新增跨模块引用时沿用此惯例。

## 通用红包视图（packet_base.py）

`PacketView` 同时支撑三种红包：用户红包（`"user"`，少数幸运儿模式）、机器人大红包（`"big"`，全员随机有上限模式）、自爆红包（`"selfdestruct"`）。要点：

- 参与前提：答对一道十以内加减法人机验证（`utils.make_arithmetic_question`），答错失去资格；
- 消息编辑限流：两次 edit 间隔至少 1 秒（Discord 429 保护）；
- 结算用 `asyncio.gather` 并发发放；
- `on_timeout` 自动开奖；用户红包无人抢时退回发送者。

## 发言掉落（quota_drop.py）

- 每条非命令发言都可能触发：30% 概率掉 0 点，否则掉 1–50 点；另有 10% 概率变成「扣减 1–50 点」事件。
- 单用户冷却 30–180 秒随机，用 SQLite `INSERT ... ON CONFLICT ... WHERE` 原子写入（`quota_drops.db`）。
- 通知**批量合并发送**：模块级缓冲区 + 每 0.5 秒刷新一次，按 1800 字符拆分（Discord 2000 上限留余量），发完 10 秒自动删除。

## 数据库与常量

| DB | 表 | 说明 |
| --- | --- | --- |
| `gacha.db` | gacha_effects / snake_charm_holder / membership_card_holder / body_swaps / offline_users | 卡牌效果与特殊状态 |
| `bank.db` | bank_accounts / bank_hatred / bank_heist_cooldowns 等 | 银行存款与抢劫 |
| `lottery.db` | lottery_pool | 彩票奖池（单行） |
| `quota_drops.db` | drop_cooldowns | 掉落冷却 |

- 建表都在各模块的 `_init_db()`，**import 时即执行**（`bank.py`/`gacha.py`/`lottery.py` 尾部直接调用）；`quota_drop.py` 在 `start_quota_drop()` 里调用。
- **调参一律改 `constants.py`**，不要在业务代码里写魔法数字。DB 路径可用同名环境变量覆盖（如 `GACHA_DB`）。

## 新增一个游戏的标准流程

1. 在 `constants.py` 定义关键词与参数（`XXX_KEYWORD`、概率、冷却等）；
2. 新建 `roulette/xxx.py`，写 `async def handle_xxx(message, client, xxx_cooldowns) -> None`（复杂交互做成 `discord.ui.View`，参考 `beg.py` 的最小样例）；
3. 在 `handlers.py`：import 常量与 handler → `start_roulette()` 里加冷却字典 → `on_message` 里加一个 `if content == XXX_KEYWORD:` 分支（注意放在掉落检查之前，且命中后 `return`）；
4. 若需要后台循环任务：写 `async def xxx_loop(bot, client)`，在 `start_roulette()` 里 `asyncio.create_task(..., name="xxx-loop")`（参考 `big_red_packet_loop`）；
5. 若需要本地状态：模块顶部 `DB_PATH = Path(os.getenv("XXX_DB", "xxx.db"))` + `_init_db()` + import 时调用；若是唯一道具/下线类状态，加到 `gacha.py` 的表里更省事。

> 更完整的「新增独立频道功能」指南（建新包、注册监听、接入 bot.py）见 [extending-guide.md](extending-guide.md)。
