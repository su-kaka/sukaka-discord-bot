"""机器人大红包：定时发送，200 点奖池，最多 10 人抢，每人随机 0-100 点。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
import httpx

from roulette.constants import (
    BIG_RED_PACKET_INTERVAL_SECONDS,
    BIG_RED_PACKET_MAX_GRABBERS,
    BIG_RED_PACKET_MAX_SHARE,
    BIG_RED_PACKET_POOL,
    BIG_RED_PACKET_TIMEOUT_SECONDS,
    QUOTA_CHANNEL_ID,
)
from roulette.packet_base import PacketView

if TYPE_CHECKING:
    from bot import SukakaBot


class BigRedPacketView(PacketView):
    """机器人大红包：200 点奖池，最多 10 人抢，每人随机 0-100 点。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            sender=None,
            client=client,
            pool=BIG_RED_PACKET_POOL,
            max_grabbers=BIG_RED_PACKET_MAX_GRABBERS,
            timeout=BIG_RED_PACKET_TIMEOUT_SECONDS,
            packet_type="big",
            split_mode="all",
            max_share=BIG_RED_PACKET_MAX_SHARE,
        )


async def big_red_packet_loop(bot: "SukakaBot", client: httpx.AsyncClient) -> None:
    """每 6 分钟向频道发送一次机器人大红包，自动对齐到整 6 分钟时刻。"""
    while True:
        # 对齐到下一个整 6 分钟（如 1:06、1:12、1:18…）
        now = asyncio.get_event_loop().time()
        interval = BIG_RED_PACKET_INTERVAL_SECONDS
        sleep_seconds = interval - (now % interval)
        await asyncio.sleep(sleep_seconds)
        try:
            channel = bot.get_channel(QUOTA_CHANNEL_ID)
            if channel is None:
                channel = await bot.fetch_channel(QUOTA_CHANNEL_ID)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                print(f"[BigRedPacket] 频道 {QUOTA_CHANNEL_ID} 不是文字频道或帖子，跳过本轮")
                continue
            view = BigRedPacketView(client)
            view.message = await channel.send(view._packet_text(), view=view)
            print(f"[BigRedPacket] 已在频道 {QUOTA_CHANNEL_ID} 发送大红包")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[BigRedPacket] 发送大红包失败: {exc}")
