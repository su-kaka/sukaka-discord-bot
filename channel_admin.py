import asyncio
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot import SukakaBot

ALLOWED_CHANNEL_ID = 1293095144806940738
DEFAULT_TIMEOUT_MINUTES = 30
MAX_TIMEOUT_MINUTES = 24 * 60
VOTE_THRESHOLD = 5
DB_PATH = Path(os.getenv("CHANNEL_MUTES_DB", "channel_mutes.db"))
CHANNEL_MUTE_PERMISSIONS = ("send_messages",)

# 恢复任务注册表与写锁：channel_admin 模块私有，不挂在 bot 实例上
_restore_tasks: dict[tuple[int, int, int], asyncio.Task[None]] = {}
_mute_lock = asyncio.Lock()

MESSAGE_LINK_PATTERN = re.compile(
    r"^https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)$"
)


def parse_whitelist(raw: str) -> set[int]:
    if not raw:
        return set()
    result: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.isdigit():
            result.add(int(chunk))
    return result


OPERATION_WHITELIST = parse_whitelist(os.getenv("MUTE_WHITELIST", ""))


class ChannelMuteError(Exception):
    pass


@dataclass
class VoteState:
    vote_id: str
    guild_id: int
    channel_id: int
    vote_message_id: int
    target_id: int
    duration_minutes: int
    initiator_id: int
    reason: str
    voter_ids: set[int] = field(default_factory=set)
    resolving: bool = False
    resolved: bool = False


@dataclass
class ChannelMuteRecord:
    guild_id: int
    channel_id: int
    target_id: int
    restore_at: float
    original_allow: int
    original_deny: int

    @property
    def key(self) -> tuple[int, int, int]:
        return self.guild_id, self.channel_id, self.target_id


def channel_mute_denial_reason(
    bot: "SukakaBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
    target: discord.Member,
) -> Optional[str]:
    if target.id == guild.owner_id:
        return "不能对服务器所有者发起频道禁言投票。"
    if target.guild_permissions.administrator:
        return "不能对拥有“管理员”权限的成员发起频道禁言投票。"

    bot_member = guild.me
    if bot_member is None and bot.user is not None:
        bot_member = guild.get_member(bot.user.id)
    if bot_member is None:
        return "无法确认机器人在当前服务器中的权限。"
    if not channel.permissions_for(bot_member).manage_roles:
        return "机器人在当前频道缺少“管理身份组”权限，无法管理频道发言权。"
    return None


def _init_db() -> None:
    """建表：频道禁言记录，主键 = (服务器, 频道, 成员)。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_mutes (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                restore_at REAL NOT NULL,
                original_allow INTEGER NOT NULL,
                original_deny INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id, target_id)
            )
            """
        )


def get_channel_mute(key: tuple[int, int, int]) -> Optional[ChannelMuteRecord]:
    """查一条禁言记录，恢复任务轮询与复核用。"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT guild_id, channel_id, target_id, restore_at,"
            " original_allow, original_deny FROM channel_mutes"
            " WHERE guild_id = ? AND channel_id = ? AND target_id = ?",
            key,
        ).fetchone()
    return ChannelMuteRecord(*row) if row else None


def save_channel_mute(record: ChannelMuteRecord) -> None:
    """插入或更新禁言记录（叠加禁言时只顺延 restore_at，原始权限保持首次保存的那份）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO channel_mutes
                (guild_id, channel_id, target_id, restore_at,
                 original_allow, original_deny)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, channel_id, target_id) DO UPDATE SET
                restore_at = excluded.restore_at
            """,
            (
                record.guild_id,
                record.channel_id,
                record.target_id,
                record.restore_at,
                record.original_allow,
                record.original_deny,
            ),
        )


def delete_channel_mute(key: tuple[int, int, int]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM channel_mutes"
            " WHERE guild_id = ? AND channel_id = ? AND target_id = ?",
            key,
        )


def load_all_channel_mutes() -> list[ChannelMuteRecord]:
    """全部禁言记录，on_ready 重排恢复任务用。"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT guild_id, channel_id, target_id, restore_at,"
            " original_allow, original_deny FROM channel_mutes"
        ).fetchall()
    return [ChannelMuteRecord(*row) for row in rows]


