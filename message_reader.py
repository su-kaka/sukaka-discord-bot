"""消息读取测试模块：验证 bot 是否能被动接收指定频道的新消息事件。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import SukakaBot

# 目标频道 ID，可通过环境变量 MESSAGE_READER_CHANNEL_ID 覆盖
DEFAULT_CHANNEL_ID = 1455038454772531311


async def on_message(message: discord.Message) -> None:
    """被动接收新消息事件并打印。

    用于验证 bot 是否拥有该频道的消息事件接收权限
    （需要 View Channel 权限 + Message Content Intent）。
    """
    channel_id = int(os.getenv("MESSAGE_READER_CHANNEL_ID", DEFAULT_CHANNEL_ID))

    # 只打印目标频道的消息
    if message.channel.id != channel_id:
        return

    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
    author = message.author.name
    is_bot = message.author.bot

    content = message.content
    if not content and message.attachments:
        content = f"[附件: {len(message.attachments)} 个]"
    elif not content and message.embeds:
        content = f"[嵌入消息: {len(message.embeds)} 个]"
    elif not content:
        content = "[空消息/无内容权限]"

    bot_tag = " [BOT]" if is_bot else ""
    print(f"[MessageReader] [{timestamp}] {author}{bot_tag}: {content}")


def start_message_reader(bot: "SukakaBot") -> None:
    """注册 on_message 监听，开始被动接收消息。"""
    channel_id = int(os.getenv("MESSAGE_READER_CHANNEL_ID", DEFAULT_CHANNEL_ID))
    bot.add_listener(on_message)
    print(f"[MessageReader] 已注册消息监听，等待频道 {channel_id} 的新消息...")
    print("[MessageReader] 提示：若一直收不到消息，请检查：")
    print("  1. bot 是否有该频道的 View Channel 权限")
    print("  2. Developer Portal 是否已开启 Message Content Intent")
    print("  3. 代码中 intents.message_content 是否为 True")
