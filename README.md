# Sukaka Discord Bot

一个面向频道管理的 Discord 机器人，支持白名单权限控制、禁言投票、消息删除与消息标注，并提供本地保活静态页面。

## 功能

- 通过环境变量读取机器人 Token：DISCORD_TOKEN
- 仅允许在指定频道使用：1293095144806940738
- 采用白名单机制：仅白名单用户可发起投票及执行管理指令
- 发起禁言投票：
	- 选择禁言对象
	- 选择禁言时长（默认 30 分钟，最长 24 小时）
	- 达到 5 票后移除目标成员在当前频道的发言权
	- 到期后自动恢复目标成员原有的频道权限
	- 所有成员均可参与投票，不受白名单限制
- 通过消息链接删除消息
- 通过消息链接标注消息（使用 Discord 置顶消息 / Pin）
- 通过消息链接取消标注消息（取消置顶 / Unpin）
- 每隔一段时间从 `carousel.txt` 向指定频道循环发送消息（默认 5 分钟）
- 监听 0.0.0.0:7861，提供静态网页用于保活

## 运行环境

- Python 3.13+
- 依赖见 requirements.txt

## 安装依赖

```bash
pip install -r requirements.txt
```

## 环境变量

启动前请设置以下环境变量：

- DISCORD_TOKEN：Discord Bot Token
- MUTE_WHITELIST：白名单用户 ID，多个用英文逗号分隔
- CHANNEL_MUTES_FILE：可选，频道禁言恢复状态文件路径，默认 `channel_mutes.json`
- CAROUSEL_FILE：可选，循环消息文件路径，默认 `carousel.txt`
- CAROUSEL_INTERVAL_MINUTES：可选，循环发送间隔（分钟），默认 `5`

示例：

```env
DISCORD_TOKEN=your_bot_token_here
MUTE_WHITELIST=123456789012345678,234567890123456789
```

说明：

- 如果 MUTE_WHITELIST 为空，则没有用户可以发起投票或执行管理指令；所有成员仍可参与已有投票。
- 机器人仅在固定频道 1293095144806940738 接收与处理指令。
- 机器人会向频道 `1455038454772531311` 循环发送 `carousel.txt` 的内容；文件为空时跳过本轮发送。

## 启动

```bash
python bot.py
```

启动后会同时：

- 登录 Discord Bot
- 注册 Slash Commands
- 启动本地保活页面：http://0.0.0.0:7861

## 指令说明

### 1) 发起禁言投票

指令：

```text
/mute_vote target:<成员> duration_minutes:<分钟，可选> reason:<原因，可选>
```

规则：

- 默认禁言时长为 30 分钟
- 最长禁言时长为 1440 分钟（24 小时）
- 投票达到 5 票后，目标成员仅在发起投票的文字频道中被禁言
- 到期后恢复目标成员在该频道原有的权限覆盖
- 机器人重启后会继续处理尚未到期的频道禁言
- 被投票成员可以参与自己的投票
- 同一成员同一时间仅允许一个进行中的禁言投票

### 2) 删除消息

指令：

```text
/delete_message message_link:<Discord 消息链接>
```

### 3) 标注消息

指令：

```text
/mark_message message_link:<Discord 消息链接>
```

说明：

- 该指令会将目标消息置顶（Pin）

### 4) 取消标注消息

指令：

```text
/unmark_message message_link:<Discord 消息链接>
```

说明：

- 该指令会取消目标消息置顶（Unpin）

## 消息链接格式

支持标准 Discord 消息链接格式：

```text
https://discord.com/channels/<guild_id>/<channel_id>/<message_id>
```

链接必须属于当前服务器。

## Discord 权限建议

请为机器人授予以下权限（至少）：

- View Channels
- Send Messages
- Send Messages（目标频道 `1455038454772531311`，用于循环发送消息）
- Use Application Commands
- Read Message History
- Manage Messages（用于删除消息、置顶消息、取消置顶消息）
- Manage Roles（用于修改和恢复成员在当前频道的发言权限）

## 保活页面

- 监听地址：0.0.0.0:7861
- 页面用途：提供轻量静态页面，便于外部探活或平台保活

## 常见问题

1. 指令不可见或未生效

- 确认机器人已成功登录
- 等待命令同步完成
- 确认机器人在目标服务器内且有 Use Application Commands 权限

2. 提示无权限

- 确认当前用户在 MUTE_WHITELIST 中
- 确认命令在指定频道 1293095144806940738 内执行
- 确认机器人角色权限足够（Manage Messages / Manage Roles）

3. 无法禁言

- 确认机器人在当前频道拥有 Manage Roles 权限
- 服务器所有者和拥有 Administrator 权限的成员会绕过频道权限覆盖，无法被频道禁言
- 检查 `CHANNEL_MUTES_FILE` 所在目录是否可写，以便到期后恢复原权限

## 项目文件

- bot.py：机器人主程序
- requirements.txt：依赖列表
- pyproject.toml：项目元数据与依赖声明
