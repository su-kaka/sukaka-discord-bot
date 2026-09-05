"""机器人大红包：定时发送，200 点奖池，最多 10 人抢，每人随机 0-100 点。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from roulette.api import adjust_quota
from roulette.constants import (
    BIG_RED_PACKET_INTERVAL_SECONDS,
    BIG_RED_PACKET_MAX_GRABBERS,
    BIG_RED_PACKET_MAX_SHARE,
    BIG_RED_PACKET_POOL,
    BIG_RED_PACKET_TIMEOUT_SECONDS,
    QUOTA_CHANNEL_ID,
)
from roulette.utils import make_arithmetic_question, split_random_capped

if TYPE_CHECKING:
    from bot import SukakaBot


class BigRedPacketView(discord.ui.View):
    """机器人大红包：200 点奖池，最多 10 人抢，每人随机 0-100 点。

    参与前需通过人机验证：答对一道十以内加减法（三个选项仅一个正确），
    答错即失去本次参与资格。
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(timeout=BIG_RED_PACKET_TIMEOUT_SECONDS)
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self.grabbers: list[discord.Member | discord.User] = []
        self.failed_users: set[int] = set()  # 答错失去资格的用户 ID
        self.question, self.answer, options = make_arithmetic_question()
        for value in options:
            self.add_item(self._make_option_button(value))

    def _make_option_button(self, value: int) -> discord.ui.Button:
        """创建一个答案选项按钮，callback 绑定对应的数值。"""
        button = discord.ui.Button(
            label=str(value),
            style=discord.ButtonStyle.danger,
            emoji="🧧",
        )

        async def _callback(interaction: discord.Interaction, option: int = value) -> None:
            await self._answer(interaction, option)

        button.callback = _callback
        return button

    def _packet_text(self) -> str:
        names = "、".join(u.mention for u in self.grabbers) or "暂无"
        return (
            f"🧧🧧 **机器人大红包**！奖池 **{BIG_RED_PACKET_POOL} 点**，"
            f"最多 {BIG_RED_PACKET_MAX_GRABBERS} 人参与（{len(self.grabbers)}/{BIG_RED_PACKET_MAX_GRABBERS}）\n"
            f"每人随机抢 0-{BIG_RED_PACKET_MAX_SHARE} 点，手快有手慢无！\n"
            f"🧮 人机验证：**{self.question}**\n"
            f"点击下方正确答案参与，答错将失去本次参与资格！\n"
            f"已参与：{names}\n"
            f"满 {BIG_RED_PACKET_MAX_GRABBERS} 人立即开奖，"
            f"{BIG_RED_PACKET_TIMEOUT_SECONDS} 秒未满按参与人数开奖。"
        )

    async def _answer(self, interaction: discord.Interaction, option: int) -> None:
        """处理选项点击：答对参与，答错失去资格。"""
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("机器人不能抢红包。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("红包已开奖。", ephemeral=True)
            return
        if user.id in self.failed_users:
            await interaction.response.send_message(
                "你已回答错误，失去本次参与资格。", ephemeral=True
            )
            return
        if any(u.id == user.id for u in self.grabbers):
            await interaction.response.send_message("你已经参与了。", ephemeral=True)
            return

        if option != self.answer:
            self.failed_users.add(user.id)
            await interaction.response.send_message(
                "❌ 回答错误，已失去本次大红包参与资格！", ephemeral=True
            )
            return

        # 原子加入：append 与满员判断之间不插入 await
        self.grabbers.append(user)
        is_full = len(self.grabbers) >= BIG_RED_PACKET_MAX_GRABBERS
        if is_full:
            self.completed = True

        await interaction.response.send_message(
            f"🧧 验证通过，已参与大红包（{len(self.grabbers)}/{BIG_RED_PACKET_MAX_GRABBERS}），等待开奖！",
            ephemeral=True,
        )

        if is_full:
            await self._settle()
        elif self.message:
            await self.message.edit(content=self._packet_text(), view=self)

    async def _settle(self) -> None:
        """开奖：参与者每人随机 0-100 点，总和不超过奖池。"""
        self.completed = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

        # 先禁用按钮并提示开奖中，避免长时间无反馈
        if self.message:
            try:
                await self.message.edit(content="🧧🧧 大红包开奖中…", view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

        shares = split_random_capped(
            BIG_RED_PACKET_POOL, len(self.grabbers), BIG_RED_PACKET_MAX_SHARE
        )

        # 并发发放额度，避免串行等待
        async def _grant(user: discord.Member | discord.User, amount: int) -> Optional[int]:
            if amount > 0:
                return await adjust_quota(self.client, "grant", user.name, amount)
            return None

        quotas = await asyncio.gather(
            *[_grant(user, amount) for user, amount in zip(self.grabbers, shares)]
        )
        results: list[tuple[discord.Member | discord.User, int, Optional[int]]] = [
            (user, amount, quota)
            for user, amount, quota in zip(self.grabbers, shares, quotas)
        ]

        total_granted = sum(amount for _, amount, _ in results)
        lines = [
            f"🧧🧧 **机器人大红包开奖！**（{len(self.grabbers)} 人参与，"
            f"共发出 {total_granted}/{BIG_RED_PACKET_POOL} 点）"
        ]
        for user, amount, new_quota in sorted(results, key=lambda r: r[1], reverse=True):
            if amount == 0:
                lines.append(f"💨 {user.mention} 手气不佳，抢到 0 点")
            elif new_quota is None:
                lines.append(f"🧧 {user.mention} 抢到 **{amount} 点**（发放失败，请联系管理员）")
            else:
                lines.append(f"🧧 {user.mention} 抢到 **{amount} 点**（当前 {new_quota} 点）")

        if self.message:
            try:
                await self.message.edit(content="\n".join(lines), view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def on_timeout(self) -> None:
        if self.completed:
            return
        if not self.grabbers:
            if self.message:
                await self.message.edit(content="🧧🧧 大红包无人参与，已过期。", view=None)
            return
        await self._settle()


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
