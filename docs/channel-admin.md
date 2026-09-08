# channel_admin.py — 频道管理命令

管理类斜杠命令模块（约 760 行）：**频道禁言投票** + **消息删除/置顶/取消置顶**。与游戏区完全独立，通过 `bot.tree`（斜杠命令）而非消息关键词工作。

## 使用限制（两层门禁）

所有命令都先过 `deny_reason(interaction)`，不满足则 ephemeral 回绝：

1. **频道限制**：只能在 `ALLOWED_CHANNEL_ID = 1293095144806940738` 这一个频道里使用；
2. **用户白名单**：用户 ID 必须在环境变量 `MUTE_WHITELIST`（逗号分隔的数字 ID）中。

```env
# .env 示例
MUTE_WHITELIST=123456789,987654321
```

## 命令一览

| 命令 | 作用 | 关键校验 |
| --- | --- | --- |
| `/mute_vote @成员 [时长] [原因]` | 发起频道禁言投票 | 不能投 bot/服主/管理员；同目标同时只能有一个投票 |
| `/delete_message <消息链接>` | 删除指定消息 | 链接必须属于当前服务器 |
| `/mark_message <消息链接>` | 置顶 | 已置顶则提示 |
| `/unmark_message <消息链接>` | 取消置顶 | 未置顶则提示 |

时长默认 30 分钟，范围 1–1440（`MAX_TIMEOUT_MINUTES`）。

## 禁言投票流程

```
/mute_vote @某人 30 刷屏
 └── 创建 VoteState（发起人自动算 1 票）→ 发 Embed + 「投票禁言（1/5）」按钮
      └── 其他人点按钮：
           ├── 校验：同频道投票 / 非bot / 未投过
           ├── < 5 票：更新按钮文字与 Embed
           └── ≥ 5 票（VOTE_THRESHOLD）：apply_channel_mute()
                 ├── 保存成员在频道的原始权限覆盖（allow/deny 对）
                 ├── 把 send_messages 设为 False
                 └── 记录 restore_at = now + 时长
                      └── 到期由后台任务自动恢复原权限、删除记录
```

要点：

- **改的是频道级权限覆盖**（channel overwrite），不是服务器禁言。被投票者只是不能**在这个频道**发言。
- **可叠加**：对同一人同一频道再次投票通过时，只更新 `restore_at`，原始权限仍是第一次保存的那份。
- **恢复机制**：`schedule_channel_mute_restore` 为每条记录起一个 `asyncio.Task` 睡到 `restore_at` 恢复；恢复失败（权限丢失等）每 60 秒重试；`discord.NotFound`（成员/频道已消失）直接清理记录。恢复任务注册表与写锁是模块级私有变量，不挂在 bot 上。
- **持久化**：记录存 SQLite（`channel_mutes.db`，`CHANNEL_MUTES_DB` 可覆盖路径），主键 = (服务器, 频道, 成员)，叠加禁言只 `UPDATE restore_at`。**重启恢复**：`on_ready` 里 `start_channel_mute_restores` 读库为每条记录重排恢复任务——机器人重启不会丢失未到期的禁言。
- **投票本身不持久化**：`mute_votes` / `active_vote_by_target` 只在内存。重启后旧投票消息的按钮会提示「此投票已不存在」。

## 频道禁言的额外校验（`channel_mute_denial_reason`）

发起前还会检查：

- 目标不能是服务器所有者；
- 目标不能有「管理员」权限；
- 机器人自己在该频道必须有 **管理身份组**（manage_roles）权限。

任一不满足，命令直接失败并说明原因（这些校验在按钮达到 5 票执行时也会再走一遍）。

## 权限需求

| 操作 | 机器人所需权限 |
| --- | --- |
| 频道禁言/恢复 | Manage Roles（且身份组在目标成员之上） |
| 删除消息 | Manage Messages |
| 置顶/取消置顶 | Manage Messages |

Intents：模块本身不读消息内容，只需默认 Intents + `members`（`fetch_member` 兜底）。

## 消息链接解析

`/delete_message` 等三个命令接受标准 Discord 消息链接：

```
https://discord.com/channels/<guild_id>/<channel_id>/<message_id>
```

`MESSAGE_LINK_PATTERN` 正则同时兼容 `ptb.`/`canary.` 域名前缀；解析后强制 `guild_id` 必须等于当前服务器 ID，防止跨服操作。

## 与其他模块的关系

- **注册时机**：`bot.py` 的 `setup_hook()` 调 `register_commands(bot)`（在 `on_ready` 的 `tree.sync()` 之前），命令通过全局斜杠命令同步生效。
- **状态存放**：投票状态挂在 `bot` 实例上（`bot.mute_votes` 等），禁言记录存模块私有的 SQLite（见 [bot.md](bot.md) 的状态容器表）。
- 与 roulette 的消息监听无任何交集：即使有人在游戏频道用这些命令，也会被 `deny_reason` 的频道检查挡掉。

## 常见改动

- **换允许频道**：改顶部 `ALLOWED_CHANNEL_ID`。
- **改票数门槛**：改 `VOTE_THRESHOLD`（按钮初始文字 `label="投票禁言（0/5）"` 里的数字是装饰性的，`update_vote_label` 运行时会用真实值覆盖，但要记得同步改以免误导）。
- **改默认/最大时长**：`DEFAULT_TIMEOUT_MINUTES` / `MAX_TIMEOUT_MINUTES`。
- **加新管理命令**：在 `register_commands(bot)` 内照抄现有 `@bot.tree.command` 样式，开头调用 `deny_reason` 校验即可（完整新模块指南见 [extending-guide.md](extending-guide.md)）。
