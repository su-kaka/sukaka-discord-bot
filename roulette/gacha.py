"""抽卡：10 点额度抽一张魔法卡，效果存 SQLite，在对应游戏中生效。"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path

import discord
import httpx

from roulette.api import adjust_quota, query_quota, query_top_quota
from roulette.constants import (
    GACHA_BLANK_CHANCE,
    GACHA_COOLDOWN_SECONDS,
    GACHA_COST,
    GACHA_DB,
    GACHA_ROB_MAX_COUNT,
    GACHA_SEDUCE_SUCCESS_CHANCE,
    MARRY_FEE,
)

DB_PATH = Path(os.getenv("GACHA_DB", GACHA_DB))

# 卡牌定义：key -> (名称, 描述, 权重)
CARD_POOL: dict[str, tuple[str, str, int]] = {
    "heaven": ("一念天堂", "下次梭哈成功翻四倍", 10),
    "lucky": ("幸运儿", "下次抢红包必定最大", 10),
    "madman": ("狂徒", f"{GACHA_ROB_MAX_COUNT} 次内抢劫必定成功", 10),
    "weak": ("虚弱", f"{GACHA_ROB_MAX_COUNT} 次内被抢劫必定被抢成功", 10),
    "seduce": ("诱惑", "强制和某人结婚（50% 概率失败）", 10),
    "robinhood": ("劫富济贫", "排名前十的用户随机分你 1-10 点", 10),
    "blank": ("空白", "无效果", 40),  # 实际概率由 GACHA_BLANK_CHANCE 控制
}


def _init_db() -> None:
    """建表：用户卡牌效果。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gacha_effects (
                discord_id INTEGER NOT NULL,
                card_key TEXT NOT NULL,
                remaining INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                PRIMARY KEY (discord_id, card_key)
            )
            """
        )


def _draw_card() -> str:
    """抽一张卡：GACHA_BLANK_CHANCE 概率空白，其余均分。"""
    if random.random() < GACHA_BLANK_CHANCE:
        return "blank"
    keys = [k for k in CARD_POOL if k != "blank"]
    return random.choice(keys)


def _add_effect(discord_id: int, card_key: str, remaining: int = 1) -> None:
    """写入或刷新卡牌效果。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO gacha_effects (discord_id, card_key, remaining, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id, card_key) DO UPDATE SET
                remaining = excluded.remaining,
                created_at = excluded.created_at
            """,
            (discord_id, card_key, remaining, time.time()),
        )


def get_effect_remaining(discord_id: int, card_key: str) -> int:
    """查询剩余生效次数，无效果返回 0。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT remaining FROM gacha_effects WHERE discord_id = ? AND card_key = ?",
            (discord_id, card_key),
        ).fetchone()
    return row[0] if row else 0


def consume_effect(discord_id: int, card_key: str) -> bool:
    """消耗一次效果，返回是否成功消耗。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE gacha_effects SET remaining = remaining - 1
            WHERE discord_id = ? AND card_key = ? AND remaining > 0
            """,
            (discord_id, card_key),
        )
        if cursor.rowcount > 0:
            conn.execute(
                "DELETE FROM gacha_effects WHERE discord_id = ? AND card_key = ? AND remaining <= 0",
                (discord_id, card_key),
            )
            return True
        return False


def has_effect(discord_id: int, card_key: str) -> bool:
    """是否持有生效中的卡牌。"""
    return get_effect_remaining(discord_id, card_key) > 0


async def handle_gacha(
    message: discord.Message,
    client: httpx.AsyncClient,
    gacha_cooldowns: dict[int, float],
) -> None:
    """处理「抽卡」命令。"""
    now = time.monotonic()
    cooldown_until = gacha_cooldowns.get(message.author.id, 0.0)
    if now < cooldown_until:
        remaining = int(cooldown_until - now) + 1
        await message.channel.send(f"🎴 抽卡冷却中，请等待 {remaining} 秒后再试。")
        return

    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🎴 查询额度失败，请稍后再试。")
        return
    if quota < GACHA_COST:
        await message.channel.send(
            f"🎴 额度不足：当前 {quota} 点，抽卡需要 {GACHA_COST} 点。"
        )
        return

    result = await adjust_quota(client, "deduct", message.author.name, GACHA_COST)
    if result is None:
        await message.channel.send("🎴 扣除额度失败，请稍后再试。")
        return

    gacha_cooldowns[message.author.id] = now + GACHA_COOLDOWN_SECONDS
    card_key = _draw_card()
    name, desc, _ = CARD_POOL[card_key]

    if card_key == "blank":
        await message.channel.send(
            f"🎴 {message.author.mention} 消耗 {GACHA_COST} 点抽卡……\n"
            f"💨 **空白**！{desc}。"
        )
        return

    # 劫富济贫立即结算，不存效果
    if card_key == "robinhood":
        await _settle_robinhood(message, client)
        return

    # 狂徒/虚弱存 10 次，其余存 1 次
    remaining = GACHA_ROB_MAX_COUNT if card_key in ("madman", "weak") else 1
    _add_effect(message.author.id, card_key, remaining)
    await message.channel.send(
        f"🎴 {message.author.mention} 消耗 {GACHA_COST} 点抽卡……\n"
        f"✨ **{name}**！{desc}。"
    )


