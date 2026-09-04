import asyncio
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from carousel import start_carousel
from channel_admin import (
    ChannelMuteRecord,
    VoteState,
    load_channel_mutes,
    register_commands,
    schedule_channel_mute_restore,
)

load_dotenv()

KEEPALIVE_HOST = "0.0.0.0"
KEEPALIVE_PORT = 7861


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


class SukakaBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.mute_votes: dict[str, VoteState] = {}
        self.active_vote_by_target: dict[tuple[int, int], str] = {}
        self.channel_mutes: dict[tuple[int, int, int], ChannelMuteRecord] = {}
        self.channel_mute_tasks: dict[tuple[int, int, int], asyncio.Task[None]] = {}
        self.channel_mute_lock = asyncio.Lock()
        self._synced = False
        self._channel_mutes_started = False
        self._carousel_started = False
        self._carousel_task: Optional[asyncio.Task[None]] = None
        load_channel_mutes(self)

    async def setup_hook(self) -> None:
        register_commands(self)

    async def on_ready(self) -> None:
        if not self._synced:
            await self.tree.sync()
            self._synced = True
        if not self._channel_mutes_started:
            self._channel_mutes_started = True
            for record in self.channel_mutes.values():
                schedule_channel_mute_restore(self, record)
        if not self._carousel_started:
            self._carousel_started = True
            self._carousel_task = start_carousel(self)
        print(f"Logged in as {self.user} ({self.user.id})")


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    start_keepalive_server()
    bot = SukakaBot()
    bot.run(token)


if __name__ == "__main__":
    main()
