"""抽卡：10 点额度抽一张魔法卡，效果存 SQLite，在对应游戏中生效。"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota, query_top_quota
from roulette.bank import (
    _add_balance,
    _get_balance,
    _set_balance,
    get_all_accounts_with_min_balance,
    has_royal_security_service,
)
from roulette.constants import (
    BANK_ROYAL_SECURITY_THRESHOLD,
    GACHA_BLANK_CHANCE,
    GACHA_COOLDOWN_SECONDS,
    GACHA_COST_PERCENT,
    GACHA_DB,
    GACHA_ERROR_MAX,
    GACHA_ERROR_MIN,
    GACHA_MIN_COST,
    GACHA_SEDUCE_SUCCESS_CHANCE,
    GACHA_SELFDESTRUCT_MAX_PERCENT,
    GACHA_SELFDESTRUCT_MIN_PERCENT,
    MARRY_FEE_PERCENT,
    MARRY_MIN_FEE,
    OFFLINE_MIN_QUOTA,
    OFFLINE_RESET_QUOTA,
    YOURNAME_SWAP_SECONDS,
)
from roulette.packet_base import PacketView
from roulette.utils import split_random

DB_PATH = Path(os.getenv("GACHA_DB", GACHA_DB))

# 卡牌定义：key -> (名称, 描述, 权重)
CARD_POOL: dict[str, tuple[str, str, int]] = {
    "heaven": ("一念天堂", "下次梭哈成功概率提升到 75%，成功翻四倍", 10),
    "lucky": ("幸运儿", "下次抢任意红包必定最大", 10),
    "madman": ("狂徒", "下次抢劫必定成功，抢劫 CD 缩短到 10 秒", 10),
    "weak": ("虚弱", "下次被抢劫必定被抢成功", 10),
    "seduce": ("诱惑", "强制和某人结婚（50% 概率失败）", 10),
    "robinhood": ("劫富济贫", "排名前十的用户随机分你他们额度的 1%-10%", 10),
    "multidraw": ("十连抽", "下次抽卡自动抽十次", 10),
    "avatar": ("天神下凡", "下次抢银行成功率翻倍", 10),
    "selfdestruct": ("自爆", f"额度归零，随机销毁 {GACHA_SELFDESTRUCT_MIN_PERCENT}%-{GACHA_SELFDESTRUCT_MAX_PERCENT}%，剩余生成红包供所有人抢", 10),
    "snake": ("蛇符咒", "排行榜隐身，不会被劫富济贫，效果永久（唯一道具，直到下一个人抽到）", 5),
    "membership": ("会员卡", "抽卡费用减半、抽卡 CD 减半，效果永久（唯一道具，直到下一个人抽到）", 5),
    "provoke": ("挑衅", "下次发起决斗时对方无法拒绝", 10),
    "error": ("错误", f"额度重置为 {GACHA_ERROR_MIN}-{GACHA_ERROR_MAX} 之间的随机值", 5),
    "retry": ("这把不算", "梭哈或决斗失败后可重来一次", 10),
    "scapegoat": ("借刀杀人", "下次被抢劫/诅咒时，随机转嫁给其他人", 10),
    "forlove": ("因为爱情", "下次结婚时获得对方所有额度", 10),
    "taxevasion": ("偷税漏税", "下次取钱手续费为 0", 10),
    "notyet": ("时候未到", "梭哈归零时自动恢复 50 点", 10),
    "yourname": ("你的名字", "【超稀有道具】使用 `你的名字@某人` 和某人交换身体：双方交换所有额度/卡牌/银行存款，5 分钟后换回，期间双方不能再被你的名字影响", 1),
    "inflation": ("通货膨胀", "【特殊道具】若银行存在存款 > 3000 点的用户，所有人存款数值减半", 5),
    "depositking": ("存为王", "【特殊道具】排行榜前十名用户自动存款一次（额度的 50% 存入银行）", 5),
    "offline": ("下线", f"【特殊道具】额度超过 {OFFLINE_MIN_QUOTA} 才能使用：额度重置为 {OFFLINE_RESET_QUOTA}，银行存款清空，无法被任何交换选择、无法抢红包、无法发言掉落额度，下次任意发言解除下线状态", 5),
    "blank": ("空白", "无效果", 40),  # 实际概率由 GACHA_BLANK_CHANCE 控制
}


def _init_db() -> None:
    """建表：用户卡牌效果 + 蛇符咒唯一持有者。"""
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snake_charm_holder (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                discord_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS membership_card_holder (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                discord_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS body_swaps (
                user_a_id INTEGER NOT NULL,
                user_b_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                swap_at REAL NOT NULL,
                PRIMARY KEY (user_a_id, user_b_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS offline_users (
                discord_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )
            """
        )


