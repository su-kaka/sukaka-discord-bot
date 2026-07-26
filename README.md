# Sukaka Discord Bot

一个面向频道管理的 Discord 机器人，支持白名单权限控制、禁言投票、消息删除与消息标注，并提供本地保活静态页面。

## 功能

- 通过环境变量读取机器人 Token：DISCORD_TOKEN
- 仅允许在指定频道使用：1293095144806940738
- 采用白名单机制：仅白名单用户可执行任何操作
- 发起禁言投票：
	- 选择禁言对象
	- 选择禁言时长（默认 30 分钟，最长 24 小时）
	- 达到 5 票后自动执行禁言（Timeout）
- 通过消息链接删除消息
- 通过消息链接标注消息（使用 Discord 置顶消息 / Pin）
- 通过消息链接取消标注消息（取消置顶 / Unpin）
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

示例：

```env
DISCORD_TOKEN=your_bot_token_here
MUTE_WHITELIST=123456789012345678,234567890123456789
```

说明：

- 如果 MUTE_WHITELIST 为空，则没有用户可以执行任何指令或投票操作。
- 机器人仅在固定频道 1293095144806940738 接收与处理指令。

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
- 投票达到 5 票后自动执行禁言
- 目标成员不可参与自己的投票
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
- Use Application Commands
- Read Message History
- Manage Messages（用于删除消息、置顶消息、取消置顶消息）
- Moderate Members（用于禁言/Timeout）

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
- 确认机器人角色权限足够（Manage Messages / Moderate Members）

3. 无法禁言

- 机器人角色层级需高于目标成员
- 机器人需拥有 Moderate Members 权限

## 项目文件

- bot.py：机器人主程序
- requirements.txt：依赖列表
- pyproject.toml：项目元数据与依赖声明