"""赌大小：6 人报名各押 5 点活动额度，开奖比大小，赢家通吃。"""

from __future__ import annotations

import os
import random
import time
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from quota_drop import handle_drop_message

if TYPE_CHECKING:
    from bot import SukakaBot

QUOTA_CHANNEL_ID = 1455038454772531311
DEFAULT_API_BASE = "https://catiecli.sukaka.top"

PLAYER_COUNT = 7
BET_AMOUNT = 5
FEE_AMOUNT = 5
JOIN_TIMEOUT_SECONDS = 120
API_TIMEOUT_SECONDS = 15
COOLDOWN_SECONDS = 10

TRIGGER_KEYWORD = "赌大小"
BEG_KEYWORD = "乞讨"
BEG_RECEIVE = 5
BEG_COST = 7
BEG_TIMEOUT_SECONDS = 60
BEG_COOLDOWN_SECONDS = 60


async def _query_quota(client: httpx.AsyncClient, username: str) -> Optional[int]:
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

        quota = await _query_quota(self.client, giver.name)
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
        giver_quota = await _adjust_quota(self.client, "deduct", giver.name, BEG_COST)
        if giver_quota is None:
            await interaction.response.send_message("扣除额度失败，请稍后再试。", ephemeral=True)
            return
        beggar_quota = await _adjust_quota(self.client, "grant", self.beggar.name, BEG_RECEIVE)
        if beggar_quota is None:
            await _adjust_quota(self.client, "grant", giver.name, BEG_COST)
            await interaction.response.send_message("发放额度失败，已退回你的额度。", ephemeral=True)
            return

        self.completed = True
        self.stop()
        self._finish()
        await interaction.response.send_message(
            f"🪙 感谢施舍！你已扣除 {BEG_COST} 点，当前额度 {giver_quota} 点。", ephemeral=True
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
    """6 人赌大小：每人 roll 1-100，点数最大者通吃。"""

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

        quota = await _query_quota(self.client, user.name)
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
            result = await _adjust_quota(self.client, "deduct", p.name, BET_AMOUNT)
            if result is None:
                for q in paid:
                    await _adjust_quota(self.client, "grant", q.name, BET_AMOUNT)
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
        new_quota = await _adjust_quota(self.client, "grant", winner.name, prize)
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


def start_roulette(bot: "SukakaBot") -> None:
    """注册统一的消息入口：赌大小触发 + 发言掉落。"""
    client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    active_game: dict[str, Optional[DiceGame]] = {"game": None}
    last_trigger_time: dict[str, float] = {"time": 0.0}
    active_begs: dict[int, BegView] = {}
    beg_cooldowns: dict[int, float] = {}

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.channel.id != QUOTA_CHANNEL_ID:
            return
        if message.author.bot:
            return

        if message.content.strip() == BEG_KEYWORD:
            existing = active_begs.get(message.author.id)
            if existing and not existing.completed and not existing.is_finished():
                await message.channel.send("🙏 你已有一个进行中的乞讨，等结束后再试。")
                return
            now = time.monotonic()
            cooldown_until = beg_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🙏 乞讨冷却中，请等待 {remaining} 秒后再试。")
                return

            def _start_cooldown(user_id: int = message.author.id) -> None:
                beg_cooldowns[user_id] = time.monotonic() + BEG_COOLDOWN_SECONDS

            view = BegView(message.author, client, on_finish=_start_cooldown)
            view.message = await message.channel.send(
                f"🙏 {message.author.mention} 正在乞讨！\n"
                f"点击按钮施舍：对方获得 {BEG_RECEIVE} 点，你扣除 {BEG_COST} 点"
                f"（需额度 ≥ {BEG_COST} 点）。{BEG_TIMEOUT_SECONDS} 秒内有效。",
                view=view,
            )
            active_begs[message.author.id] = view
            return

        if message.content.strip() == TRIGGER_KEYWORD:
            game = active_game["game"]
            if game and not game.finished:
                await message.channel.send("🎲 已有一局正在报名中，等结束后再开新局。")
                return
            now = time.monotonic()
            elapsed = now - last_trigger_time["time"]
            if elapsed < COOLDOWN_SECONDS:
                remaining = int(COOLDOWN_SECONDS - elapsed) + 1
                await message.channel.send(f"🎲 命令冷却中，请等待 {remaining} 秒后再试。")
                return
            last_trigger_time["time"] = now
            game = DiceGame(bot, message.channel, client)
            active_game["game"] = game
            await game.open_lobby()
            return

        await handle_drop_message(client, message)

    print(f"[DiceGame] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」开局（{PLAYER_COUNT} 人局）")
