"""黑市：发送「黑市」拉出商店界面，随机 10 种卡牌明码标价，点击购买，卖光自动重置。"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import discord
import httpx

from roulette.api import adjust_quota, query_quota
from roulette.constants import (
    BLACK_MARKET_DB,
    BLACK_MARKET_EXCLUDED_CARDS,
    BLACK_MARKET_EXCLUDED_KINDS,
    BLACK_MARKET_KEYWORD,
    BLACK_MARKET_PRICE_MAX,
    BLACK_MARKET_PRICE_MIN,
    BLACK_MARKET_SLOTS,
    BLACK_MARKET_STOCK_MAX,
    BLACK_MARKET_STOCK_MIN,
    BLACK_MARKET_TIMEOUT_SECONDS,
)
from roulette.gacha import (
    BAG_CARDS,
    CARD_POOL,
    INSTANT_SETTLE_CARDS,
    UNIQUE_CARDS,
    _add_effect_on_draw,
    _settle_depositking,
    _settle_error,
    _settle_inflation,
    _settle_robinhood,
    _settle_selfdestruct,
    _settle_sellout,
    _settle_weak,
    has_collector,
    has_effect,
)

DB_PATH = Path(os.getenv("BLACK_MARKET_DB", BLACK_MARKET_DB))

# 卡牌种类归类：唯一道具 / 即时生效卡 / 背包道具卡 / 状态 buff 类
KIND_UNIQUE = "unique"
KIND_INSTANT = "instant"
KIND_BAG = "bag"


def _card_kind(card_key: str) -> Optional[str]:
    """返回卡牌种类：唯一道具 / 即时生效卡 / 背包道具卡。"""
    if card_key in UNIQUE_CARDS:
        return KIND_UNIQUE
    if card_key in INSTANT_SETTLE_CARDS:
        return KIND_INSTANT
    if card_key in BAG_CARDS:
        return KIND_BAG
    return None


# 黑市出售范围：背包道具卡 + 即时生效卡，排除指定种类（目前：唯一道具）与排除名单（空白/许愿池/通货膨胀/存为王/虚弱）
_base_pool = (BAG_CARDS | INSTANT_SETTLE_CARDS) - BLACK_MARKET_EXCLUDED_CARDS
BLACK_MARKET_POOL = sorted(
    key for key in _base_pool if _card_kind(key) not in BLACK_MARKET_EXCLUDED_KINDS
)

# 即时生效卡的结算函数映射（统一签名 (message, client, announce)）：
# 黑市购买后立即静默结算，由黑市自己播报购买结果
Settler = Callable[[discord.Message, httpx.AsyncClient, bool], Awaitable[None]]
_INSTANT_SETTLERS: dict[str, Settler] = {
    "robinhood": lambda m, c, a: _settle_robinhood(m, c, announce=a),
    "selfdestruct": lambda m, c, a: _settle_selfdestruct(m, c, announce=a),
    "error": lambda m, c, a: _settle_error(m, c, announce=a),
    "inflation": lambda m, c, a: _settle_inflation(m, announce=a),
    "depositking": lambda m, c, a: _settle_depositking(m, c, announce=a),
    "sellout": lambda m, c, a: _settle_sellout(m, c, announce=a),
    "weak": lambda m, c, a: _settle_weak(m, announce=a),
}


def _init_db() -> None:
    """建表：黑市货架（全服共享，每种在售卡牌一行）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS black_market_shelf (
                card_key TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                stock INTEGER NOT NULL,
                listed_at REAL NOT NULL
            )
            """
        )


def _restock() -> None:
    """黑市上架新货：清空货架，随机 10 种卡牌，价格与库存随机。"""
    keys = random.sample(BLACK_MARKET_POOL, min(BLACK_MARKET_SLOTS, len(BLACK_MARKET_POOL)))
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM black_market_shelf")
        conn.executemany(
            "INSERT INTO black_market_shelf (card_key, price, stock, listed_at) VALUES (?, ?, ?, ?)",
            [
                (
                    key,
                    random.randint(BLACK_MARKET_PRICE_MIN, BLACK_MARKET_PRICE_MAX),
                    random.randint(BLACK_MARKET_STOCK_MIN, BLACK_MARKET_STOCK_MAX),
                    now,
                )
                for key in keys
            ],
        )


def _load_shelf() -> list[tuple[str, int, int]]:
    """纯读取货架（不补货），返回 (card_key, price, stock) 列表。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT card_key, price, stock FROM black_market_shelf WHERE stock > 0"
        ).fetchall()
    return rows


