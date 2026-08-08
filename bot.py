import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

ALLOWED_CHANNEL_ID = 1293095144806940738
DEFAULT_TIMEOUT_MINUTES = 30
MAX_TIMEOUT_MINUTES = 24 * 60
VOTE_THRESHOLD = 5
KEEPALIVE_HOST = "0.0.0.0"
KEEPALIVE_PORT = 7861

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


class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        content = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Sukaka Bot</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f5f7fb;color:#222;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}"
            ".card{background:#fff;padding:24px 28px;border-radius:12px;"
            "box-shadow:0 8px 24px rgba(0,0,0,.08);max-width:560px;}"
            "h1{margin:0 0 8px 0;font-size:26px;}p{margin:0;color:#555;line-height:1.6;}</style>"
            "</head><body><div class='card'><h1>Sukaka Discord Bot</h1>"
            "<p>Bot is running.</p><p>Keepalive endpoint: 0.0.0.0:7861</p></div></body></html>"
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_keepalive_server() -> None:
    server = ThreadingHTTPServer((KEEPALIVE_HOST, KEEPALIVE_PORT), KeepAliveHandler)
    thread = Thread(target=server.serve_forever, name="keepalive-http", daemon=True)
    thread.start()
    print(f"Keepalive server listening on http://{KEEPALIVE_HOST}:{KEEPALIVE_PORT}")


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
    resolved: bool = False


class SukakaBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.mute_votes: dict[str, VoteState] = {}
        self.active_vote_by_target: dict[tuple[int, int], str] = {}
        self._synced = False

    async def setup_hook(self) -> None:
        self.register_commands()

    async def on_ready(self) -> None:
        if not self._synced:
            await self.tree.sync()
            self._synced = True
        print(f"Logged in as {self.user} ({self.user.id})")

    def register_commands(self) -> None:
        @self.tree.command(
            name="mute_vote",
            description="发起成员禁言投票",
        )
        @app_commands.describe(
            target="要禁言的成员",
            duration_minutes="禁言时长（分钟，默认 30，最长 1440）",
            reason="禁言原因（可选）",
        )
        async def mute_vote(
            interaction: discord.Interaction,
            target: discord.Member,
            duration_minutes: app_commands.Range[int, 1, MAX_TIMEOUT_MINUTES] = DEFAULT_TIMEOUT_MINUTES,
            reason: Optional[str] = None,
        ) -> None:
            denied_message = self._deny_reason(interaction)
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

            active_key = (interaction.guild.id, target.id)
            existing_vote_id = self.active_vote_by_target.get(active_key)
            if existing_vote_id:
                existing_state = self.mute_votes.get(existing_vote_id)
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

            view = MuteVoteView(bot=self, vote_id=vote_id)
            view.update_vote_label(len(state.voter_ids))
            embed = self._build_vote_embed(state, len(state.voter_ids), resolved=False)

            await interaction.response.send_message(embed=embed, view=view)
            message = await interaction.original_response()

            state.vote_message_id = message.id
            self.mute_votes[vote_id] = state
            self.active_vote_by_target[active_key] = vote_id

        @self.tree.command(
            name="delete_message",
            description="通过 Discord 消息链接删除消息",
        )
        @app_commands.describe(message_link="Discord 消息链接")
        async def delete_message(
            interaction: discord.Interaction,
            message_link: str,
        ) -> None:
            denied_message = self._deny_reason(interaction)
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
                message = await fetch_message(self, channel_id, message_id)
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

        @self.tree.command(
            name="mark_message",
            description="通过 Discord 消息链接置顶消息",
        )
        @app_commands.describe(message_link="Discord 消息链接")
        async def mark_message(
            interaction: discord.Interaction,
            message_link: str,
        ) -> None:
            denied_message = self._deny_reason(interaction)
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
                message = await fetch_message(self, channel_id, message_id)
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

        @self.tree.command(
            name="unmark_message",
            description="通过 Discord 消息链接取消置顶",
        )
        @app_commands.describe(message_link="Discord 消息链接")
        async def unmark_message(
            interaction: discord.Interaction,
            message_link: str,
        ) -> None:
            denied_message = self._deny_reason(interaction)
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
                message = await fetch_message(self, channel_id, message_id)
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

    def _is_allowed_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == ALLOWED_CHANNEL_ID

    def _is_whitelisted(self, user_id: int) -> bool:
        return user_id in OPERATION_WHITELIST

    def _deny_reason(self, interaction: discord.Interaction) -> Optional[str]:
        if not self._is_allowed_channel(interaction):
            return f"此机器人只能在 <#{ALLOWED_CHANNEL_ID}> 中使用。"
        if not self._is_whitelisted(interaction.user.id):
            return "你没有使用此机器人的权限。"
        return None

    def _build_vote_embed(self, state: VoteState, votes: int, resolved: bool) -> discord.Embed:
        status = "已通过" if resolved else "投票中"
        embed = discord.Embed(title="禁言投票", color=discord.Color.red())
        embed.add_field(name="状态", value=status, inline=True)
        embed.add_field(name="票数", value=f"{votes}/{VOTE_THRESHOLD}", inline=True)
        embed.add_field(name="目标成员", value=f"<@{state.target_id}>", inline=False)
        embed.add_field(
            name="禁言时长",
            value=f"{state.duration_minutes} 分钟",
            inline=True,
        )
        embed.add_field(name="原因", value=state.reason, inline=False)
        embed.set_footer(text=f"发起人 ID：{state.initiator_id}")
        return embed


class MuteVoteView(discord.ui.View):
    def __init__(self, bot: SukakaBot, vote_id: str) -> None:
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

        if interaction.channel_id != state.channel_id:
            await interaction.response.send_message(
                "只能在发起投票的频道中投票。",
                ephemeral=True,
            )
            return

        if not self.bot._is_whitelisted(interaction.user.id):
            await interaction.response.send_message(
                "你没有参与禁言投票的权限。",
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
            embed = self.bot._build_vote_embed(state, votes, resolved=False)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        guild = self.bot.get_guild(state.guild_id)
        if guild is None:
            await interaction.response.send_message(
                "无法找到当前服务器，未执行禁言。",
                ephemeral=True,
            )
            return

        try:
            member = guild.get_member(state.target_id)
            if member is None:
                member = await guild.fetch_member(state.target_id)

            until = discord.utils.utcnow() + timedelta(minutes=state.duration_minutes)
            await member.timeout(until, reason=f"Vote mute: {state.reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "禁言失败：机器人缺少“禁言成员”权限，或角色层级不足。",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"禁言失败：{exc}",
                ephemeral=True,
            )
            return

        state.resolved = True
        self.bot.active_vote_by_target.pop((state.guild_id, state.target_id), None)

        self.update_vote_label(votes)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        embed = self.bot._build_vote_embed(state, votes, resolved=True)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"投票已通过，<@{state.target_id}> 已被禁言 {state.duration_minutes} 分钟。"
        )


def parse_message_link(message_link: str) -> Optional[tuple[int, int, int]]:
    match = MESSAGE_LINK_PATTERN.match(message_link.strip())
    if not match:
        return None
    guild_id, channel_id, message_id = match.groups()
    return int(guild_id), int(channel_id), int(message_id)


async def fetch_message(
    bot: SukakaBot,
    channel_id: int,
    message_id: int,
) -> discord.Message:
    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await bot.fetch_channel(channel_id)

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError("Unsupported channel type")

    return await channel.fetch_message(message_id)


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    start_keepalive_server()
    bot = SukakaBot()
    bot.run(token)


if __name__ == "__main__":
    main()
