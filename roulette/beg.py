"""乞讨：他人点击施舍按钮，乞讨者得 5 点，施舍者扣 7 点。"""

from __future__ import annotations

from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import BEG_COST, BEG_RECEIVE, BEG_TIMEOUT_SECONDS


class BegView(discord.ui.View):
    """乞讨视图：他人点击施舍按钮，乞讨者得 5 点，施舍者扣 7 点。"""

    def __init__(
        self,
        beggar: discord.Member | discord.User,
        client: httpx.AsyncClient,
        on_finish: Optional[object] = None,
    ) -> None:
        super().__init__(timeout=BEG_TIMEOUT_SECONDS)
        self.beggar = beggar
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self._on_finish = on_finish

    def _finish(self) -> None:
        """乞讨结束（成功或超时）时触发冷却回调。"""
        if callable(self._on_finish):
            self._on_finish()

    @discord.ui.button(label=f"施舍（-{BEG_COST} 点）", style=discord.ButtonStyle.success, emoji="🪙")
    async def give_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        giver = interaction.user
        if giver.id == self.beggar.id:
            await interaction.response.send_message("不能施舍给自己。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("这次乞讨已经结束了。", ephemeral=True)
            return

        quota = await query_quota(self.client, giver.name)
        if quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return
        if quota < BEG_COST:
            await interaction.response.send_message(
                f"额度不足：当前 {quota} 点，施舍需要至少 {BEG_COST} 点。", ephemeral=True
            )
            return

        # 查询期间可能已被他人抢先施舍，重新检查
        if self.completed:
            await interaction.response.send_message("这次乞讨已经结束了。", ephemeral=True)
            return

        # 先扣施舍者，成功后再发乞讨者；失败则回滚
        giver_quota = await adjust_quota(self.client, "deduct", giver.name, BEG_COST)
        if giver_quota is None:
            await interaction.response.send_message("扣除额度失败，请稍后再试。", ephemeral=True)
            return
        beggar_quota = await adjust_quota(self.client, "grant", self.beggar.name, BEG_RECEIVE)
        if beggar_quota is None:
            await adjust_quota(self.client, "grant", giver.name, BEG_COST)
            await interaction.response.send_message("发放额度失败，已退回你的额度。", ephemeral=True)
            return

        self.completed = True
        self.stop()
        self._finish()
        await interaction.response.send_message(
            f"🪙 {giver.mention} 施舍了 {self.beggar.mention}！"
            f"你扣除 {BEG_COST} 点（当前 {giver_quota} 点），"
            f"对方获得 {BEG_RECEIVE} 点（当前 {beggar_quota} 点）。"
        )
        if self.message:
            await self.message.edit(
                content=(
                    f"🙏 {self.beggar.mention} 的乞讨已被 {giver.mention} 施舍！\n"
                    f"{self.beggar.mention} 获得 {BEG_RECEIVE} 点（当前 {beggar_quota} 点），"
                    f"{giver.mention} 扣除 {BEG_COST} 点（当前 {giver_quota} 点）。"
                ),
                view=None,
            )

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self._finish()
        if self.message:
            await self.message.edit(
                content=f"🙏 {self.beggar.mention} 的乞讨无人理会，已过期。", view=None
            )
