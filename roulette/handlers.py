"""统一的消息入口：各小游戏命令分发 + 发言掉落。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from quota_drop import handle_drop_message

from roulette.api import adjust_quota, query_quota
from roulette.beg import BegView
from roulette.big_red_packet import big_red_packet_loop
from roulette.constants import (
    ALLIN_COOLDOWN_SECONDS,
    ALLIN_FEE_PERCENT,
    ALLIN_KEYWORD,
    ALLIN_MIN_QUOTA,
    API_TIMEOUT_SECONDS,
    BEG_COOLDOWN_SECONDS,
    BEG_COST,
    BEG_KEYWORD,
    BEG_RECEIVE,
    BEG_TIMEOUT_SECONDS,
    BIG_RED_PACKET_INTERVAL_SECONDS,
    BIG_RED_PACKET_POOL,
    COOLDOWN_SECONDS,
    CURSE_KEYWORD,
    DUEL_COOLDOWN_SECONDS,
    DUEL_FEE_PERCENT,
    DUEL_KEYWORD,
    DUEL_MIN_QUOTA,
    DUEL_TIMEOUT_SECONDS,
    GACHA_KEYWORD,
    LEADERBOARD_KEYWORD,
    MARRY_COOLDOWN_SECONDS,
    MY_CARDS_KEYWORD,
    MARRY_FEE_PERCENT,
    MARRY_KEYWORD,
    MARRY_MIN_FEE,
    MARRY_TIMEOUT_SECONDS,
    PLAYER_COUNT,
    QUOTA_CHANNEL_ID,
    RED_PACKET_COOLDOWN_SECONDS,
    RED_PACKET_COST_PERCENT,
    RED_PACKET_FEE_PERCENT,
    RED_PACKET_KEYWORD,
    RED_PACKET_MIN_COST,
    ROB_KEYWORD,
    SEDUCE_KEYWORD,
    TRIGGER_KEYWORD,
)
from roulette.curse import handle_curse
from roulette.dice_game import DiceGame
from roulette.duel import DuelView
from roulette.gacha import consume_effect, handle_gacha, handle_my_cards, handle_seduce
from roulette.leaderboard import handle_leaderboard
from roulette.marry import MarryView
from roulette.red_packet import RedPacketView
from roulette.rob import handle_rob

if TYPE_CHECKING:
    from bot import SukakaBot


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
    gacha_cooldowns: dict[int, float] = {}
    cursed_users: set[int] = set()  # 被诅咒的用户 ID

    asyncio.create_task(
        big_red_packet_loop(bot, client),
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
                    f"扣总额度 {MARRY_FEE_PERCENT}% 手续费（最低 {MARRY_MIN_FEE} 点），剩余平分。"
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
                f"同意后两人额度合并，扣总额度 {MARRY_FEE_PERCENT}% 手续费（最低 {MARRY_MIN_FEE} 点），剩余平分。\n"
                f"{partner.mention} 请在 {MARRY_TIMEOUT_SECONDS} 秒内回应。",
                view=view,
            )
            return

        # 诅咒：诅咒 @某人
        if content.startswith(CURSE_KEYWORD):
            await handle_curse(message, client, curse_cooldowns, cursed_users)
            return

        # 决斗：决斗 @某人
        if content.startswith(DUEL_KEYWORD):
            if not message.mentions:
                await message.channel.send(
                    f"⚔️ 用法：`决斗 @某人`，双方押上额度最少者的全部额度，"
                    f"赢家获得 80%（{DUEL_FEE_PERCENT}% 手续费销毁），双方均需 ≥ {DUEL_MIN_QUOTA} 点。"
                )
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
                f"双方押上额度最少者的全部额度，赢家获得 80%（{DUEL_FEE_PERCENT}% 手续费销毁）。\n"
                f"{opponent.mention} 请在 {DUEL_TIMEOUT_SECONDS} 秒内接受或拒绝。",
                view=view,
            )
            return

        # 抢劫：抢劫 @某人
        if content.startswith(ROB_KEYWORD):
            await handle_rob(message, client, rob_cooldowns, cursed_users)
            return

        # 排行榜：展示活动额度前十用户
        if content == LEADERBOARD_KEYWORD:
            await handle_leaderboard(message, client)
            return

        # 抽卡：10 点抽一张魔法卡
        if content == GACHA_KEYWORD:
            await handle_gacha(message, client, gacha_cooldowns)
            return

        # 我的卡牌：查看持有的持续型卡牌
        if content == MY_CARDS_KEYWORD:
            await handle_my_cards(message)
            return

        # 诱惑：使用诱惑卡强制结婚
        if content.startswith(SEDUCE_KEYWORD):
            await handle_seduce(message, client)
            return

        # 梭哈：全部额度押上，扣 2 点手续费后 50% 翻倍或清零
        if content == ALLIN_KEYWORD:
            now = time.monotonic()
            cooldown_until = allin_cooldowns.get(message.author.id, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now) + 1
                await message.channel.send(f"🎰 梭哈冷却中，请等待 {remaining} 秒后再试。")
                return

            quota = await query_quota(client, message.author.name)
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
            deducted = await adjust_quota(client, "deduct", message.author.name, quota)
            if deducted is None:
                await message.channel.send("🎰 扣除额度失败，请稍后再试。")
                return

            stake = quota  # 全部额度作为赌注
            # 一念天堂生效：成功概率不变（文字描述仍为 75%），成功翻四倍
            heaven = consume_effect(message.author.id, "heaven")
            success_chance = 0.5
            if random.random() < success_chance:
                multiplier = 4 if heaven else 2
                gross_prize = stake * multiplier
                fee = int(gross_prize * ALLIN_FEE_PERCENT / 100)
                prize = gross_prize - fee
                new_quota = await adjust_quota(client, "grant", message.author.name, prize)
                heaven_note = "\n🃏 一念天堂生效！成功概率提升，翻四倍！" if heaven else ""
                if new_quota is None:
                    await message.channel.send(
                        f"🎰 {message.author.mention} 梭哈 **{quota} 点** 翻倍成功！"
                        f"但奖金发放失败，请联系管理员手动补发 {prize} 点。"
                    )
                    return
                await message.channel.send(
                    f"🎰🎉 {message.author.mention} 梭哈 **{quota} 点**\n"
                    f"🃏 翻倍成功！毛奖金 **{gross_prize} 点**，手续费 {fee} 点（{ALLIN_FEE_PERCENT}%）销毁，实得 **{prize} 点**，当前额度 {new_quota} 点！{heaven_note}"
                )
            else:
                # 清零：全部销毁
                await message.channel.send(
                    f"🎰💥 {message.author.mention} 梭哈 **{quota} 点**\n"
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

            quota = await query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🧧 查询额度失败，请稍后再试。")
                return
            cost = max(RED_PACKET_MIN_COST, int(quota * RED_PACKET_COST_PERCENT / 100))
            if quota < cost:
                await message.channel.send(
                    f"🧧 额度不足：当前 {quota} 点，发红包需要 {cost} 点（额度的 {RED_PACKET_COST_PERCENT}%，最少 {RED_PACKET_MIN_COST} 点）。"
                )
                return

            # 先扣款再发红包
            result = await adjust_quota(client, "deduct", message.author.name, cost)
            if result is None:
                await message.channel.send("🧧 扣除额度失败，请稍后再试。")
                return

            pool = cost - int(cost * RED_PACKET_FEE_PERCENT / 100)

            def _rp_cooldown(user_id: int = message.author.id) -> None:
                red_packet_cooldowns[user_id] = time.monotonic() + RED_PACKET_COOLDOWN_SECONDS

            view = RedPacketView(message.author, client, cost, pool, on_finish=_rp_cooldown)
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

        await handle_drop_message(client, message)

    print(f"[DiceGame] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」开局（{PLAYER_COUNT} 人局）")
    print(
        f"[BigRedPacket] 已启动，每 {BIG_RED_PACKET_INTERVAL_SECONDS // 60} 分钟"
        f"在频道 {QUOTA_CHANNEL_ID} 发送 {BIG_RED_PACKET_POOL} 点大红包"
    )
