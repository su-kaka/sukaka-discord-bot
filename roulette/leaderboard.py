"""排行榜：展示活动额度前十用户。"""

from __future__ import annotations

import discord
import httpx

from roulette.api import query_top_quota
from roulette.constants import LEADERBOARD_TOP_N


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
    lines = ["🏆 **活动额度排行榜**"]
    for rank, (username, quota) in enumerate(top_users[:LEADERBOARD_TOP_N], 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
        # 尝试把 API 用户名解析成服务器成员，显示为 @提及（自动显示昵称）
        display = username
        if guild:
            member = guild.get_member_named(username)
            if member is None:
                member = discord.utils.find(
                    lambda m: m.name == username or m.global_name == username,
                    guild.members,
                )
            if member:
                display = member.mention
        lines.append(f"{medal} {display} — **{quota} 点**")
    await message.channel.send("\n".join(lines))
