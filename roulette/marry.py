"""结婚：对方同意后，两人额度合并，扣 10 点手续费，剩余平分。"""

from __future__ import annotations

from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import MARRY_FEE_PERCENT, MARRY_MIN_FEE, MARRY_TIMEOUT_SECONDS


class MarryView(discord.ui.View):
    """结婚视图：对方同意后，两人额度合并，扣 10 点手续费，剩余平分。"""

    def __init__(
        self,
        proposer: discord.Member | discord.User,
        partner: discord.Member | discord.User,
        client: httpx.AsyncClient,
        on_finish: Optional[object] = None,
    ) -> None:
        super().__init__(timeout=MARRY_TIMEOUT_SECONDS)
        self.proposer = proposer
        self.partner = partner
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self._on_finish = on_finish

    def _finish(self) -> None:
        if callable(self._on_finish):
            self._on_finish()

    @discord.ui.button(label="我愿意 💍", style=discord.ButtonStyle.success, emoji="💍")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        user = interaction.user
        if user.id == self.proposer.id:
            await interaction.response.send_message("不能和自己结婚。", ephemeral=True)
            return
        if user.id != self.partner.id:
            await interaction.response.send_message("只有被求婚者能接受。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("婚礼已结束。", ephemeral=True)
            return

        # 查询双方额度
        p_quota = await query_quota(self.client, self.proposer.name)
        q_quota = await query_quota(self.client, self.partner.name)
        if p_quota is None or q_quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return

        total = p_quota + q_quota
        fee = max(MARRY_MIN_FEE, int(total * MARRY_FEE_PERCENT / 100))
        if total < fee:
            await interaction.response.send_message(
                f"两人总额度仅 {total} 点，不足以支付 {fee} 点手续费（总额度的 {MARRY_FEE_PERCENT}%，最低 {MARRY_MIN_FEE} 点），婚礼取消。",
                ephemeral=True,
            )
            return

        if self.completed:
            await interaction.response.send_message("婚礼已结束。", ephemeral=True)
            return

        # 先清零双方，再平分（total - fee）
        for player, quota in ((self.proposer, p_quota), (self.partner, q_quota)):
            if quota > 0:
                result = await adjust_quota(self.client, "deduct", player.name, quota)
                if result is None:
                    await interaction.response.send_message("结算失败，请稍后再试。", ephemeral=True)
                    return

        share = (total - fee) // 2
        bonus = (total - fee) % 2  # 奇数时多出 1 点给求婚者
        p_share = share + bonus
        q_share = share

        p_new = await adjust_quota(self.client, "grant", self.proposer.name, p_share)
        q_new = await adjust_quota(self.client, "grant", self.partner.name, q_share)

        self.completed = True
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        self._finish()

        result_text = (
            f"💍 **婚礼完成！** {self.proposer.mention} 和 {self.partner.mention} 结为夫妻！\n"
            f"两人额度合并共 {total} 点，手续费 {fee} 点已销毁，剩余 {total - fee} 点平分。\n"
            f"{self.proposer.mention} 分得 **{p_share} 点**（当前 {p_new if p_new is not None else '?'} 点）\n"
            f"{self.partner.mention} 分得 **{q_share} 点**（当前 {q_new if q_new is not None else '?'} 点）"
        )
        await interaction.response.send_message(result_text)
        if self.message:
            await self.message.edit(view=None)

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.secondary, emoji="💔")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.partner.id:
            await interaction.response.send_message("只有被求婚者能拒绝。", ephemeral=True)
            return
        if self.completed:
            return
        self.completed = True
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        self._finish()
        await interaction.response.send_message(
            f"💔 {self.partner.mention} 拒绝了 {self.proposer.mention} 的求婚。"
        )
        if self.message:
            await self.message.edit(view=None)

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self._finish()
        if self.message:
            await self.message.edit(
                content=f"💔 {self.partner.mention} 未回应，{self.proposer.mention} 的求婚已过期。",
                view=None,
            )
