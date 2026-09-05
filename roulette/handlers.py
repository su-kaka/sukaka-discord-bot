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
    BANK_BALANCE_KEYWORD,
    BANK_HEIST_KEYWORD,
    BANK_KEYWORD,
    BANK_LOAN_KEYWORD,
    BANK_WITHDRAW_KEYWORD,
    BANKER_KEYWORD,
    BANKER_MIN_QUOTA,
    BANKER_RUN_KEYWORD,
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
    RULES_KEYWORD,
    SEDUCE_KEYWORD,
    TRIGGER_KEYWORD,
)
from roulette.bank import handle_bank_balance, handle_deposit, handle_loan, handle_withdraw
from roulette.bank_heist import handle_bank_heist
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
    current_banker: dict[str, Optional[discord.Member | discord.User]] = {"banker": None}
    active_begs: dict[int, BegView] = {}
    beg_cooldowns: dict[int, float] = {}
    duel_cooldowns: dict[int, float] = {}
    red_packet_cooldowns: dict[int, float] = {}
    rob_cooldowns: dict[int, float] = {}
    marry_cooldowns: dict[int, float] = {}
    curse_cooldowns: dict[int, float] = {}
    allin_cooldowns: dict[int, float] = {}
    gacha_cooldowns: dict[int, float] = {}
    heist_cooldowns: dict[int, float] = {}
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

        # 存钱：将 50% 额度存入地精银行
        if content == BANK_KEYWORD:
            await handle_deposit(message, client)
            return

        # 取钱：取出全部存款，随机扣除 50%-100% 手续费
        if content == BANK_WITHDRAW_KEYWORD:
            await handle_withdraw(message, client)
            return

        # 我的钱：查看地精银行余额
        if content == BANK_BALANCE_KEYWORD:
            await handle_bank_balance(message)
            return

        # 贷款：向存款充足的用户借款
        if content == BANK_LOAN_KEYWORD:
            await handle_loan(message, client)
            return

        # 抢银行：三人组队抢劫银行
        if content == BANK_HEIST_KEYWORD:
            await handle_bank_heist(message, client, heist_cooldowns)
            return

        # 规则：回复整套游戏规则
        if content == RULES_KEYWORD:
            rules = (
                "📜 **游戏区规则**\n\n"
                "🎲 **赌大小**：与庄家对赌，各押 5 点 roll 点，赢家得 9 点（1 点销毁）。发送「当庄家」可成为庄家（需 ≥ 50 点），庄家破产自动切换为机器人庄家，庄家可发送「跑路」取消当庄家。\n"
                "🙏 **乞讨**：发起乞讨，别人施舍你 5 点（施舍者扣 7 点）。\n"
                "⚔️ **决斗**：双方押上额度最少者的全部额度，赢家获得 80%（20% 销毁），需 ≥ 10 点。\n"
                "🧧 **红包**：押额度的 10%（最少 10 点），80% 分给最多 3 个幸运儿（20% 销毁）。\n"
                "🔫 **抢劫**：50% 抢到对方 10%-30% 额度，50% 被反杀扣自己 10%-30%（抢到部分随机销毁 1%-50%，实得不超过自身额度），需 ≥ 10 点。\n"
                "💍 **结婚**：两人额度合并，扣 10% 手续费（最低 10 点），剩余平分。\n"
                "🔮 **诅咒**：押 10 点，被诅咒者下次抢劫必被反杀、决斗必输。\n"
                "🎰 **梭哈**：押全部额度，50% 翻倍（一念天堂翻四倍），成功后扣 20% 手续费，失败清零。\n"
                "🎴 **抽卡**：押额度的 10%（最少 10 点），50% 空白，其余获得魔法卡。\n"
                "🏦 **地精银行**：发送「存钱」押 50%（最低 10 点），发送「取钱」随机扣 1%-50% 手续费。存款超 1000 点解锁普通安保（防抢劫），超 2000 点解锁皇家安保（防抢劫/诱惑/劫富济贫）。\n"
                "💳 **贷款**：发送「贷款」随机向存款 ≥ 100 点的用户借款 50 点，需还 60 点（借款账号得 55 点：50 本金 + 5 利息，5 点手续费销毁）。未还清前无法再次贷款，存钱时优先偿还贷款。\n"
                "🏦💰 **抢银行**：三人组队抢银行，随机选 1-3 个存款 ≥ 100 的目标，装备总和决定成功率（跑刀+5%/起枪+15%/全甲+30%），成功返还投入+收益，失败损失投入。\n"
                "💥 **自爆**：额度归零，随机销毁 25%-50%，剩余生成红包。\n"
                "🧧🧧 **大红包**：机器人每 6 分钟发 200 点，最多 10 人抢，每人 0-100 点。\n"
                "🏆 **排行榜**：展示活动额度前十用户。"
            )
            await message.channel.send(rules)
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
            # 一念天堂生效：成功概率提升到 75%，成功翻四倍
            heaven = consume_effect(message.author.id, "heaven")
            success_chance = 0.75 if heaven else 0.5
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

        # 当庄家：成为当前庄家（只能存在一个）
        if content == BANKER_KEYWORD:
            quota = await query_quota(client, message.author.name)
            if quota is None:
                await message.channel.send("🎲 查询额度失败，请稍后再试。")
                return
            if quota < BANKER_MIN_QUOTA:
                await message.channel.send(
                    f"🎲 额度不足：当前 {quota} 点，当庄家需要至少 {BANKER_MIN_QUOTA} 点。"
                )
                return
            current_banker["banker"] = message.author
            await message.channel.send(
                f"🎲 {message.author.mention} 已成为当前庄家！\n"
                f"其他玩家发送「赌大小」即可与庄家对赌。"
            )
            return

        # 跑路：取消当庄家
        if content == BANKER_RUN_KEYWORD:
            if current_banker["banker"] is None:
                await message.channel.send("🎲 当前没有庄家。")
                return
            if current_banker["banker"].id != message.author.id:
                await message.channel.send(
                    f"🎲 只有当前庄家 {current_banker['banker'].mention} 才能跑路。"
                )
                return
            current_banker["banker"] = None
            await message.channel.send(
                f"🎲 {message.author.mention} 已跑路，庄家位置空缺！\n"
                f"发送「当庄家」可成为新庄家。"
            )
            return

        # 赌大小：与庄家对赌
        if content == TRIGGER_KEYWORD:
            game = DiceGame(bot, message.channel, client, banker=current_banker["banker"])
            await game.play(message.author)
            # 如果庄家破产，清除庄家
            if game.banker is None and current_banker["banker"] is not None:
                current_banker["banker"] = None
            return

        await handle_drop_message(client, message)

    print(f"[DiceGame] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」与庄家对赌，发送「{BANKER_KEYWORD}」成为庄家")
    print(
        f"[BigRedPacket] 已启动，每 {BIG_RED_PACKET_INTERVAL_SECONDS // 60} 分钟"
        f"在频道 {QUOTA_CHANNEL_ID} 发送 {BIG_RED_PACKET_POOL} 点大红包"
    )
