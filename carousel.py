import asyncio
import os
from pathlib import Path
from typing import Optional

import discord

CAROUSEL_CHANNEL_ID = 1455038454772531311
CAROUSEL_FILE = Path(os.getenv("CAROUSEL_FILE", "carousel.txt"))
DEFAULT_CAROUSEL_INTERVAL_MINUTES = 10


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


async def carousel_loop(client: discord.Client) -> None:
    interval_minutes = carousel_interval_minutes()
    while True:
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

        await asyncio.sleep(interval_minutes * 60)


def start_carousel(client: discord.Client) -> asyncio.Task[None]:
    return asyncio.create_task(
        carousel_loop(client),
        name="carousel-message-loop",
    )
