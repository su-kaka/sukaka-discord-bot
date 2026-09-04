"""Buckshot Roulette 多人混战：6 人一局，各押 5 点活动额度，最后存活者通吃。"""

from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING, Optional

import discord
import httpx

from quota_drop import handle_drop_message

if TYPE_CHECKING:
    from bot import SukakaBot

QUOTA_CHANNEL_ID = 1455038454772531311
DEFAULT_API_BASE = "https://catiecli.sukaka.top"

PLAYER_COUNT = 6
BET_AMOUNT = 5
MAX_HP = 4
MAX_ITEMS = 4
JOIN_TIMEOUT_SECONDS = 120
TURN_TIMEOUT_SECONDS = 60
API_TIMEOUT_SECONDS = 15

TRIGGER_KEYWORD = "轮盘赌"

ITEMS = {
    "magnifier": {"emoji": "🔍", "name": "放大镜"},
    "phone": {"emoji": "📱", "name": "手机"},
    "saw": {"emoji": "🪚", "name": "手锯"},
    "cuffs": {"emoji": "🔗", "name": "手铐"},
    "beer": {"emoji": "🍺", "name": "啤酒"},
    "inverter": {"emoji": "🔄", "name": "逆转器"},
    "adrenaline": {"emoji": "💉", "name": "肾上腺素"},
    "cigarette": {"emoji": "🚬", "name": "香烟"},
    "medicine": {"emoji": "💊", "name": "过期药"},
}
ITEM_GROUPS = {
    "control": {"saw", "cuffs", "adrenaline"},
    "heal": {"cigarette", "medicine"},
}


