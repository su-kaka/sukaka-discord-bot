"""找妈妈：家庭组共享登记。三个斜杠命令 /登记妈妈 /找妈妈 /家庭组教程，全部响应 ephemeral，仅发起者本人可见。"""

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

MAMA_CHANNEL_ID = 1455038454772531311  # 只在这个频道响应 mama 斜杠命令

# 「/家庭组教程」发送 docs/family-group-guide.md 的渲染结果，改教程只改那一个文件
GUIDE_FILE = Path(os.getenv("MAMA_GUIDE_FILE", "docs/family-group-guide.md"))

DB_PATH = Path(os.getenv("MAMA_DB", "mama.db"))

PROMPT_VIEW_TIMEOUT = 300  # /登记妈妈 提示消息上的按钮有效期（秒）
LIST_VIEW_TIMEOUT = 600  # /找妈妈 列表消息上的下拉菜单有效期（秒）
REGION_MAX_LENGTH = 50  # 区域输入框长度上限（保证能进 Select description）
NOTE_MAX_LENGTH = 200  # 备注输入框长度上限
SELECT_MAX_OPTIONS = 25  # Discord Select 选项硬上限
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
            + "。其他人发送 /找妈妈 可以看到你。",
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
                "这条登记已不存在（可能已被删除），请重新发送 /找妈妈 刷新列表。",
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
    """承载下拉菜单的容器，timeout 与菜单有效期对齐。"""

    def __init__(self, rows: list[MamaRegistration]) -> None:
        super().__init__(timeout=LIST_VIEW_TIMEOUT)
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


def _render_list_text(rows: list[MamaRegistration]) -> str:
    """按区域分组渲染登记列表成整段文本（分块交给 _split_text_chunks）。"""
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
    lines.append(footer)
    return "\n".join(lines)


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
    """注册三个斜杠命令（由 bot.py 的 setup_hook 调用，on_ready 的 tree.sync 同步生效）。

    全部响应都是 ephemeral：只有发起者本人可见，频道里不产生任何公开消息。
    命令仅限 MAMA_CHANNEL_ID 频道内使用，其他频道里提示「仅限找妈妈频道」。
    """

    async def _deny_if_wrong_channel(interaction: discord.Interaction) -> bool:
        """频道白名单检查：不在找妈妈频道时回复提示并返回 True。"""
        if interaction.channel_id == MAMA_CHANNEL_ID:
            return False
        await interaction.response.send_message(
            f"👶 mama 命令仅限 <#{MAMA_CHANNEL_ID}> 频道使用。", ephemeral=True
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

        # 列表不 ping 任何登记者（mention 仅渲染为 @名字，不产生通知）
        no_mentions = discord.AllowedMentions.none()
        view = MamaSelectView(rows)
        chunks = _split_text_chunks(_render_list_text(rows))
        await interaction.response.send_message(
            chunks[0],
            view=view if len(chunks) == 1 else None,
            ephemeral=True,
            allowed_mentions=no_mentions,
        )
        for index, chunk in enumerate(chunks[1:], start=1):
            # 后续分块走 followup；最后一块挂下拉菜单
            await interaction.followup.send(
                chunk,
                view=view if index == len(chunks) - 1 else None,
                ephemeral=True,
                allowed_mentions=no_mentions,
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

    print(
        "[Mama] 斜杠命令已注册：/登记妈妈 /找妈妈 /家庭组教程"
        f"（全部 ephemeral，仅发起者可见），数据库 {DB_PATH}"
    )


_init_db()
