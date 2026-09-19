"""祝福：神性（唯一道具）持有者专属能力——被祝福者梭哈成功率提高到 75%。"""

from __future__ import annotations

import random

import discord

from roulette.constants import DIVINITY_EXHAUST_CHANCE
from roulette.gacha import _add_effect, clear_divinity_holder, has_divinity, has_effect, is_offline


async def handle_bless(message: discord.Message) -> None:
    """处理「祝福 @某人」命令：神性持有者专属。

    被祝福者梭哈成功率提高到 75%（不与一念天堂叠加，一念天堂覆盖时祝福不消耗），
    每次祝福有 DIVINITY_EXHAUST_CHANCE 概率神力耗尽（神性销毁）。"""
    if not message.mentions:
        await message.channel.send(
            f"✨ 用法：`祝福 @某人`（需持有唯一道具 **神性**），"
            f"被祝福者梭哈成功率提高到 75%（不与一念天堂叠加），"
            f"每次祝福有 {round(DIVINITY_EXHAUST_CHANCE * 100, 2)}% 概率神力耗尽。"
        )
        return
    if not has_divinity(message.author.id):
        await message.channel.send("✨ 你没有 **神性**（唯一道具，抽卡获得），无法祝福他人。")
        return
    target = message.mentions[0]
    if target.bot:
        await message.channel.send("✨ 不能祝福机器人。")
        return
    if is_offline(target.id):
        await message.channel.send(f"🔌 {target.mention} 处于下线状态，无法被祝福！")
        return
    if has_effect(target.id, "bless"):
        await message.channel.send(f"✨ {target.mention} 已经受到祝福的眷顾了。")
        return

    # 授予祝福效果（进背包，随「我的卡牌」展示，可被抢夺/变卖/交换）
    _add_effect(target.id, "bless", 1)

    # 每次祝福有概率神力耗尽（神性销毁）
    if random.random() < DIVINITY_EXHAUST_CHANCE:
        clear_divinity_holder()
        exhaust_note = "\n💥 神力耗尽，**神性**已消散！"
    else:
        exhaust_note = "\n✨ 神性犹存，可继续祝福他人。"

    await message.channel.send(
        f"✨ {message.author.mention} 祝福了 {target.mention}！\n"
        f"🌟 {target.mention} 的梭哈成功率提高到 **75%**（不与一念天堂叠加）。{exhaust_note}"
    )
