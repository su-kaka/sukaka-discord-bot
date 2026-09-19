"""通用红包视图：支持人机验证、多种红包类型，全员随机分完整池。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Callable, Optional

import discord
import httpx

from roulette.api import adjust_quota
from roulette.utils import make_arithmetic_question, split_random


class PacketView(discord.ui.View):
    """通用红包视图。

    参数说明：
        sender: 发送者（机器人红包传 None）。
        client: HTTP 客户端。
        pool: 奖池总额。
        max_grabbers: 最大参与人数。
        timeout: 超时秒数。
        packet_type: 红包类型标识（"user" / "big" / "selfdestruct"）。
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
        cost: Optional[int] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.sender = sender
        self.client = client
        self.pool = pool
        self.max_grabbers = max_grabbers
        self.packet_type = packet_type
        self.cost = cost
        self._on_finish = on_finish

        self.message: Optional[discord.Message] = None
        self.completed = False
        self.grabbers: list[discord.Member | discord.User] = []
        self.failed_users: set[int] = set()
        self._start_time = time.monotonic()  # 视图创建时间，用于计算剩余超时
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
            rule = "奖池随机分给所有参与的人，人满立即开奖！"
        elif self.packet_type == "selfdestruct":
            title = f"💥 {self.sender.mention} 自爆了一个红包！"
            rule = "奖池随机分给所有参与的人，人满立即开奖！"
        else:
            title = f"🧧 {self.sender.mention} 发了一个红包！"
            fee = (self.cost or self.pool) - self.pool
            rule = (
                f"{self.pool} 点随机分给所有参与的人"
                f"（红包 {self.cost} 点，{fee} 点销毁），人满立即开奖！"
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
        # 下线状态：无法抢红包（延迟导入避免循环依赖）
        from roulette.gacha import is_offline

        if is_offline(user.id):
            await interaction.response.send_message(
                "🔌 你处于下线状态，无法抢红包！发言可解除下线状态。", ephemeral=True
            )
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
                    # 编辑后重新注册 view 会重置超时计时器，先记下剩余时间再恢复
                    remaining = max(self.timeout - (now - self._start_time), 0.0)
                    await self.message.edit(content=self._packet_text(), view=self)
                    if remaining <= 0:
                        await self._settle()
                    else:
                        self.timeout = remaining
                except (discord.NotFound, discord.HTTPException):
                    pass

    # ---------- 结算 ----------

    async def _settle(self) -> None:
        if self.completed:
            return
        self.completed = True
        self._finish()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

        count = len(self.grabbers)
        if count == 0:
            await self._handle_empty()
            return

        # 全员随机分完整池
        shares = split_random(self.pool, count)
        # 幸运儿生效：所有触发效果的幸运儿并列最大（延迟导入避免循环依赖）
        from roulette.gacha import consume_effect

        lucky_users = [u for u in self.grabbers if consume_effect(u.id, "lucky")]
        lucky_note = ""
        if lucky_users:
            # 把前 N 大的份额全部给幸运儿，其余人分剩下的
            order = sorted(range(count), key=lambda i: shares[i], reverse=True)
            top_vals = [shares[i] for i in order[:len(lucky_users)]]
            rest_vals = [shares[i] for i in order[len(lucky_users):]]
            lucky_set = {u.id for u in lucky_users}
            ti = ri = 0
            for i, u in enumerate(self.grabbers):
                if u.id in lucky_set:
                    shares[i] = top_vals[ti]
                    ti += 1
                else:
                    shares[i] = rest_vals[ri]
                    ri += 1
            lucky_note = (
                f"\n🃏 幸运儿生效！{'、'.join(u.mention for u in lucky_users)} 并列抢到最大份！"
            )
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
            header = (
                f"🧧 {self.sender.mention} 的红包开奖！"
                f"（{count} 人参与，奖池 {self.pool} 点）"
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
                # 先 stop() 再编辑：stop 后 is_finished() 为 True，
                # message.edit 不会重新注册 view，超时计时器也不会重启
                self.stop()
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
                self.stop()
                await self.message.edit(content=text, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def on_timeout(self) -> None:
        if self.completed:
            return
        await self._settle()