def _get_shelf() -> list[tuple[str, int, int]]:
    """查询货架；空架（卖光/首次开张）自动补货。"""
    rows = _load_shelf()
    if not rows:
        _restock()
        rows = _load_shelf()
    return rows


def _try_purchase(card_key: str) -> Optional[tuple[int, int]]:
    """原子扣减 1 件库存，返回 (价格, 剩余库存)；无货返回 None。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE black_market_shelf SET stock = stock - 1
            WHERE card_key = ? AND stock > 0
            """,
            (card_key,),
        )
        if cursor.rowcount <= 0:
            return None
        row = conn.execute(
            "SELECT price, stock FROM black_market_shelf WHERE card_key = ?",
            (card_key,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def _return_stock(card_key: str) -> None:
    """购买失败时回补 1 件库存（货架已重置则无操作）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE black_market_shelf SET stock = stock + 1 WHERE card_key = ?",
            (card_key,),
        )


def _shelf_text(rows: list[tuple[str, int, int]], restocked: bool = False) -> str:
    """组装货架文案。"""
    header = "🌒 **黑 市**"
    if restocked:
        header += "（新货上架！）"
    lines = [header]
    for card_key, price, stock in rows:
        name, desc, _ = CARD_POOL.get(card_key, (card_key, "", 0))
        tag = "⚡即时" if card_key in INSTANT_SETTLE_CARDS else "🎒背包"
        lines.append(f"• **{name}** — {price} 点/张 ×{stock} 件【{tag}】\n  └ {desc}")
    lines.append(
        "🃏 不卖唯一道具、空白、许愿池、虚弱与自爆；背包道具需持有「收藏家」才能叠加数量；全部卖光自动上新。点击按钮购买一件。"
    )
    return "\n".join(lines)


class BlackMarketView(discord.ui.View):
    """黑市购买视图：每种在售卡牌一个按钮，点击即买一件（额度足够时）。

    货架全服共享（存 black_market.db），多个黑市界面同时存在也指向同一货架；
    库存扣减用 SQLite 原子 UPDATE，同一界面内用锁串行处理避免并发刷按钮。
    """

    def __init__(self, message: discord.Message, client: httpx.AsyncClient) -> None:
        super().__init__(timeout=BLACK_MARKET_TIMEOUT_SECONDS)
        self.message_obj = message
        self.client = client
        self.message: Optional[discord.Message] = None
        self._lock = asyncio.Lock()
        self._start_time = time.monotonic()  # 视图创建时间，用于计算剩余超时

        rows = _get_shelf()
        self._build_buttons(rows)

    def _build_buttons(self, rows: list[tuple[str, int, int]]) -> None:
        """按货架生成购买按钮（每行 5 个）。"""
        self.clear_items()
        for index, (card_key, price, _stock) in enumerate(rows):
            name, _, _ = CARD_POOL.get(card_key, (card_key, "", 0))
            button = discord.ui.Button(
                label=f"{name} {price}点",
                style=discord.ButtonStyle.primary,
                emoji="🃏",
                row=index // 5,
            )

            async def _buy(
                interaction: discord.Interaction,
                key: str = card_key,
            ) -> None:
                await self._purchase(interaction, key)

            button.callback = _buy
            self.add_item(button)

    async def _restock_and_refresh(self, interaction: discord.Interaction) -> None:
        """卖光后重新上架并刷新界面（保留剩余超时时间）。"""
        _restock()
        rows = _load_shelf()
        self._build_buttons(rows)
        now = time.monotonic()
        remaining = max(self.timeout - (now - self._start_time), 0.0)
        try:
            # 先 stop() 再编辑：避免 message.edit 重新注册 view 重置超时计时器，
            # 行为与 packet_base 一致（编辑后用剩余时间恢复计时）
            self.stop()
            await interaction.response.edit_message(
                content=_shelf_text(rows, restocked=True),
                view=self,
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        finally:
            self._start_time = now
            self.timeout = remaining

    async def _purchase(self, interaction: discord.Interaction, card_key: str) -> None:
        async with self._lock:
            user = interaction.user
            name, desc, _ = CARD_POOL.get(card_key, (card_key, "", 0))

            # 背包道具：已持有且未持有收藏家时不允许购买（收藏家才能叠加数量）
            if (
                card_key not in INSTANT_SETTLE_CARDS
                and has_effect(user.id, card_key)
                and not has_collector(user.id)
            ):
                await interaction.response.send_message(
                    f"🌒 你已持有 **{name}**，需持有「收藏家」才能叠加数量，先别浪费额度了。",
                    ephemeral=True,
                )
                return

            # 原子扣库存：并发购买同一件商品只有一个能成功
            purchase = _try_purchase(card_key)
            if purchase is None:
                if not _load_shelf():
                    # 货架卖光：重新上架并刷新
                    await self._restock_and_refresh(interaction)
                else:
                    await interaction.response.send_message(
                        "🌒 这件商品刚刚被别人买走了！", ephemeral=True
                    )
                return
            price, remaining = purchase

            # 扣额度：查询失败/额度不足则回补库存
            quota = await query_quota(self.client, user.name)
            if quota is None:
                _return_stock(card_key)
                await interaction.response.send_message(
                    "🌒 查询额度失败，请稍后再试。", ephemeral=True
                )
                return
            if quota < price:
                _return_stock(card_key)
                await interaction.response.send_message(
                    f"🌒 额度不足：当前 {quota} 点，**{name}** 需要 {price} 点。",
                    ephemeral=True,
                )
                return
            deducted = await adjust_quota(self.client, "deduct", user.name, price)
            if deducted is None:
                _return_stock(card_key)
                await interaction.response.send_message(
                    "🌒 扣除额度失败，请稍后再试。", ephemeral=True
                )
                return

            # 发货：即时生效卡立即静默结算，背包道具卡入包（与抽卡同款收藏家叠加规则）
            note = ""
            if card_key in INSTANT_SETTLE_CARDS:
                settler = _INSTANT_SETTLERS.get(card_key)
                if settler is None:
                    # 防御：不在结算映射内的即时卡退化为入包
                    _add_effect_on_draw(user.id, card_key)
                    note = "\n🎒 已放入背包。"
                else:
                    await settler(self.message_obj, self.client, False)
            else:
                current, stacked = _add_effect_on_draw(user.id, card_key)
                if stacked:
                    note = f"\n🎒 收藏家生效，叠加至 ×{current}，可用 `我的卡牌` 查看。"
                else:
                    note = "\n🎒 已放入背包，可用 `我的卡牌` 查看。"

            result_text = (
                f"🌒 {user.mention} 花 **{price} 点** 买下 **{name}**"
                f"（该商品剩余 {remaining} 件）！\n✨ {desc}{note}"
            )

            # 卖光：重新上架并刷新界面，购买结果用 followup 播报
            rows_now = _load_shelf()
            if not rows_now:
                await self._restock_and_refresh(interaction)
                try:
                    await interaction.followup.send(result_text)
                except (discord.NotFound, discord.HTTPException):
                    pass
                return

            try:
                await interaction.response.edit_message(
                    content=_shelf_text(rows_now) + f"\n\n{result_text}",
                    view=self,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    async def on_timeout(self) -> None:
        """超时打烊：移除按钮，货架库存保留。"""
        self.clear_items()
        try:
            if self.message:
                await self.message.edit(
                    content=f"🌒 黑市打烊了……发送「{BLACK_MARKET_KEYWORD}」重新开张。",
                    view=self,
                )
        except (discord.NotFound, discord.HTTPException):
            pass


async def handle_black_market(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「黑市」命令：拉出黑市购买界面。"""
    rows = _get_shelf()
    view = BlackMarketView(message, client)
    if not view.children:
        await message.channel.send("🌒 黑市暂时无货。")
        return
    view.message = await message.channel.send(
        _shelf_text(rows),
        view=view,
    )


_init_db()
