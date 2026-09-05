"""抢银行：三人组队抢劫银行，随机选取 1-3 个目标，装备总和决定成功率。"""

from __future__ import annotations

import random
import time
from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.bank import (
    _set_balance,
    get_all_accounts_with_min_balance,
    mark_heist_cooldown,
    set_hatred,
    has_hatred,
)
from roulette.constants import (
    BANK_HEIST_BASE_SUCCESS,
    BANK_HEIST_COOLDOWN_SECONDS,
    BANK_HEIST_GEAR_COEFFICIENTS,
    BANK_HEIST_GEAR_COST_PERCENT,
    BANK_HEIST_GEAR_MIN_QUOTA,
    BANK_HEIST_GEAR_NAMES,
    BANK_HEIST_GEAR_SUCCESS_BONUS,
    BANK_HEIST_JOIN_TIMEOUT_SECONDS,
    BANK_HEIST_MAX_TARGETS,
    BANK_HEIST_MIN_BALANCE,
    BANK_HEIST_MIN_TARGETS,
    BANK_HEIST_PROFIT_SHARE,
    BANK_HEIST_TARGET_COOLDOWN_SECONDS,
    BANK_HEIST_TEAM_SIZE,
)


class BankHeistView(discord.ui.View):
    """抢银行组队视图：三人报名，选择装备，60 秒 timeout。"""

    def __init__(
        self,
        leader: discord.Member | discord.User,
        client: httpx.AsyncClient,
        on_finish: Optional[object] = None,
    ) -> None:
        super().__init__(timeout=BANK_HEIST_JOIN_TIMEOUT_SECONDS)
        self.leader = leader
        self.client = client
        self.message: Optional[discord.Message] = None
        self.completed = False
        self._on_finish = on_finish
        # 队员列表：[(user, gear_key), ...]，gear_key 为 "knife"/"gun"/"armor"
        self.members: list[tuple[discord.Member | discord.User, str]] = []

    def _finish(self) -> None:
        if callable(self._on_finish):
            self._on_finish()

    def _gear_display(self, gear_key: str) -> str:
        """装备显示名称。"""
        return BANK_HEIST_GEAR_NAMES.get(gear_key, gear_key)

    def _member_list_text(self) -> str:
        """队员列表文本。"""
        if not self.members:
            return "暂无队员"
        lines = []
        for i, (user, gear) in enumerate(self.members, 1):
            lines.append(f"{i}. {user.mention} — {self._gear_display(gear)}")
        return "\n".join(lines)

    def _check_member(self, user: discord.Member | discord.User) -> Optional[str]:
        """检查用户是否可加入，返回错误信息或 None。"""
        if user.bot:
            return "机器人不能参与抢银行。"
        if user.id == self.leader.id:
            return "你是发起人，已自动加入。"
        if any(m[0].id == user.id for m in self.members):
            return "你已报名。"
        if len(self.members) >= BANK_HEIST_TEAM_SIZE:
            return "队伍已满。"
        return None

    async def _validate_gear(self, user: discord.Member | discord.User, gear_key: str) -> Optional[str]:
        """验证装备选择，返回错误信息或 None。"""
        quota = await query_quota(self.client, user.name)
        if quota is None:
            return "查询额度失败，请稍后再试。"
        min_quota = BANK_HEIST_GEAR_MIN_QUOTA[gear_key]
        if quota < min_quota:
            return f"额度不足：当前 {quota} 点，{self._gear_display(gear_key)} 需要至少 {min_quota} 点。"
        if has_hatred(user.id):
            return "你有仇恨状态，无法参与抢银行。请先存钱解除仇恨。"
        return None

    async def _deduct_gear_cost(self, user: discord.Member | discord.User, gear_key: str) -> Optional[int]:
        """扣除装备投入，返回扣除金额或 None。"""
        quota = await query_quota(self.client, user.name)
        if quota is None:
            return None
        cost_percent = BANK_HEIST_GEAR_COST_PERCENT[gear_key]
        cost = max(1, int(quota * cost_percent / 100))
        result = await adjust_quota(self.client, "deduct", user.name, cost)
        if result is None:
            return None
        return cost

    @discord.ui.button(label="跑刀", style=discord.ButtonStyle.secondary, emoji="🔪")
    async def knife_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._join(interaction, "knife")

    @discord.ui.button(label="起枪", style=discord.ButtonStyle.primary, emoji="🔫")
    async def gun_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._join(interaction, "gun")

    @discord.ui.button(label="全甲", style=discord.ButtonStyle.danger, emoji="🛡️")
    async def armor_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._join(interaction, "armor")

    async def _join(self, interaction: discord.Interaction, gear_key: str) -> None:
        """处理报名。"""
        user = interaction.user
        error = self._check_member(user)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        error = await self._validate_gear(user, gear_key)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        # 扣除装备投入
        cost = await self._deduct_gear_cost(user, gear_key)
        if cost is None:
            await interaction.response.send_message("扣除装备投入失败，请稍后再试。", ephemeral=True)
            return

        self.members.append((user, gear_key))
        await interaction.response.send_message(
            f"✅ {user.mention} 已报名 {self._gear_display(gear_key)}（投入 {cost} 点）！",
            ephemeral=True,
        )

        # 更新队伍显示
        if self.message:
            await self.message.edit(content=self._team_text())

        # 满员自动开始
        if len(self.members) >= BANK_HEIST_TEAM_SIZE:
            await self._start_heist()

    def _team_text(self) -> str:
        """队伍状态文本。"""
        return (
            f"🏦 **抢银行组队中** ({len(self.members)}/{BANK_HEIST_TEAM_SIZE})\n"
            f"发起人：{self.leader.mention}\n"
            f"{self._member_list_text()}\n"
            f"点击按钮选择装备报名，满 {BANK_HEIST_TEAM_SIZE} 人自动开始！"
        )

    async def _start_heist(self) -> None:
        """开始抢劫。"""
        if self.completed:
            return
        self.completed = True
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        self._finish()

        # 计算团队装备总和和成功率
        total_coefficient = sum(BANK_HEIST_GEAR_COEFFICIENTS[gear] for _, gear in self.members)
        total_success_bonus = sum(BANK_HEIST_GEAR_SUCCESS_BONUS[gear] for _, gear in self.members)
        success_rate = BANK_HEIST_BASE_SUCCESS + total_success_bonus
        success_rate = min(success_rate, 95)  # 上限 95%

        # 随机选取 1-3 个目标
        targets = get_all_accounts_with_min_balance(BANK_HEIST_MIN_BALANCE)
        if not targets:
            await self.message.channel.send(
                f"🏦 抢银行失败：没有存款 ≥ {BANK_HEIST_MIN_BALANCE} 的目标。"
            )
            await self._refund_all()
            return

        num_targets = random.randint(BANK_HEIST_MIN_TARGETS, min(BANK_HEIST_MAX_TARGETS, len(targets)))
        selected_targets = random.sample(targets, num_targets)

        # 判定成功
        roll = random.random() * 100
        success = roll < success_rate

        if success:
            await self._settle_success(selected_targets, total_coefficient)
        else:
            await self._settle_failure(selected_targets, success_rate, roll)

        if self.message:
            await self.message.edit(view=None)

    async def _refund_all(self) -> None:
        """退还所有队员投入。"""
        for user, gear_key in self.members:
            quota = await query_quota(self.client, user.name)
            if quota is not None:
                cost_percent = BANK_HEIST_GEAR_COST_PERCENT[gear_key]
                cost = max(1, int(quota * cost_percent / 100))
                await adjust_quota(self.client, "grant", user.name, cost)

    async def _settle_success(
        self,
        targets: list[tuple[int, int]],
        total_coefficient: int,
    ) -> None:
        """成功结算。"""
        total_loot = 0
        target_details = []

        # 从每个目标扣除 5%-25%
        for discord_id, balance in targets:
            loot_percent = random.randint(5, 25)
            loot = max(1, int(balance * loot_percent / 100))
            new_balance = balance - loot
            _set_balance(discord_id, new_balance)
            mark_heist_cooldown(discord_id)
            total_loot += loot
            target_details.append(f"<@{discord_id}> 存款 {balance} → 被抢 {loot} 点（{loot_percent}%）")

        # 分配收益：70% 给队员，20% 销毁，10% 银行
        team_share = int(total_loot * BANK_HEIST_PROFIT_SHARE / 100)
        destroy_share = int(total_loot * 20 / 100)
        bank_share = total_loot - team_share - destroy_share

        # 按系数分配
        member_shares = []
        for user, gear_key in self.members:
            coeff = BANK_HEIST_GEAR_COEFFICIENTS[gear_key]
            share = int(team_share * coeff / total_coefficient)
            # 返还投入 + 收益
            quota = await query_quota(self.client, user.name)
            if quota is not None:
                cost_percent = BANK_HEIST_GEAR_COST_PERCENT[gear_key]
                cost = max(1, int(quota * cost_percent / 100))
                total_return = cost + share
                new_quota = await adjust_quota(self.client, "grant", user.name, total_return)
                member_shares.append(
                    f"{user.mention} 返还 {cost} + 分得 {share} = **{total_return} 点**（当前 {new_quota} 点）"
                )
            # 标记仇恨
            set_hatred(user.id)

        # 发送结果
        result_text = (
            f"🏦💰 **抢银行成功！**\n"
            f"目标数：{len(targets)}，总收益：{total_loot} 点\n"
            f"目标明细：\n" + "\n".join(target_details) + "\n"
            f"销毁 {destroy_share} 点，银行手续费 {bank_share} 点\n"
            f"队员分配：\n" + "\n".join(member_shares) + "\n"
            f"⚠️ 所有队员已被标记仇恨，下次存钱将被没收！"
        )
        await self.message.channel.send(result_text)

    async def _settle_failure(
        self,
        targets: list[tuple[int, int]],
        success_rate: float,
        roll: float,
    ) -> None:
        """失败结算。"""
        target_text = "\n".join([f"<@{discord_id}> 存款 {balance} 点" for discord_id, balance in targets])
        await self.message.channel.send(
            f"🏦💥 **抢银行失败！**\n"
            f"团队成功率 {success_rate:.0f}%，判定值 {roll:.1f}\n"
            f"目标：\n{target_text}\n"
            f"所有队员投入已损失！"
        )

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self.completed = True
        self._finish()
        # 退还投入
        await self._refund_all()
        if self.message:
            await self.message.edit(
                content=f"🏦 抢银行组队超时，已解散并退还投入。",
                view=None,
            )


