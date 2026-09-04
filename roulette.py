"""赌大小：6 人报名各押 5 点活动额度，开奖比大小，赢家通吃。"""

from __future__ import annotations

import asyncio
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

PLAYER_COUNT = 5
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

DUEL_KEYWORD = "决斗"
DUEL_BET = 6
DUEL_PRIZE = 10  # 12 - 2 销毁
DUEL_TIMEOUT_SECONDS = 60
DUEL_COOLDOWN_SECONDS = 30

RED_PACKET_KEYWORD = "红包"
RED_PACKET_COST = 10
RED_PACKET_MAX_GRABBERS = 8
RED_PACKET_MAX_WINNERS = 3  # 最多 3 人中奖，其余抢 0 点
RED_PACKET_POOL = 8  # 10 - 2 销毁，随机分给中奖者
RED_PACKET_TIMEOUT_SECONDS = 60
RED_PACKET_COOLDOWN_SECONDS = 30

ROB_KEYWORD = "抢劫"
ROB_MIN_AMOUNT = 1
ROB_MAX_AMOUNT = 5
ROB_MIN_QUOTA = 5  # 低于此额度无抢劫能力
ROB_FEE = 1  # 每次额度交换销毁 1 点
ROB_COOLDOWN_SECONDS = 60

MARRY_KEYWORD = "结婚"
MARRY_FEE = 10  # 手续费销毁
MARRY_TIMEOUT_SECONDS = 60
MARRY_COOLDOWN_SECONDS = 300

CURSE_KEYWORD = "诅咒"
CURSE_COST = 10  # 诅咒费用，全销毁
CURSE_COOLDOWN_SECONDS = 300

ALLIN_KEYWORD = "梭哈"
ALLIN_MIN_QUOTA = 10  # 额度 ≥ 10 点才能梭哈
ALLIN_FEE = 2  # 手续费，全销毁
ALLIN_COOLDOWN_SECONDS = 60

TRAP_KEYWORD = "陷阱"
TRAP_COST = 20  # 设置陷阱消耗 20 点，全销毁
TRAP_COUNT = 4  # 一次设置 4 个陷阱
TRAP_MIN_AMOUNT = 1  # 触发陷阱最少扣 1 点
TRAP_MAX_AMOUNT = 10  # 触发陷阱最多扣 10 点
TRAP_COOLDOWN_SECONDS = 300

LEADERBOARD_KEYWORD = "排行榜"
LEADERBOARD_TOP_N = 10

BIG_RED_PACKET_INTERVAL_SECONDS = 360  # 机器人每 6 分钟发一次大红包
BIG_RED_PACKET_POOL = 200  # 奖池 200 点
BIG_RED_PACKET_MAX_GRABBERS = 10  # 最多 10 人参与
BIG_RED_PACKET_MAX_SHARE = 100  # 单人最多抢到 100 点
BIG_RED_PACKET_TIMEOUT_SECONDS = 300  # 5 分钟未满员也开奖


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