def set_offline(discord_id: int) -> None:
    """标记用户进入下线状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO offline_users (discord_id, created_at) VALUES (?, ?)",
            (discord_id, time.time()),
        )


def is_offline(discord_id: int) -> bool:
    """用户是否处于下线状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM offline_users WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return row is not None


def clear_offline(discord_id: int) -> bool:
    """解除用户下线状态，返回是否之前处于下线状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM offline_users WHERE discord_id = ?",
            (discord_id,),
        )
        return cursor.rowcount > 0


def set_snake_charm_holder(discord_id: int) -> None:
    """设置蛇符咒唯一持有者（覆盖旧持有者）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO snake_charm_holder (id, discord_id, created_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                discord_id = excluded.discord_id,
                created_at = excluded.created_at
            """,
            (discord_id, time.time()),
        )


def get_snake_charm_holder() -> Optional[int]:
    """查询当前蛇符咒持有者，无持有者返回 None。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT discord_id FROM snake_charm_holder WHERE id = 1"
        ).fetchone()
    return row[0] if row else None


def has_snake_charm(discord_id: int) -> bool:
    """是否持有蛇符咒（唯一道具）。"""
    return get_snake_charm_holder() == discord_id


def set_membership_card_holder(discord_id: int) -> None:
    """设置会员卡唯一持有者（覆盖旧持有者）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO membership_card_holder (id, discord_id, created_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                discord_id = excluded.discord_id,
                created_at = excluded.created_at
            """,
            (discord_id, time.time()),
        )


def get_membership_card_holder() -> Optional[int]:
    """查询当前会员卡持有者，无持有者返回 None。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT discord_id FROM membership_card_holder WHERE id = 1"
        ).fetchone()
    return row[0] if row else None


def has_membership_card(discord_id: int) -> bool:
    """是否持有会员卡（唯一道具）。"""
    return get_membership_card_holder() == discord_id


def _draw_card(exclude: Optional[set[str]] = None) -> str:
    """抽一张卡：GACHA_BLANK_CHANCE 概率空白，其余按权重随机。"""
    if random.random() < GACHA_BLANK_CHANCE:
        return "blank"
    keys = [k for k in CARD_POOL if k != "blank" and (exclude is None or k not in exclude)]
    weights = [CARD_POOL[k][2] for k in keys]
    return random.choices(keys, weights=weights, k=1)[0]


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


def get_user_cards(discord_id: int) -> list[tuple[str, int]]:
    """查询用户持有的持续型卡牌，返回 (card_key, remaining) 列表。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT card_key, remaining FROM gacha_effects WHERE discord_id = ? AND remaining > 0",
            (discord_id,),
        ).fetchall()
    return rows


def steal_random_card(robber_id: int, target_id: int) -> Optional[str]:
    """抢劫成功时随机偷取对方身上一个道具（含蛇符咒、会员卡），返回道具名称，无道具可偷返回 None。"""
    candidates = [card_key for card_key, _ in get_user_cards(target_id)]
    if has_snake_charm(target_id):
        candidates.append("snake")
    if has_membership_card(target_id):
        candidates.append("membership")
    if not candidates:
        return None
    card_key = random.choice(candidates)
    if card_key == "snake":
        set_snake_charm_holder(robber_id)
    elif card_key == "membership":
        set_membership_card_holder(robber_id)
    else:
        consume_effect(target_id, card_key)
        _add_effect(robber_id, card_key, 1)
    name, _, _ = CARD_POOL.get(card_key, (card_key, "", 0))
    return name


