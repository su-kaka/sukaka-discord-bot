"""找妈妈：家庭组共享登记。发送「登记妈妈」弹窗编辑登记，发送「找妈妈」查看登记列表。"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional

import discord

if TYPE_CHECKING:
    from bot import SukakaBot

MAMA_CHANNEL_ID = 1455038454772531311
REGISTER_KEYWORD = "登记妈妈"
FIND_KEYWORD = "找妈妈"
GUIDE_KEYWORD = "家庭组教程"
# 「家庭组教程」发送 docs/family-group-guide.md 的渲染结果，改教程只改那一个文件
GUIDE_FILE = Path(os.getenv("MAMA_GUIDE_FILE", "docs/family-group-guide.md"))

DB_PATH = Path(os.getenv("MAMA_DB", "mama.db"))

PROMPT_DELETE_AFTER = 60  # 登记提示消息存活秒数
LIST_DELETE_AFTER = 120  # 列表消息存活秒数
REGION_MAX_LENGTH = 50  # 区域输入框长度上限（保证能进 Select description）
NOTE_MAX_LENGTH = 200  # 备注输入框长度上限
SELECT_MAX_OPTIONS = 25  # Discord Select 选项硬上限
LIST_MAX_CHARS = 1800  # 列表单条消息字符上限（Discord 限 2000，留余量）


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
    """建表：一人一条登记记录。"""
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


class RegisterPromptView(discord.ui.View):
    """「登记妈妈」提示消息上的两个操作按钮。

    消息由 delete_after 自动删除，View 的 timeout 与消息存活时间对齐；
    超时后无需（也不能）编辑消息，故不覆写 on_timeout。
    """

    def __init__(self, owner: discord.Member | discord.User) -> None:
        super().__init__(timeout=PROMPT_DELETE_AFTER)
        self.owner = owner

    @discord.ui.button(label="登记/更新我的登记", style=discord.ButtonStyle.primary, emoji="👶")
    async def register_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "这不是你的登记消息，请自己发送「登记妈妈」再操作。", ephemeral=True
            )
            return
        await interaction.response.send_modal(RegisterModal(interaction.user))

    @discord.ui.button(label="删除我的登记", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "这不是你的登记消息，请自己发送「登记妈妈」再操作。", ephemeral=True
            )
            return
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
            placeholder="例如：美东 / 美西 / 欧洲",
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
            + "。其他人发送「找妈妈」可以看到你。",
            ephemeral=True,
        )


class MamaSelect(discord.ui.Select):
    """找妈妈列表上的下拉菜单：选中后仅本人可见地展示对方登记。"""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="选择你要找的妈妈……",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # 列表发出后对方可能已删除登记，回调时重新查库
        row: Optional[MamaRegistration] = None
        try:
            row = get_registration(int(self.values[0]))
        except (sqlite3.Error, ValueError) as exc:
            print(f"[Mama] 查询登记失败：{exc}")
        if row is None:
            await interaction.response.send_message(
                "这条登记已不存在（可能已被删除），请重新发送「找妈妈」刷新列表。",
                ephemeral=True,
            )
            return
        # ephemeral 消息仅本人可见、不通知对方，引导直接联系
        await interaction.response.send_message(
            f"👶 找 {row.mention} 妈妈！区域：{row.region}"
            + (f"｜备注：{row.note}" if row.note else "")
            + "\n请直接点击对方头像私信，或在频道里 @ 对方联系。",
            ephemeral=True,
        )


class MamaSelectView(discord.ui.View):
    """承载下拉菜单的容器，timeout 与列表消息存活时间对齐。"""

    def __init__(self, rows: list[MamaRegistration]) -> None:
        super().__init__(timeout=LIST_DELETE_AFTER)
        self.add_item(MamaSelect(_build_select_options(rows)))


def _build_select_options(rows: list[MamaRegistration]) -> list[discord.SelectOption]:
    """构造下拉菜单选项，超过上限时按登记时间截断到前 25 条。"""
    options = []
    for row in rows[:SELECT_MAX_OPTIONS]:
        label = row.display_name or f"用户{row.discord_id % 100000}"
        description = f"{row.region}｜{row.note}" if row.note else row.region
        options.append(
            discord.SelectOption(
                label=label[:100],
                value=str(row.discord_id),
                description=description[:100],
            )
        )
    return options


def _render_list_chunks(rows: list[MamaRegistration]) -> list[str]:
    """按区域分组渲染列表，超长时按 LIST_MAX_CHARS 贪心分块。"""
    grouped: dict[str, list[MamaRegistration]] = {}
    for row in rows:
        grouped.setdefault(row.region, []).append(row)

    lines = ["👶 **找妈妈登记列表**"]
    for region, members in grouped.items():
        lines.append(f"\n**{region}**")
        for row in members:
            lines.append(f"- {row.mention}：{row.note}" if row.note else f"- {row.mention}")

    footer = f"\n共 {len(rows)} 人已登记。在下方菜单选择一位妈妈，获取联系提示。"
    if len(rows) > SELECT_MAX_OPTIONS:
        footer += f"（下拉菜单最多显示前 {SELECT_MAX_OPTIONS} 位，按登记时间）"

    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > LIST_MAX_CHARS:
            chunks.append(current)
            current = line
        else:
            current = candidate
    chunks.append(f"{current}\n{footer}" if current else footer)
    return chunks


async def _handle_register_prompt(message: discord.Message) -> None:
    """处理「登记妈妈」：发一条带操作按钮的提示消息（60 秒自动删）。"""
    try:
        existing = get_registration(message.author.id)
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

    await message.channel.send(
        f"👶 {message.author.mention} 正在登记/更新妈妈信息！{extra}\n"
        f"点击下方按钮操作，{PROMPT_DELETE_AFTER} 秒后本消息自动删除。",
        view=RegisterPromptView(message.author),
        delete_after=PROMPT_DELETE_AFTER,
    )


async def _handle_find(message: discord.Message) -> None:
    """处理「找妈妈」：发按区域分组的登记列表 + 下拉菜单（120 秒自动删）。"""
    try:
        rows = get_all_registrations()
    except sqlite3.Error as exc:
        print(f"[Mama] 读取登记列表失败：{exc}")
        await message.channel.send(
            "读取登记列表失败，请稍后再试。", delete_after=LIST_DELETE_AFTER
        )
        return
    if not rows:
        await message.channel.send(
            "当前还没有任何妈妈登记。想当妈妈的话，发送「登记妈妈」登记一下吧！",
            delete_after=LIST_DELETE_AFTER,
        )
        return

    # 列表不 ping 任何登记者（mention 仅渲染为 @名字，不产生通知）
    no_mentions = discord.AllowedMentions.none()
    chunks = _render_list_chunks(rows)
    for chunk in chunks[:-1]:
        await message.channel.send(
            chunk, delete_after=LIST_DELETE_AFTER, allowed_mentions=no_mentions
        )
    await message.channel.send(
        chunks[-1],
        view=MamaSelectView(rows),
        delete_after=LIST_DELETE_AFTER,
        allowed_mentions=no_mentions,
    )


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


async def _handle_guide(message: discord.Message) -> None:
    """处理「家庭组教程」：发送组家庭教程文字（120 秒自动删）。"""
    try:
        guide_text = _load_guide_text()
    except OSError:
        await message.channel.send(
            "教程文件读取失败，请联系管理员。", delete_after=LIST_DELETE_AFTER
        )
        return
    await message.channel.send(
        guide_text,
        delete_after=LIST_DELETE_AFTER,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def handle_mama_message(message: discord.Message) -> None:
    """找妈妈频道消息入口（由 bot.py 的消息分发调用）。"""
    # 防御性双检：正常情况下分发方已过滤
    if message.channel.id != MAMA_CHANNEL_ID or message.author.bot:
        return
    content = message.content.strip()
    if content == REGISTER_KEYWORD:
        await _handle_register_prompt(message)
    elif content == FIND_KEYWORD:
        await _handle_find(message)
    elif content == GUIDE_KEYWORD:
        await _handle_guide(message)
    # 其他消息静默忽略


def start_mama(bot: "SukakaBot") -> None:
    """启动找妈妈服务：建表（import 时已完成）+ 注册消息入口 + 打印启动日志。"""
    bot.register_message_handler(MAMA_CHANNEL_ID, handle_mama_message)
    print(
        f"[Mama] 已启动，监听频道 {MAMA_CHANNEL_ID}，"
        f"发送「{REGISTER_KEYWORD}」登记，发送「{FIND_KEYWORD}」查询，"
        f"发送「{GUIDE_KEYWORD}」看教程，数据库 {DB_PATH}"
    )


_init_db()
