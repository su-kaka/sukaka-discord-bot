"""地精银行：存钱、取钱、查余额。"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    BANK_DB,
    BANK_DEPOSIT_PERCENT,
    BANK_HEIST_TARGET_COOLDOWN_SECONDS,
    BANK_MIN_DEPOSIT,
    BANK_ROYAL_SECURITY_THRESHOLD,
    BANK_SECURITY_THRESHOLD,
    BANK_WITHDRAW_MAX_PERCENT,
    BANK_WITHDRAW_MIN_PERCENT,
)

DB_PATH = Path(os.getenv("BANK_DB", BANK_DB))


def _init_db() -> None:
    """建表：用户银行存款 + 仇恨状态 + 抢劫冷却。"""
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_hatred (
                discord_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_heist_cooldowns (
                discord_id INTEGER PRIMARY KEY,
                cooldown_until REAL NOT NULL
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


def has_security_service(discord_id: int) -> bool:
    """存款超过 1000 点解锁普通安保：无法被抢劫。"""
    return _get_balance(discord_id) > BANK_SECURITY_THRESHOLD


def has_royal_security_service(discord_id: int) -> bool:
    """存款超过 2000 点解锁皇家安保：无法被抢劫、诱惑、劫富济贫。"""
    return _get_balance(discord_id) > BANK_ROYAL_SECURITY_THRESHOLD


def get_all_accounts_with_min_balance(min_balance: int) -> list[tuple[int, int]]:
    """查询所有存款 ≥ min_balance 的账号，返回 (discord_id, balance) 列表。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT discord_id, balance FROM bank_accounts WHERE balance >= ?",
            (min_balance,),
        ).fetchall()
    return rows


def set_hatred(discord_id: int) -> None:
    """标记仇恨状态。"""
    now = sqlite3.connect(DB_PATH).execute("SELECT strftime('%s', 'now')").fetchone()[0]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bank_hatred (discord_id, created_at) VALUES (?, ?)",
            (discord_id, now),
        )


def has_hatred(discord_id: int) -> bool:
    """是否有仇恨状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM bank_hatred WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return row is not None


def clear_hatred(discord_id: int) -> None:
    """清除仇恨状态。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM bank_hatred WHERE discord_id = ?",
            (discord_id,),
        )


def mark_heist_cooldown(discord_id: int) -> None:
    """标记目标被抢冷却。"""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bank_heist_cooldowns (discord_id, cooldown_until) VALUES (?, ?)",
            (discord_id, now + BANK_HEIST_TARGET_COOLDOWN_SECONDS),
        )


def is_heist_cooldown(discord_id: int) -> bool:
    """目标是否在被抢冷却中。"""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT cooldown_until FROM bank_heist_cooldowns WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return row is not None and row[0] > now


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

    # 仇恨没收
    hatred_note = ""
    if has_hatred(message.author.id):
        clear_hatred(message.author.id)
        hatred_note = "\n🔥 仇恨解除！本次存款被强制没收！"
        new_balance = _get_balance(message.author.id)
    else:
        new_balance = _add_balance(message.author.id, amount)

    security_note = ""
    if has_royal_security_service(message.author.id):
        security_note = f"\n👑 存款超过 {BANK_ROYAL_SECURITY_THRESHOLD} 点，皇家安保已解锁：无法被抢劫、诱惑、劫富济贫！"
    elif has_security_service(message.author.id):
        security_note = f"\n🛡️ 存款超过 {BANK_SECURITY_THRESHOLD} 点，普通安保已解锁：无法被抢劫！"
    await message.channel.send(
        f"🏦 {message.author.mention} 存入 **{amount} 点** 到地精银行！\n"
        f"银行余额：**{new_balance} 点**，当前额度：**{result} 点**。{security_note}{hatred_note}"
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
    security_note = ""
    if has_royal_security_service(message.author.id):
        security_note = f"\n👑 皇家安保生效中（存款 > {BANK_ROYAL_SECURITY_THRESHOLD} 点）：无法被抢劫、诱惑、劫富济贫。"
    elif has_security_service(message.author.id):
        security_note = f"\n🛡️ 普通安保生效中（存款 > {BANK_SECURITY_THRESHOLD} 点）：无法被抢劫。"
    await message.channel.send(
        f"🏦 {message.author.mention} 的地精银行余额：**{balance} 点**。{security_note}"
    )


_init_db()
