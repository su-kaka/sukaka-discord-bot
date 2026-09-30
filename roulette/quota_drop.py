"""「来财」关键词掉落活动额度：每个自然日（北京时间）第一次必定掉落固定点数，之后随机掉落，单用户冷却。"""

from __future__ import annotations

import random
import sqlite3
import time

import discord
import httpx

from paths import data_file
from roulette.api import adjust_quota
from roulette.constants import (
    METEOR_DISSIPATE_CHANCE,
    QUOTA_CHANNEL_ID,
    QUOTA_DROP_COOLDOWN_MAX_SECONDS,
    QUOTA_DROP_COOLDOWN_MIN_SECONDS,
    QUOTA_DROP_DAILY_BONUS,
    QUOTA_DROP_DAILY_UTC_OFFSET_SECONDS,
    QUOTA_DROP_DB,
    QUOTA_DROP_DEDUCT_CHANCE,
    QUOTA_DROP_DEDUCT_MAX,
    QUOTA_DROP_DEDUCT_MIN,
    QUOTA_DROP_KEYWORD,
    QUOTA_DROP_MAX,
    QUOTA_DROP_NOTIFY_DELETE_AFTER,
    QUOTA_DROP_ZERO_CHANCE,
)

DB_PATH = data_file("QUOTA_DROP_DB", QUOTA_DROP_DB)


def _roll_drop_amount() -> int:
    """随机掉落点数：QUOTA_DROP_ZERO_CHANCE 概率为 0，否则 1-上限均匀随机。"""
    if random.random() < QUOTA_DROP_ZERO_CHANCE:
        return 0
    return random.randint(1, QUOTA_DROP_MAX)


def _init_db() -> None:
    """建表并清理过期冷却记录。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drop_cooldowns (
                discord_id TEXT PRIMARY KEY,
                cooldown_until REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_first_drops (
                discord_id TEXT PRIMARY KEY,
                day TEXT NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM drop_cooldowns WHERE cooldown_until <= ?", (time.time(),))


def _try_set_cooldown(discord_id: str, cooldown_until: float) -> bool:
    """原子地检查并写入冷却。返回 True 表示之前无冷却（可以参与掉落）。"""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO drop_cooldowns (discord_id, cooldown_until)
            VALUES (?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET cooldown_until = excluded.cooldown_until
            WHERE drop_cooldowns.cooldown_until <= ?
            """,
            (discord_id, cooldown_until, now),
        )
        return cursor.rowcount > 0


def _beijing_today() -> str:
    """当前北京时间的自然日（YYYY-MM-DD）。北京时间为 UTC+8 固定偏移、无夏令时。"""
    return time.strftime(
        "%Y-%m-%d", time.gmtime(time.time() + QUOTA_DROP_DAILY_UTC_OFFSET_SECONDS)
    )


def _try_claim_daily(discord_id: str, day: str) -> bool:
    """原子地记录每日首次「来财」。返回 True 表示今天尚未记录（可发放每日保底）。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO daily_first_drops (discord_id, day)
            VALUES (?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET day = excluded.day
            WHERE daily_first_drops.day < excluded.day
            """,
            (discord_id, day),
        )
        return cursor.rowcount > 0