def start_channel_mute_restores(bot: "SukakaBot") -> None:
    """重启恢复：为数据库里每条未到期记录重排恢复任务（on_ready 调用一次）。"""
    records = load_all_channel_mutes()
    for record in records:
        schedule_channel_mute_restore(bot, record)
    if records:
        print(f"[ChannelAdmin] 已恢复 {len(records)} 条未到期的频道禁言记录")


def schedule_channel_mute_restore(bot: "SukakaBot", record: ChannelMuteRecord) -> None:
    existing_task = _restore_tasks.pop(record.key, None)
    if existing_task is not None:
        existing_task.cancel()

    task = asyncio.create_task(
        restore_channel_mute_when_due(bot, record.key),
        name=f"channel-mute-restore-{record.channel_id}-{record.target_id}",
    )
    _restore_tasks[record.key] = task
    task.add_done_callback(
        lambda completed, key=record.key: discard_channel_mute_task(
            key,
            completed,
        )
    )


def discard_channel_mute_task(
    key: tuple[int, int, int],
    completed_task: asyncio.Task[None],
) -> None:
    if _restore_tasks.get(key) is completed_task:
        _restore_tasks.pop(key, None)


async def apply_channel_mute(
    bot: "SukakaBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
    member: discord.Member,
    duration_minutes: int,
    reason: str,
) -> None:
    denied_message = channel_mute_denial_reason(bot, guild, channel, member)
    if denied_message:
        raise ChannelMuteError(denied_message)

    key = guild.id, channel.id, member.id
    async with _mute_lock:
        previous_record = get_channel_mute(key)
        if previous_record is None:
            original_overwrite = channel.overwrites_for(member)
            original_allow, original_deny = original_overwrite.pair()
            record = ChannelMuteRecord(
                guild_id=guild.id,
                channel_id=channel.id,
                target_id=member.id,
                restore_at=time.time() + duration_minutes * 60,
                original_allow=original_allow.value,
                original_deny=original_deny.value,
            )
        else:
            record = replace(
                previous_record,
                restore_at=time.time() + duration_minutes * 60,
            )

        save_channel_mute(record)

        muted_overwrite = channel.overwrites_for(member)
        for permission_name in CHANNEL_MUTE_PERMISSIONS:
            setattr(muted_overwrite, permission_name, False)

        try:
            await channel.set_permissions(
                member,
                overwrite=muted_overwrite,
                reason=reason,
            )
        except Exception:
            if previous_record is None:
                delete_channel_mute(key)
            else:
                save_channel_mute(previous_record)
            raise

        schedule_channel_mute_restore(bot, record)


