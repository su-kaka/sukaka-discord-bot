"""排行榜：展示活动额度前十用户。"""

from __future__ import annotations

from typing import Optional

import discord
import httpx

from roulette.api import query_top_quota
from roulette.constants import LEADERBOARD_TOP_N
from roulette.gacha import get_offline_users, get_snake_charm_holder, is_offline
from roulette.utils import resolve_member_by_id, resolve_member_by_name


async def _resolve_member_name(
    guild: Optional[discord.Guild], discord_id: int
) -> Optional[str]:
    """通过 discord_id 解析用户名，找不到返回 None（REST 兜底版）。"""
    member = await resolve_member_by_id(guild, discord_id)
    return member.name if member else None


async def handle_leaderboard(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「排行榜」命令。"""
    guild = message.guild
    # 服务端排除：蛇符咒持有者 + 下线用户（按用户名传给 /top 的 exclude 参数）
    snake_holder = get_snake_charm_holder()
    exclude_names: set[str] = set()
    if snake_holder is not None:
        name = await _resolve_member_name(guild, snake_holder)
        if name:
            exclude_names.add(name)
    for discord_id in get_offline_users():
        name = await _resolve_member_name(guild, discord_id)
        if name:
            exclude_names.add(name)
    top_users = await query_top_quota(client, exclude_names)
    if top_users is None:
        await message.channel.send("🏆 查询排行榜失败，请稍后再试。")
        return
    if not top_users:
        await message.channel.send("🏆 暂无排行数据。")
        return
    lines = ["🏆 **活动额度排行榜**"]
    rank = 0
    for username, quota in top_users:
        if rank >= LEADERBOARD_TOP_N:
            break
        # 蛇符咒持有者隐身
        display = username
        member = await resolve_member_by_name(guild, username) if guild else None
        if member:
            display = member.mention
        if member and snake_holder and member.id == snake_holder:
            continue
        # 下线状态：不出现在排行榜
        if member and is_offline(member.id):
            continue
        rank += 1
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
        lines.append(f"{medal} {display} — **{quota} 点**")
    await message.channel.send("\n".join(lines))
