"""赌大小：多人报名各押 5 点活动额度，开奖比大小，赢家通吃。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    BET_AMOUNT,
    FEE_AMOUNT,
    JOIN_TIMEOUT_SECONDS,
    PLAYER_COUNT,
)

if TYPE_CHECKING:
    from bot import SukakaBot


class JoinView(discord.ui.View):
    def __init__(self, game: "DiceGame") -> None:
        super().__init__(timeout=JOIN_TIMEOUT_SECONDS)
        self.game = game

    @discord.ui.button(label="参加（押 5 点）", style=discord.ButtonStyle.primary, emoji="🎲")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.game.add_player(interaction)

    async def on_timeout(self) -> None:
        await self.game.cancel()


class DiceGame:
    """多人赌大小：每人 roll 1-100，点数最大者通吃。"""

    def __init__(self, bot: "SukakaBot", channel: discord.abc.Messageable, client: httpx.AsyncClient) -> None:
        self.bot = bot
        self.channel = channel
        self.client = client
        self.players: list[discord.Member | discord.User] = []
        self.message: Optional[discord.Message] = None
        self.join_view: Optional[JoinView] = None
        self.started = False
        self.finished = False

    async def open_lobby(self) -> None:
        self.join_view = JoinView(self)
        self.message = await self.channel.send(self._lobby_text(), view=self.join_view)

    def _lobby_text(self) -> str:
        names = "、".join(p.mention for p in self.players) or "暂无"
        return (
            f"🎲 **赌大小**（{len(self.players)}/{PLAYER_COUNT}）\n"
            f"规则：每人随机 roll 1-100 点，点数最大者通吃奖池。\n"
            f"入场费：{BET_AMOUNT} 点活动额度，奖池 {PLAYER_COUNT * BET_AMOUNT} 点"
            f"（其中 {FEE_AMOUNT} 点作为手续费销毁）。\n"
            f"已加入：{names}\n"
            f"满 {PLAYER_COUNT} 人自动开奖，{JOIN_TIMEOUT_SECONDS} 秒未满自动取消。"
        )

    async def add_player(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if self.started or self.finished:
            await interaction.response.send_message("这一局已经开始了。", ephemeral=True)
            return
        if any(p.id == user.id for p in self.players):
            await interaction.response.send_message("你已经加入了。", ephemeral=True)
            return

        quota = await query_quota(self.client, user.name)
        if quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return
        if quota < BET_AMOUNT:
            await interaction.response.send_message(
                f"额度不足：当前 {quota} 点，需要至少 {BET_AMOUNT} 点。", ephemeral=True
            )
            return

        # 查询额度期间可能已有其他交互抢先开局/取消，必须重新检查
        if self.started or self.finished:
            await interaction.response.send_message("这一局已经开始了。", ephemeral=True)
            return
        if any(p.id == user.id for p in self.players):
            await interaction.response.send_message("你已经加入了。", ephemeral=True)
            return

        # 原子决策：append → 判断是否满员 → 标记 started，中间不插入 await，
        # 保证单事件循环下只有一个交互能触发开局
        self.players.append(user)
        should_start = len(self.players) >= PLAYER_COUNT
        if should_start:
            self.started = True
            if self.join_view:
                self.join_view.stop()

        await interaction.response.send_message(f"已加入！（{len(self.players)}/{PLAYER_COUNT}）", ephemeral=True)

        if should_start:
            await self._run_game()
        elif self.message:
            await self.message.edit(content=self._lobby_text(), view=self.join_view)

    async def cancel(self) -> None:
        if self.finished or self.started:
            return
        self.finished = True
        if self.message:
            await self.message.edit(
                content=f"🎲 人数不足（{len(self.players)}/{PLAYER_COUNT}），已取消。", view=None
            )

    async def _run_game(self) -> None:
        # 收入场费
        paid: list[discord.Member | discord.User] = []
        for p in self.players:
            result = await adjust_quota(self.client, "deduct", p.name, BET_AMOUNT)
            if result is None:
                for q in paid:
                    await adjust_quota(self.client, "grant", q.name, BET_AMOUNT)
                await self.channel.send("⚠️ 收取入场费失败，本局取消，已扣除的额度已退回。")
                self.finished = True
                return
            paid.append(p)

        # 开奖
        rolls: list[tuple[discord.Member | discord.User, int]] = [
            (p, random.randint(1, 100)) for p in self.players
        ]
        rolls.sort(key=lambda x: x[1], reverse=True)

        lines = ["🎲 **开奖结果**"]
        for rank, (p, point) in enumerate(rolls, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
            lines.append(f"{medal} {p.mention} rolled **{point}**")
        await self.channel.send("\n".join(lines))

        # 处理平局：最高点并列则加赛
        top_point = rolls[0][1]
        winners = [p for p, point in rolls if point == top_point]
        while len(winners) > 1:
            await self.channel.send(f"⚔️ 最高点 {top_point} 并列，加赛一轮！")
            tie_rolls = {p: random.randint(1, 100) for p in winners}
            tie_lines = [f"{p.mention} rolled **{point}**" for p, point in tie_rolls.items()]
            await self.channel.send("\n".join(tie_lines))
            top_point = max(tie_rolls.values())
            winners = [p for p, point in tie_rolls.items() if point == top_point]

        winner = winners[0]
        pot = BET_AMOUNT * len(self.players)
        prize = pot - FEE_AMOUNT  # 销毁 FEE_AMOUNT 点作为手续费
        new_quota = await adjust_quota(self.client, "grant", winner.name, prize)
        if new_quota is None:
            await self.channel.send(
                f"🏆 {winner.mention} 获胜！但奖池发放失败，请联系管理员手动补发 {prize} 点。"
            )
        else:
            await self.channel.send(
                f"🏆 {winner.mention} 点数最大，通吃奖池 **{prize} 点**"
                f"（奖池 {pot} 点，手续费 {FEE_AMOUNT} 点已销毁）！当前额度 {new_quota} 点。"
            )
        self.finished = True