async def handle_my_cards(message: discord.Message) -> None:
    """处理「我的卡牌」命令：查看持有的持续型卡牌。"""
    cards = get_user_cards(message.author.id)
    if not cards:
        await message.channel.send("🎴 你目前没有生效中的卡牌。")
        return

    lines = [f"🎴 {message.author.mention} 的卡牌："]
    for card_key, remaining in cards:
        name, desc, _ = CARD_POOL.get(card_key, (card_key, "未知效果", 0))
        lines.append(f"• **{name}** ×{remaining} — {desc}")
    await message.channel.send("\n".join(lines))


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

    # 会员卡：费用减半、CD 减半
    is_member = has_membership_card(message.author.id)
    cost = max(GACHA_MIN_COST, int(quota * GACHA_COST_PERCENT / 100))
    if is_member:
        cost = max(1, cost // 2)
    if quota < cost:
        await message.channel.send(
            f"🎴 额度不足：当前 {quota} 点，抽卡需要 {cost} 点（额度的 {GACHA_COST_PERCENT}%，最低 {GACHA_MIN_COST} 点）。"
        )
        return

    result = await adjust_quota(client, "deduct", message.author.name, cost)
    if result is None:
        await message.channel.send("🎴 扣除额度失败，请稍后再试。")
        return

    cooldown = GACHA_COOLDOWN_SECONDS // 2 if is_member else GACHA_COOLDOWN_SECONDS
    gacha_cooldowns[message.author.id] = now + cooldown

    # 十连抽生效：自动抽十次
    if consume_effect(message.author.id, "multidraw"):
        await _handle_multidraw(message, client, cost)
        return

    card_key = _draw_card()
    name, desc, _ = CARD_POOL[card_key]

    if card_key == "blank":
        await message.channel.send(
            f"🎴 {message.author.mention} 消耗 {cost} 点抽卡……\n"
            f"💨 **空白**！{desc}。"
        )
        return

    # 劫富济贫立即结算，不存效果
    if card_key == "robinhood":
        await _settle_robinhood(message, client)
        return

    # 自爆立即结算，不存效果
    if card_key == "selfdestruct":
        await _settle_selfdestruct(message, client)
        return

    # 蛇符咒：唯一道具，立即替换持有者
    if card_key == "snake":
        old_holder = get_snake_charm_holder()
        set_snake_charm_holder(message.author.id)
        transfer_note = ""
        if old_holder and old_holder != message.author.id:
            transfer_note = f"\n🐍 蛇符咒已从 <@{old_holder}> 手中转移！"
        await message.channel.send(
            f"🎴 {message.author.mention} 消耗 {cost} 点抽卡……\n"
            f"🐍 **{name}**！{desc}。{transfer_note}"
        )
        return

    # 会员卡：唯一道具，立即替换持有者
    if card_key == "membership":
        old_holder = get_membership_card_holder()
        set_membership_card_holder(message.author.id)
        transfer_note = ""
        if old_holder and old_holder != message.author.id:
            transfer_note = f"\n💳 会员卡已从 <@{old_holder}> 手中转移！"
        await message.channel.send(
            f"🎴 {message.author.mention} 消耗 {cost} 点抽卡……\n"
            f"💳 **{name}**！{desc}。{transfer_note}"
        )
        return

    # 错误：立即结算，额度重置为随机值
    if card_key == "error":
        await _settle_error(message, client)
        return

    # 通货膨胀：立即结算，不存效果
    if card_key == "inflation":
        await _settle_inflation(message)
        return

    # 存为王：立即结算，不存效果
    if card_key == "depositking":
        await _settle_depositking(message, client)
        return

    # 所有卡牌均只生效 1 次
    _add_effect(message.author.id, card_key, 1)
    await message.channel.send(
        f"🎴 {message.author.mention} 消耗 {cost} 点抽卡……\n"
        f"✨ **{name}**！{desc}。"
    )


async def _handle_multidraw(message: discord.Message, client: httpx.AsyncClient, cost: int) -> None:
    """十连抽：一次抽十张卡，逐张结算（自爆卡不进十连池子）。"""
    lines = [f"🎴 {message.author.mention} 发动 **十连抽**！消耗 {cost} 点抽十次："]
    for i in range(10):
        card_key = _draw_card(exclude={"selfdestruct"})
        name, desc, _ = CARD_POOL[card_key]

        if card_key == "blank":
            lines.append(f"{i+1}. 💨 空白")
            continue

        if card_key == "robinhood":
            lines.append(f"{i+1}. ✨ **{name}**！立即结算……")
            await _settle_robinhood(message, client)
            continue

        if card_key == "snake":
            old_holder = get_snake_charm_holder()
            set_snake_charm_holder(message.author.id)
            transfer_note = f"（从 <@{old_holder}> 手中转移）" if old_holder and old_holder != message.author.id else ""
            lines.append(f"{i+1}. 🐍 **{name}**！{desc}{transfer_note}")
            continue

        if card_key == "membership":
            old_holder = get_membership_card_holder()
            set_membership_card_holder(message.author.id)
            transfer_note = f"（从 <@{old_holder}> 手中转移）" if old_holder and old_holder != message.author.id else ""
            lines.append(f"{i+1}. 💳 **{name}**！{desc}{transfer_note}")
            continue

        if card_key == "error":
            lines.append(f"{i+1}. 💥 **{name}**！立即结算……")
            await _settle_error(message, client)
            continue

        if card_key == "inflation":
            lines.append(f"{i+1}. 💸 **{name}**！立即结算……")
            await _settle_inflation(message)
            continue

        if card_key == "depositking":
            lines.append(f"{i+1}. 🏦 **{name}**！立即结算……")
            await _settle_depositking(message, client)
            continue

        _add_effect(message.author.id, card_key, 1)
        lines.append(f"{i+1}. ✨ **{name}**！{desc}")

    await message.channel.send("\n".join(lines))


async def _settle_error(message: discord.Message, client: httpx.AsyncClient) -> None:
    """错误：将额度重置为 1-1000 之间的随机值。"""
    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🎴 查询额度失败，请稍后再试。")
        return

    new_quota = random.randint(GACHA_ERROR_MIN, GACHA_ERROR_MAX)
    if quota > 0:
        result = await adjust_quota(client, "deduct", message.author.name, quota)
        if result is None:
            await message.channel.send("🎴 扣除额度失败，请稍后再试。")
            return
    granted = await adjust_quota(client, "grant", message.author.name, new_quota)
    if granted is None:
        await message.channel.send("🎴 额度重置失败，请联系管理员。")
        return

    await message.channel.send(
        f"🎴 {message.author.mention} 抽中 **错误**！\n"
        f"💥 额度从 **{quota} 点** 重置为 **{new_quota} 点**！"
    )


async def _settle_robinhood(message: discord.Message, client: httpx.AsyncClient) -> None:
    """劫富济贫：排名前十的用户随机分你他们额度的 1%-10%。"""
    top_users = await query_top_quota(client)
    if not top_users:
        await message.channel.send("🎴 劫富济贫失败：暂无排行数据。")
        return

    total_gain = 0
    lines = [f"🎴 {message.author.mention} 发动 **劫富济贫**！"]
    guild = message.guild
    for username, quota in top_users[:10]:
        if username == message.author.name or quota <= 0:
            continue
        # 皇家安保：无法被劫富济贫
        if guild:
            member = guild.get_member_named(username)
            if member is None:
                member = discord.utils.find(
                    lambda m: m.name == username or m.global_name == username,
                    guild.members,
                )
            if member and has_royal_security_service(member.id):
                lines.append(f"👑 {username} 有皇家安保，无法被劫富济贫！")
                continue
            if member and has_snake_charm(member.id):
                lines.append(f"🐍 {username} 持有蛇符咒，无法被劫富济贫！")
                continue
            if member and is_offline(member.id):
                lines.append(f"🔌 {username} 处于下线状态，无法被劫富济贫！")
                continue
        amount = random.randint(1, 10)
        stolen = min(int(quota * amount / 100), quota)
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


class SelfDestructPacketView(PacketView):
    """自爆红包：自爆者额度归零，奖池随机分给抢红包的人。"""

    def __init__(
        self,
        sender: discord.Member | discord.User,
        pool: int,
        client: httpx.AsyncClient,
    ) -> None:
        super().__init__(
            sender=sender,
            client=client,
            pool=pool,
            max_grabbers=10,
            timeout=60,
            packet_type="selfdestruct",
            split_mode="all",
        )


async def _settle_depositking(message: discord.Message, client: httpx.AsyncClient) -> None:
    """存为王：排行榜前十名用户自动存款一次（额度的 50% 存入银行）。"""
    top_users = await query_top_quota(client)
    if not top_users:
        await message.channel.send("🏦 存为王失败：暂无排行数据。")
        return

    lines = [f"🏦 {message.author.mention} 抽中 **存为王**！前十名自动存款："]
    deposited = 0
    for username, quota in top_users[:10]:
        if quota <= 0:
            continue
        # 找到 discord_id
        guild = message.guild
        discord_id = None
        if guild:
            member = guild.get_member_named(username)
            if member is None:
                member = discord.utils.find(
                    lambda m: m.name == username or m.global_name == username,
                    guild.members,
                )
            if member:
                discord_id = member.id
        if discord_id is None:
            continue
        # 蛇符咒：不受影响
        if has_snake_charm(discord_id):
            lines.append(f"🐍 {username} 持有蛇符咒，不受影响！")
            continue
        # 下线状态：不受影响
        if is_offline(discord_id):
            lines.append(f"🔌 {username} 处于下线状态，不受影响！")
            continue
        amount = int(quota * 50 / 100)
        if amount <= 0:
            continue
        result = await adjust_quota(client, "deduct", username, amount)
        if result is None:
            continue
        _add_balance(discord_id, amount)
        deposited += 1
        lines.append(f"💰 {username} 存入 **{amount} 点**")

    if deposited == 0:
        lines.append("💨 前十名都身无分文，无人存款。")
    await message.channel.send("\n".join(lines))


async def _settle_inflation(message: discord.Message) -> None:
    """通货膨胀：若存在存款 > 3000 的用户，所有人存款减半。"""
    accounts = get_all_accounts_with_min_balance(1)
    if not accounts:
        await message.channel.send("💸 银行空无一人，通货膨胀无效果。")
        return

    has_rich = any(balance > 3000 for _, balance in accounts)
    if not has_rich:
        await message.channel.send("💸 银行没有存款超过 3000 点的用户，通货膨胀无效果。")
        return

    for discord_id, balance in accounts:
        _set_balance(discord_id, balance // 2)

    await message.channel.send(
        f"💸 {message.author.mention} 抽中 **通货膨胀**！所有人存款减半！"
    )


async def _settle_selfdestruct(message: discord.Message, client: httpx.AsyncClient) -> None:
    """自爆：额度归零，随机销毁 25%-50%，剩余生成红包。"""
    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("💥 查询额度失败，请稍后再试。")
        return
    if quota <= 0:
        await message.channel.send("💥 你额度为 0，自爆无效果。")
        return

    # 清零额度
    result = await adjust_quota(client, "deduct", message.author.name, quota)
    if result is None:
        await message.channel.send("💥 扣除额度失败，请稍后再试。")
        return

    destroy_percent = random.randint(GACHA_SELFDESTRUCT_MIN_PERCENT, GACHA_SELFDESTRUCT_MAX_PERCENT)
    destroyed = int(quota * destroy_percent / 100)
    pool = quota - destroyed

    await message.channel.send(
        f"💥 {message.author.mention} 抽中 **自爆**！额度 **{quota} 点** 已归零！\n"
        f"销毁 **{destroyed} 点**（{destroy_percent}%），剩余 **{pool} 点** 生成红包！"
    )

    if pool <= 0:
        return

    # 生成自爆红包
    view = SelfDestructPacketView(message.author, pool, client)
    view.message = await message.channel.send(view._packet_text(), view=view)


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
    if is_offline(partner.id):
        await message.channel.send(f"🔌 {partner.mention} 处于下线状态，无法被诱惑！")
        return
    if has_royal_security_service(partner.id):
        await message.channel.send(
            f"👑 {partner.mention} 的银行存款超过 {BANK_ROYAL_SECURITY_THRESHOLD} 点，"
            f"已解锁皇家安保，无法被诱惑！"
        )
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
    fee = max(MARRY_MIN_FEE, int(total * MARRY_FEE_PERCENT / 100))
    if total < fee:
        await message.channel.send(
            f"💘 两人总额度仅 {total} 点，不足以支付 {fee} 点手续费（总额度的 {MARRY_FEE_PERCENT}%，最低 {MARRY_MIN_FEE} 点），婚礼取消。"
        )
        return

    for player, quota in ((message.author, p_quota), (partner, q_quota)):
        if quota > 0:
            result = await adjust_quota(client, "deduct", player.name, quota)
            if result is None:
                await message.channel.send("💘 结算失败，请稍后再试。")
                return

    # 因为爱情：双方都有时抵消，否则被诱惑方获得对方所有额度
    forlove_note = ""
    author_forlove = consume_effect(message.author.id, "forlove")
    partner_forlove = consume_effect(partner.id, "forlove")
    if author_forlove and partner_forlove:
        share = (total - fee) // 2
        bonus = (total - fee) % 2
        p_share = share + bonus
        q_share = share
        forlove_note = "\n💕💕 双方都有**因为爱情**，互相抵消！正常平分！"
    elif partner_forlove:
        p_share, q_share = 0, total - fee
        forlove_note = f"\n💕 **因为爱情**生效！{partner.mention} 获得对方所有额度！"
    else:
        share = (total - fee) // 2
        bonus = (total - fee) % 2
        p_share = share + bonus
        q_share = share

    p_new = await adjust_quota(client, "grant", message.author.name, p_share) if p_share > 0 else 0
    q_new = await adjust_quota(client, "grant", partner.name, q_share) if q_share > 0 else 0

    await message.channel.send(
        f"💘 {message.author.mention} 对 {partner.mention} 使用诱惑……\n"
        f"💍 **强制结婚成功！** 两人额度合并共 {total} 点，手续费 {fee} 点已销毁。{forlove_note}\n"
        f"{message.author.mention} 分得 **{p_share} 点**（当前 {p_new if p_new is not None else '?'} 点）\n"
        f"{partner.mention} 分得 **{q_share} 点**（当前 {q_new if q_new is not None else '?'} 点）"
    )


# 你的名字：交换身体状态持久化到 SQLite，重启后自动恢复

def _record_body_swap(user_a_id: int, user_b_id: int, channel_id: int) -> None:
    """记录交换身体状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO body_swaps (user_a_id, user_b_id, channel_id, swap_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_a_id, user_b_id, channel_id, time.time()),
        )


def _remove_body_swap(user_a_id: int, user_b_id: int) -> None:
    """移除交换身体状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM body_swaps
            WHERE (user_a_id = ? AND user_b_id = ?) OR (user_a_id = ? AND user_b_id = ?)
            """,
            (user_a_id, user_b_id, user_b_id, user_a_id),
        )


def is_body_swapped(discord_id: int) -> bool:
    """是否处于交换身体状态（期间不能再被你的名字影响）。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM body_swaps
            WHERE user_a_id = ? OR user_b_id = ?
            LIMIT 1
            """,
            (discord_id, discord_id),
        ).fetchone()
    return row is not None


def _swap_cards(user_a_id: int, user_b_id: int) -> None:
    """交换双方所有卡牌效果。"""
    temp_id = -user_a_id  # 临时 ID，避免主键冲突
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE gacha_effects SET discord_id = ? WHERE discord_id = ?",
            (temp_id, user_a_id),
        )
        conn.execute(
            "UPDATE gacha_effects SET discord_id = ? WHERE discord_id = ?",
            (user_a_id, user_b_id),
        )
        conn.execute(
            "UPDATE gacha_effects SET discord_id = ? WHERE discord_id = ?",
            (user_b_id, temp_id),
        )


async def _swap_bodies(
    user_a: discord.Member | discord.User,
    user_b: discord.Member | discord.User,
    client: httpx.AsyncClient,
) -> bool:
    """交换双方所有额度、卡牌和银行存款，返回是否成功。"""
    a_quota = await query_quota(client, user_a.name)
    b_quota = await query_quota(client, user_b.name)
    if a_quota is None or b_quota is None:
        return False

    if a_quota > 0 and await adjust_quota(client, "deduct", user_a.name, a_quota) is None:
        return False
    if b_quota > 0 and await adjust_quota(client, "deduct", user_b.name, b_quota) is None:
        if a_quota > 0:
            await adjust_quota(client, "grant", user_a.name, a_quota)  # 回滚
        return False
    if a_quota > 0:
        await adjust_quota(client, "grant", user_b.name, a_quota)
    if b_quota > 0:
        await adjust_quota(client, "grant", user_a.name, b_quota)

    _swap_cards(user_a.id, user_b.id)

    # 交换银行存款
    a_balance = _get_balance(user_a.id)
    b_balance = _get_balance(user_b.id)
    _set_balance(user_a.id, b_balance)
    _set_balance(user_b.id, a_balance)

    return True


async def _swap_back_later(
    channel: discord.abc.Messageable,
    user_a: discord.Member | discord.User,
    user_b: discord.Member | discord.User,
    client: httpx.AsyncClient,
    delay: float,
) -> None:
    """延迟后换回身体。"""
    await asyncio.sleep(delay)
    try:
        if await _swap_bodies(user_a, user_b, client):
            await channel.send(f"🌀 {user_a.mention} 和 {user_b.mention} 的身体换回来了！")
        else:
            await channel.send(f"🌀 {user_a.mention} 和 {user_b.mention} 换回身体失败，请联系管理员。")
    finally:
        _remove_body_swap(user_a.id, user_b.id)


async def handle_yourname(
    message: discord.Message,
    client: httpx.AsyncClient,
) -> None:
    """你的名字：和某人交换身体，双方交换所有额度/卡牌/银行存款，5 分钟后换回。"""
    if not message.mentions:
        await message.channel.send("🌀 用法：`你的名字 @某人`，双方交换所有额度/卡牌/银行存款，5 分钟后换回。")
        return
    partner = message.mentions[0]
    if partner.id == message.author.id:
        await message.channel.send("🌀 不能对自己使用你的名字。")
        return
    if partner.bot:
        await message.channel.send("🌀 不能对机器人使用你的名字。")
        return
    if is_body_swapped(message.author.id) or is_body_swapped(partner.id):
        await message.channel.send("🌀 其中一方正在交换身体中，期间不能再被你的名字影响！")
        return
    if is_offline(partner.id):
        await message.channel.send(f"🔌 {partner.mention} 处于下线状态，无法被交换选择！")
        return
    if not consume_effect(message.author.id, "yourname"):
        await message.channel.send("🌀 你没有生效中的「你的名字」卡。")
        return

    if not await _swap_bodies(message.author, partner, client):
        await message.channel.send("🌀 交换身体失败，请稍后再试。")
        return

    minutes = YOURNAME_SWAP_SECONDS // 60
    await message.channel.send(
        f"🌀 {message.author.mention} 对 {partner.mention} 使用 **你的名字**！\n"
        f"💫 双方交换了所有额度、卡牌和银行存款，{minutes} 分钟后换回！期间双方不能再被你的名字影响。"
    )

    _record_body_swap(message.author.id, partner.id, message.channel.id)
    asyncio.create_task(
        _swap_back_later(message.channel, message.author, partner, client, YOURNAME_SWAP_SECONDS)
    )


async def restore_body_swaps(bot: discord.Client, client: httpx.AsyncClient) -> None:
    """重启后恢复未完成的交换身体状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT user_a_id, user_b_id, channel_id, swap_at FROM body_swaps"
        ).fetchall()

    for user_a_id, user_b_id, channel_id, swap_at in rows:
        elapsed = time.time() - swap_at
        remaining = YOURNAME_SWAP_SECONDS - elapsed

        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except discord.HTTPException:
                _remove_body_swap(user_a_id, user_b_id)
                continue

        user_a = bot.get_user(user_a_id)
        if user_a is None:
            try:
                user_a = await bot.fetch_user(user_a_id)
            except discord.HTTPException:
                _remove_body_swap(user_a_id, user_b_id)
                continue

        user_b = bot.get_user(user_b_id)
        if user_b is None:
            try:
                user_b = await bot.fetch_user(user_b_id)
            except discord.HTTPException:
                _remove_body_swap(user_a_id, user_b_id)
                continue

        if remaining <= 0:
            # 已超时，立即换回
            if await _swap_bodies(user_a, user_b, client):
                await channel.send(f"🌀 {user_a.mention} 和 {user_b.mention} 的身体换回来了！")
            _remove_body_swap(user_a_id, user_b_id)
        else:
            asyncio.create_task(
                _swap_back_later(channel, user_a, user_b, client, remaining)
            )


async def handle_offline(
    message: discord.Message,
    client: httpx.AsyncClient,
) -> None:
    """下线卡：额度超过 500 才能使用，额度重置为 500，银行存款清空，进入下线状态。"""
    if not consume_effect(message.author.id, "offline"):
        await message.channel.send("🔌 你没有生效中的「下线」卡。")
        return

    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🔌 查询额度失败，请稍后再试。")
        return
    if quota <= OFFLINE_MIN_QUOTA:
        # 额度不足，返还卡牌
        _add_effect(message.author.id, "offline", 1)
        await message.channel.send(
            f"🔌 额度不足：当前 {quota} 点，使用「下线」需要额度超过 {OFFLINE_MIN_QUOTA} 点。"
        )
        return

    # 额度重置为 500：先扣全部，再发 500
    deducted = await adjust_quota(client, "deduct", message.author.name, quota)
    if deducted is None:
        _add_effect(message.author.id, "offline", 1)
        await message.channel.send("🔌 扣除额度失败，请稍后再试。")
        return
    new_quota = await adjust_quota(client, "grant", message.author.name, OFFLINE_RESET_QUOTA)
    if new_quota is None:
        await message.channel.send("🔌 额度重置失败，请联系管理员。")
        return

    # 清空银行存款
    old_balance = _get_balance(message.author.id)
    _set_balance(message.author.id, 0)

    # 进入下线状态
    set_offline(message.author.id)

    await message.channel.send(
        f"🔌 {message.author.mention} 使用 **下线**！\n"
        f"💥 额度从 **{quota} 点** 重置为 **{OFFLINE_RESET_QUOTA} 点**，"
        f"银行存款 **{old_balance} 点** 已清空！\n"
        f"📴 已进入下线状态：无法被任何交换选择、无法抢红包、无法发言掉落额度。\n"
        f"💬 下次任意发言将解除下线状态。"
    )


_init_db()
