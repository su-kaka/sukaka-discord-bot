"""排行榜：展示活动额度前十用户。"""

from __future__ import annotations

import discord
import httpx

from roulette.api import query_top_quota
from roulette.constants import LEADERBOARD_TOP_N
from roulette.gacha import get_snake_charm_holder, is_offline


async def handle_leaderboard(message: discord.Message, client: httpx.AsyncClient) -> None:
    """处理「排行榜」命令。"""
    top_users = await query_top_quota(client)
    if top_users is None:
        await message.channel.send("🏆 查询排行榜失败，请稍后再试。")
        return
    if not top_users:
        await message.channel.send("🏆 暂无排行数据。")
        return
    guild = message.guild
    snake_holder = get_snake_charm_holder()
    lines = ["🏆 **活动额度排行榜**"]
    rank = 0
    for username, quota in top_users:
        if rank >= LEADERBOARD_TOP_N:
            break
        # 蛇符咒持有者隐身
        display = username
        member = None
        if guild:
            member = guild.get_member_named(username)
            if member is None:
                member = discord.utils.find(
                    lambda m: m.name == username or m.global_name == username,
                    guild.members,
                )
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