async def handle_bank_heist(
    message: discord.Message,
    client: httpx.AsyncClient,
    heist_cooldowns: dict[int, float],
) -> None:
    """处理「抢银行」命令。"""
    # 检查冷却
    now = time.monotonic()
    cooldown_until = heist_cooldowns.get(message.author.id, 0.0)
    if now < cooldown_until:
        remaining = int(cooldown_until - now) + 1
        await message.channel.send(f"🏦 抢银行冷却中，请等待 {remaining} 秒后再试。")
        return

    # 检查仇恨
    if has_hatred(message.author.id):
        await message.channel.send(
            "🏦 你有仇恨状态，无法参与抢银行。请先存钱解除仇恨。"
        )
        return

    # 检查额度
    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🏦 查询额度失败，请稍后再试。")
        return
    if quota < BANK_HEIST_GEAR_MIN_QUOTA["knife"]:
        await message.channel.send(
            f"🏦 额度不足：当前 {quota} 点，跑刀需要至少 {BANK_HEIST_GEAR_MIN_QUOTA['knife']} 点。"
        )
        return

    def _heist_cooldown(user_id: int = message.author.id) -> None:
        heist_cooldowns[user_id] = time.monotonic() + BANK_HEIST_COOLDOWN_SECONDS

    view = BankHeistView(message.author, client, on_finish=_heist_cooldown)
    view.message = await message.channel.send(view._team_text(), view=view)
