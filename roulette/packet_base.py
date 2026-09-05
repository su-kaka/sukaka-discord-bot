"""通用红包视图：支持人机验证、多种分配模式、多种红包类型。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Callable, Optional

import discord
import httpx

from roulette.api import adjust_quota
from roulette.utils import make_arithmetic_question, split_random, split_random_capped


class PacketView(discord.ui.View):
    """通用红包视图。

    参数说明：
        sender: 发送者（机器人红包传 None）。
        client: HTTP 客户端。
        pool: 奖池总额。
        max_grabbers: 最大参与人数。
        timeout: 超时秒数。
        packet_type: 红包类型标识（"user" / "big" / "selfdestruct"）。
        split_mode: 分配模式（"winners" 少数幸运儿 / "all" 全员随机）。
        max_winners: split_mode="winners" 时的幸运儿数量。
        max_share: split_mode="all" 时的单人上限。
        cost: 发送者成本（无人参与时退回，None 表示不退）。
        on_finish: 结束回调。
    """

    def __init__(
        self,
        sender: Optional[discord.Member | discord.User],
        client: httpx.AsyncClient,
        pool: int,
        max_grabbers: int,
        timeout: int,
        packet_type: str,
        split_mode: str,
        max_winners: Optional[int] = None,
        max_share: Optional[int] = None,
        cost: Optional[int] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.sender = sender
        self.client = client
        self.pool = pool
        self.max_grabbers = max_grabbers
        self.packet_type = packet_type
        self.split_mode = split_mode
        self.max_winners = max_winners
        self.max_share = max_share
        self.cost = cost
        self._on_finish = on_finish

        self.message: Optional[discord.Message] = None
        self.completed = False
        self.grabbers: list[discord.Member | discord.User] = []
        self.failed_users: set[int] = set()
        self._last_edit_time = 0.0  # 上次编辑时间，用于限流
        self.question, self.answer, options = make_arithmetic_question()
        for value in options:
            self.add_item(self._make_option_button(value))

    # ---------- 内部工具 ----------

    def _finish(self) -> None:
        if callable(self._on_finish):
            self._on_finish()

    def _make_option_button(self, value: int) -> discord.ui.Button:
        emoji = {"user": "🧧", "big": "🧧", "selfdestruct": "💥"}.get(self.packet_type, "🧧")
        button = discord.ui.Button(
            label=str(value),
            style=discord.ButtonStyle.danger,
            emoji=emoji,
        )

        async def _callback(interaction: discord.Interaction, option: int = value) -> None:
            await self._answer(interaction, option)

        button.callback = _callback
        return button

    def _packet_text(self) -> str:
        names = "、".join(u.mention for u in self.grabbers) or "暂无"
        if self.packet_type == "big":
            title = "🧧🧧 **机器人大红包**！"
            rule = f"每人随机抢 0-{self.max_share} 点，手快有手慢无！"
        elif self.packet_type == "selfdestruct":
            title = f"💥 {self.sender.mention} 自爆了一个红包！"
            rule = "奖池随机分给抢红包的人，手快有手慢无！"
        else:
            title = f"🧧 {self.sender.mention} 发了一个红包！"
            fee = (self.cost or self.pool) - self.pool
            rule = (
                f"{self.pool} 点随机分给最多 {self.max_winners} 个幸运儿"
                f"（红包 {self.cost} 点，{fee} 点销毁），其余人抢 0 点！"
            )

        return (
            f"{title}（{len(self.grabbers)}/{self.max_grabbers}）\n"
            f"奖池 **{self.pool} 点**，{rule}\n"
            f"🧮 人机验证：**{self.question}**\n"
            f"点击下方正确答案参与，答错将失去本次参与资格！\n"
            f"已参与：{names}\n"
            f"满 {self.max_grabbers} 人立即开奖，{self.timeout} 秒未满按参与人数开奖。"
        )

    # ---------- 交互处理 ----------

    async def _answer(self, interaction: discord.Interaction, option: int) -> None:
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("机器人不能抢红包。", ephemeral=True)
            return
        if self.sender and user.id == self.sender.id:
            msg = "不能抢自己的红包。" if self.packet_type == "user" else "不能抢自己的自爆红包。"
            await interaction.response.send_message(msg, ephemeral=True)
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
            packet_name = "大红包" if self.packet_type == "big" else "自爆红包" if self.packet_type == "selfdestruct" else "红包"
            await interaction.response.send_message(
                f"❌ 回答错误，已失去本次{packet_name}参与资格！", ephemeral=True
            )
            return

        # 原子加入：append 与满员判断之间不插入 await
        self.grabbers.append(user)
        is_full = len(self.grabbers) >= self.max_grabbers

        packet_name = "大红包" if self.packet_type == "big" else "自爆红包" if self.packet_type == "selfdestruct" else "红包"
        await interaction.response.send_message(
            f"🧧 验证通过，已参与{packet_name}（{len(self.grabbers)}/{self.max_grabbers}），等待开奖！",
            ephemeral=True,
        )

        if is_full:
            await self._settle()
        elif self.message and not self.completed:
            # 两次编辑间隔至少 1 秒，避免触发 Discord 429 限流
            now = time.monotonic()
            if now - self._last_edit_time >= 1.0:
                self._last_edit_time = now
                try:
                    await self.message.edit(content=self._packet_text(), view=self)
                except (discord.NotFound, discord.HTTPException):
                    pass

    # ---------- 结算 ----------

    async def _settle(self) -> None:
        if self.completed:
            return
        self.completed = True
        self.stop()
        self._finish()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

        count = len(self.grabbers)
        if count == 0:
            await self._handle_empty()
            return

        # 计算份额
        lucky_note = ""
        if self.split_mode == "winners":
            winner_count = min(self.max_winners or 1, count)
            winners = random.sample(self.grabbers, winner_count)
            shares = split_random(self.pool, winner_count)
            results_map = dict(zip(winners, shares))
            for user in self.grabbers:
                if user not in results_map:
                    results_map[user] = 0
            ordered_results = [(user, results_map[user]) for user in self.grabbers]
        else:
            shares = split_random_capped(self.pool, count, self.max_share or self.pool)
            # 幸运儿生效：下次抢大红包必定最大（延迟导入避免循环依赖）
            from roulette.gacha import consume_effect

            for user in self.grabbers:
                if consume_effect(user.id, "lucky"):
                    max_idx = shares.index(max(shares))
                    user_idx = self.grabbers.index(user)
                    shares[user_idx], shares[max_idx] = shares[max_idx], shares[user_idx]
                    lucky_note = f"\n🃏 幸运儿生效！{user.mention} 必定抢到最大份！"
                    break
            ordered_results = list(zip(self.grabbers, shares))

        # 并发发放额度
        async def _grant(user: discord.Member | discord.User, amount: int) -> Optional[int]:
            if amount > 0:
                return await adjust_quota(self.client, "grant", user.name, amount)
            return None

        quotas = await asyncio.gather(
            *[_grant(user, amount) for user, amount in ordered_results]
        )
        results: list[tuple[discord.Member | discord.User, int, Optional[int]]] = [
            (user, amount, quota)
            for (user, amount), quota in zip(ordered_results, quotas)
        ]

        # 组装开奖文案
        if self.packet_type == "big":
            total_granted = sum(amount for _, amount, _ in results)
            header = (
                f"🧧🧧 **机器人大红包开奖！**（{count} 人参与，"
                f"共发出 {total_granted}/{self.pool} 点）{lucky_note}"
            )
        elif self.packet_type == "selfdestruct":
            header = (
                f"💥 {self.sender.mention} 的自爆红包开奖！"
                f"（{count} 人参与，奖池 {self.pool} 点）"
            )
        else:
            winner_count = sum(1 for _, amount, _ in results if amount > 0)
            header = (
                f"🧧 {self.sender.mention} 的红包开奖！"
                f"（{count} 人参与，{winner_count} 人中奖，奖池 {self.pool} 点）"
            )

        lines = [header]
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

    async def _handle_empty(self) -> None:
        """无人参与时的处理。"""
        if self.packet_type == "user" and self.cost is not None and self.sender is not None:
            await adjust_quota(self.client, "grant", self.sender.name, self.cost)
            text = f"🧧 {self.sender.mention} 的红包无人参与，已退回 {self.cost} 点。"
        elif self.packet_type == "big":
            text = "🧧🧧 大红包无人参与，已过期。"
        else:
            text = "💥 自爆红包无人参与，已过期。"

        if self.message:
            try:
                await self.message.edit(content=text, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def on_timeout(self) -> None:
        if self.completed:
            return
        await self._settle()
