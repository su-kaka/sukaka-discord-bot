"""机器人大红包：定时发送，500 点奖池，最多 8 人抢，全员分完整池。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
import httpx

from roulette.constants import (
    BIG_RED_PACKET_INTERVAL_SECONDS,
    BIG_RED_PACKET_POOL,
    PACKET_TIMEOUT_SECONDS,
    QUOTA_CHANNEL_ID,
    RED_PACKET_MAX_GRABBERS,
)
from roulette.packet_base import PacketView

if TYPE_CHECKING:
    from bot import SukakaBot


class BigRedPacketView(PacketView):
    """机器人大红包：500 点奖池，最多 8 人抢，全员分完整池。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            sender=None,
            client=client,
            pool=BIG_RED_PACKET_POOL,
            max_grabbers=RED_PACKET_MAX_GRABBERS,
            timeout=PACKET_TIMEOUT_SECONDS,
            packet_type="big",
        )


async def big_red_packet_loop(bot: "SukakaBot", client: httpx.AsyncClient) -> None:
    """每 10 分钟向频道发送一次机器人大红包，自动对齐到整 10 分钟时刻。"""
    while True:
        # 对齐到下一个整 10 分钟（如 1:10、1:20、1:30…）
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