async def restore_channel_mute_when_due(
    bot: "SukakaBot",
    key: tuple[int, int, int],
) -> None:
    while True:
        record = get_channel_mute(key)
        if record is None:
            return

        delay = record.restore_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
            continue

        async with _mute_lock:
            record = get_channel_mute(key)
            if record is None:
                return
            if record.restore_at > time.time():
                continue

            try:
                guild = bot.get_guild(record.guild_id)
                if guild is None:
                    raise ChannelMuteError("无法找到服务器")

                channel = guild.get_channel(record.channel_id)
                if channel is None:
                    fetched_channel = await bot.fetch_channel(record.channel_id)
                    channel = fetched_channel
                if not isinstance(channel, discord.TextChannel):
                    raise ChannelMuteError("无法找到文字频道")

                member = guild.get_member(record.target_id)
                if member is None:
                    member = await guild.fetch_member(record.target_id)

                original_overwrite = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(record.original_allow),
                    discord.Permissions(record.original_deny),
                )
                restored_overwrite = channel.overwrites_for(member)
                for permission_name in CHANNEL_MUTE_PERMISSIONS:
                    setattr(
                        restored_overwrite,
                        permission_name,
                        getattr(original_overwrite, permission_name),
                    )
                if restored_overwrite.is_empty():
                    restored_overwrite = None

                await channel.set_permissions(
                    member,
                    overwrite=restored_overwrite,
                    reason="Channel vote mute expired",
                )
            except discord.NotFound:
                delete_channel_mute(key)
                return
            except (ChannelMuteError, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Failed to restore channel mute {key}: {exc}")
            else:
                delete_channel_mute(key)
                return

        await asyncio.sleep(60)


_init_db()


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == ALLOWED_CHANNEL_ID


def is_whitelisted(user_id: int) -> bool:
    return user_id in OPERATION_WHITELIST


def deny_reason(interaction: discord.Interaction) -> Optional[str]:
    if not is_allowed_channel(interaction):
        return f"此机器人只能在 <#{ALLOWED_CHANNEL_ID}> 中使用。"
    if not is_whitelisted(interaction.user.id):
        return "你没有使用此机器人的权限。"
    return None


def build_vote_embed(state: VoteState, votes: int, resolved: bool) -> discord.Embed:
    status = "已通过" if resolved else "投票中"
    embed = discord.Embed(title="频道禁言投票", color=discord.Color.red())
    embed.add_field(name="状态", value=status, inline=True)
    embed.add_field(name="票数", value=f"{votes}/{VOTE_THRESHOLD}", inline=True)
    embed.add_field(name="目标成员", value=f"<@{state.target_id}>", inline=False)
    embed.add_field(
        name="禁言时长",
        value=f"{state.duration_minutes} 分钟",
        inline=True,
    )
    embed.add_field(name="原因", value=state.reason, inline=False)
    embed.add_field(
        name="发起人",
        value=f"<@{state.initiator_id}>",
        inline=False,
    )
    return embed


def parse_message_link(message_link: str) -> Optional[tuple[int, int, int]]:
    match = MESSAGE_LINK_PATTERN.match(message_link.strip())
    if not match:
        return None
    guild_id, channel_id, message_id = match.groups()
    return int(guild_id), int(channel_id), int(message_id)


async def fetch_message(
    bot: discord.Client,
    channel_id: int,
    message_id: int,
) -> discord.Message:
    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await bot.fetch_channel(channel_id)

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError("Unsupported channel type")

    return await channel.fetch_message(message_id)


class MuteVoteView(discord.ui.View):
    def __init__(self, bot: "SukakaBot", vote_id: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.vote_id = vote_id

    def update_vote_label(self, votes: int) -> None:
        self.vote_button.label = f"投票禁言（{votes}/{VOTE_THRESHOLD}）"

    @discord.ui.button(label="投票禁言（0/5）", style=discord.ButtonStyle.danger)
    async def vote_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        state = self.bot.mute_votes.get(self.vote_id)
        if state is None:
            await interaction.response.send_message(
                "此投票已不存在，可能因机器人重启而失效。",
                ephemeral=True,
            )
            return

        if state.resolved:
            await interaction.response.send_message(
                "此投票已经结束。",
                ephemeral=True,
            )
            return

        if state.resolving:
            await interaction.response.send_message(
                "投票已达到门槛，正在执行频道禁言。",
                ephemeral=True,
            )
            return

        if interaction.channel_id != state.channel_id:
            await interaction.response.send_message(
                "只能在发起投票的频道中投票。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        if interaction.user.bot:
            await interaction.response.send_message(
                "机器人不能参与投票。",
                ephemeral=True,
            )
            return

        if user_id in state.voter_ids:
            await interaction.response.send_message(
                "你已经投过票了。",
                ephemeral=True,
            )
            return

        state.voter_ids.add(user_id)
        votes = len(state.voter_ids)

        if votes < VOTE_THRESHOLD:
            self.update_vote_label(votes)
            embed = build_vote_embed(state, votes, resolved=False)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        state.resolving = True
        guild = self.bot.get_guild(state.guild_id)
        if guild is None:
            state.resolving = False
            state.voter_ids.discard(user_id)
            await interaction.response.send_message(
                "无法找到当前服务器，未执行禁言。",
                ephemeral=True,
            )
            return

        try:
            channel = guild.get_channel(state.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(state.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ChannelMuteError("无法找到发起投票的文字频道。")

            member = guild.get_member(state.target_id)
            if member is None:
                member = await guild.fetch_member(state.target_id)

            await apply_channel_mute(
                self.bot,
                guild,
                channel,
                member,
                state.duration_minutes,
                reason=f"Vote channel mute: {state.reason}",
            )
        except ChannelMuteError as exc:
            state.resolving = False
            state.voter_ids.discard(user_id)
            await interaction.response.send_message(
                f"频道禁言失败：{exc}",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            state.resolving = False
            state.voter_ids.discard(user_id)
            await interaction.response.send_message(
                "频道禁言失败：机器人缺少“管理身份组”权限，"
                "或无法修改当前频道的成员权限。",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            state.resolving = False
            state.voter_ids.discard(user_id)
            await interaction.response.send_message(
                f"频道禁言失败：{exc}",
                ephemeral=True,
            )
            return

        state.resolved = True
        state.resolving = False
        self.bot.active_vote_by_target.pop((state.guild_id, state.target_id), None)

        self.update_vote_label(votes)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        embed = build_vote_embed(state, votes, resolved=True)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"投票已通过，<@{state.target_id}> 已在本频道禁言 "
            f"{state.duration_minutes} 分钟。"
        )


def register_commands(bot: "SukakaBot") -> None:
    @bot.tree.command(
        name="mute_vote",
        description="发起成员频道禁言投票",
    )
    @app_commands.describe(
        target="要在当前频道禁言的成员",
        duration_minutes="频道禁言时长（分钟，默认 30，最长 1440）",
        reason="频道禁言原因（可选）",
    )
    async def mute_vote(
        interaction: discord.Interaction,
        target: discord.Member,
        duration_minutes: app_commands.Range[int, 1, MAX_TIMEOUT_MINUTES] = DEFAULT_TIMEOUT_MINUTES,
        reason: Optional[str] = None,
    ) -> None:
        denied_message = deny_reason(interaction)
        if denied_message:
            await interaction.response.send_message(denied_message, ephemeral=True)
            return

        if target.bot:
            await interaction.response.send_message(
                "不能对机器人发起禁言投票。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "此命令只能在服务器中使用。",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "频道禁言投票只能在服务器文字频道中发起。",
                ephemeral=True,
            )
            return

        denied_message = channel_mute_denial_reason(
            bot,
            interaction.guild,
            channel,
            target,
        )
        if denied_message:
            await interaction.response.send_message(denied_message, ephemeral=True)
            return

        active_key = (interaction.guild.id, target.id)
        existing_vote_id = bot.active_vote_by_target.get(active_key)
        if existing_vote_id:
            existing_state = bot.mute_votes.get(existing_vote_id)
            if existing_state and not existing_state.resolved:
                await interaction.response.send_message(
                    "该成员已有一个进行中的禁言投票。",
                    ephemeral=True,
                )
                return

        vote_id = str(uuid.uuid4())
        vote_reason = reason or "未填写原因"
        state = VoteState(
            vote_id=vote_id,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel_id,
            vote_message_id=0,
            target_id=target.id,
            duration_minutes=int(duration_minutes),
            initiator_id=interaction.user.id,
            reason=vote_reason,
            voter_ids={interaction.user.id},
        )

        view = MuteVoteView(bot=bot, vote_id=vote_id)
        view.update_vote_label(len(state.voter_ids))
        embed = build_vote_embed(state, len(state.voter_ids), resolved=False)

        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()

        state.vote_message_id = message.id
        bot.mute_votes[vote_id] = state
        bot.active_vote_by_target[active_key] = vote_id

    @bot.tree.command(
        name="delete_message",
        description="通过 Discord 消息链接删除消息",
    )
    @app_commands.describe(message_link="Discord 消息链接")
    async def delete_message(
        interaction: discord.Interaction,
        message_link: str,
    ) -> None:
        denied_message = deny_reason(interaction)
        if denied_message:
            await interaction.response.send_message(denied_message, ephemeral=True)
            return

        parsed = parse_message_link(message_link)
        if parsed is None:
            await interaction.response.send_message(
                "消息链接格式无效。请粘贴从 Discord 复制的完整消息链接。",
                ephemeral=True,
            )
            return

        guild_id, channel_id, message_id = parsed
        if interaction.guild is None or guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "该消息链接不属于当前服务器。",
                ephemeral=True,
            )
            return

        try:
            message = await fetch_message(bot, channel_id, message_id)
            await message.delete()
        except ValueError:
            await interaction.response.send_message(
                "该消息所在的频道类型暂不支持。",
                ephemeral=True,
            )
            return
        except discord.NotFound:
            await interaction.response.send_message(
                "未找到目标频道或消息，消息可能已被删除。",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "删除失败：机器人在目标频道缺少“管理消息”权限。",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"删除失败：{exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("消息已删除。", ephemeral=True)

    @bot.tree.command(
        name="mark_message",
        description="通过 Discord 消息链接置顶消息",
    )
    @app_commands.describe(message_link="Discord 消息链接")
    async def mark_message(
        interaction: discord.Interaction,
        message_link: str,
    ) -> None:
        denied_message = deny_reason(interaction)
        if denied_message:
            await interaction.response.send_message(denied_message, ephemeral=True)
            return

        parsed = parse_message_link(message_link)
        if parsed is None:
            await interaction.response.send_message(
                "消息链接格式无效。请粘贴从 Discord 复制的完整消息链接。",
                ephemeral=True,
            )
            return

        guild_id, channel_id, message_id = parsed
        if interaction.guild is None or guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "该消息链接不属于当前服务器。",
                ephemeral=True,
            )
            return

        try:
            message = await fetch_message(bot, channel_id, message_id)
            if message.pinned:
                await interaction.response.send_message(
                    "该消息已经置顶。",
                    ephemeral=True,
                )
                return
            await message.pin(reason=f"Marked by {interaction.user.id} via bot command")
        except ValueError:
            await interaction.response.send_message(
                "该消息所在的频道类型暂不支持。",
                ephemeral=True,
            )
            return
        except discord.NotFound:
            await interaction.response.send_message(
                "未找到目标频道或消息，消息可能已被删除。",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "置顶失败：机器人在目标频道缺少“管理消息”权限。",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"置顶失败：{exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("消息已置顶。", ephemeral=True)

    @bot.tree.command(
        name="unmark_message",
        description="通过 Discord 消息链接取消置顶",
    )
    @app_commands.describe(message_link="Discord 消息链接")
    async def unmark_message(
        interaction: discord.Interaction,
        message_link: str,
    ) -> None:
        denied_message = deny_reason(interaction)
        if denied_message:
            await interaction.response.send_message(denied_message, ephemeral=True)
            return

        parsed = parse_message_link(message_link)
        if parsed is None:
            await interaction.response.send_message(
                "消息链接格式无效。请粘贴从 Discord 复制的完整消息链接。",
                ephemeral=True,
            )
            return

        guild_id, channel_id, message_id = parsed
        if interaction.guild is None or guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "该消息链接不属于当前服务器。",
                ephemeral=True,
            )
            return

        try:
            message = await fetch_message(bot, channel_id, message_id)
            if not message.pinned:
                await interaction.response.send_message(
                    "该消息当前未置顶。",
                    ephemeral=True,
                )
                return
            await message.unpin(reason=f"Unmarked by {interaction.user.id} via bot command")
        except ValueError:
            await interaction.response.send_message(
                "该消息所在的频道类型暂不支持。",
                ephemeral=True,
            )
            return
        except discord.NotFound:
            await interaction.response.send_message(
                "未找到目标频道或消息，消息可能已被删除。",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "取消置顶失败：机器人在目标频道缺少“管理消息”权限。",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"取消置顶失败：{exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("已取消置顶。", ephemeral=True)
