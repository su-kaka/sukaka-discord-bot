"""轮盘赌小游戏：6 人一局，每人押 5 点活动额度，赢家通吃。"""

from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING, Optional

import discord
import httpx

if TYPE_CHECKING:
    from bot import SukakaBot

QUOTA_CHANNEL_ID = 1455038454772531311
DEFAULT_API_BASE = "https://catiecli.sukaka.top"

PLAYER_COUNT = 6
BET_AMOUNT = 5
CHAMBERS = 6
JOIN_TIMEOUT_SECONDS = 60
TURN_DELAY_SECONDS = 2
API_TIMEOUT_SECONDS = 15

TRIGGER_KEYWORD = "轮盘赌"


async def _query_quota(client: httpx.AsyncClient, username: str) -> Optional[int]:
    """查询用户活动额度，失败返回 None。"""
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.request(
            "GET",
            f"{api_base}/api/activity-quota/query",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def _adjust_quota(
    client: httpx.AsyncClient, endpoint: str, username: str, amount: int
) -> Optional[int]:
    """调用 grant/deduct，成功返回当前额度，失败返回 None。"""
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.post(
            f"{api_base}/api/activity-quota/{endpoint}",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username, "amount": amount},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("current_activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


class RouletteView(discord.ui.View):
    """带「参加」按钮的开局消息。"""

    def __init__(self, game: "RouletteGame") -> None:
        super().__init__(timeout=JOIN_TIMEOUT_SECONDS)
        self.game = game

    @discord.ui.button(label="参加（押 5 点）", style=discord.ButtonStyle.primary, emoji="🔫")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.game.add_player(interaction)

    async def on_timeout(self) -> None:
        await self.game.cancel()


class RouletteGame:
    """一局轮盘赌的完整生命周期。"""

    def __init__(self, bot: "SukakaBot", channel: discord.abc.Messageable, client: httpx.AsyncClient) -> None:
        self.bot = bot
        self.channel = channel
        self.client = client
        self.players: list[discord.Member | discord.User] = []
        self.message: Optional[discord.Message] = None
        self.view: Optional[RouletteView] = None
        self.started = False
        self.finished = False

    async def open_lobby(self) -> None:
        self.view = RouletteView(self)
        self.message = await self.channel.send(self._lobby_text(), view=self.view)

    def _lobby_text(self) -> str:
        names = "、".join(p.mention for p in self.players) or "暂无"
        return (
            f"🔫 **轮盘赌**（{len(self.players)}/{PLAYER_COUNT}）\n"
            f"规则：6 发弹夹 1 颗实弹，轮流开枪，中弹出局，最后存活者通吃奖池。\n"
            f"入场费：{BET_AMOUNT} 点活动额度（需余额 ≥{BET_AMOUNT}），奖池 {PLAYER_COUNT * BET_AMOUNT} 点。\n"
            f"已加入：{names}\n"
            f"满 {PLAYER_COUNT} 人自动开始，{JOIN_TIMEOUT_SECONDS} 秒未满自动取消。"
        )

    async def add_player(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if self.started or self.finished:
            await interaction.response.send_message("这一局已经开始了。", ephemeral=True)
            return
        if any(p.id == user.id for p in self.players):
            await interaction.response.send_message("你已经加入了。", ephemeral=True)
            return

        quota = await _query_quota(self.client, user.name)
        if quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return
        if quota < BET_AMOUNT:
            await interaction.response.send_message(
                f"额度不足：当前 {quota} 点，需要至少 {BET_AMOUNT} 点。", ephemeral=True
            )
            return

        self.players.append(user)
        await interaction.response.send_message(f"已加入！（{len(self.players)}/{PLAYER_COUNT}）", ephemeral=True)

        if len(self.players) >= PLAYER_COUNT:
            self.started = True
            if self.view:
                self.view.stop()
            await self._run_game()
        elif self.message:
            await self.message.edit(content=self._lobby_text(), view=self.view)

    async def cancel(self) -> None:
        if self.finished or self.started:
            return
        self.finished = True
        if self.message:
            await self.message.edit(
                content=f"🔫 轮盘赌人数不足（{len(self.players)}/{PLAYER_COUNT}），已取消。", view=None
            )

    async def _run_game(self) -> None:
        players = list(self.players)
        random.shuffle(players)

        # 收取入场费
        paid: list[discord.Member | discord.User] = []
        for p in players:
            result = await _adjust_quota(self.client, "deduct", p.name, BET_AMOUNT)
            if result is None:
                # 扣费失败：退款并中止
                for q in paid:
                    await _adjust_quota(self.client, "grant", q.name, BET_AMOUNT)
                await self.channel.send("⚠️ 收取入场费失败，本局取消，已扣除的额度已退回。")
                self.finished = True
                return
            paid.append(p)

        pot = BET_AMOUNT * len(players)
        await self.channel.send(
            f"🔫 **轮盘赌开始！** 奖池 {pot} 点。\n"
            f"顺序：{' → '.join(p.mention for p in players)}"
        )
        await asyncio.sleep(TURN_DELAY_SECONDS)

        # 弹夹：随机一格是实弹
        bullet = random.randrange(CHAMBERS)
        chamber_index = 0
        alive = list(players)
        turn = 0

        while len(alive) > 1:
            current = alive[turn % len(alive)]
            fired = chamber_index == bullet
            chamber_index += 1

            if fired:
                alive.remove(current)
                await self.channel.send(f"💥 {current.mention} 开枪……**砰！中弹出局！** 剩余 {len(alive)} 人。")
                # 重新装填
                bullet = random.randrange(CHAMBERS)
                chamber_index = 0
                if len(alive) > 1:
                    await asyncio.sleep(TURN_DELAY_SECONDS)
                    turn = turn % len(alive)
            else:
                await self.channel.send(f"😮‍💨 {current.mention} 开枪……咔，空弹。")
                turn += 1
            await asyncio.sleep(TURN_DELAY_SECONDS)

        winner = alive[0]
        new_quota = await _adjust_quota(self.client, "grant", winner.name, pot)
        if new_quota is None:
            await self.channel.send(
                f"🏆 {winner.mention} 获胜！但奖池发放失败，请联系管理员手动补发 {pot} 点。"
            )
        else:
            await self.channel.send(
                f"🏆 {winner.mention} 活到最后，通吃奖池 **{pot} 点**！当前额度 {new_quota} 点。"
            )
        self.finished = True


def start_roulette(bot: "SukakaBot") -> None:
    """注册轮盘赌触发监听。"""
    client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    active_game: dict[str, Optional[RouletteGame]] = {"game": None}

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.channel.id != QUOTA_CHANNEL_ID:
            return
        if message.author.bot:
            return
        if message.content.strip() != TRIGGER_KEYWORD:
            return

        game = active_game["game"]
        if game and not game.finished:
            await message.channel.send("🔫 已有一局正在进行或报名中，等结束后再开新局。")
            return

        game = RouletteGame(bot, message.channel, client)
        active_game["game"] = game
        await game.open_lobby()

    print(f"[Roulette] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」开局")
