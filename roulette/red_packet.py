"""红包：发送者押额度的 10%（最少 10 点），80% 随机分给抢红包的人（20% 销毁）。"""

from __future__ import annotations

from typing import Optional

import discord
import httpx

from roulette.constants import (
    RED_PACKET_MAX_GRABBERS,
    RED_PACKET_MAX_WINNERS,
    RED_PACKET_TIMEOUT_SECONDS,
)
from roulette.packet_base import PacketView


class RedPacketView(PacketView):
    """用户红包视图：发送者押额度的 10%（最少 10 点），80% 随机分给抢红包的人（20% 销毁）。"""

    def __init__(
        self,
        sender: discord.Member | discord.User,
        client: httpx.AsyncClient,
        cost: int,
        pool: int,
        on_finish: Optional[object] = None,
    ) -> None:
        super().__init__(
            sender=sender,
            client=client,
            pool=pool,
            max_grabbers=RED_PACKET_MAX_GRABBERS,
            timeout=RED_PACKET_TIMEOUT_SECONDS,
            packet_type="user",
            split_mode="winners",
            max_winners=RED_PACKET_MAX_WINNERS,
            cost=cost,
            on_finish=on_finish,
        )
