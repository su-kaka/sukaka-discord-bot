import asyncio
import os
import time
from pathlib import Path

import discord

CAROUSEL_CHANNEL_ID = 1455038454772531311
CAROUSEL_FILE = Path(os.getenv("CAROUSEL_FILE", "carousel.txt"))
DEFAULT_CAROUSEL_INTERVAL_MINUTES = 5


def carousel_interval_minutes() -> float:
    raw_interval = os.getenv(
        "CAROUSEL_INTERVAL_MINUTES",
        str(DEFAULT_CAROUSEL_INTERVAL_MINUTES),
    )
    try:
        interval_minutes = float(raw_interval)
    except ValueError:
        print(
            f"Invalid CAROUSEL_INTERVAL_MINUTES={raw_interval!r}; "
            f"using {DEFAULT_CAROUSEL_INTERVAL_MINUTES} minutes."
        )
        return DEFAULT_CAROUSEL_INTERVAL_MINUTES
    if interval_minutes <= 0:
        print(
            f"CAROUSEL_INTERVAL_MINUTES must be positive; "
            f"using {DEFAULT_CAROUSEL_INTERVAL_MINUTES} minutes."
        )
        return DEFAULT_CAROUSEL_INTERVAL_MINUTES
    return interval_minutes


def seconds_until_next_slot(interval_minutes: float) -> float:
    """计算距离下一个对齐时间点的秒数。

    例如间隔 10 分钟：10:00、10:10、10:20 ... 整点对齐。
    """
    interval_seconds = interval_minutes * 60
    now = time.time()
    # 距离上一个对齐点已经过了多久
    elapsed = now % interval_seconds
    # 距离下一个对齐点还有多久；若刚好在点上则等一整个周期
    remaining = interval_seconds - elapsed
    if remaining >= interval_seconds - 1e-6:
        remaining = 0.0
    return remaining


async def carousel_loop(client: discord.Client) -> None:
    interval_minutes = carousel_interval_minutes()
    while True:
        # 先睡到下一个对齐时刻，再发送，保证按固定时钟时间轮播
        wait_seconds = seconds_until_next_slot(interval_minutes)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        try:
            content = CAROUSEL_FILE.read_text(encoding="utf-8").strip()
            if content:
                channel = client.get_channel(CAROUSEL_CHANNEL_ID)
                if channel is None:
                    channel = await client.fetch_channel(CAROUSEL_CHANNEL_ID)
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    raise ValueError("目标频道不是文字频道或帖子")
                await channel.send(content)
                print(f"Carousel message sent to channel {CAROUSEL_CHANNEL_ID}")
        except FileNotFoundError:
            print(f"Carousel file not found: {CAROUSEL_FILE}")
        except (OSError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"Failed to send carousel message: {exc}")


def start_carousel(client: discord.Client) -> asyncio.Task[None]:
    return asyncio.create_task(
        carousel_loop(client),
        name="carousel-message-loop",
    )
