"""找妈妈：家庭组共享登记。六个斜杠命令 /登记妈妈 /找妈妈 /家庭组教程 /拉黑 /解除拉黑 /拉黑列表，全部响应 ephemeral，仅发起者本人可见。"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional

import discord
from discord import app_commands

from paths import data_file

if TYPE_CHECKING:
    from bot import SukakaBot

MAMA_ENABLED = True  # 总开关：改为 False 可彻底关闭找妈妈功能（不注册任何斜杠命令）
MAMA_CHANNEL_ID = 1455038454772531311  # 只在这个频道响应 mama 斜杠命令

# /找妈妈 需要持有以下身份组之一；没有则引导去领频道消息处领取
REQUIRED_ROLE_IDS = (
    1383835973384802396,
    1134611078203052122,
)
# 拥有以下任一身份组的用户禁止使用 /找妈妈（优先于上方白名单）
BLOCKED_ROLE_IDS = (
    1271383429346365450,
)
# 拥有 /拉黑 /解除拉黑 使用权的用户（服务端硬编码，后续拓展往里加 ID 即可）
BLOCK_COMMAND_USER_IDS = (
    1077843520917872670,
    793866542151368746)
# 没领身份组的用户点 /找妈妈 时，引导去这条消息处领取身份组
ROLE_CLAIM_LINK = "https://discord.com/channels/1134557553011998840/1383603412956090578/1536717951170773155"

# 「/家庭组教程」发送 docs/family-group-guide.md 的渲染结果，改教程只改那一个文件
GUIDE_FILE = Path(os.getenv("MAMA_GUIDE_FILE", "docs/family-group-guide.md"))

DB_PATH = data_file("MAMA_DB", "mama.db")

PROMPT_VIEW_TIMEOUT = 300  # /登记妈妈 提示消息上的按钮有效期（秒）
LIST_VIEW_TIMEOUT = 600  # /找妈妈 列表消息上的翻页/详情按钮有效期（秒）
REGION_MAX_LENGTH = 50  # 区域输入框长度上限（保证能进 Select description）
NOTE_MAX_LENGTH = 200  # 备注输入框长度上限
PAGE_SIZE = 10  # /找妈妈 列表每页行数（每行 1 个 TextDisplay + 对应名字按钮，40 组件上限内安全）
ACTION_ROW_MAX_BUTTONS = 5  # Discord 单个 ActionRow 按钮硬上限（详情按钮按行分组用）
MESSAGE_MAX_CHARS = 1800  # 单条消息字符上限（Discord 限 2000，留余量）


class MamaRegistration(NamedTuple):
    discord_id: int
    display_name: str
    region: str
    note: str
    created_at: float

    @property
    def mention(self) -> str:
        return f"<@{self.discord_id}>"


def _init_db() -> None:
    """建表：一人一条登记记录 + 一人一条拉黑记录。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mama_registrations (
                discord_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                region TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mama_blocks (
                discord_id INTEGER PRIMARY KEY,
                blocked_at REAL NOT NULL
            )
            """
        )


def get_registration(discord_id: int) -> Optional[MamaRegistration]:
    """查单人登记，用于 Modal 预填 / 删除存在性检查 / Select 回调复核。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT discord_id, display_name, region, note, created_at"
            " FROM mama_registrations WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return MamaRegistration._make(row) if row else None


def upsert_registration(
    discord_id: int, display_name: str, region: str, note: str
) -> None:
    """新增或更新登记（一人一条，重复登记即覆盖更新）。"""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO mama_registrations
                (discord_id, display_name, region, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                region = excluded.region,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (discord_id, display_name, region, note, now, now),
        )


def delete_registration(discord_id: int) -> bool:
    """删除登记，返回是否确实删除了一条。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM mama_registrations WHERE discord_id = ?",
            (discord_id,),
        )
        return cursor.rowcount > 0


def get_all_registrations() -> list[MamaRegistration]:
    """返回全部登记，按首次登记时间升序（组内排序稳定）。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT discord_id, display_name, region, note, created_at"
            " FROM mama_registrations ORDER BY created_at ASC"
        ).fetchall()
    return [MamaRegistration._make(row) for row in rows]