def _rollback_daily_claim(discord_id: str, day: str) -> None:
    """额度发放失败时回滚每日记录，用户当日仍可重新触发。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM daily_first_drops WHERE discord_id = ? AND day = ?",
            (discord_id, day),
        )


async def _send_notification(channel: discord.abc.Messageable, text: str) -> None:
    """直接发送一条掉落通知，失败时仅打印日志。"""
    try:
        await channel.send(text, delete_after=QUOTA_DROP_NOTIFY_DELETE_AFTER)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[QuotaDrop] 提醒发送失败：{exc}")


async def _maybe_dissipate_meteor(message: discord.Message) -> None:
    """流星雨持有者每次掉落后有概率星光消散（销毁流星雨并播报）。"""
    # 延迟导入避免循环依赖
    from roulette.gacha import clear_meteor_shower_holder, has_meteor_shower

    if not has_meteor_shower(message.author.id):
        return
    if random.random() < METEOR_DISSIPATE_CHANCE:
        if clear_meteor_shower_holder(message.author.id):
            await _send_notification(
                message.channel,
                f"💫 {message.author.mention} 身上的流星雨星光渐渐消散，流星雨效果已结束……",
            )


async def handle_drop_message(client: httpx.AsyncClient, message: discord.Message) -> None:
    """处理一条「来财」发言的掉落逻辑（由统一的消息入口在关键词命中时调用）。"""
    # 下线状态：无法通过「来财」掉落额度（延迟导入避免循环依赖）
    from roulette.gacha import has_meteor_shower, is_offline

    if is_offline(message.author.id):
        return

    discord_id = str(message.author.id)
    username = message.author.name

    # 每日首次（北京时间自然日）：必定掉落固定点数，优先于流星雨与随机掉落
    day = _beijing_today()
    if _try_claim_daily(discord_id, day):
        current_quota = await adjust_quota(client, "grant", username, QUOTA_DROP_DAILY_BONUS)
        if current_quota is None:
            # 发放失败：回滚每日记录，当日保留重试机会
            _rollback_daily_claim(discord_id, day)
            return
        await _send_notification(
            message.channel,
            f"🧧 {message.author.mention} 今日首次「{QUOTA_DROP_KEYWORD}」，必定进账 {QUOTA_DROP_DAILY_BONUS} 点活动额度，当前额度 {current_quota} 点！",
        )
        # 每日保底也是一次掉落：同样进入随机冷却（冷却中不覆盖已有记录），并判定流星雨星光消散
        cooldown_seconds = random.uniform(
            QUOTA_DROP_COOLDOWN_MIN_SECONDS, QUOTA_DROP_COOLDOWN_MAX_SECONDS
        )
        _try_set_cooldown(discord_id, time.time() + cooldown_seconds)
        await _maybe_dissipate_meteor(message)
        return

    # 流星雨：持有者「来财」无冷却、必定掉落额度（不掉 0、免疫扣减事件）
    meteor = has_meteor_shower(message.author.id)
    if meteor:
        amount = random.randint(1, QUOTA_DROP_MAX)
    else:
        amount = _roll_drop_amount()
        cooldown_seconds = random.uniform(
            QUOTA_DROP_COOLDOWN_MIN_SECONDS, QUOTA_DROP_COOLDOWN_MAX_SECONDS
        )
        cooldown_until = time.time() + cooldown_seconds

        # 原子检查+写入冷却；无论结果如何都进冷却
        if not _try_set_cooldown(discord_id, cooldown_until):
            return

        # 一定概率触发扣减事件（流星雨持有者不会走到该分支）
        if random.random() < QUOTA_DROP_DEDUCT_CHANCE:
            deduct_amount = random.randint(QUOTA_DROP_DEDUCT_MIN, QUOTA_DROP_DEDUCT_MAX)
            current_quota = await adjust_quota(client, "deduct", username, deduct_amount)
            if current_quota is None:
                return
            await _send_notification(
                message.channel,
                f"💸 {message.author.mention} 运气不佳，被扣减 {deduct_amount} 点活动额度，当前额度 {current_quota} 点……",
            )
            return

        if amount == 0:
            await _send_notification(
                message.channel,
                f"💨 {message.author.mention} 很遗憾，这次没有掉落额度，下次好运！",
            )
            return

    current_quota = await adjust_quota(client, "grant", username, amount)
    if current_quota is None:
        return

    if meteor:
        await _send_notification(
            message.channel,
            f"☄️ {message.author.mention} 流星雨眷顾，必定掉落 {amount} 点活动额度，当前额度 {current_quota} 点！",
        )
        # 每次掉落有概率星光消散（流星雨销毁）
        await _maybe_dissipate_meteor(message)
    else:
        await _send_notification(
            message.channel,
            f"🎉 {message.author.mention} 幸运掉落 {amount} 点活动额度，当前额度 {current_quota} 点！",
        )


def start_quota_drop() -> None:
    """初始化掉落服务（建库、打印启动信息）。"""
    _init_db()
    print(
        f"[QuotaDrop] 已启动，监听频道 {QUOTA_CHANNEL_ID}，关键词「{QUOTA_DROP_KEYWORD}」触发掉落，"
        f"每日首次（北京时间自然日）必定掉落 {QUOTA_DROP_DAILY_BONUS} 点，冷却数据库 {DB_PATH}"
    )
