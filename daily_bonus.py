"""每日发言奖励：监听固定频道，每个自然日（北京时间）首次发言静默发放活动额度。

关键词命中排除列表（打卡/签到/发言）的发言不触发，避免无意义灌水。"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import discord
import httpx

from paths import data_file
from roulette.api import adjust_quota
from roulette.constants import API_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from bot import SukakaBot

DAILY_BONUS_CHANNEL_ID = 1455038454772531311  # 监听频道（轮播/家庭组频道）
DAILY_BONUS_AMOUNT = 1500  # 每日首次发言发放额度
DAILY_BONUS_UTC_OFFSET_SECONDS = 8 * 3600  # 北京时间相对 UTC 的固定偏移（无夏令时）
# 命中任一关键词的发言不算「有效发言」，防止为领奖励刷无意义内容
DAILY_BONUS_EXCLUDED_KEYWORDS = ("打卡", "签到", "发言", "来财" )
DB_PATH = data_file("DAILY_BONUS_DB", "daily_bonus.db")


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bonus_claims (
                discord_id TEXT PRIMARY KEY,
                day TEXT NOT NULL
            )
            """
        )


def _beijing_today() -> str:
    """当前北京时间的自然日（YYYY-MM-DD）。"""
    return time.strftime(
        "%Y-%m-%d", time.gmtime(time.time() + DAILY_BONUS_UTC_OFFSET_SECONDS)
    )


def _try_claim(discord_id: str, day: str) -> bool:
    """原子记录当日发放。返回 True 表示今天尚未发放过（可以发放）。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO daily_bonus_claims (discord_id, day)
            VALUES (?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET day = excluded.day
            WHERE daily_bonus_claims.day < excluded.day
            """,
            (discord_id, day),
        )
        return cursor.rowcount > 0


def _rollback_claim(discord_id: str, day: str) -> None:
    """发放失败时回滚当日记录，用户当日发言仍可重新触发。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM daily_bonus_claims WHERE discord_id = ? AND day = ?",
            (discord_id, day),
        )


def start_daily_bonus(bot: "SukakaBot") -> None:
    """初始化：建库、注册消息入口、打印启动日志。"""
    _init_db()
    client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)

    async def handle_message(message: discord.Message) -> None:
        """频道内任意用户发言：当日首次发放额度，静默无提醒。"""
        content = message.content or ""
        if any(keyword in content for keyword in DAILY_BONUS_EXCLUDED_KEYWORDS):
            return
        discord_id = str(message.author.id)
        day = _beijing_today()
        if not _try_claim(discord_id, day):
            return
        new_quota = await adjust_quota(
            client, "grant", message.author.name, DAILY_BONUS_AMOUNT
        )
        if new_quota is None:
            # 发放失败：回滚当日记录；仅打印日志，不做任何提醒
            _rollback_claim(discord_id, day)
            print(f"[DailyBonus] {message.author.name} 发放失败，已回滚当日记录")
            return
        print(
            f"[DailyBonus] {message.author.name} 每日发言奖励 +{DAILY_BONUS_AMOUNT}，"
            f"当前额度 {new_quota} 点"
        )

    bot.register_message_handler(DAILY_BONUS_CHANNEL_ID, handle_message)
    print(
        f"[DailyBonus] 已启动，监听频道 {DAILY_BONUS_CHANNEL_ID}，"
        f"每个自然日（北京时间）首次发言静默 +{DAILY_BONUS_AMOUNT} 点，"
        f"含「{'」「'.join(DAILY_BONUS_EXCLUDED_KEYWORDS)}」的发言不触发，数据库 {DB_PATH}"
    )
