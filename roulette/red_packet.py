"""红包：发送者押 10 点，8 点随机分给抢红包的人（2 点销毁）。"""

from __future__ import annotations

import random
from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota
from roulette.constants import (
    RED_PACKET_COST,
    RED_PACKET_MAX_GRABBERS,
    RED_PACKET_MAX_WINNERS,
    RED_PACKET_POOL,
    RED_PACKET_TIMEOUT_SECONDS,
)
from roulette.utils import split_random


class RedPacketView(discord.ui.View):
    """红包视图：发送者押 10 点，8 点随机分给抢红包的人（2 点销毁）。"""

    def __init__(
        self,
        sender: discord.Member | discord.User,
        client: httpx.AsyncClient,
        on_finish: Optional[object] = None,
    ) -> None:
        super().__init__(timeout=RED_PACKET_TIMEOUT_SECONDS)
        self.sender = sender
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self.grabbers: list[discord.Member | discord.User] = []
        self._on_finish = on_finish

    def _finish(self) -> None:
        if callable(self._on_finish):
            self._on_finish()

    def _packet_text(self) -> str:
        names = "、".join(u.mention for u in self.grabbers) or "暂无"
        return (
            f"🧧 {self.sender.mention} 发了一个红包！（{len(self.grabbers)}/{RED_PACKET_MAX_GRABBERS}）\n"
            f"{RED_PACKET_POOL} 点随机分给最多 {RED_PACKET_MAX_WINNERS} 个幸运儿"
            f"（红包 {RED_PACKET_COST} 点，2 点销毁），其余人抢 0 点！\n"
            f"已参与：{names}\n"
            f"满 {RED_PACKET_MAX_GRABBERS} 人立即开奖，{RED_PACKET_TIMEOUT_SECONDS} 秒未满按参与人数开奖。"
        )

    @discord.ui.button(label="抢红包", style=discord.ButtonStyle.danger, emoji="🧧")
    async def grab_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        user = interaction.user
        if user.id == self.sender.id:
            await interaction.response.send_message("不能抢自己的红包。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("红包已开奖。", ephemeral=True)
            return
        if any(u.id == user.id for u in self.grabbers):
            await interaction.response.send_message("你已经参与了。", ephemeral=True)
            return

        # 原子加入：append 与满员判断之间不插入 await
        self.grabbers.append(user)
        is_full = len(self.grabbers) >= RED_PACKET_MAX_GRABBERS
        if is_full:
            self.completed = True

        await interaction.response.send_message(
            f"🧧 已参与 {self.sender.mention} 的红包（{len(self.grabbers)}/{RED_PACKET_MAX_GRABBERS}），"
            "等待开奖！",
            ephemeral=True,
        )

        if is_full:
            await self._settle()
        elif self.message:
            await self.message.edit(content=self._packet_text(), view=self)

    async def _settle(self) -> None:
        """开奖：随机选出最多 3 个幸运儿分奖池，其余人 0 点。"""
        if self.completed:
            return
        self.completed = True
        self.stop()
        self._finish()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        winner_count = min(RED_PACKET_MAX_WINNERS, len(self.grabbers))
        winners = random.sample(self.grabbers, winner_count)
        shares = split_random(RED_PACKET_POOL, winner_count)

        results: list[tuple[discord.Member | discord.User, int, Optional[int]]] = []
        for user, amount in zip(winners, shares):
            new_quota = await adjust_quota(self.client, "grant", user.name, amount)
            results.append((user, amount, new_quota))
        for user in self.grabbers:
            if user not in winners:
                results.append((user, 0, None))

        lines = [
            f"🧧 {self.sender.mention} 的红包开奖！"
            f"（{len(self.grabbers)} 人参与，{winner_count} 人中奖，奖池 {RED_PACKET_POOL} 点）"
        ]
        for user, amount, new_quota in sorted(results, key=lambda r: r[1], reverse=True):
            if amount == 0:
                lines.append(f"💨 {user.mention} 手气不佳，抢到 0 点")
            elif new_quota is None:
                lines.append(f"🧧 {user.mention} 抢到 **{amount} 点**（发放失败，请联系管理员）")
            else:
                lines.append(f"🧧 {user.mention} 抢到 **{amount} 点**（当前 {new_quota} 点）")

        if self.message:
            await self.message.edit(content="\n".join(lines), view=None)

    async def on_timeout(self) -> None:
        if self.completed:
            return
        if not self.grabbers:
            self._finish()
            await adjust_quota(self.client, "grant", self.sender.name, RED_PACKET_COST)
            if self.message:
                await self.message.edit(
                    content=f"🧧 {self.sender.mention} 的红包无人参与，已退回 {RED_PACKET_COST} 点。",
                    view=None,
                )
            return
        # 有人参与则按参与人数开奖
        await self._settle()
