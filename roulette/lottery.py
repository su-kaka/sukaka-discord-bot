"""彩票：10 点买一张，5% 概率赢走全部奖池，失败 8 点进奖池（2 点销毁）。"""

from __future__ import annotations

import os
import random
import sqlite3
from pathlib import Path

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    LOTTERY_BASE_POOL,
    LOTTERY_COST,
    LOTTERY_DB,
    LOTTERY_POOL_CONTRIBUTE,
    LOTTERY_WIN_CHANCE,
)

DB_PATH = Path(os.getenv("LOTTERY_DB", LOTTERY_DB))


def _init_db() -> None:
    """建表：彩票奖池（单行）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lottery_pool (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                pool INTEGER NOT NULL
            )
            """
        )
        # 初始化基础奖池
        conn.execute(
            "INSERT OR IGNORE INTO lottery_pool (id, pool) VALUES (1, ?)",
            (LOTTERY_BASE_POOL,),
        )


def get_pool() -> int:
    """查询当前奖池。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT pool FROM lottery_pool WHERE id = 1").fetchone()
    return row[0] if row else LOTTERY_BASE_POOL


def _add_pool(amount: int) -> None:
    """奖池增加。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE lottery_pool SET pool = pool + ? WHERE id = 1", (amount,))


def _reset_pool() -> None:
    """奖池重置为基础额度。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE lottery_pool SET pool = ? WHERE id = 1", (LOTTERY_BASE_POOL,))


async def handle_lottery(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「买彩票」命令。"""
    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🎟️ 查询额度失败，请稍后再试。")
        return
    if quota < LOTTERY_COST:
        await message.channel.send(
            f"🎟️ 额度不足：当前 {quota} 点，买彩票需要 {LOTTERY_COST} 点。"
        )
        return

    result = await adjust_quota(client, "deduct", message.author.name, LOTTERY_COST)
    if result is None:
        await message.channel.send("🎟️ 扣除额度失败，请稍后再试。")
        return

    pool = get_pool()

    if random.random() < LOTTERY_WIN_CHANCE:
        # 中奖：拿走全部奖池，奖池重置为基础额度
        _reset_pool()
        new_quota = await adjust_quota(client, "grant", message.author.name, pool)
        if new_quota is None:
            await message.channel.send(
                f"🎟️🎉 {message.author.mention} 中大奖了！但奖金发放失败，请联系管理员手动补发 {pool} 点。"
            )
            return
        await message.channel.send(
            f"🎟️🎉 **中大奖！** {message.author.mention} 花 {LOTTERY_COST} 点买彩票，\n"
            f"一举赢走全部奖池 **{pool} 点**！当前额度 {new_quota} 点！\n"
            f"奖池已重置为 {LOTTERY_BASE_POOL} 点。"
        )
        return

    # 未中奖：8 点进奖池，2 点销毁
    _add_pool(LOTTERY_POOL_CONTRIBUTE)
    new_pool = pool + LOTTERY_POOL_CONTRIBUTE
    await message.channel.send(
        f"🎟️ {message.author.mention} 花 {LOTTERY_COST} 点买彩票……\n"
        f"💨 没中！{LOTTERY_POOL_CONTRIBUTE} 点进入奖池（{LOTTERY_COST - LOTTERY_POOL_CONTRIBUTE} 点销毁），"
        f"当前奖池 **{new_pool} 点**。"
    )


_init_db()
