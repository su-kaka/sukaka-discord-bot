import asyncio
import os
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from carousel import start_carousel
from channel_admin import (
    VoteState,
    register_commands,
    start_channel_mute_restores,
)
from mama import start_mama
from roulette import start_roulette

load_dotenv()

KEEPALIVE_HOST = "0.0.0.0"
KEEPALIVE_PORT = 7861

# 消息处理器：频道 ID -> [async (message) -> None]，各模块在 start_xxx 里注册
MessageHandler = Callable[[discord.Message], Awaitable[None]]


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
        intents.message_content = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.mute_votes: dict[str, VoteState] = {}
        self.active_vote_by_target: dict[tuple[int, int], str] = {}
        # 消息分发注册表：频道 ID -> 各模块注册的处理器列表
        self.message_handlers: dict[int, list[MessageHandler]] = defaultdict(list)
        self._synced = False
        self._channel_mutes_started = False
        self._carousel_started = False
        self._carousel_task: Optional[asyncio.Task[None]] = None
        self._roulette_started = False
        self._mama_started = False

    async def setup_hook(self) -> None:
        register_commands(self)

    def register_message_handler(self, channel_id: int, handler: MessageHandler) -> None:
        """注册某频道的消息处理器。各功能模块在 start_xxx 里调用，自行声明监听的频道。"""
        self.message_handlers[channel_id].append(handler)

    async def on_message(self, message: discord.Message) -> None:
        """唯一的消息入口：按频道分发给注册了该频道的模块。"""
        if message.author.bot:
            return
        for handler in self.message_handlers.get(message.channel.id, ()):
            await handler(message)

    async def on_ready(self) -> None:
        if not self._synced:
            await self.tree.sync()
            self._synced = True
        if not self._channel_mutes_started:
            self._channel_mutes_started = True
            start_channel_mute_restores(self)
        if not self._carousel_started:
            self._carousel_started = True
            self._carousel_task = start_carousel(self)
        if not self._roulette_started:
            self._roulette_started = True
            start_roulette(self)
        if not self._mama_started:
            self._mama_started = True
            start_mama(self)
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
