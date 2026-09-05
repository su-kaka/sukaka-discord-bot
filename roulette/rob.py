"""抢劫：50% 抢到对方 1-5 点，50% 被反杀自己扣 1-5 点。"""

from __future__ import annotations

import random
import time

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    ROB_COOLDOWN_SECONDS,
    ROB_FEE,
    ROB_KEYWORD,
    ROB_MAX_AMOUNT,
    ROB_MIN_AMOUNT,
    ROB_MIN_QUOTA,
)
from roulette.gacha import consume_effect


async def handle_rob(
    message: discord.Message,
    client: httpx.AsyncClient,
    rob_cooldowns: dict[int, float],
    cursed_users: set[int],
) -> None:
    """处理「抢劫 @某人」命令。"""
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

    robber_quota = await query_quota(client, message.author.name)
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
    # 狂徒生效：抢劫必定成功
    elif consume_effect(message.author.id, "madman"):
        success = True
        await message.channel.send(
            f"🃏 狂徒生效！{message.author.mention} 的抢劫必定成功！"
        )
    # 虚弱生效：被抢劫必定成功
    elif consume_effect(target.id, "weak"):
        success = True
        await message.channel.send(
            f"🃏 虚弱生效！{target.mention} 无法抵抗抢劫！"
        )
    else:
        success = random.random() < 0.5

    if success:
        # 抢劫成功：对方有多少扣多少（最多 amount），销毁 ROB_FEE 点
        target_quota = await query_quota(client, target.name)
        if target_quota is None:
            await message.channel.send("🔫 查询对方额度失败，抢劫取消。")
            return
        stolen = min(amount, target_quota)
        if stolen <= 0:
            await message.channel.send(
                f"🔫 {message.author.mention} 抢劫 {target.mention}，但对方身无分文，一无所获！"
            )
            return
        deducted = await adjust_quota(client, "deduct", target.name, stolen)
        if deducted is None:
            await message.channel.send("🔫 抢劫失败，请稍后再试。")
            return
        gain = stolen - ROB_FEE
        if gain > 0:
            new_quota = await adjust_quota(client, "grant", message.author.name, gain)
            if new_quota is None:
                await adjust_quota(client, "grant", target.name, stolen)
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
        new_quota = await adjust_quota(client, "deduct", message.author.name, loss)
        if new_quota is None:
            await message.channel.send("🔫 结算失败，请稍后再试。")
            return
        await message.channel.send(
            f"🛡️ {message.author.mention} 抢劫 {target.mention} 被反杀！\n"
            f"被扣除 **{loss} 点**（已销毁），当前额度 {new_quota} 点。"
        )
