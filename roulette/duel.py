"""决斗：双方押上额度最少者的全部额度，赢家获得 80%（20% 手续费销毁）。"""

from __future__ import annotations

import random
from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import DUEL_FEE_PERCENT, DUEL_MIN_QUOTA, DUEL_TIMEOUT_SECONDS


class DuelView(discord.ui.View):
    """决斗视图：被挑战者接受后，双方押上额度最少者的全部额度，赢家获得 80%（20% 手续费销毁）。"""

    def __init__(
        self,
        challenger: discord.Member | discord.User,
        opponent: discord.Member | discord.User,
        client: httpx.AsyncClient,
        on_finish: Optional[object] = None,
        cursed_users: Optional[set[int]] = None,
    ) -> None:
        super().__init__(timeout=DUEL_TIMEOUT_SECONDS)
        self.challenger = challenger
        self.opponent = opponent
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self._on_finish = on_finish
        self._cursed_users = cursed_users if cursed_users is not None else set()

    def _finish(self) -> None:
        if callable(self._on_finish):
            self._on_finish()

    @discord.ui.button(label="接受决斗", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        user = interaction.user
        if user.id == self.challenger.id:
            await interaction.response.send_message("不能和自己决斗。", ephemeral=True)
            return
        if user.id != self.opponent.id:
            await interaction.response.send_message("只有被挑战者能接受决斗。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("决斗已结束。", ephemeral=True)
            return

        # 查询双方额度
        quotas: dict[int, int] = {}
        for player in (self.challenger, self.opponent):
            quota = await query_quota(self.client, player.name)
            if quota is None:
                await interaction.response.send_message(
                    f"查询 {player.display_name} 额度失败，请稍后再试。", ephemeral=True
                )
                return
            if quota < DUEL_MIN_QUOTA:
                await interaction.response.send_message(
                    f"{player.display_name} 额度不足（当前 {quota} 点，需 ≥ {DUEL_MIN_QUOTA} 点），决斗取消。",
                    ephemeral=True,
                )
                return
            quotas[player.id] = quota

        if self.completed:
            await interaction.response.send_message("决斗已结束。", ephemeral=True)
            return

        # 赌注为双方额度最少者的全部额度
        stake = min(quotas[self.challenger.id], quotas[self.opponent.id])

        # 收双方赌注
        paid: list[discord.Member | discord.User] = []
        for player in (self.challenger, self.opponent):
            result = await adjust_quota(self.client, "deduct", player.name, stake)
            if result is None:
                for q in paid:
                    await adjust_quota(self.client, "grant", q.name, stake)
                await interaction.response.send_message("收取赌注失败，决斗取消。", ephemeral=True)
                return
            paid.append(player)

        self.completed = True
        self.stop()
        self._finish()

        # roll 点定胜负，平局加赛
        c_roll, o_roll = random.randint(1, 100), random.randint(1, 100)
        while c_roll == o_roll:
            c_roll, o_roll = random.randint(1, 100), random.randint(1, 100)

        # 诅咒生效：被诅咒者决斗必输
        curse_note = ""
        c_cursed = self.challenger.id in self._cursed_users
        o_cursed = self.opponent.id in self._cursed_users
        if c_cursed and not o_cursed:
            self._cursed_users.discard(self.challenger.id)
            winner, loser = self.opponent, self.challenger
            curse_note = f"\n🔮 诅咒生效！{self.challenger.mention} 注定失败！"
        elif o_cursed and not c_cursed:
            self._cursed_users.discard(self.opponent.id)
            winner, loser = self.challenger, self.opponent
            curse_note = f"\n🔮 诅咒生效！{self.opponent.mention} 注定失败！"
        else:
            winner, loser = (self.challenger, self.opponent) if c_roll > o_roll else (self.opponent, self.challenger)

        gross_prize = stake * 2
        fee = int(gross_prize * DUEL_FEE_PERCENT / 100)
        prize = gross_prize - fee
        new_quota = await adjust_quota(self.client, "grant", winner.name, prize)
        result_text = (
            f"⚔️ **决斗结果**\n"
            f"{self.challenger.mention} rolled **{c_roll}**\n"
            f"{self.opponent.mention} rolled **{o_roll}**\n"
            f"{curse_note}"
        )
        if new_quota is None:
            result_text += f"🏆 {winner.mention} 获胜！但奖金发放失败，请联系管理员手动补发 {prize} 点。"
        else:
            result_text += (
                f"🏆 {winner.mention} 获胜，赢得 **{prize} 点**"
                f"（奖池 {gross_prize} 点，手续费 {fee} 点销毁）！当前额度 {new_quota} 点。"
            )

        await interaction.response.send_message(result_text)
        if self.message:
            await self.message.edit(view=None)

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.secondary, emoji="🏳️")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("只有被挑战者能拒绝。", ephemeral=True)
            return
        if self.completed:
            return
        self.completed = True
        self.stop()
        self._finish()
        await interaction.response.send_message(
            f"🏳️ {self.opponent.mention} 拒绝了 {self.challenger.mention} 的决斗。"
        )
        if self.message:
            await self.message.edit(view=None)

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self._finish()
        if self.message:
            await self.message.edit(
                content=f"⚔️ {self.opponent.mention} 未应战，{self.challenger.mention} 的决斗已过期。",
                view=None,
            )
