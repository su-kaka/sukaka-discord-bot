"""roulette 包共享工具函数。"""

from __future__ import annotations

import random
import time
from typing import Optional

import discord

from roulette.constants import BIG_RED_PACKET_OPTIONS_COUNT

# 成员解析结果缓存：(guild_id, key) -> (member, timestamp)。
# 关闭启动全量成员分块后 guild.get_member() 经常未命中，REST 查询有频率限制，
# 这里对解析结果做短期缓存，避免频繁打 REST。
_MEMBER_CACHE: dict[tuple[int, str], tuple[discord.Member, float]] = {}
_MEMBER_CACHE_TTL_SECONDS = 600.0
_MEMBER_CACHE_MAX_ENTRIES = 1000  # 容量上限，防止只写不读的过期条目无限堆积


def _cache_get(guild_id: int, key: str) -> Optional[discord.Member]:
    entry = _MEMBER_CACHE.get((guild_id, key))
    if entry is None:
        return None
    member, cached_at = entry
    if time.monotonic() - cached_at > _MEMBER_CACHE_TTL_SECONDS:
        _MEMBER_CACHE.pop((guild_id, key), None)
        return None
    return member


def _cache_sweep() -> None:
    """容量控制：先清扫全部过期条目，仍超上限则按写入时间淘汰最旧的。

    TTL 是惰性清理（查到才删），只写不读的过期条目会一直留着，
    所以在写入路径上做主动容量控制，保证内存有界。
    """
    now = time.monotonic()
    for k in [
        k
        for k, (_, cached_at) in _MEMBER_CACHE.items()
        if now - cached_at > _MEMBER_CACHE_TTL_SECONDS
    ]:
        _MEMBER_CACHE.pop(k, None)
    if len(_MEMBER_CACHE) < _MEMBER_CACHE_MAX_ENTRIES:
        return
    # 回落到 90% 水位，避免在临界点反复清扫
    target = int(_MEMBER_CACHE_MAX_ENTRIES * 0.9)
    oldest = sorted(_MEMBER_CACHE.items(), key=lambda kv: kv[1][1])
    for k, _ in oldest[: len(_MEMBER_CACHE) - target]:
        _MEMBER_CACHE.pop(k, None)


def _cache_put(guild_id: int, key: str, member: discord.Member) -> None:
    if len(_MEMBER_CACHE) >= _MEMBER_CACHE_MAX_ENTRIES:
        _cache_sweep()
    _MEMBER_CACHE[(guild_id, key)] = (member, time.monotonic())


async def resolve_member_by_id(
    guild: Optional[discord.Guild], discord_id: int
) -> Optional[discord.Member]:
    """按 discord_id 解析成员：缓存 → 成员缓存 → REST 兜底。

    bot 关闭了启动全量成员分块（chunk_guilds_at_startup=False），
    guild.get_member() 命中率低，这里提供统一的按需解析入口。
    """
    if guild is None:
        return None
    cached = _cache_get(guild.id, f"id:{discord_id}")
    if cached is not None:
        return cached
    member = guild.get_member(discord_id)
    if member is not None:
        _cache_put(guild.id, f"id:{discord_id}", member)
        return member
    try:
        member = await guild.fetch_member(discord_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
    _cache_put(guild.id, f"id:{discord_id}", member)
    return member


async def resolve_member_by_name(
    guild: Optional[discord.Guild], username: str
) -> Optional[discord.Member]:
    """按 Discord 用户名解析成员：缓存 → 成员缓存 → REST 按名字前缀搜索。

    REST 搜索（search_members）支持用户名/昵称前缀匹配，不受 members Intent 影响。
    """
    if guild is None:
        return None
    cached = _cache_get(guild.id, f"name:{username}")
    if cached is not None:
        return cached
    member = guild.get_member_named(username)
    if member is not None:
        _cache_put(guild.id, f"name:{username}", member)
        return member
    member = discord.utils.find(
        lambda m: m.name == username or m.global_name == username,
        guild.members,
    )
    if member is not None:
        _cache_put(guild.id, f"name:{username}", member)
        return member
    try:
        # query 语义为前缀匹配；命中后逐个精确比对，避免「alice」误匹配「alice2」
        found = await guild.query_members(query=username, limit=100, cache=True)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        return None
    member = discord.utils.find(
        lambda m: m.name == username or m.global_name == username, found
    )
    if member is not None:
        _cache_put(guild.id, f"name:{username}", member)
    return member


def split_random(pool: int, count: int) -> list[int]:
    """把 pool 点随机分成 count 份，总和正好等于 pool。

    pool 足够时每份至少 1 点；pool 不足 count 时只有 pool 份各得 1 点，其余为 0。
    """
    if count <= 0:
        return []
    if pool < count:
        shares = [0] * count
        for i in random.sample(range(count), pool):
            shares[i] = 1
        return shares
    if count == 1:
        return [pool]
    cuts = sorted(random.sample(range(1, pool), count - 1))
    parts = [b - a for a, b in zip([0] + cuts, cuts + [pool])]
    random.shuffle(parts)
    return parts


def make_arithmetic_question() -> tuple[str, int, list[int]]:
    """生成一道十以内加减法题，返回 (题目文本, 正确答案, 打乱后的选项列表)。"""
    a = random.randint(0, 10)
    b = random.randint(0, 10)
    if random.random() < 0.5:
        answer = a + b
        question = f"{a} + {b} = ?"
    else:
        a, b = max(a, b), min(a, b)  # 保证结果非负
        answer = a - b
        question = f"{a} - {b} = ?"

    # 生成不重复且非负的干扰项，凑满选项数
    options = {answer}
    while len(options) < BIG_RED_PACKET_OPTIONS_COUNT:
        distractor = answer + random.randint(-5, 5)
        if distractor >= 0:
            options.add(distractor)
    shuffled = list(options)
    random.shuffle(shuffled)
    return question, answer, shuffled
