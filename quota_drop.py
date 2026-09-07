"""发言随机掉落活动额度：监听目标频道发言，随机掉落 0-5 点额度，单用户冷却 1-60 分钟。"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx
import discord

if TYPE_CHECKING:
    from bot import SukakaBot

QUOTA_CHANNEL_ID = 1455038454772531311
DEFAULT_API_BASE = "https://catiecli.sukaka.top"
DB_PATH = Path(os.getenv("QUOTA_DROP_DB", "quota_drops.db"))

DROP_MIN = 0
DROP_MAX = 50
# 掉落 0 点的概率（0-1），默认 30%；剩余概率由 1-20 点均匀平分
DROP_ZERO_CHANCE = float(os.getenv("QUOTA_DROP_ZERO_CHANCE", "0.3"))
# 触发扣减事件的概率（0-1），默认 10%
DEDUCT_CHANCE = float(os.getenv("QUOTA_DEDUCT_CHANCE", "0.1"))
DEDUCT_MIN = 1
DEDUCT_MAX = 50
COOLDOWN_MIN_SECONDS = int(os.getenv("QUOTA_DROP_COOLDOWN_MIN", "30"))
COOLDOWN_MAX_SECONDS = int(os.getenv("QUOTA_DROP_COOLDOWN_MAX", "180"))
NOTIFY_DELETE_AFTER = 10
API_TIMEOUT_SECONDS = 15


def _roll_drop_amount() -> int:
    """随机掉落点数：DROP_ZERO_CHANCE 概率为 0，否则 1-20 均匀随机。"""
    if random.random() < DROP_ZERO_CHANCE:
        return 0
    return random.randint(1, DROP_MAX)


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


async def _call_quota_api(
    client: httpx.AsyncClient, endpoint: str, username: str, amount: int
) -> Optional[int]:
    """调用活动额度 API（grant/deduct），成功返回当前额度，失败返回 None。"""
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        print("[QuotaDrop] 错误：未配置 ACTIVITY_QUOTA_API_KEY")
        return None

    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.post(
            f"{api_base}/api/activity-quota/{endpoint}",
            headers={
                "Content-Type": "application/json",
                "X-Activity-Quota-Key": api_key,
            },
            json={"username": username, "amount": amount},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("current_activity_quota", 0))
        detail = data.get("detail", "未知错误") if isinstance(data, dict) else str(data)
        print(f"[QuotaDrop] {endpoint} 失败（HTTP {response.status_code}）：{detail}")
        return None
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[QuotaDrop] {endpoint} 请求异常：{exc}")
        return None


# ── 批量发送：控制发消息频率不低于 0.5s，多条通知合并 ──────────────
_batch_buffer: list[str] = []
_batch_lock = asyncio.Lock()
_batch_task: Optional[asyncio.Task[None]] = None
_last_flush_time: float = 0.0
MIN_SEND_INTERVAL = 0.5
MAX_BATCH_CHARS = 1800  # Discord 限制 2000 字符，留余量


async def _flush_batch(channel: discord.abc.Messageable) -> None:
    """将缓冲区中的通知合并为一条消息发送，确保距上次发送至少 MIN_SEND_INTERVAL 秒。"""
    global _last_flush_time, _batch_task
    elapsed = time.time() - _last_flush_time
    if elapsed < MIN_SEND_INTERVAL:
        await asyncio.sleep(MIN_SEND_INTERVAL - elapsed)

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
        if current and len(current) + 1 + len(msg) > MAX_BATCH_CHARS:
            chunks.append(current)
            current = msg
        else:
            current = f"{current}\n{msg}" if current else msg
    if current:
        chunks.append(current)

    for chunk in chunks:
        try:
            await channel.send(chunk, delete_after=NOTIFY_DELETE_AFTER)
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
    cooldown_seconds = random.uniform(COOLDOWN_MIN_SECONDS, COOLDOWN_MAX_SECONDS)
    cooldown_until = time.time() + cooldown_seconds

    # 原子检查+写入冷却；无论结果如何都进冷却
    if not _try_set_cooldown(discord_id, cooldown_until):
        return

    # 10% 概率触发扣减事件
    if random.random() < DEDUCT_CHANCE:
        deduct_amount = random.randint(DEDUCT_MIN, DEDUCT_MAX)
        current_quota = await _call_quota_api(client, "deduct", username, deduct_amount)
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

    current_quota = await _call_quota_api(client, "grant", username, amount)
    if current_quota is None:
        return

    print(f"[QuotaDrop] {username} 掉落 {amount} 点，当前额度 {current_quota}，冷却 {cooldown_seconds:.0f} 秒")
    await _queue_notification(
        message.channel,
        f"🎉 {message.author.mention} 幸运掉落 {amount} 点活动额度，当前额度 {current_quota} 点！",
    )


def start_quota_drop(bot: "SukakaBot") -> httpx.AsyncClient:
    """初始化掉落服务，返回共享的 HTTP 客户端。"""
    _init_db()
    client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    bot.quota_drop_client = client  # type: ignore[attr-defined]
    print(f"[QuotaDrop] 已启动，监听频道 {QUOTA_CHANNEL_ID}，冷却数据库 {DB_PATH}")
    return client