async def _query_quota(client: httpx.AsyncClient, username: str) -> Optional[int]:
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.request(
            "GET",
            f"{api_base}/api/activity-quota/query",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def _adjust_quota(
    client: httpx.AsyncClient, endpoint: str, username: str, amount: int
) -> Optional[int]:
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.post(
            f"{api_base}/api/activity-quota/{endpoint}",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username, "amount": amount},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("current_activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


def _new_chamber() -> list[bool]:
    """5-8 发弹巢，实弹占 40%-60%。True=实弹。"""
    size = random.randint(5, 8)
    live_count = max(1, round(size * random.uniform(0.4, 0.6)))
    chamber = [True] * live_count + [False] * (size - live_count)
    random.shuffle(chamber)
    return chamber


def _draw_items(count: int, existing: Optional[list[str]] = None) -> list[str]:
    """随机发道具，遵守组上限（含已有道具）。"""
    result = list(existing or [])
    drawn: list[str] = []
    for _ in range(count):
        available = []
        for key in ITEMS:
            group = next((g for g, members in ITEM_GROUPS.items() if key in members), None)
            if group and any(k in ITEM_GROUPS[group] for k in result):
                continue
            available.append(key)
        if not available:
            break
        pick = random.choice(available)
        result.append(pick)
        drawn.append(pick)
    return drawn


class Player:
    def __init__(self, user: discord.Member | discord.User) -> None:
        self.user = user
        self.hp = MAX_HP
        self.items: list[str] = []
        self.known: dict[int, bool] = {}
        self.cuffed = False
        self.saw_active = False
        self.alive = True

    @property
    def mention(self) -> str:
        return self.user.mention

    @property
    def name(self) -> str:
        return self.user.name

    def items_text(self) -> str:
        return " ".join(ITEMS[i]["emoji"] for i in self.items) if self.items else "无"


class JoinView(discord.ui.View):
    def __init__(self, game: "BuckshotGame") -> None:
        super().__init__(timeout=JOIN_TIMEOUT_SECONDS)
        self.game = game

    @discord.ui.button(label="参加（押 5 点）", style=discord.ButtonStyle.primary, emoji="🔫")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.game.add_player(interaction)

    async def on_timeout(self) -> None:
        await self.game.cancel()


class ShootTargetSelect(discord.ui.Select):
    """选择射击目标（打某个对手）。"""

    def __init__(self, game: "BuckshotGame") -> None:
        self.game = game
        current = game.current_player()
        options = [
            discord.SelectOption(
                label=p.user.display_name,
                value=str(p.user.id),
                emoji="🔫",
                description=f"{p.hp} 血",
            )
            for p in game.alive_players()
            if p.user.id != current.user.id
        ]
        super().__init__(placeholder="选择目标……", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.current_player().user.id:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return
        await interaction.response.defer()
        target_id = int(self.values[0])
        await self.game.shoot(target_self=False, target_id=target_id)


class CuffsTargetSelect(discord.ui.Select):
    """选择手铐目标。"""

    def __init__(self, game: "BuckshotGame") -> None:
        self.game = game
        current = game.current_player()
        options = [
            discord.SelectOption(
                label=p.user.display_name,
                value=str(p.user.id),
                emoji="🔗",
                description=f"{p.hp} 血",
            )
            for p in game.alive_players()
            if p.user.id != current.user.id
        ]
        super().__init__(placeholder="选择要铐住的人……", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.current_player().user.id:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return
        await interaction.response.defer()
        target_id = int(self.values[0])
        await self.game.use_cuffs(target_id)


class GameView(discord.ui.View):
    def __init__(self, game: "BuckshotGame") -> None:
        super().__init__(timeout=TURN_TIMEOUT_SECONDS)
        self.game = game
        current = game.current_player()

        self.add_item(ShootSelfButton(game))
        self.add_item(ShootTargetSelect(game))
        for item_key in current.items:
            if item_key == "cuffs":
                continue  # 手铐用下拉菜单选目标
            self.add_item(ItemButton(game, item_key))
        if "cuffs" in current.items and len(game.chamber) > 1:
            self.add_item(CuffsTargetSelect(game))
        self.add_item(InfoButton(game))

    async def on_timeout(self) -> None:
        await self.game.auto_shoot()


class ShootSelfButton(discord.ui.Button):
    def __init__(self, game: "BuckshotGame") -> None:
        super().__init__(label="💀 打自己", style=discord.ButtonStyle.danger)
        self.game = game

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.current_player().user.id:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.game.shoot(target_self=True)


class ItemButton(discord.ui.Button):
    def __init__(self, game: "BuckshotGame", item_key: str) -> None:
        item = ITEMS[item_key]
        super().__init__(label=f"{item['emoji']} {item['name']}", style=discord.ButtonStyle.secondary)
        self.game = game
        self.item_key = item_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.current_player().user.id:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.game.use_item(self.item_key)


class InfoButton(discord.ui.Button):
    def __init__(self, game: "BuckshotGame") -> None:
        super().__init__(label="📜 我的情报", style=discord.ButtonStyle.secondary)
        self.game = game

    async def callback(self, interaction: discord.Interaction) -> None:
        player = self.game.get_player(interaction.user.id)
        if not player:
            await interaction.response.send_message("你不是本局玩家。", ephemeral=True)
            return
        if not player.known:
            await interaction.response.send_message("暂无情报。", ephemeral=True)
            return
        lines = [f"第 {pos + 1} 发：{'实弹' if live else '空弹'}" for pos, live in sorted(player.known.items())]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class BuckshotGame:
    def __init__(self, bot: "SukakaBot", channel: discord.abc.Messageable, client: httpx.AsyncClient) -> None:
        self.bot = bot
        self.channel = channel
        self.client = client
        self.players: list[Player] = []
        self.message: Optional[discord.Message] = None
        self.join_view: Optional[JoinView] = None
        self.started = False
        self.finished = False
        self.chamber: list[bool] = []
        self.turn_index = 0
        self.first_player_index = 0

    def alive_players(self) -> list[Player]:
        return [p for p in self.players if p.alive]

    def current_player(self) -> Player:
        return self.players[self.turn_index]

    def get_player(self, user_id: int) -> Optional[Player]:
        for p in self.players:
            if p.user.id == user_id:
                return p
        return None

    async def open_lobby(self) -> None:
        self.join_view = JoinView(self)
        self.message = await self.channel.send(self._lobby_text(), view=self.join_view)

    def _lobby_text(self) -> str:
        names = "、".join(p.mention for p in self.players) or "暂无"
        return (
            f"🔫 **Buckshot Roulette 多人混战**（{len(self.players)}/{PLAYER_COUNT}）\n"
            f"规则：每人 {MAX_HP} 血，弹巢 5-8 发（实弹 40%-60%），死亡淘汰，最后存活者通吃奖池。\n"
            f"入场费：{BET_AMOUNT} 点活动额度，奖池 {PLAYER_COUNT * BET_AMOUNT} 点。\n"
            f"已加入：{names}\n"
            f"满 {PLAYER_COUNT} 人自动开始，{JOIN_TIMEOUT_SECONDS} 秒未满自动取消。"
        )

    async def add_player(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if self.started or self.finished:
            await interaction.response.send_message("这一局已经开始了。", ephemeral=True)
            return
        if any(p.user.id == user.id for p in self.players):
            await interaction.response.send_message("你已经加入了。", ephemeral=True)
            return

        quota = await _query_quota(self.client, user.name)
        if quota is None:
            await interaction.response.send_message("查询额度失败，请稍后再试。", ephemeral=True)
            return
        if quota < BET_AMOUNT:
            await interaction.response.send_message(
                f"额度不足：当前 {quota} 点，需要至少 {BET_AMOUNT} 点。", ephemeral=True
            )
            return

        self.players.append(Player(user))
        await interaction.response.send_message(f"已加入！（{len(self.players)}/{PLAYER_COUNT}）", ephemeral=True)

        if len(self.players) >= PLAYER_COUNT:
            self.started = True
            if self.join_view:
                self.join_view.stop()
            await self._run_game()
        elif self.message:
            await self.message.edit(content=self._lobby_text(), view=self.join_view)

    async def cancel(self) -> None:
        if self.finished or self.started:
            return
        self.finished = True
        if self.message:
            await self.message.edit(
                content=f"🔫 人数不足（{len(self.players)}/{PLAYER_COUNT}），已取消。", view=None
            )

    async def _run_game(self) -> None:
        paid: list[Player] = []
        for p in self.players:
            result = await _adjust_quota(self.client, "deduct", p.name, BET_AMOUNT)
            if result is None:
                for q in paid:
                    await _adjust_quota(self.client, "grant", q.name, BET_AMOUNT)
                await self.channel.send("⚠️ 收取入场费失败，本局取消，已扣除的额度已退回。")
                self.finished = True
                return
            paid.append(p)

        random.shuffle(self.players)
        self.first_player_index = 0
        self.turn_index = 0
        for p in self.players:
            p.items = _draw_items(random.randint(2, 3))
        self.chamber = _new_chamber()

        await self.channel.send(
            f"🔫 **Buckshot Roulette 开始！** 奖池 {BET_AMOUNT * len(self.players)} 点。\n"
            f"顺序：{' → '.join(p.mention for p in self.players)}"
        )
        await self._send_turn_message()

    def _game_state_text(self) -> str:
        lines = [f"📦 弹巢剩余 {len(self.chamber)} 发"]
        for p in self.players:
            status = f"❤️ {p.hp}" if p.alive else "💀"
            items = p.items_text() if p.alive else ""
            lines.append(f"{status} {p.mention} {items}")
        lines.append(f"👉 轮到 {self.current_player().mention}")
        return "\n".join(lines)

    async def _send_turn_message(self) -> None:
        if self.finished:
            return
        view = GameView(self)
        await self.channel.send(self._game_state_text(), view=view)

    def _next_alive_index(self, from_index: int) -> int:
        """从 from_index 之后找下一个存活玩家的索引。"""
        n = len(self.players)
        for offset in range(1, n + 1):
            idx = (from_index + offset) % n
            if self.players[idx].alive:
                return idx
        return from_index

    async def _advance_turn(self) -> None:
        """推进到下一个存活且未被铐的玩家。"""
        idx = self._next_alive_index(self.turn_index)
        # 处理手铐跳过（可能连续多人被铐）
        skipped: list[Player] = []
        while self.players[idx].cuffed:
            self.players[idx].cuffed = False
            skipped.append(self.players[idx])
            idx = self._next_alive_index(idx)
        for p in skipped:
            await self.channel.send(f"🔗 {p.mention} 被手铐跳过回合！")
        self.turn_index = idx
        await self._check_chamber_empty()

    async def _check_chamber_empty(self) -> None:
        if self.chamber:
            await self._send_turn_message()
            return
        self.chamber = _new_chamber()
        for p in self.players:
            p.known.clear()
            p.cuffed = False
            if p.alive:
                p.items.extend(_draw_items(random.randint(2, 3), p.items))
                p.items = p.items[:MAX_ITEMS]
        # 先手强制轮换到下一个存活玩家
        self.first_player_index = self._next_alive_index(self.first_player_index)
        self.turn_index = self.first_player_index
        await self.channel.send(
            f"🔄 弹巢打空，重新装填！存活玩家补发道具，先手轮换为 {self.current_player().mention}"
        )
        await self._send_turn_message()

    def _shift_known(self) -> None:
        for p in self.players:
            p.known = {k - 1: v for k, v in p.known.items() if k > 0}

    async def _eliminate(self, player: Player) -> None:
        player.alive = False
        player.hp = 0
        await self.channel.send(f"💀 {player.mention} 出局！剩余 {len(self.alive_players())} 人。")

    async def shoot(self, target_self: bool, target_id: Optional[int] = None) -> None:
        if self.finished or not self.chamber:
            return

        current = self.current_player()
        is_live = self.chamber.pop(0)
        self._shift_known()

        damage = 2 if current.saw_active and is_live else 1
        current.saw_active = False

        if target_self:
            if is_live:
                current.hp -= damage
                await self.channel.send(
                    f"💥 {current.mention} 对自己开枪……**实弹！** 扣 {damage} 血，剩余 {max(0, current.hp)} 血。"
                )
                if current.hp <= 0:
                    await self._eliminate(current)
                    if await self._check_winner():
                        return
                    await self._advance_turn()
                    return
                await self._advance_turn()
            else:
                await self.channel.send(f"😮‍💨 {current.mention} 对自己开枪……空弹！保住回合。")
                await self._check_chamber_empty()
        else:
            target = self.get_player(target_id) if target_id else None
            if not target or not target.alive:
                await self.channel.send("⚠️ 目标无效。")
                await self._send_turn_message()
                return
            if is_live:
                target.hp -= damage
                await self.channel.send(
                    f"💥 {current.mention} 对 {target.mention} 开枪……**实弹！** 对方扣 {damage} 血，剩余 {max(0, target.hp)} 血。"
                )
                if target.hp <= 0:
                    await self._eliminate(target)
                    if await self._check_winner():
                        return
            else:
                await self.channel.send(f"😮‍💨 {current.mention} 对 {target.mention} 开枪……空弹。")
            await self._advance_turn()

    async def _check_winner(self) -> bool:
        alive = self.alive_players()
        if len(alive) > 1:
            return False
        await self._end_game(winner=alive[0])
        return True

    async def auto_shoot(self) -> None:
        if self.finished:
            return
        current = self.current_player()
        await self.channel.send(f"⏱ {current.mention} 超时，自动开枪！")
        # 随机打自己或随机一个存活对手
        others = [p for p in self.alive_players() if p.user.id != current.user.id]
        if others and random.random() < 0.5:
            await self.shoot(target_self=False, target_id=random.choice(others).user.id)
        else:
            await self.shoot(target_self=True)

    async def use_cuffs(self, target_id: int) -> None:
        """手铐选目标。"""
        if self.finished:
            return
        current = self.current_player()
        target = self.get_player(target_id)
        if "cuffs" not in current.items or not target or not target.alive:
            return
        current.items.remove("cuffs")
        target.cuffed = True
        await self.channel.send(f"🔗 {current.mention} 给 {target.mention} 戴上手铐，对方下一回合跳过！")
        await self._send_turn_message()

    async def use_item(self, item_key: str) -> None:
        if self.finished:
            return
        current = self.current_player()

        if item_key not in current.items:
            return
        if item_key in ("phone", "cuffs") and len(self.chamber) <= 1:
            await self.channel.send(f"⚠️ 只剩 1 发时不能使用{ITEMS[item_key]['name']}。")
            await self._send_turn_message()
            return

        current.items.remove(item_key)

        if item_key == "magnifier":
            current.known[0] = self.chamber[0]
            await self.channel.send(f"🔍 {current.mention} 使用放大镜查看了当前子弹。（结果仅本人可见）")

        elif item_key == "phone":
            unknown = [i for i in range(1, len(self.chamber)) if i not in current.known]
            if unknown:
                pos = random.choice(unknown)
                current.known[pos] = self.chamber[pos]
                await self.channel.send(f"📱 {current.mention} 使用手机预知了第 {pos + 1} 发。（结果仅本人可见）")
            else:
                await self.channel.send(f"📱 {current.mention} 使用手机，但没有可探知的弹位了。")

        elif item_key == "saw":
            current.saw_active = True
            await self.channel.send(f"🪚 {current.mention} 使用手锯，下一发实弹伤害翻倍！")

        elif item_key == "beer":
            ejected = self.chamber.pop(0)
            self._shift_known()
            await self.channel.send(f"🍺 {current.mention} 使用啤酒，弹出一发{'实弹' if ejected else '空弹'}！")
            if not self.chamber:
                await self._check_chamber_empty()
                return

        elif item_key == "inverter":
            self.chamber[0] = not self.chamber[0]
            for p in self.players:
                if 0 in p.known:
                    p.known[0] = self.chamber[0]
            await self.channel.send(f"🔄 {current.mention} 使用逆转器，当前子弹类型已翻转！")

        elif item_key == "adrenaline":
            # 随机偷一个存活对手的一件道具
            candidates = [p for p in self.alive_players() if p.user.id != current.user.id and p.items]
            if candidates:
                victim = random.choice(candidates)
                stolen = random.choice(victim.items)
                victim.items.remove(stolen)
                current.items.append(stolen)
                await self.channel.send(
                    f"💉 {current.mention} 使用肾上腺素，偷走了 {victim.mention} 的 {ITEMS[stolen]['emoji']} {ITEMS[stolen]['name']}！"
                )
            else:
                await self.channel.send(f"💉 {current.mention} 使用肾上腺素，但没有可偷的道具。")

        elif item_key == "cigarette":
            current.hp = min(MAX_HP, current.hp + 1)
            await self.channel.send(f"🚬 {current.mention} 抽了根烟，回复 1 血，当前 {current.hp} 血。")

        elif item_key == "medicine":
            if random.random() < 0.4:
                current.hp = min(MAX_HP, current.hp + 2)
                await self.channel.send(f"💊 {current.mention} 吃了过期药……有效！回复 2 血，当前 {current.hp} 血。")
            else:
                current.hp -= 1
                await self.channel.send(f"💊 {current.mention} 吃了过期药……有毒！扣 1 血，当前 {current.hp} 血。")
                if current.hp <= 0:
                    await self._eliminate(current)
                    if await self._check_winner():
                        return
                    await self._advance_turn()
                    return

        await self._send_turn_message()

    async def _end_game(self, winner: Player) -> None:
        self.finished = True
        pot = BET_AMOUNT * len(self.players)
        new_quota = await _adjust_quota(self.client, "grant", winner.name, pot)
        if new_quota is None:
            await self.channel.send(
                f"🏆 {winner.mention} 活到最后！但奖池发放失败，请联系管理员手动补发 {pot} 点。"
            )
        else:
            await self.channel.send(
                f"🏆 {winner.mention} 活到最后，通吃奖池 **{pot} 点**！当前额度 {new_quota} 点。"
            )


def start_roulette(bot: "SukakaBot") -> None:
    """注册统一的消息入口：轮盘赌触发 + 发言掉落。"""
    client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    active_game: dict[str, Optional[BuckshotGame]] = {"game": None}

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.channel.id != QUOTA_CHANNEL_ID:
            return
        if message.author.bot:
            return

        if message.content.strip() == TRIGGER_KEYWORD:
            game = active_game["game"]
            if game and not game.finished:
                await message.channel.send("🔫 已有一局正在进行或报名中，等结束后再开新局。")
                return
            game = BuckshotGame(bot, message.channel, client)
            active_game["game"] = game
            await game.open_lobby()
            return

        await handle_drop_message(client, message)

    print(f"[Roulette] 已启动，在频道 {QUOTA_CHANNEL_ID} 发送「{TRIGGER_KEYWORD}」开局（{PLAYER_COUNT} 人局）")
