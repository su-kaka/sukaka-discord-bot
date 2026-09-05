"""诅咒：押 10 点（全销毁），被诅咒者下次抢劫必被反杀、决斗必输。"""

from __future__ import annotations

import time

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import CURSE_COOLDOWN_SECONDS, CURSE_COST, CURSE_KEYWORD


async def handle_curse(
    message: discord.Message,
    client: httpx.AsyncClient,
    curse_cooldowns: dict[int, float],
    cursed_users: set[int],
) -> None:
    """处理「诅咒 @某人」命令。"""
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

    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🔮 查询额度失败，请稍后再试。")
        return
    if quota < CURSE_COST:
        await message.channel.send(
            f"🔮 额度不足：当前 {quota} 点，诅咒需要 {CURSE_COST} 点。"
        )
        return

    result = await adjust_quota(client, "deduct", message.author.name, CURSE_COST)
    if result is None:
        await message.channel.send("🔮 扣除额度失败，请稍后再试。")
        return

    curse_cooldowns[message.author.id] = now + CURSE_COOLDOWN_SECONDS
    cursed_users.add(target.id)
    await message.channel.send(
        f"🔮 {message.author.mention} 诅咒了 {target.mention}！\n"
        f"{target.mention} 下次抢劫必被反杀、决斗必输（生效一次后解除）。"
    )
