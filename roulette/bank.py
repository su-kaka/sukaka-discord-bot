"""地精银行：存钱、取钱、查余额。"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    BANK_DB,
    BANK_DEPOSIT_PERCENT,
    BANK_HEIST_TARGET_COOLDOWN_SECONDS,
    BANK_LOAN_AMOUNT,
    BANK_LOAN_FEE,
    BANK_LOAN_LENDER_GAIN,
    BANK_LOAN_MIN_LENDER_BALANCE,
    BANK_LOAN_REPAY,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_loans (
                discord_id INTEGER PRIMARY KEY,
                lender_id INTEGER NOT NULL,
                remaining INTEGER NOT NULL,
                created_at REAL NOT NULL
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


def get_loan(discord_id: int) -> Optional[tuple[int, int]]:
    """查询未还清贷款，返回 (lender_id, remaining) 或 None。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT lender_id, remaining FROM bank_loans WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def has_loan(discord_id: int) -> bool:
    """是否有未还清贷款。"""
    return get_loan(discord_id) is not None


def _create_loan(discord_id: int, lender_id: int) -> None:
    """创建贷款记录。"""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO bank_loans (discord_id, lender_id, remaining, created_at) VALUES (?, ?, ?, ?)",
            (discord_id, lender_id, BANK_LOAN_REPAY, now),
        )


def _repay_loan(discord_id: int, amount: int) -> tuple[int, int, int]:
    """还款，返回 (实还金额, 剩余欠款, lender_id)。"""
    loan = get_loan(discord_id)
    if loan is None:
        return 0, 0, 0
    lender_id, remaining = loan
    repaid = min(amount, remaining)
    new_remaining = remaining - repaid
    with sqlite3.connect(DB_PATH) as conn:
        if new_remaining <= 0:
            conn.execute("DELETE FROM bank_loans WHERE discord_id = ?", (discord_id,))
        else:
            conn.execute(
                "UPDATE bank_loans SET remaining = ? WHERE discord_id = ?",
                (new_remaining, discord_id),
            )
    return repaid, new_remaining, lender_id


def _pick_lender(exclude_id: int) -> Optional[tuple[int, int]]:
    """随机选一个存款 ≥ BANK_LOAN_MIN_LENDER_BALANCE 的借款账号。"""
    candidates = get_all_accounts_with_min_balance(BANK_LOAN_MIN_LENDER_BALANCE)
    candidates = [(did, bal) for did, bal in candidates if did != exclude_id]
    if not candidates:
        return None
    return random.choice(candidates)


async def handle_loan(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「贷款」命令：向存款充足的用户借款 50 点，需还 60 点。"""
    if has_loan(message.author.id):
        loan = get_loan(message.author.id)
        await message.channel.send(
            f"🏦 你还有 **{loan[1]} 点** 贷款未还清，无法再次贷款！"
        )
        return

    lender = _pick_lender(message.author.id)
    if lender is None:
        await message.channel.send(
            f"🏦 贷款失败：没有存款 ≥ {BANK_LOAN_MIN_LENDER_BALANCE} 点的借款账号。"
        )
        return

    lender_id, lender_balance = lender

    # 从借款账号扣款（只扣本金 50 点）
    new_lender_balance = _deduct_balance(lender_id, BANK_LOAN_AMOUNT)
    actual_deducted = lender_balance - new_lender_balance
    if actual_deducted < BANK_LOAN_AMOUNT:
        await message.channel.send("🏦 贷款失败：借款账号余额不足。")
        return

    # 给贷款用户发放额度
    new_quota = await adjust_quota(client, "grant", message.author.name, BANK_LOAN_AMOUNT)
    if new_quota is None:
        # 发放失败，退回借款账号
        _add_balance(lender_id, actual_deducted)
        await message.channel.send("🏦 贷款发放失败，请稍后再试。")
        return

    # 创建贷款记录
    _create_loan(message.author.id, lender_id)

    lender_member = message.guild.get_member(lender_id) if message.guild else None
    lender_display = lender_member.mention if lender_member else f"用户 {lender_id}"

    await message.channel.send(
        f"🏦 {message.author.mention} 成功贷款 **{BANK_LOAN_AMOUNT} 点**！\n"
        f"借款账号：{lender_display}（被扣除 **{actual_deducted} 点** 存款）\n"
        f"需还款 **{BANK_LOAN_REPAY} 点**（其中 {BANK_LOAN_LENDER_GAIN} 点归借款账号：50 本金 + 5 利息，{BANK_LOAN_FEE} 点手续费销毁）。\n"
        f"💡 发送「存钱」将优先偿还贷款。"
    )


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

    # 优先还贷款
    loan_note = ""
    remaining_deposit = amount
    if has_loan(message.author.id):
        repaid, remaining_loan, lender_id = _repay_loan(message.author.id, amount)
        remaining_deposit = amount - repaid
        lender_member = message.guild.get_member(lender_id) if message.guild else None
        lender_display = lender_member.mention if lender_member else f"用户 {lender_id}"
        if remaining_loan <= 0:
            loan_note = f"\n💳 已还清贷款 **{repaid} 点** 给 {lender_display}！"
        else:
            loan_note = f"\n💳 偿还贷款 **{repaid} 点** 给 {lender_display}，剩余欠款 **{remaining_loan} 点**。"

    # 仇恨没收
    hatred_note = ""
    if has_hatred(message.author.id):
        clear_hatred(message.author.id)
        hatred_note = "\n🔥 仇恨解除！本次存款被强制没收！"
        new_balance = _get_balance(message.author.id)
    elif remaining_deposit > 0:
        new_balance = _add_balance(message.author.id, remaining_deposit)
    else:
        new_balance = _get_balance(message.author.id)

    security_note = ""
    if has_royal_security_service(message.author.id):
        security_note = f"\n👑 存款超过 {BANK_ROYAL_SECURITY_THRESHOLD} 点，皇家安保已解锁：无法被抢劫、诱惑、劫富济贫！"
    elif has_security_service(message.author.id):
        security_note = f"\n🛡️ 存款超过 {BANK_SECURITY_THRESHOLD} 点，普通安保已解锁：无法被抢劫！"
    await message.channel.send(
        f"🏦 {message.author.mention} 存入 **{amount} 点** 到地精银行！\n"
        f"银行余额：**{new_balance} 点**，当前额度：**{result} 点**。{security_note}{hatred_note}{loan_note}"
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
