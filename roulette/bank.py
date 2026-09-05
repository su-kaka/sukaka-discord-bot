"""地精银行：存钱、取钱、查余额。"""

from __future__ import annotations

import os
import random
import sqlite3
from pathlib import Path

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    BANK_DB,
    BANK_DEPOSIT_PERCENT,
    BANK_MIN_DEPOSIT,
    BANK_WITHDRAW_MAX_PERCENT,
    BANK_WITHDRAW_MIN_PERCENT,
)

DB_PATH = Path(os.getenv("BANK_DB", BANK_DB))


def _init_db() -> None:
    """建表：用户银行存款。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_accounts (
                discord_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


def _get_balance(discord_id: int) -> int:
    """查询用户银行余额。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT balance FROM bank_accounts WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
        return row[0] if row else 0


def _set_balance(discord_id: int, balance: int) -> None:
    """设置用户银行余额。"""
    now = sqlite3.connect(DB_PATH).execute("SELECT strftime('%s', 'now')").fetchone()[0]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO bank_accounts (discord_id, balance, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET balance = ?, updated_at = ?
            """,
            (discord_id, balance, now, now, balance, now),
        )


def _add_balance(discord_id: int, amount: int) -> int:
    """增加余额，返回新余额。"""
    current = _get_balance(discord_id)
    new_balance = current + amount
    _set_balance(discord_id, new_balance)
    return new_balance


def _deduct_balance(discord_id: int, amount: int) -> int:
    """减少余额，返回新余额。余额不足时扣到 0。"""
    current = _get_balance(discord_id)
    deduct = min(amount, current)
    new_balance = current - deduct
    _set_balance(discord_id, new_balance)
    return new_balance


async def handle_deposit(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「存钱」命令：将 50% 额度存入地精银行。"""
    quota = await query_quota(client, message.author.name)
    if quota is None:
        await message.channel.send("🏦 查询额度失败，请稍后再试。")
        return

    amount = int(quota * BANK_DEPOSIT_PERCENT / 100)
    if amount < BANK_MIN_DEPOSIT:
        await message.channel.send(
            f"🏦 存款失败：当前额度 {quota} 点，{BANK_DEPOSIT_PERCENT}% 为 {amount} 点，"
            f"低于最低起存金额 {BANK_MIN_DEPOSIT} 点。"
        )
        return

    result = await adjust_quota(client, "deduct", message.author.name, amount)
    if result is None:
        await message.channel.send("🏦 扣除额度失败，请稍后再试。")
        return

    new_balance = _add_balance(message.author.id, amount)
    await message.channel.send(
        f"🏦 {message.author.mention} 存入 **{amount} 点** 到地精银行！\n"
        f"银行余额：**{new_balance} 点**，当前额度：**{result} 点**。"
    )


async def handle_withdraw(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「取钱」命令：取出全部存款，随机扣除 50%-100% 手续费。"""
    balance = _get_balance(message.author.id)
    if balance <= 0:
        await message.channel.send("🏦 你在地精银行没有存款。")
        return

    fee_percent = random.randint(BANK_WITHDRAW_MIN_PERCENT, BANK_WITHDRAW_MAX_PERCENT)
    fee = int(balance * fee_percent / 100)
    amount = balance - fee

    # 清零银行账户
    _set_balance(message.author.id, 0)

    if amount > 0:
        new_quota = await adjust_quota(client, "grant", message.author.name, amount)
        if new_quota is None:
            # 发放失败，退回银行
            _set_balance(message.author.id, balance)
            await message.channel.send("🏦 取款失败，请稍后再试。")
            return
    else:
        new_quota = await query_quota(client, message.author.name)

    await message.channel.send(
        f"🏦 {message.author.mention} 从地精银行取出 **{balance} 点**！\n"
        f"手续费 **{fee} 点**（{fee_percent}%），实得 **{amount} 点**，当前额度：**{new_quota} 点**。"
    )


async def handle_bank_balance(message: discord.Message) -> None:
    """处理「我的钱」命令：查看地精银行余额。"""
    balance = _get_balance(message.author.id)
    await message.channel.send(
        f"🏦 {message.author.mention} 的地精银行余额：**{balance} 点**。"
    )


_init_db()