def is_blocked(discord_id: int) -> bool:
    """查某人是否被拉黑，/找妈妈 入口与 /拉黑 幂等提示用。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM mama_blocks WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    return row is not None


def add_block(discord_id: int) -> bool:
    """拉黑某人，返回是否新插入（重复拉黑不报错，幂等）。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO mama_blocks (discord_id, blocked_at) VALUES (?, ?)",
            (discord_id, time.time()),
        )
        return cursor.rowcount > 0


def remove_block(discord_id: int) -> bool:
    """解除拉黑，返回是否确实移除了一条。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM mama_blocks WHERE discord_id = ?", (discord_id,)
        )
        return cursor.rowcount > 0


def get_all_blocks() -> list[tuple[int, float]]:
    """返回全部拉黑记录，按拉黑时间升序。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT discord_id, blocked_at FROM mama_blocks ORDER BY blocked_at ASC"
        ).fetchall()
    return [(int(row[0]), float(row[1])) for row in rows]


class RegisterPromptView(discord.ui.View):
    """「/登记妈妈」ephemeral 提示消息上的两个操作按钮。

    提示消息是 ephemeral（仅发起者可见），ephemeral 消息的组件只能由
    原交互用户使用，因此不需要 owner 校验。View 超时后按钮失效，
    重发 /登记妈妈 即可获得新的提示。
    """

    def __init__(self) -> None:
        super().__init__(timeout=PROMPT_VIEW_TIMEOUT)

    @discord.ui.button(label="登记/更新我的登记", style=discord.ButtonStyle.primary, emoji="👶")
    async def register_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(RegisterModal(interaction.user))

    @discord.ui.button(label="删除我的登记", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        try:
            deleted = delete_registration(interaction.user.id)
        except sqlite3.Error as exc:
            print(f"[Mama] 删除登记失败 user={interaction.user.id}：{exc}")
            await interaction.response.send_message(
                "删除失败：数据库错误，请稍后再试。", ephemeral=True
            )
            return
        if deleted:
            await interaction.response.send_message("已删除你的登记。", ephemeral=True)
        else:
            await interaction.response.send_message(
                "你还没有登记记录，无需删除。", ephemeral=True
            )


class RegisterModal(discord.ui.Modal):
    """登记表单：区域必填 + 备注选填，已有登记时预填现有值。"""

    def __init__(self, owner: discord.Member | discord.User) -> None:
        super().__init__(title="登记/更新我的登记", timeout=None)
        try:
            existing = get_registration(owner.id)
        except sqlite3.Error:
            existing = None
        self.region_input = discord.ui.TextInput(
            label="区域（必填）",
            style=discord.TextStyle.short,
            placeholder="例如：美国加州 / 德国 / 日本（美国具体到州，其他具体到国）",
            required=True,
            max_length=REGION_MAX_LENGTH,
            default=existing.region if existing else None,
        )
        self.note_input = discord.ui.TextInput(
            label="备注（选填）",
            style=discord.TextStyle.paragraph,
            placeholder="例如：还有 1 个位置，周末在线",
            required=False,
            max_length=NOTE_MAX_LENGTH,
            default=existing.note if existing else None,
        )
        self.add_item(self.region_input)
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 折叠全部空白（含换行），保证列表「每人一行」；required 挡不住纯空格，需服务端复检
        region = " ".join(self.region_input.value.split())
        note = " ".join(self.note_input.value.split())
        if not region:
            await interaction.response.send_message(
                "区域不能为空，请重新点「登记/更新我的登记」按钮填写。", ephemeral=True
            )
            return
        try:
            upsert_registration(
                interaction.user.id, interaction.user.display_name, region, note
            )
        except sqlite3.Error as exc:
            print(f"[Mama] 登记失败 user={interaction.user.id}：{exc}")
            await interaction.response.send_message(
                "登记失败：数据库错误，请稍后再试。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"👶 已保存你的登记！区域：{region}"
            + (f"，备注：{note}" if note else "")
            + "。其他人发送 /找妈妈 可以看到你。",
            ephemeral=True,
        )


class MamaListView(discord.ui.LayoutView):
    """/找妈妈 列表（Components V2）：每页 PAGE_SIZE 行（TextDisplay）+ 对应名字按钮。

    翻页/详情回调都重新查库后重建整页（列表发出后数据可能变化），重建的
    组件经 edit_message 重注册派发。ephemeral 消息组件仅发起者可用，无需
    owner 校验。
    """

    def __init__(self, rows: list[MamaRegistration], page: int = 0) -> None:
        super().__init__(timeout=LIST_VIEW_TIMEOUT)
        self.page = page
        self._render(rows, page)

    def _render(self, rows: list[MamaRegistration], page: int) -> None:
        """把当前实例的组件重建为第 page 页（0 基）。调用方保证 rows 非空。

        结构：标题 + 区域标题/行（每行一个 TextDisplay）+ 名字「详情」按钮排
        （每 5 个一排，与上方行顺序对应）+ 分隔线/页脚 + 翻页排。
        最坏情况（每行区域都不同）约 2*PAGE_SIZE+5 组件，Discord 单消息
        40 组件上限下 PAGE_SIZE=10 安全。
        """
        total = len(rows)
        pages = math.ceil(total / PAGE_SIZE)
        page = max(0, min(page, pages - 1))  # 数据变少时页码回落
        self.page = page

        self.clear_items()
        start = page * PAGE_SIZE
        page_rows = rows[start : start + PAGE_SIZE]

        self.add_item(discord.ui.TextDisplay(f"👶 **找妈妈登记列表**（共 {total} 人）"))
        current_region: Optional[str] = None
        for row in page_rows:
            # 区域是自由文本：按相邻同区域分组渲染，跨页不合并
            if row.region != current_region:
                current_region = row.region
                self.add_item(discord.ui.TextDisplay(f"**{current_region}**"))
            line = f"- {row.mention}" + (f"：{row.note}" if row.note else "")
            self.add_item(discord.ui.TextDisplay(line))

        # 详情按钮排：与上方行顺序一一对应，标签用登记者名字；
        # ActionRow 上限 5 个按钮，PAGE_SIZE=10 需拆成两排
        detail_buttons = [self._detail_button(row) for row in page_rows]
        for i in range(0, len(detail_buttons), ACTION_ROW_MAX_BUTTONS):
            row = discord.ui.ActionRow()
            for button in detail_buttons[i : i + ACTION_ROW_MAX_BUTTONS]:
                row.add_item(button)
            self.add_item(row)

        self.add_item(discord.ui.Separator())
        self.add_item(
            discord.ui.TextDisplay(
                f"第 {page + 1} / {pages} 页 · 点名字按钮查看联系方式"
            )
        )

        if pages > 1:
            nav = discord.ui.ActionRow()
            prev = discord.ui.Button(
                label="上一页", emoji="⬅️", disabled=page == 0
            )
            prev.callback = self._make_pager(page - 1)
            nav.add_item(prev)
            next_ = discord.ui.Button(
                label="下一页", emoji="➡️", disabled=page == pages - 1
            )
            next_.callback = self._make_pager(page + 1)
            nav.add_item(next_)
            self.add_item(nav)

    def _make_pager(
        self, target: int
    ) -> "Callable[[discord.Interaction], Awaitable[None]]":
        async def pager(interaction: discord.Interaction) -> None:
            await self._refresh(interaction, target)

        return pager

    def _detail_button(
        self, row: MamaRegistration
    ) -> "discord.ui.Button['MamaListView']":
        async def show_detail(interaction: discord.Interaction) -> None:
            # 列表发出后对方可能已删除登记，回调时重新查库
            try:
                fresh = get_registration(row.discord_id)
            except sqlite3.Error as exc:
                print(f"[Mama] 查询登记失败：{exc}")
                fresh = None
            if fresh is None:
                await interaction.response.send_message(
                    "这条登记已不存在（可能已被删除），请重新发送 /找妈妈 刷新列表。",
                    ephemeral=True,
                )
                return
            # ephemeral 消息仅本人可见、不通知对方，引导直接联系
            await interaction.response.send_message(
                f"👶 找 {fresh.mention} 妈妈！区域：{fresh.region}"
                + (f"｜备注：{fresh.note}" if fresh.note else "")
                + "\n请直接点击对方头像私信，或在频道里 @ 对方联系。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        label = row.display_name or f"用户{row.discord_id % 100000}"
        button = discord.ui.Button(
            label=label[:80], style=discord.ButtonStyle.secondary
        )
        button.callback = show_detail
        return button

    async def _refresh(
        self, interaction: discord.Interaction, target: int
    ) -> None:
        """翻页：重新查库 → 重建组件 → edit 原消息（edit_message 自动注册新组件派发）。"""
        try:
            rows = get_all_registrations()
        except sqlite3.Error as exc:
            print(f"[Mama] 读取登记列表失败：{exc}")
            await interaction.response.send_message(
                "读取登记列表失败，请稍后再试。", ephemeral=True
            )
            return
        if not rows:
            # 全部登记都被删了：列表失去意义，直接把消息替换成空态提示
            await interaction.response.edit_message(
                content="当前还没有任何妈妈登记。想当妈妈的话，发 /登记妈妈 登记一下吧！",
                view=None,
            )
            self.stop()
            return
        self._render(rows, target)
        await interaction.response.edit_message(view=self, allowed_mentions=None)


def _split_text_chunks(text: str) -> list[str]:
    """按 MESSAGE_MAX_CHARS 贪心分块，超长内容拆成多条消息（第一块 response、其余 followup）。"""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MESSAGE_MAX_CHARS:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _load_guide_text() -> str:
    """把 family-group-guide.md 渲染成 Discord 消息文本。

    仅处理本项目文档用到的少量 Markdown 语法：标题/加粗/列表转纯文本，
    HTML 注释（<!-- -->，写维护说明用）整段丢弃，其余原样保留。
    每轮发送时重新读文件，改教程无需重启。
    """
    try:
        raw = GUIDE_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[Mama] 读取教程文件失败 {GUIDE_FILE}：{exc}")
        raise
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)  # 丢弃 HTML 注释块
    # 折叠连续空行为单个空行，避免注释删除/Windows 换行残留大片空白
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
        elif stripped.startswith("# "):  # 文档大标题 → emoji 标题行
            lines.append("👶 " + stripped[2:])
        elif stripped.startswith("## "):  # 小节标题 → 加粗行
            lines.append(f"**{stripped[3:]}**")
        elif stripped.startswith("- "):  # 无序列表 → • 前缀
            lines.append("• " + stripped[2:])
        else:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def register_commands(bot: "SukakaBot") -> None:
    """注册六个斜杠命令（由 bot.py 的 setup_hook 调用，on_ready 的 tree.sync 同步生效）。

    全部响应都是 ephemeral：只有发起者本人可见，频道里不产生任何公开消息。
    /登记妈妈 /找妈妈 /家庭组教程 仅限 MAMA_CHANNEL_ID 频道内使用，
    其他频道里提示「仅限找妈妈频道」；/拉黑 /解除拉黑 /拉黑列表 不限频道，
    但仅限 BLOCK_COMMAND_USER_IDS 里的管理员使用。
    """
    if not MAMA_ENABLED:
        print("[Mama] 已通过开关关闭，跳过命令注册")
        return

    async def _deny_if_wrong_channel(interaction: discord.Interaction) -> bool:
        """频道白名单检查：不在找妈妈频道时回复提示并返回 True。"""
        if interaction.channel_id == MAMA_CHANNEL_ID:
            return False
        await interaction.response.send_message(
            f"👶 mama 命令仅限 <#{MAMA_CHANNEL_ID}> 频道使用。", ephemeral=True
        )
        return True

    async def _deny_if_not_block_admin(interaction: discord.Interaction) -> bool:
        """管理员检查：不在 BLOCK_COMMAND_USER_IDS 里时拒绝，返回 True 表示拒绝。"""
        if interaction.user.id in BLOCK_COMMAND_USER_IDS:
            return False
        await interaction.response.send_message(
            "⚠️ 你没有权限使用这个命令。", ephemeral=True
        )
        return True

    async def _deny_if_missing_role(interaction: discord.Interaction) -> bool:
        """拉黑/身份组检查：被拉黑用户直接拒绝；需领取指定身份组之一，缺组时提示领取链接。"""
        member = interaction.user
        # 频道限 guild 内使用，正常都是 Member；防御 DM/异常场景下拿不到身份组
        if isinstance(member, discord.Member):
            # 用户级拉黑优先：命中即拒绝，不看身份组
            if is_blocked(member.id):
                await interaction.response.send_message(
                    "⚠️ 你已被管理员限制使用 /找妈妈。\n"
                    "如有疑问请联系管理员。",
                    ephemeral=True,
                )
                return True
            # 身份组黑名单次之：命中即拒绝，不再看白名单
            if any(member.get_role(role_id) for role_id in BLOCKED_ROLE_IDS):
                await interaction.response.send_message(
                    "⚠️ 你已被警告，无法使用 /找妈妈。\n"
                    "如有疑问请联系管理员。",
                    ephemeral=True,
                )
                return True
            if any(member.get_role(role_id) for role_id in REQUIRED_ROLE_IDS):
                return False
        await interaction.response.send_message(
            "👶 使用 /找妈妈 需要先领取对应身份组。\n"
            f"请到这里领取：{ROLE_CLAIM_LINK}",
            ephemeral=True,
        )
        return True

    @bot.tree.command(
        name="登记妈妈",
        description="登记/更新/删除我的家庭组共享登记",
    )
    async def register_mama(interaction: discord.Interaction) -> None:
        if await _deny_if_wrong_channel(interaction):
            return
        try:
            existing = get_registration(interaction.user.id)
        except sqlite3.Error:
            existing = None
        if existing:
            extra = (
                f"\n你当前已登记：区域「{existing.region}」"
                + (f"，备注「{existing.note}」" if existing.note else "")
                + "。再次提交即覆盖更新。"
            )
        else:
            extra = "\n还没有登记过，填写后即完成登记。"

        await interaction.response.send_message(
            f"👶 登记/更新妈妈信息！{extra}\n"
            f"点击下方按钮操作（按钮 {PROMPT_VIEW_TIMEOUT // 60} 分钟内有效，"
            "过期请重发 /登记妈妈）。",
            view=RegisterPromptView(),
            ephemeral=True,
        )

    @bot.tree.command(
        name="找妈妈",
        description="查看家庭组共享登记列表，找一位妈妈",
    )
    async def find_mama(interaction: discord.Interaction) -> None:
        if await _deny_if_wrong_channel(interaction):
            return
        if await _deny_if_missing_role(interaction):
            return
        try:
            rows = get_all_registrations()
        except sqlite3.Error as exc:
            print(f"[Mama] 读取登记列表失败：{exc}")
            await interaction.response.send_message(
                "读取登记列表失败，请稍后再试。", ephemeral=True
            )
            return
        if not rows:
            await interaction.response.send_message(
                "当前还没有任何妈妈登记。想当妈妈的话，发 /登记妈妈 登记一下吧！",
                ephemeral=True,
            )
            return

        # Components V2 分页列表；mention 仅渲染不 ping（v2 消息的 mention 同样不通知）
        await interaction.response.send_message(
            view=MamaListView(rows),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @bot.tree.command(
        name="家庭组教程",
        description="查看 Gemini Pro 家庭组共享组建教程",
    )
    async def family_group_guide(interaction: discord.Interaction) -> None:
        if await _deny_if_wrong_channel(interaction):
            return
        try:
            guide_text = _load_guide_text()
        except OSError:
            await interaction.response.send_message(
                "教程文件读取失败，请联系管理员。", ephemeral=True
            )
            return
        chunks = _split_text_chunks(guide_text)
        if not chunks:
            await interaction.response.send_message("教程内容为空。", ephemeral=True)
            return
        no_mentions = discord.AllowedMentions.none()
        await interaction.response.send_message(
            chunks[0], ephemeral=True, allowed_mentions=no_mentions
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(
                chunk, ephemeral=True, allowed_mentions=no_mentions
            )

    # 拉黑命令不限频道（管理员可能在任何地方处理）；但仅限 BLOCK_COMMAND_USER_IDS 使用
    @bot.tree.command(
        name="拉黑",
        description="禁止指定用户使用 /找妈妈（仅管理员）",
    )
    @app_commands.describe(target="要拉黑的用户")
    async def block_user(
        interaction: discord.Interaction, target: discord.Member
    ) -> None:
        if await _deny_if_not_block_admin(interaction):
            return
        if target.bot:
            await interaction.response.send_message(
                "拉黑机器人没有意义，请选择普通用户。", ephemeral=True
            )
            return
        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "不能拉黑你自己。", ephemeral=True
            )
            return
        try:
            added = add_block(target.id)
        except sqlite3.Error as exc:
            print(
                f"[Mama] 拉黑失败 operator={interaction.user.id}"
                f" target={target.id}：{exc}"
            )
            await interaction.response.send_message(
                "拉黑失败：数据库错误，请稍后再试。", ephemeral=True
            )
            return
        if added:
            await interaction.response.send_message(
                f"⛔ 已拉黑 {target.mention}，其将无法使用 /找妈妈。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"{target.mention} 已在拉黑列表中，无需重复拉黑。",
                ephemeral=True,
            )

    @bot.tree.command(
        name="解除拉黑",
        description="恢复指定用户使用 /找妈妈 的权限（仅管理员）",
    )
    @app_commands.describe(target="要解除拉黑的用户（已退服也可填 ID）")
    async def unblock_user(
        interaction: discord.Interaction, target: discord.User
    ) -> None:
        if await _deny_if_not_block_admin(interaction):
            return
        try:
            removed = remove_block(target.id)
        except sqlite3.Error as exc:
            print(
                f"[Mama] 解除拉黑失败 operator={interaction.user.id}"
                f" target={target.id}：{exc}"
            )
            await interaction.response.send_message(
                "解除拉黑失败：数据库错误，请稍后再试。", ephemeral=True
            )
            return
        if removed:
            await interaction.response.send_message(
                f"✅ 已解除 {target.mention} 的拉黑，其可以重新使用 /找妈妈。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"{target.mention} 不在拉黑列表中。",
                ephemeral=True,
            )

    @bot.tree.command(
        name="拉黑列表",
        description="查看当前 /找妈妈 拉黑名单（仅管理员）",
    )
    async def block_list(interaction: discord.Interaction) -> None:
        if await _deny_if_not_block_admin(interaction):
            return
        try:
            blocks = get_all_blocks()
        except sqlite3.Error as exc:
            print(f"[Mama] 读取拉黑列表失败：{exc}")
            await interaction.response.send_message(
                "读取拉黑列表失败，请稍后再试。", ephemeral=True
            )
            return
        if not blocks:
            await interaction.response.send_message(
                "拉黑列表为空。", ephemeral=True
            )
            return
        lines = ["⛔ **/找妈妈 拉黑名单**"]
        for user_id, blocked_at in blocks:
            blocked_at_text = time.strftime(
                "%Y-%m-%d %H:%M UTC", time.gmtime(blocked_at)
            )
            lines.append(f"- <@{user_id}>（{blocked_at_text} 拉黑）")
        lines.append(f"共 {len(blocks)} 人。")
        # 列表不 ping 被拉黑者（mention 仅渲染为 @名字，不产生通知）
        no_mentions = discord.AllowedMentions.none()
        chunks = _split_text_chunks("\n".join(lines))
        await interaction.response.send_message(
            chunks[0], ephemeral=True, allowed_mentions=no_mentions
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(
                chunk, ephemeral=True, allowed_mentions=no_mentions
            )

    print(
        "[Mama] 斜杠命令已注册：/登记妈妈 /找妈妈 /家庭组教程 "
        "/拉黑 /解除拉黑 /拉黑列表"
        f"（全部 ephemeral，仅发起者可见），数据库 {DB_PATH}"
    )


_init_db()