async def _settle_robinhood(message: discord.Message, client: httpx.AsyncClient) -> None:
    """劫富济贫：排名前十的用户随机分你 1-10 点。"""
    top_users = await query_top_quota(client)
    if not top_users:
        await message.channel.send("🎴 劫富济贫失败：暂无排行数据。")
        return

    total_gain = 0
    lines = [f"🎴 {message.author.mention} 发动 **劫富济贫**！"]
    for username, quota in top_users[:10]:
        if username == message.author.name or quota <= 0:
            continue
        amount = random.randint(1, 10)
        stolen = min(amount, quota)
        deducted = await adjust_quota(client, "deduct", username, stolen)
        if deducted is None:
            continue
        granted = await adjust_quota(client, "grant", message.author.name, stolen)
        if granted is None:
            await adjust_quota(client, "grant", username, stolen)
            continue
        total_gain += stolen
        lines.append(f"💰 {username} 分出 **{stolen} 点**")

    if total_gain > 0:
        lines.append(f"🎉 共劫富济贫 **{total_gain} 点**！")
    else:
        lines.append("💨 前十名都身无分文，一无所获。")
    await message.channel.send("\n".join(lines))


async def handle_seduce(
    message: discord.Message,
    client: httpx.AsyncClient,
) -> None:
    """诱惑卡：强制和某人结婚（50% 概率失败）。"""
    if not message.mentions:
        await message.channel.send("💘 用法：`诱惑 @某人`，强制结婚（50% 概率失败）。")
        return
    partner = message.mentions[0]
    if partner.id == message.author.id:
        await message.channel.send("💘 不能对自己使用诱惑。")
        return
    if partner.bot:
        await message.channel.send("💘 不能对机器人使用诱惑。")
        return

    if not consume_effect(message.author.id, "seduce"):
        await message.channel.send("💘 你没有生效中的「诱惑」卡。")
        return

    if random.random() >= GACHA_SEDUCE_SUCCESS_CHANCE:
        await message.channel.send(
            f"💘 {message.author.mention} 对 {partner.mention} 使用诱惑……\n"
            f"💔 诱惑失败！对方不为所动。"
        )
        return

    # 强制结婚：合并额度，扣手续费，剩余平分
    p_quota = await query_quota(client, message.author.name)
    q_quota = await query_quota(client, partner.name)
    if p_quota is None or q_quota is None:
        await message.channel.send("💘 查询额度失败，请稍后再试。")
        return

    total = p_quota + q_quota
    if total < MARRY_FEE:
        await message.channel.send(
            f"💘 两人总额度仅 {total} 点，不足以支付 {MARRY_FEE} 点手续费，婚礼取消。"
        )
        return

    for player, quota in ((message.author, p_quota), (partner, q_quota)):
        if quota > 0:
            result = await adjust_quota(client, "deduct", player.name, quota)
            if result is None:
                await message.channel.send("💘 结算失败，请稍后再试。")
                return

    share = (total - MARRY_FEE) // 2
    bonus = (total - MARRY_FEE) % 2
    p_share = share + bonus
    q_share = share

    p_new = await adjust_quota(client, "grant", message.author.name, p_share)
    q_new = await adjust_quota(client, "grant", partner.name, q_share)

    await message.channel.send(
        f"💘 {message.author.mention} 对 {partner.mention} 使用诱惑……\n"
        f"💍 **强制结婚成功！** 两人额度合并共 {total} 点，手续费 {MARRY_FEE} 点已销毁。\n"
        f"{message.author.mention} 分得 **{p_share} 点**（当前 {p_new if p_new is not None else '?'} 点）\n"
        f"{partner.mention} 分得 **{q_share} 点**（当前 {q_new if q_new is not None else '?'} 点）"
    )


_init_db()
