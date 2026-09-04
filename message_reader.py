"""消息读取测试模块：用于验证 bot 是否有权限读取指定频道的历史消息。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import SukakaBot

# 目标频道 ID，可通过环境变量 MESSAGE_READER_CHANNEL_ID 覆盖
DEFAULT_CHANNEL_ID = 1455038454772531311
# 默认读取最近 10 条消息
DEFAULT_LIMIT = 10


async def test_read_messages(bot: "SukakaBot") -> None:
    """读取指定频道的最近消息并打印到控制台。

    用于验证 bot 是否拥有该频道的 Read Message History 权限。
    """
    channel_id = int(os.getenv("MESSAGE_READER_CHANNEL_ID", DEFAULT_CHANNEL_ID))
    limit = int(os.getenv("MESSAGE_READER_LIMIT", DEFAULT_LIMIT))

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            print(f"[MessageReader] 错误：找不到频道 {channel_id}")
            return
        except discord.Forbidden:
            print(f"[MessageReader] 错误：没有权限访问频道 {channel_id}")
            return
        except discord.HTTPException as e:
            print(f"[MessageReader] 错误：获取频道失败 - {e}")
            return

    if not isinstance(channel, discord.TextChannel):
        print(f"[MessageReader] 错误：频道 {channel_id} 不是文字频道")
        return

    print(f"[MessageReader] 开始读取频道 #{channel.name} ({channel_id}) 的最近 {limit} 条消息...")

    try:
        messages = [msg async for msg in channel.history(limit=limit)]
    except discord.Forbidden:
        print(f"[MessageReader] 错误：没有权限读取频道 {channel_id} 的消息历史")
        print("[MessageReader] 请确认 bot 拥有该频道的 View Channel 和 Read Message History 权限")
        return
    except discord.HTTPException as e:
        print(f"[MessageReader] 错误：读取消息历史失败 - {e}")
        return

    if not messages:
        print(f"[MessageReader] 频道 #{channel.name} 暂无消息")
        return

    print(f"[MessageReader] 成功读取 {len(messages)} 条消息：")
    print("-" * 60)
    for msg in reversed(messages):  # 按时间正序打印
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{msg.author.name}#{msg.author.discriminator}" if msg.author.discriminator != "0" else msg.author.name
        content = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
        if not content and msg.attachments:
            content = f"[附件: {len(msg.attachments)} 个]"
        elif not content and msg.embeds:
            content = f"[嵌入消息: {len(msg.embeds)} 个]"
        elif not content:
            content = "[空消息]"
        print(f"[{timestamp}] {author}: {content}")
    print("-" * 60)
    print(f"[MessageReader] 读取完成，bot 拥有频道 {channel_id} 的消息读取权限 ✓")


def start_message_reader(bot: "SukakaBot") -> None:
    """在 bot ready 后启动消息读取测试。"""
    bot.loop.create_task(test_read_messages(bot))
