"""活动额度 API 封装。"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from roulette.constants import DEFAULT_API_BASE


async def query_quota(client: httpx.AsyncClient, username: str) -> Optional[int]:
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.request(
            "GET",
            f"{api_base}/api/activity-quota/query",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def adjust_quota(
    client: httpx.AsyncClient, endpoint: str, username: str, amount: int
) -> Optional[int]:
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.post(
            f"{api_base}/api/activity-quota/{endpoint}",
            headers={"X-Activity-Quota-Key": api_key},
            json={"username": username, "amount": amount},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            return int(data.get("current_activity_quota", 0))
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def query_top_quota(client: httpx.AsyncClient) -> Optional[list[tuple[str, int]]]:
    """查询活动额度前十用户，返回 (username, quota) 列表。"""
    api_key = os.getenv("ACTIVITY_QUOTA_API_KEY")
    if not api_key:
        return None
    api_base = os.getenv("ACTIVITY_QUOTA_API_BASE", DEFAULT_API_BASE)
    try:
        response = await client.get(
            f"{api_base}/api/activity-quota/top",
            headers={"X-Activity-Quota-Key": api_key},
        )
        data = response.json()
        if response.is_success and data.get("success") is True:
            users = data.get("users", [])
            return [(u["username"], int(u["activity_quota"])) for u in users]
        return None
    except (httpx.HTTPError, ValueError, KeyError):
        return None
