"""发言随机掉落活动额度：监听目标频道发言，随机掉落 0-50 点额度，单用户冷却。"""

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

from roulette.api import adjust_quota
from roulette.constants import (
    QUOTA_CHANNEL_ID,
    QUOTA_DROP_COOLDOWN_MAX_SECONDS,
    QUOTA_DROP_COOLDOWN_MIN_SECONDS,
    QUOTA_DROP_DB,
    QUOTA_DROP_DEDUCT_CHANCE,
    QUOTA_DROP_DEDUCT_MAX,
    QUOTA_DROP_DEDUCT_MIN,
    QUOTA_DROP_MAX,
    QUOTA_DROP_MAX_BATCH_CHARS,
    QUOTA_DROP_MIN_SEND_INTERVAL,
    QUOTA_DROP_NOTIFY_DELETE_AFTER,
    QUOTA_DROP_ZERO_CHANCE,
)

DB_PATH = Path(os.getenv("QUOTA_DROP_DB", QUOTA_DROP_DB))

_batch_buffer: list[str] = []
_batch_lock = asyncio.Lock()
_batch_task: Optional[asyncio.Task[None]] = None
_last_flush_time: float = 0.0


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


async def _flush_batch(channel: discord.abc.Messageable) -> None:
    """将缓冲区中的通知合并为一条消息发送，确保距上次发送至少最小间隔秒。"""
    global _last_flush_time, _batch_task
    elapsed = time.time() - _last_flush_time
    if elapsed < QUOTA_DROP_MIN_SEND_INTERVAL:
        await asyncio.sleep(QUOTA_DROP_MIN_SEND_INTERVAL - elapsed)

    async with _batch_lock:
        _batch_task = None
        if not _batch_buffer:
            return
        messages = _batch_buffer.copy()
        _batch_buffer.clear()

    _last_flush_time = time.time()
    # 按 Discord 长度限制拆分发送
    chunks: list[str] = []
    current = ""
    for msg in messages:
        if current and len(current) + 1 + len(msg) > QUOTA_DROP_MAX_BATCH_CHARS:
            chunks.append(current)
            current = msg
        else:
            current = f"{current}\n{msg}" if current else msg
    if current:
        chunks.append(current)

    for chunk in chunks:
        try:
            await channel.send(chunk, delete_after=QUOTA_DROP_NOTIFY_DELETE_AFTER)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[QuotaDrop] 批量提醒发送失败：{exc}")


async def _queue_notification(channel: discord.abc.Messageable, text: str) -> None:
    """将一条通知加入缓冲区，并确保有刷新任务在运行。"""
    global _batch_task
    async with _batch_lock:
        _batch_buffer.append(text)
        if _batch_task is None or _batch_task.done():
            _batch_task = asyncio.create_task(_flush_batch(channel))


async def handle_drop_message(client: httpx.AsyncClient, message: discord.Message) -> None:
    """处理一条发言的掉落逻辑（由统一的消息入口调用）。"""
    # 下线状态：无法发言掉落额度（延迟导入避免循环依赖）
    from roulette.gacha import is_offline

    if is_offline(message.author.id):
        return

    discord_id = str(message.author.id)
    username = message.author.name

    amount = _roll_drop_amount()
    cooldown_seconds = random.uniform(
        QUOTA_DROP_COOLDOWN_MIN_SECONDS, QUOTA_DROP_COOLDOWN_MAX_SECONDS
    )
    cooldown_until = time.time() + cooldown_seconds

    # 原子检查+写入冷却；无论结果如何都进冷却
    if not _try_set_cooldown(discord_id, cooldown_until):
        return

    # 一定概率触发扣减事件
    if random.random() < QUOTA_DROP_DEDUCT_CHANCE:
        deduct_amount = random.randint(QUOTA_DROP_DEDUCT_MIN, QUOTA_DROP_DEDUCT_MAX)
        current_quota = await adjust_quota(client, "deduct", username, deduct_amount)
        if current_quota is None:
            return
        print(f"[QuotaDrop] {username} 被扣减 {deduct_amount} 点，当前额度 {current_quota}，冷却 {cooldown_seconds:.0f} 秒")
        await _queue_notification(
            message.channel,
            f"💸 {message.author.mention} 运气不佳，被扣减 {deduct_amount} 点活动额度，当前额度 {current_quota} 点……",
        )
        return

    if amount == 0:
        print(f"[QuotaDrop] {username} 掉落 0 点，冷却 {cooldown_seconds:.0f} 秒")
        await _queue_notification(
            message.channel,
            f"💨 {message.author.mention} 很遗憾，这次没有掉落额度，下次好运！",
        )
        return

    current_quota = await adjust_quota(client, "grant", username, amount)
    if current_quota is None:
        return

    print(f"[QuotaDrop] {username} 掉落 {amount} 点，当前额度 {current_quota}，冷却 {cooldown_seconds:.0f} 秒")
    await _queue_notification(
        message.channel,
        f"🎉 {message.author.mention} 幸运掉落 {amount} 点活动额度，当前额度 {current_quota} 点！",
    )


def start_quota_drop() -> None:
    """初始化掉落服务（建库、打印启动信息）。"""
    _init_db()
    print(f"[QuotaDrop] 已启动，监听频道 {QUOTA_CHANNEL_ID}，冷却数据库 {DB_PATH}")
