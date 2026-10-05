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
from daily_bonus import start_daily_bonus  # noqa: E402
from mama import register_commands as register_mama_commands  # noqa: E402
from roulette import start_roulette  # noqa: E402

# 消息处理器：频道 ID -> [async (message) -> None]，各模块在 start_xxx 里注册
MessageHandler = Callable[[discord.Message], Awaitable[None]]


class SukakaBot(discord.Client):
    def __init__(self) -> None:
        # 只开实际用到的事件流，收窄网关流量（详见 docs/bot.md「Intents 配置」）：
        # - guilds：频道/服务器结构（发消息、找频道必需）
        # - guild_messages：服务器消息事件（MESSAGE_CREATE）—— on_message 触发源，必需！
        #   注意：message_content 只解锁消息的「内容字段」，消息「事件本身」由
        #   guild_messages 控制；Intents.default() 内含它，none() 不含，极易漏开。
        # - members：成员变更事件（成员解析 REST 兜底仍可用，无需全量缓存）
        # - message_content：读取消息内容（游戏关键词识别必需）
        # 注意：default() 会附带 bans/emojis/integrations/webhooks/voice_states
        # 等本项目用不到的事件流，全部省掉。
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True
        intents.message_content = True
        super().__init__(
            intents=intents,
            # 不在启动时全量分块下载成员列表：万人服务器每次启动能省几十 MB 流量；
            # 成员按需走 REST 解析（utils.resolve_member_xxx），个别场景多一次 HTTP，
            # 但整体流量与启动速度都大幅优于全量缓存。
            chunk_guilds_at_startup=False,
        )

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
        self._daily_bonus_started = False

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
        if not self._daily_bonus_started:
            self._daily_bonus_started = True
            start_daily_bonus(self)
        print(f"Logged in as {self.user} ({self.user.id})")


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot = SukakaBot()
    bot.run(token)


if __name__ == "__main__":
    main()
