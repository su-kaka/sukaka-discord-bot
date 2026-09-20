import asyncio
import os
from collections import defaultdict
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

# 必须在导入业务模块之前加载 .env：
# channel_admin 等模块在 import 时就会读取 os.getenv，导入后加载就晚了。
load_dotenv()

from carousel import start_carousel  # noqa: E402
from channel_admin import (  # noqa: E402
    VoteState,
    register_commands,
    start_channel_mute_restores,
)
from mama import register_commands as register_mama_commands  # noqa: E402
from roulette import start_roulette  # noqa: E402

# 消息处理器：频道 ID -> [async (message) -> None]，各模块在 start_xxx 里注册
MessageHandler = Callable[[discord.Message], Awaitable[None]]


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

    async def setup_hook(self) -> None:
        register_commands(self)
        register_mama_commands(self)

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
        print(f"Logged in as {self.user} ({self.user.id})")


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot = SukakaBot()
    bot.run(token)


if __name__ == "__main__":
    main()
