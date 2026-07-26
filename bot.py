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
KEEPALIVE_HOST = "127.0.0.1"
KEEPALIVE_PORT = 7861

MESSAGE_LINK_PATTERN = re.compile(
    r"^https?://(?:ptb\\.|canary\\.)?discord(?:app)?\\.com/channels/(\\d+)/(\\d+)/(\\d+)$"
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
            "<p>Bot is running.</p><p>Keepalive endpoint: 127.0.0.1:7861</p></div></body></html>"
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
            description="Start a mute vote for a member in this channel.",
        )
        @app_commands.describe(
            target="Member to mute",
            duration_minutes="Mute duration in minutes (default 30, max 1440)",
            reason="Reason for the mute",
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
                    "Bots cannot be muted by vote.",
                    ephemeral=True,
                )
                return

            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command can only be used in a server.",
                    ephemeral=True,
                )
                return

            active_key = (interaction.guild.id, target.id)
            existing_vote_id = self.active_vote_by_target.get(active_key)
            if existing_vote_id:
                existing_state = self.mute_votes.get(existing_vote_id)
                if existing_state and not existing_state.resolved:
                    await interaction.response.send_message(
                        "There is already an active mute vote for this member.",
                        ephemeral=True,
                    )
                    return

            vote_id = str(uuid.uuid4())
            vote_reason = reason or "Mute vote"
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
            description="Delete a message by its Discord message link.",
        )
        @app_commands.describe(message_link="Discord message link")
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
                    "Invalid message link format.",
                    ephemeral=True,
                )
                return

            guild_id, channel_id, message_id = parsed
            if interaction.guild is None or guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "The message link must belong to this server.",
                    ephemeral=True,
                )
                return

            try:
                message = await fetch_message(self, channel_id, message_id)
                await message.delete()
            except ValueError:
                await interaction.response.send_message(
                    "Unsupported channel type in message link.",
                    ephemeral=True,
                )
                return
            except discord.NotFound:
                await interaction.response.send_message(
                    "Message or channel not found.",
                    ephemeral=True,
                )
                return
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I do not have permission to delete this message.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.response.send_message(
                    f"Delete failed: {exc}",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message("Message deleted.", ephemeral=True)

        @self.tree.command(
            name="mark_message",
            description="Pin a message by its Discord message link.",
        )
        @app_commands.describe(message_link="Discord message link")
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
                    "Invalid message link format.",
                    ephemeral=True,
                )
                return

            guild_id, channel_id, message_id = parsed
            if interaction.guild is None or guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "The message link must belong to this server.",
                    ephemeral=True,
                )
                return

            try:
                message = await fetch_message(self, channel_id, message_id)
                if message.pinned:
                    await interaction.response.send_message(
                        "Message is already pinned.",
                        ephemeral=True,
                    )
                    return
                await message.pin(reason=f"Marked by {interaction.user.id} via bot command")
            except ValueError:
                await interaction.response.send_message(
                    "Unsupported channel type in message link.",
                    ephemeral=True,
                )
                return
            except discord.NotFound:
                await interaction.response.send_message(
                    "Message or channel not found.",
                    ephemeral=True,
                )
                return
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I do not have permission to pin this message.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.response.send_message(
                    f"Mark failed: {exc}",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message("Message pinned.", ephemeral=True)

        @self.tree.command(
            name="unmark_message",
            description="Unpin a message by its Discord message link.",
        )
        @app_commands.describe(message_link="Discord message link")
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
                    "Invalid message link format.",
                    ephemeral=True,
                )
                return

            guild_id, channel_id, message_id = parsed
            if interaction.guild is None or guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "The message link must belong to this server.",
                    ephemeral=True,
                )
                return

            try:
                message = await fetch_message(self, channel_id, message_id)
                if not message.pinned:
                    await interaction.response.send_message(
                        "Message is not pinned.",
                        ephemeral=True,
                    )
                    return
                await message.unpin(reason=f"Unmarked by {interaction.user.id} via bot command")
            except ValueError:
                await interaction.response.send_message(
                    "Unsupported channel type in message link.",
                    ephemeral=True,
                )
                return
            except discord.NotFound:
                await interaction.response.send_message(
                    "Message or channel not found.",
                    ephemeral=True,
                )
                return
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I do not have permission to unpin this message.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.response.send_message(
                    f"Unmark failed: {exc}",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message("Message unpinned.", ephemeral=True)

    def _is_allowed_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == ALLOWED_CHANNEL_ID

    def _is_whitelisted(self, user_id: int) -> bool:
        return user_id in OPERATION_WHITELIST

    def _deny_reason(self, interaction: discord.Interaction) -> Optional[str]:
        if not self._is_allowed_channel(interaction):
            return f"This bot can only be used in channel {ALLOWED_CHANNEL_ID}."
        if not self._is_whitelisted(interaction.user.id):
            return "You are not in MUTE_WHITELIST, so you cannot use this bot."
        return None

    def _build_vote_embed(self, state: VoteState, votes: int, resolved: bool) -> discord.Embed:
        status = "Passed" if resolved else "Voting"
        embed = discord.Embed(title="Mute Vote", color=discord.Color.red())
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Votes", value=f"{votes}/{VOTE_THRESHOLD}", inline=True)
        embed.add_field(name="Target", value=f"<@{state.target_id}>", inline=False)
        embed.add_field(
            name="Duration",
            value=f"{state.duration_minutes} minute(s)",
            inline=True,
        )
        embed.add_field(name="Reason", value=state.reason, inline=False)
        embed.set_footer(text=f"Initiator: {state.initiator_id}")
        return embed


class MuteVoteView(discord.ui.View):
    def __init__(self, bot: SukakaBot, vote_id: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.vote_id = vote_id

    def update_vote_label(self, votes: int) -> None:
        self.vote_button.label = f"Vote mute ({votes}/{VOTE_THRESHOLD})"

    @discord.ui.button(label="Vote mute (0/5)", style=discord.ButtonStyle.danger)
    async def vote_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        state = self.bot.mute_votes.get(self.vote_id)
        if state is None:
            await interaction.response.send_message(
                "This vote no longer exists.",
                ephemeral=True,
            )
            return

        if state.resolved:
            await interaction.response.send_message(
                "This vote has already been resolved.",
                ephemeral=True,
            )
            return

        if interaction.channel_id != state.channel_id:
            await interaction.response.send_message(
                "You can only vote in the original vote channel.",
                ephemeral=True,
            )
            return

        if not self.bot._is_whitelisted(interaction.user.id):
            await interaction.response.send_message(
                "You are not in MUTE_WHITELIST, so you cannot use this bot.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        if interaction.user.bot:
            await interaction.response.send_message(
                "Bots cannot vote.",
                ephemeral=True,
            )
            return

        if user_id == state.target_id:
            await interaction.response.send_message(
                "Target member cannot vote in this mute vote.",
                ephemeral=True,
            )
            return

        if user_id in state.voter_ids:
            await interaction.response.send_message(
                "You have already voted.",
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
                "Guild not found. Cannot apply mute.",
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
                "I do not have permission to timeout this member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"Failed to apply timeout: {exc}",
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
            f"Mute vote passed. <@{state.target_id}> has been timed out for {state.duration_minutes} minute(s)."
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
