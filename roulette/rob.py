"""抢劫：50% 抢到对方 10%-30% 点，50% 被反杀自己扣 10%-30% 点；成功时随机抢夺对方身上一个道具。"""

from __future__ import annotations

import random
import time

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.bank import has_security_service
from roulette.constants import (
    BANK_SECURITY_THRESHOLD,
    ROB_COOLDOWN_SECONDS,
    ROB_FEE_MAX_PERCENT,
    ROB_FEE_MIN_PERCENT,
    ROB_MAX_PERCENT,
    ROB_MIN_PERCENT,
    ROB_MIN_QUOTA,
)
from roulette.gacha import consume_effect, has_effect, steal_random_card


async def handle_rob(
    message: discord.Message,
    client: httpx.AsyncClient,
    rob_cooldowns: dict[int, float],
    cursed_users: set[int],
) -> None:
    """处理「抢劫 @某人」命令。"""
    if not message.mentions:
        await message.channel.send(
            f"🔫 用法：`抢劫 @某人`，50% 抢到对方 {ROB_MIN_PERCENT}%-{ROB_MAX_PERCENT}% 额度，"
            f"50% 被反杀自己扣 {ROB_MIN_PERCENT}%-{ROB_MAX_PERCENT}% 额度"
            f"（抢到部分随机销毁 {ROB_FEE_MIN_PERCENT}%-{ROB_FEE_MAX_PERCENT}%，需额度 ≥ {ROB_MIN_QUOTA} 点，"
            f"成功时随机抢夺对方身上一个道具）。"
        )
        return
    target = message.mentions[0]
    if target.id == message.author.id:
        await message.channel.send("🔫 不能抢劫自己。")
        return
    if target.bot:
        await message.channel.send("🔫 不能抢劫机器人。")
        return
    if has_security_service(target.id):
        await message.channel.send(
            f"🛡️ {target.mention} 的银行存款超过 {BANK_SECURITY_THRESHOLD} 点，"
            f"已解锁安保服务，无法被抢劫！"
        )
        return
    now = time.monotonic()
    cooldown_until = rob_cooldowns.get(message.author.id, 0.0)
    if now < cooldown_until:
        remaining = int(cooldown_until - now) + 1
        await message.channel.send(f"🔫 抢劫冷却中，请等待 {remaining} 秒后再试。")
        return

    robber_quota = await query_quota(client, message.author.name)
    if robber_quota is None:
        await message.channel.send("🔫 查询额度失败，请稍后再试。")
        return
    if robber_quota < ROB_MIN_QUOTA:
        await message.channel.send(
            f"🔫 你太穷了（当前 {robber_quota} 点），额度 ≥ {ROB_MIN_QUOTA} 点才有抢劫能力。"
        )
        return

    # 狂徒生效：抢劫 CD 缩短到 10 秒
    cooldown = 10 if has_effect(message.author.id, "madman") else ROB_COOLDOWN_SECONDS
    rob_cooldowns[message.author.id] = now + cooldown
    percent = random.randint(ROB_MIN_PERCENT, ROB_MAX_PERCENT)

    # 借刀杀人：被抢劫时随机转嫁给别人（从银行存款用户中选）
    scapegoat_note = ""
    if consume_effect(target.id, "scapegoat"):
        from roulette.bank import get_all_accounts_with_min_balance
        # 从有存款的用户中随机选一个替罪羊（排除自己和抢劫者）
        accounts = get_all_accounts_with_min_balance(1)
        candidates = [
            uid for uid, _ in accounts
            if uid != target.id and uid != message.author.id
        ]
        if candidates:
            scapegoat_id = random.choice(candidates)
            scapegoat = message.guild.get_member(scapegoat_id) if message.guild else None
            if scapegoat:
                scapegoat_note = f"\n🎭 借刀杀人！{target.mention} 将抢劫转嫁给了 {scapegoat.mention}！"
                target = scapegoat

    # 诅咒生效：被诅咒者抢劫必被反杀
    if message.author.id in cursed_users:
        cursed_users.discard(message.author.id)
        success = False
        await message.channel.send(
            f"🔮 诅咒生效！{message.author.mention} 的抢劫注定失败！{scapegoat_note}"
        )
    # 狂徒生效：抢劫必定成功
    elif consume_effect(message.author.id, "madman"):
        success = True
        await message.channel.send(
            f"🃏 狂徒生效！{message.author.mention} 的抢劫必定成功！{scapegoat_note}"
        )
    # 虚弱生效：被抢劫必定成功
    elif consume_effect(target.id, "weak"):
        success = True
        await message.channel.send(
            f"🃏 虚弱生效！{target.mention} 无法抵抗抢劫！{scapegoat_note}"
        )
    else:
        success = random.random() < 0.5
        if scapegoat_note:
            await message.channel.send(scapegoat_note)

    if success:
        # 抢劫成功：按对方额度的百分比抢夺，实得不超过抢劫者身家，随机销毁 1%-50%
        target_quota = await query_quota(client, target.name)
        if target_quota is None:
            await message.channel.send("🔫 查询对方额度失败，抢劫取消。")
            return
        stolen = max(1, int(target_quota * percent / 100))
        stolen = min(stolen, target_quota)
        if stolen <= 0:
            card_name = steal_random_card(message.author.id, target.id)
            if card_name:
                await message.channel.send(
                    f"🔫 {message.author.mention} 抢劫 {target.mention}，对方身无分文，\n"
                    f"但顺手抢到了对方身上的道具 **{card_name}**！"
                )
            else:
                await message.channel.send(
                    f"🔫 {message.author.mention} 抢劫 {target.mention}，但对方身无分文，一无所获！"
                )
            return
        deducted = await adjust_quota(client, "deduct", target.name, stolen)
        if deducted is None:
            await message.channel.send("🔫 抢劫失败，请稍后再试。")
            return
        fee_percent = random.randint(ROB_FEE_MIN_PERCENT, ROB_FEE_MAX_PERCENT)
        fee = min(stolen, max(1, int(stolen * fee_percent / 100)))
        gain = stolen - fee
        # 实得不能超过抢劫者身家总额
        if gain > robber_quota:
            gain = robber_quota
            fee = stolen - gain
        if gain > 0:
            new_quota = await adjust_quota(client, "grant", message.author.name, gain)
            if new_quota is None:
                await adjust_quota(client, "grant", target.name, stolen)
                await message.channel.send("🔫 转账失败，已退回对方额度。")
                return
        else:
            new_quota = robber_quota
        # 顺手牵羊：随机抢夺对方身上一个道具
        card_name = steal_random_card(message.author.id, target.id)
        card_note = (
            f"\n🎁 顺手牵羊！抢到 {target.mention} 身上的道具 **{card_name}**！"
            if card_name
            else ""
        )
        await message.channel.send(
            f"🔫 {message.author.mention} 抢劫 {target.mention} 成功！\n"
            f"抢到 **{stolen} 点**（{percent}% 额度，销毁 {fee} 点（{fee_percent}%），实得 {gain} 点，当前 {new_quota} 点），"
            f"{target.mention} 剩余 {deducted} 点。{card_note}"
        )
    else:
        # 被反杀：按自己额度的百分比扣除，全销毁
        loss = max(1, int(robber_quota * percent / 100))
        loss = min(loss, robber_quota)
        new_quota = await adjust_quota(client, "deduct", message.author.name, loss)
        if new_quota is None:
            await message.channel.send("🔫 结算失败，请稍后再试。")
            return
        await message.channel.send(
            f"🛡️ {message.author.mention} 抢劫 {target.mention} 被反杀！\n"
            f"被扣除 **{loss} 点**（{percent}% 额度，已销毁），当前额度 {new_quota} 点。"
        )