async def _query_top_quota(client: httpx.AsyncClient) -> Optional[list[tuple[str, int]]]:
    """查询活动额度前十用户，返回 (username, quota) 列表。"""
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.get(
            f"{api_base}/api/activity-quota/top",
            headers={"X-Activity-Quota-Key": api_key},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            users = data.get("users", [])
            return [(u["username"], int(u["activity_quota"])) for u in users]
        return None
    except (httpx.HTTPError, ValueError, KeyError):
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
        p_quota = await _query_quota(self.client, self.proposer.name)
        q_quota = await _query_quota(self.client, self.partner.name)
        if p_quota is None or q_quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return

        total = p_quota + q_quota
        if total < MARRY_FEE:
            await interaction.response.send_message(
                f"两人总额度仅 {total} 点，不足以支付 {MARRY_FEE} 点手续费，婚礼取消。",
                ephemeral=True,
            )
            return

        if self.completed:
            await interaction.response.send_message("婚礼已结束。", ephemeral=True)
            return

        # 先清零双方，再平分（total - fee）
        for player, quota in ((self.proposer, p_quota), (self.partner, q_quota)):
            if quota > 0:
                result = await _adjust_quota(self.client, "deduct", player.name, quota)
                if result is None:
                    await interaction.response.send_message("结算失败，请稍后再试。", ephemeral=True)
                    return

        share = (total - MARRY_FEE) // 2
        bonus = (total - MARRY_FEE) % 2  # 奇数时多出 1 点给求婚者
        p_share = share + bonus
        q_share = share

        p_new = await _adjust_quota(self.client, "grant", self.proposer.name, p_share)
        q_new = await _adjust_quota(self.client, "grant", self.partner.name, q_share)

        self.completed = True
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        self._finish()

        result_text = (
            f"💍 **婚礼完成！** {self.proposer.mention} 和 {self.partner.mention} 结为夫妻！\n"
            f"两人额度合并共 {total} 点，手续费 {MARRY_FEE} 点已销毁，剩余 {total - MARRY_FEE} 点平分。\n"
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


class DuelView(discord.ui.View):
    """决斗视图：被挑战者接受后，双方各押 6 点 roll 点，赢家得 10 点（2 点销毁）。"""

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

    @discord.ui.button(label="接受决斗（押 6 点）", style=discord.ButtonStyle.danger, emoji="⚔️")
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

        # 检查双方额度
        for player in (self.challenger, self.opponent):
            quota = await _query_quota(self.client, player.name)
            if quota is None:
                await interaction.response.send_message(
                    f"查询 {player.display_name} 额度失败，请稍后再试。", ephemeral=True
                )
                return
            if quota < DUEL_BET:
                await interaction.response.send_message(
                    f"{player.display_name} 额度不足（当前 {quota} 点，需 {DUEL_BET} 点），决斗取消。",
                    ephemeral=True,
                )
                return

        if self.completed:
            await interaction.response.send_message("决斗已结束。", ephemeral=True)
            return

        # 收双方赌注
        paid: list[discord.Member | discord.User] = []
        for player in (self.challenger, self.opponent):
            result = await _adjust_quota(self.client, "deduct", player.name, DUEL_BET)
            if result is None:
                for q in paid:
                    await _adjust_quota(self.client, "grant", q.name, DUEL_BET)
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

        new_quota = await _adjust_quota(self.client, "grant", winner.name, DUEL_PRIZE)
        result_text = (
            f"⚔️ **决斗结果**\n"
            f"{self.challenger.mention} rolled **{c_roll}**\n"
            f"{self.opponent.mention} rolled **{o_roll}**\n"
            f"{curse_note}"
        )
        if new_quota is None:
            result_text += f"🏆 {winner.mention} 获胜！但奖金发放失败，请联系管理员手动补发 {DUEL_PRIZE} 点。"
        else:
            result_text += (
                f"🏆 {winner.mention} 获胜，赢得 **{DUEL_PRIZE} 点**"
                f"（奖池 {DUEL_BET * 2} 点，2 点销毁）！当前额度 {new_quota} 点。"
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


def _split_random(pool: int, count: int) -> list[int]:
    """把 pool 点随机分成 count 份，每份至少 1 点。"""
    if count <= 0:
        return []
    if count == 1:
        return [pool]
    cuts = sorted(random.sample(range(1, pool), count - 1))
    parts = [b - a for a, b in zip([0] + cuts, cuts + [pool])]
    random.shuffle(parts)
    return parts


def _split_random_capped(pool: int, count: int, cap: int) -> list[int]:
    """把 pool 点随机分成 count 份，每份 0-cap 点，总和不超过 pool。"""
    if count <= 0 or pool <= 0:
        return []
    amounts = [random.randint(0, cap) for _ in range(count)]
    total = sum(amounts)
    if total > pool:
        amounts = [a * pool // total for a in amounts]
    return amounts


class BigRedPacketView(discord.ui.View):
    """机器人大红包：200 点奖池，最多 10 人抢，每人随机 0-100 点。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(timeout=BIG_RED_PACKET_TIMEOUT_SECONDS)
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self.grabbers: list[discord.Member | discord.User] = []

    def _packet_text(self) -> str:
        names = "、".join(u.mention for u in self.grabbers) or "暂无"
        return (
            f"🧧🧧 **机器人大红包**！奖池 **{BIG_RED_PACKET_POOL} 点**，"
            f"最多 {BIG_RED_PACKET_MAX_GRABBERS} 人参与（{len(self.grabbers)}/{BIG_RED_PACKET_MAX_GRABBERS}）\n"
            f"每人随机抢 0-{BIG_RED_PACKET_MAX_SHARE} 点，手快有手慢无！\n"
            f"已参与：{names}\n"
            f"满 {BIG_RED_PACKET_MAX_GRABBERS} 人立即开奖，"
            f"{BIG_RED_PACKET_TIMEOUT_SECONDS} 秒未满按参与人数开奖。"
        )

    @discord.ui.button(label="抢红包", style=discord.ButtonStyle.danger, emoji="🧧")
    async def grab_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("机器人不能抢红包。", ephemeral=True)
            return
        if self.completed:
            await interaction.response.send_message("红包已开奖。", ephemeral=True)
            return
        if any(u.id == user.id for u in self.grabbers):
            await interaction.response.send_message("你已经参与了。", ephemeral=True)
            return

        # 原子加入：append 与满员判断之间不插入 await
        self.grabbers.append(user)
        is_full = len(self.grabbers) >= BIG_RED_PACKET_MAX_GRABBERS
        if is_full:
            self.completed = True

        await interaction.response.send_message(
            f"🧧 已参与大红包（{len(self.grabbers)}/{BIG_RED_PACKET_MAX_GRABBERS}），等待开奖！",
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

        shares = _split_random_capped(
            BIG_RED_PACKET_POOL, len(self.grabbers), BIG_RED_PACKET_MAX_SHARE
        )

        results: list[tuple[discord.Member | discord.User, int, Optional[int]]] = []
        for user, amount in zip(self.grabbers, shares):
            if amount > 0:
                new_quota = await _adjust_quota(self.client, "grant", user.name, amount)
            else:
                new_quota = None
            results.append((user, amount, new_quota))

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
            await self.message.edit(content="\n".join(lines), view=None)

    async def on_timeout(self) -> None:
        if self.completed:
            return
        if not self.grabbers:
            if self.message:
                await self.message.edit(content="🧧🧧 大红包无人参与，已过期。", view=None)
            return
        await self._settle()


async def _big_red_packet_loop(bot: "SukakaBot", client: httpx.AsyncClient) -> None:
    """每 6 分钟向频道发送一次机器人大红包。"""
    while True:
        await asyncio.sleep(BIG_RED_PACKET_INTERVAL_SECONDS)
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
        self.completed = True
        self._finish()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        winner_count = min(RED_PACKET_MAX_WINNERS, len(self.grabbers))
        winners = random.sample(self.grabbers, winner_count)
        shares = _split_random(RED_PACKET_POOL, winner_count)

        results: list[tuple[discord.Member | discord.User, int, Optional[int]]] = []
        for user, amount in zip(winners, shares):
            new_quota = await _adjust_quota(self.client, "grant", user.name, amount)
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
            await _adjust_quota(self.client, "grant", self.sender.name, RED_PACKET_COST)
            if self.message:
                await self.message.edit(
                    content=f"🧧 {self.sender.mention} 的红包无人参与，已退回 {RED_PACKET_COST} 点。",
                    view=None,
                )
            return
        # 有人参与则按参与人数开奖
        await self._settle()


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
    duel_cooldowns: dict[int, float] = {}
    red_packet_cooldowns: dict[int, float] = {}
    rob_cooldowns: dict[int, float] = {}
    marry_cooldowns: dict[int, float] = {}
    curse_cooldowns: dict[int, float] = {}
    allin_cooldowns: dict[int, float] = {}
    trap_cooldowns: dict[int, float] = {}
    active_traps: dict[int, list[int]] = {}  # 设置者 ID -> 剩余陷阱触发次数列表
    cursed_users: set[int] = set()  # 被诅咒的用户 ID

    asyncio.create_task(
        _big_red_packet_loop(bot, client),
        name="big-red-packet-loop",
    )

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.channel.id != QUOTA_CHANNEL_ID:
            return
        if message.author.bot:
            return

        content = message.content.strip()

        # 结婚：结婚 @某人
        if content.startswith(MARRY_KEYWORD):
            if not message.mentions:
                await message.channel.send(
                    f"💍 用法：`结婚 @某人`，对方同意后两人额度合并，"
                    f"扣 {MARRY_FEE} 点手续费，剩余平分。"
                )
                return
            partner = message.mentions[0]
            if partner.id == message.author.id:
                await message.channel.send("💍 不能和自己结婚。")
                return
            if partner.bot:
                await message.channel.send("💍 不能和机器人结婚。")
                return
            now = time.monotonic()
            cooldown_until = marry_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"💍 求婚冷却中，请等待 {remaining} 秒后再试。")
                return

            def _marry_cooldown(user_id: int = message.author.id) -> None:
                marry_cooldowns[user_id] = time.monotonic() + MARRY_COOLDOWN_SECONDS

            view = MarryView(message.author, partner, client, on_finish=_marry_cooldown)
            view.message = await message.channel.send(
                f"💍 {message.author.mention} 向 {partner.mention} 求婚！\n"
                f"同意后两人额度合并，扣 {MARRY_FEE} 点手续费，剩余平分。\n"
                f"{partner.mention} 请在 {MARRY_TIMEOUT_SECONDS} 秒内回应。",
                view=view,
            )
            return

        # 诅咒：诅咒 @某人
        if content.startswith(CURSE_KEYWORD):
            if not message.mentions:
                await message.channel.send(
                    f"🔮 用法：`诅咒 @某人`，押 {CURSE_COST} 点（全销毁），"
                    "被诅咒者下次抢劫必被反杀、决斗必输，生效一次后解除。"
                )
                return
            target = message.mentions[0]
            if target.id == message.author.id:
                await message.channel.send("🔮 不能诅咒自己。")
                return
            if target.bot:
                await message.channel.send("🔮 不能诅咒机器人。")
                return
            if target.id in cursed_users:
                await message.channel.send(f"🔮 {target.mention} 已经身中诅咒了。")
                return
            now = time.monotonic()
            cooldown_until = curse_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🔮 诅咒冷却中，请等待 {remaining} 秒后再试。")
                return

            quota = await _query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🔮 查询额度失败，请稍后再试。")
                return
            if quota < CURSE_COST:
                await message.channel.send(
                    f"🔮 额度不足：当前 {quota} 点，诅咒需要 {CURSE_COST} 点。"
                )
                return

            result = await _adjust_quota(client, "deduct", message.author.name, CURSE_COST)
            if result is None:
                await message.channel.send("🔮 扣除额度失败，请稍后再试。")
                return

            curse_cooldowns[message.author.id] = now + CURSE_COOLDOWN_SECONDS
            cursed_users.add(target.id)
            await message.channel.send(
                f"🔮 {message.author.mention} 诅咒了 {target.mention}！\n"
                f"{target.mention} 下次抢劫必被反杀、决斗必输（生效一次后解除）。"
            )
            return

        # 决斗：决斗 @某人
        if content.startswith(DUEL_KEYWORD):
            if not message.mentions:
                await message.channel.send("⚔️ 用法：`决斗 @某人`，双方各押 6 点，赢家得 10 点。")
                return
            opponent = message.mentions[0]
            if opponent.id == message.author.id:
                await message.channel.send("⚔️ 不能和自己决斗。")
                return
            if opponent.bot:
                await message.channel.send("⚔️ 不能和机器人决斗。")
                return
            now = time.monotonic()
            cooldown_until = duel_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"⚔️ 决斗冷却中，请等待 {remaining} 秒后再试。")
                return

            def _duel_cooldown(user_id: int = message.author.id) -> None:
                duel_cooldowns[user_id] = time.monotonic() + DUEL_COOLDOWN_SECONDS

            view = DuelView(message.author, opponent, client, on_finish=_duel_cooldown, cursed_users=cursed_users)
            view.message = await message.channel.send(
                f"⚔️ {message.author.mention} 向 {opponent.mention} 发起决斗！\n"
                f"双方各押 {DUEL_BET} 点，roll 点定胜负，赢家得 {DUEL_PRIZE} 点（2 点销毁）。\n"
                f"{opponent.mention} 请在 {DUEL_TIMEOUT_SECONDS} 秒内接受或拒绝。",
                view=view,
            )
            return

        # 抢劫：抢劫 @某人
        if content.startswith(ROB_KEYWORD):
            if not message.mentions:
                await message.channel.send(
                    f"🔫 用法：`抢劫 @某人`，50% 抢到对方 {ROB_MIN_AMOUNT}-{ROB_MAX_AMOUNT} 点，"
                    f"50% 被反杀自己扣 {ROB_MIN_AMOUNT}-{ROB_MAX_AMOUNT} 点"
                    f"（每次额度交换销毁 {ROB_FEE} 点，需额度 ≥ {ROB_MIN_QUOTA} 点）。"
                )
                return
            target = message.mentions[0]
            if target.id == message.author.id:
                await message.channel.send("🔫 不能抢劫自己。")
                return
            if target.bot:
                await message.channel.send("🔫 不能抢劫机器人。")
                return
            now = time.monotonic()
            cooldown_until = rob_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🔫 抢劫冷却中，请等待 {remaining} 秒后再试。")
                return

            robber_quota = await _query_quota(client, message.author.name)
            if robber_quota is None:
                await message.channel.send("🔫 查询额度失败，请稍后再试。")
                return
            if robber_quota < ROB_MIN_QUOTA:
                await message.channel.send(
                    f"🔫 你太穷了（当前 {robber_quota} 点），额度 ≥ {ROB_MIN_QUOTA} 点才有抢劫能力。"
                )
                return

            rob_cooldowns[message.author.id] = now + ROB_COOLDOWN_SECONDS
            amount = random.randint(ROB_MIN_AMOUNT, ROB_MAX_AMOUNT)

            # 诅咒生效：被诅咒者抢劫必被反杀
            if message.author.id in cursed_users:
                cursed_users.discard(message.author.id)
                success = False
                await message.channel.send(
                    f"🔮 诅咒生效！{message.author.mention} 的抢劫注定失败！"
                )
            else:
                success = random.random() < 0.5

            if success:
                # 抢劫成功：对方有多少扣多少（最多 amount），销毁 ROB_FEE 点
                target_quota = await _query_quota(client, target.name)
                if target_quota is None:
                    await message.channel.send("🔫 查询对方额度失败，抢劫取消。")
                    return
                stolen = min(amount, target_quota)
                if stolen <= 0:
                    await message.channel.send(
                        f"🔫 {message.author.mention} 抢劫 {target.mention}，但对方身无分文，一无所获！"
                    )
                    return
                deducted = await _adjust_quota(client, "deduct", target.name, stolen)
                if deducted is None:
                    await message.channel.send("🔫 抢劫失败，请稍后再试。")
                    return
                gain = stolen - ROB_FEE
                if gain > 0:
                    new_quota = await _adjust_quota(client, "grant", message.author.name, gain)
                    if new_quota is None:
                        await _adjust_quota(client, "grant", target.name, stolen)
                        await message.channel.send("🔫 转账失败，已退回对方额度。")
                        return
                else:
                    new_quota = robber_quota
                await message.channel.send(
                    f"🔫 {message.author.mention} 抢劫 {target.mention} 成功！\n"
                    f"抢到 **{stolen} 点**（销毁 {ROB_FEE} 点，实得 {gain} 点，当前 {new_quota} 点），"
                    f"{target.mention} 剩余 {deducted} 点。"
                )
            else:
                # 被反杀：自己扣 amount（有多少扣多少），全销毁
                loss = min(amount, robber_quota)
                new_quota = await _adjust_quota(client, "deduct", message.author.name, loss)
                if new_quota is None:
                    await message.channel.send("🔫 结算失败，请稍后再试。")
                    return
                await message.channel.send(
                    f"🛡️ {message.author.mention} 抢劫 {target.mention} 被反杀！\n"
                    f"被扣除 **{loss} 点**（已销毁），当前额度 {new_quota} 点。"
                )
            return

        # 排行榜：展示活动额度前十用户
        if content == LEADERBOARD_KEYWORD:
            top_users = await _query_top_quota(client)
            if top_users is None:
                await message.channel.send("🏆 查询排行榜失败，请稍后再试。")
                return
            if not top_users:
                await message.channel.send("🏆 暂无排行数据。")
                return
            guild = message.guild
            lines = ["🏆 **活动额度排行榜**"]
            for rank, (username, quota) in enumerate(top_users[:LEADERBOARD_TOP_N], 1):
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
                # 尝试把 API 用户名解析成服务器成员，显示为 @提及（自动显示昵称）
                display = username
                if guild:
                    member = guild.get_member_named(username)
                    if member is None:
                        member = discord.utils.find(
                            lambda m: m.name == username or m.global_name == username,
                            guild.members,
                        )
                    if member:
                        display = member.mention
                lines.append(f"{medal} {display} — **{quota} 点**")
            await message.channel.send("\n".join(lines))
            return

        # 陷阱：消耗 20 点设置 4 个陷阱，他人发言触发后随机扣 1-10 点给设置者
        if content == TRAP_KEYWORD:
            now = time.monotonic()
            cooldown_until = trap_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🪤 陷阱冷却中，请等待 {remaining} 秒后再试。")
                return
            if message.author.id in active_traps and active_traps[message.author.id]:
                remaining_traps = len(active_traps[message.author.id])
                await message.channel.send(
                    f"🪤 你还有 {remaining_traps} 个陷阱未触发，等全部触发后再设置。"
                )
                return

            quota = await _query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🪤 查询额度失败，请稍后再试。")
                return
            if quota < TRAP_COST:
                await message.channel.send(
                    f"🪤 额度不足：当前 {quota} 点，设置陷阱需要 {TRAP_COST} 点。"
                )
                return

            result = await _adjust_quota(client, "deduct", message.author.name, TRAP_COST)
            if result is None:
                await message.channel.send("🪤 扣除额度失败，请稍后再试。")
                return

            trap_cooldowns[message.author.id] = now + TRAP_COOLDOWN_SECONDS
            active_traps[message.author.id] = [0] * TRAP_COUNT  # 用列表长度记录剩余陷阱数
            await message.channel.send(
                f"🪤 {message.author.mention} 消耗 {TRAP_COST} 点设置了 {TRAP_COUNT} 个陷阱！\n"
                f"其他人在此频道发言将随机触发陷阱，被扣 {TRAP_MIN_AMOUNT}-{TRAP_MAX_AMOUNT} 点转给设置者。"
            )
            return

        # 梭哈：全部额度押上，扣 2 点手续费后 50% 翻倍或清零
        if content == ALLIN_KEYWORD:
            now = time.monotonic()
            cooldown_until = allin_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🎰 梭哈冷却中，请等待 {remaining} 秒后再试。")
                return

            quota = await _query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🎰 查询额度失败，请稍后再试。")
                return
            if quota < ALLIN_MIN_QUOTA:
                await message.channel.send(
                    f"🎰 额度不足：当前 {quota} 点，梭哈需要至少 {ALLIN_MIN_QUOTA} 点。"
                )
                return

            allin_cooldowns[message.author.id] = now + ALLIN_COOLDOWN_SECONDS

            # 先清零全部额度
            deducted = await _adjust_quota(client, "deduct", message.author.name, quota)
            if deducted is None:
                await message.channel.send("🎰 扣除额度失败，请稍后再试。")
                return

            stake = quota - ALLIN_FEE  # 扣手续费后的赌注
            if random.random() < 0.5:
                # 翻倍：返还 stake * 2
                prize = stake * 2
                new_quota = await _adjust_quota(client, "grant", message.author.name, prize)
                if new_quota is None:
                    await message.channel.send(
                        f"🎰 {message.author.mention} 梭哈 **{quota} 点** 翻倍成功！"
                        f"但奖金发放失败，请联系管理员手动补发 {prize} 点。"
                    )
                    return
                await message.channel.send(
                    f"🎰🎉 {message.author.mention} 梭哈 **{quota} 点**（手续费 {ALLIN_FEE} 点销毁）\n"
                    f"🃏 翻倍成功！赢得 **{prize} 点**，当前额度 {new_quota} 点！"
                )
            else:
                # 清零：全部销毁
                await message.channel.send(
                    f"🎰💥 {message.author.mention} 梭哈 **{quota} 点**（手续费 {ALLIN_FEE} 点销毁）\n"
                    f"🃏 运气不佳，全部清零！当前额度 0 点。"
                )
            return

        # 红包
        if content == RED_PACKET_KEYWORD:
            now = time.monotonic()
            cooldown_until = red_packet_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🧧 红包冷却中，请等待 {remaining} 秒后再试。")
                return

            quota = await _query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🧧 查询额度失败，请稍后再试。")
                return
            if quota < RED_PACKET_COST:
                await message.channel.send(
                    f"🧧 额度不足：当前 {quota} 点，发红包需要 {RED_PACKET_COST} 点。"
                )
                return

            # 先扣款再发红包
            result = await _adjust_quota(client, "deduct", message.author.name, RED_PACKET_COST)
            if result is None:
                await message.channel.send("🧧 扣除额度失败，请稍后再试。")
                return

            def _rp_cooldown(user_id: int = message.author.id) -> None:
                red_packet_cooldowns[user_id] = time.monotonic() + RED_PACKET_COOLDOWN_SECONDS

            view = RedPacketView(message.author, client, on_finish=_rp_cooldown)
            view.message = await message.channel.send(view._packet_text(), view=view)
            return

        if content == BEG_KEYWORD:
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

        if content == TRIGGER_KEYWORD:
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

        # 触发陷阱：只要频道里有他人设置的陷阱，发言立即触发
        if active_traps:
            setter_ids = [uid for uid, traps in active_traps.items() if traps and uid != message.author.id]
            if setter_ids:
                setter_id = setter_ids[0]  # 按设置顺序触发
                traps = active_traps[setter_id]
                traps.pop()  # 消耗一个陷阱
                if not traps:
                    del active_traps[setter_id]
                amount = random.randint(TRAP_MIN_AMOUNT, TRAP_MAX_AMOUNT)
                victim_quota = await _query_quota(client, message.author.name)
                if victim_quota is not None and victim_quota > 0:
                    stolen = min(amount, victim_quota)
                    deducted = await _adjust_quota(client, "deduct", message.author.name, stolen)
                    if deducted is not None:
                        setter = bot.get_user(setter_id)
                        setter_name = setter.name if setter else None
                        granted: Optional[int] = None
                        if setter_name:
                            granted = await _adjust_quota(client, "grant", setter_name, stolen)
                        if granted is None and setter_name:
                            # 发放失败则退回受害者
                            await _adjust_quota(client, "grant", message.author.name, stolen)
                        else:
                            setter_mention = f"<@{setter_id}>"
                            await message.channel.send(
                                f"🪤 {message.author.mention} 踩中了 {setter_mention} 的陷阱！\n"
                                f"被扣除 **{stolen} 点**（当前 {deducted} 点），"
                                f"{setter_mention} 获得 {stolen} 点。"
                            )
                            return

        await handle_drop_message(client, message)

    print(f"[DiceGame] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」开局（{PLAYER_COUNT} 人局）")
    print(
        f"[BigRedPacket] 已启动，每 {BIG_RED_PACKET_INTERVAL_SECONDS // 60} 分钟"
        f"在频道 {QUOTA_CHANNEL_ID} 发送 {BIG_RED_PACKET_POOL} 点大红包"
    )
