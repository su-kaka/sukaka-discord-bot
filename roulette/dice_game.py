"""赌大小：庄家模式，玩家与庄家对赌，赢家获得对方赌注（手续费 1 点销毁）。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import BET_AMOUNT, FEE_AMOUNT

if TYPE_CHECKING:
    from bot import SukakaBot


class DiceGame:
    """庄家模式赌大小：玩家与庄家 roll 点比大小，赢家通吃。"""

    def __init__(
        self,
        bot: "SukakaBot",
        channel: discord.abc.Messageable,
        client: httpx.AsyncClient,
        banker: Optional[discord.Member | discord.User] = None,
    ) -> None:
        self.bot = bot
        self.channel = channel
        self.client = client
        self.banker = banker  # None 表示机器人当庄家
        self.finished = False

    async def play(self, player: discord.Member | discord.User) -> None:
        """玩家与庄家对赌。"""
        # 查询玩家额度
        player_quota = await query_quota(self.client, player.name)
        if player_quota is None:
            await self.channel.send("🎲 查询额度失败，请稍后再试。")
            return
        if player_quota < BET_AMOUNT:
            await self.channel.send(
                f"🎲 {player.mention} 额度不足：当前 {player_quota} 点，需要至少 {BET_AMOUNT} 点。"
            )
            return

        # 查询庄家额度（机器人庄家无限额度）
        banker_quota: Optional[int] = None
        if self.banker is not None:
            banker_quota = await query_quota(self.client, self.banker.name)
            if banker_quota is None:
                await self.channel.send("🎲 查询庄家额度失败，请稍后再试。")
                return
            if banker_quota < BET_AMOUNT:
                # 庄家额度不足，自动切换为机器人庄家
                await self.channel.send(
                    f"🎲 庄家 {self.banker.mention} 额度不足（当前 {banker_quota} 点），"
                    f"已自动切换为机器人庄家。"
                )
                self.banker = None
                banker_quota = None

        # 收玩家赌注
        result = await adjust_quota(self.client, "deduct", player.name, BET_AMOUNT)
        if result is None:
            await self.channel.send("🎲 扣除额度失败，请稍后再试。")
            return

        # 收庄家赌注（机器人庄家不收）
        banker_paid = False
        if self.banker is not None:
            banker_result = await adjust_quota(self.client, "deduct", self.banker.name, BET_AMOUNT)
            if banker_result is None:
                await adjust_quota(self.client, "grant", player.name, BET_AMOUNT)
                await self.channel.send("🎲 扣除庄家额度失败，本局取消。")
                return
            banker_paid = True

        # roll 点
        player_roll = random.randint(1, 100)
        banker_roll = random.randint(1, 100)

        banker_name = self.banker.mention if self.banker else "🤖 机器人庄家"
        result_text = (
            f"🎲 **赌大小**\n"
            f"{player.mention} rolled **{player_roll}**\n"
            f"{banker_name} rolled **{banker_roll}**\n"
        )

        if player_roll == banker_roll:
            # 平局，退回赌注
            await adjust_quota(self.client, "grant", player.name, BET_AMOUNT)
            if banker_paid and self.banker:
                await adjust_quota(self.client, "grant", self.banker.name, BET_AMOUNT)
            result_text += "🤝 平局！赌注已退回。"
            await self.channel.send(result_text)
            self.finished = True
            return

        pot = BET_AMOUNT * 2
        prize = pot - FEE_AMOUNT  # 销毁 1 点手续费

        if player_roll > banker_roll:
            # 玩家赢
            new_quota = await adjust_quota(self.client, "grant", player.name, prize)
            if new_quota is None:
                result_text += (
                    f"🏆 {player.mention} 获胜！但奖金发放失败，请联系管理员手动补发 {prize} 点。"
                )
            else:
                result_text += (
                    f"🏆 {player.mention} 获胜，赢得 **{prize} 点**"
                    f"（奖池 {pot} 点，手续费 {FEE_AMOUNT} 点销毁）！当前额度 {new_quota} 点。"
                )
        else:
            # 庄家赢
            if self.banker is not None:
                new_quota = await adjust_quota(self.client, "grant", self.banker.name, prize)
                if new_quota is None:
                    result_text += (
                        f"🏆 {banker_name} 获胜！但奖金发放失败，请联系管理员手动补发 {prize} 点。"
                    )
                else:
                    result_text += (
                        f"🏆 {banker_name} 获胜，赢得 **{prize} 点**"
                        f"（奖池 {pot} 点，手续费 {FEE_AMOUNT} 点销毁）！"
                    )
                    # 检查庄家是否破产
                    if new_quota is not None and new_quota <= 0:
                        result_text += f"\n💸 庄家 {self.banker.mention} 额度归零，已自动切换为机器人庄家。"
                        self.banker = None
            else:
                # 机器人庄家赢，奖金销毁
                result_text += (
                    f"🏆 {banker_name} 获胜，赢得 **{prize} 点**"
                    f"（奖池 {pot} 点，手续费 {FEE_AMOUNT} 点销毁，奖金由机器人回收）！"
                )

        await self.channel.send(result_text)
        self.finished = True
