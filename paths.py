"""项目数据文件路径统一解析。

所有本地 SQLite 数据库默认集中存放在 DATA_DIR（默认 ./data），
不再散落在项目根目录，方便备份与整体迁移。

路径优先级（从高到低）：
1. 各模块同名环境变量指定的完整路径（如 GACHA_DB=/var/lib/bot/gacha.db）；
2. DATA_DIR 环境变量整体改址（默认 ./data）；
3. 默认 DATA_DIR/<文件名>。

首次解析时若目标文件不存在、而当前工作目录（项目根）下有同名旧库，
会自动把旧库搬入新位置，升级后无需手动迁移。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))


def data_file(env_var: str, filename: str) -> Path:
    """解析数据文件路径，确保父目录存在。

    env_var:  可覆盖完整路径的环境变量名（如 "GACHA_DB"），未设置时落到 DATA_DIR。
    filename: 默认文件名。
    """
    override = os.getenv(env_var)
    path = Path(override) if override else DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    # 一次性迁移：项目根目录存在同名旧库而目标位置还没有时，自动搬入
    if not path.exists():
        legacy = Path.cwd() / filename
        if legacy.is_file():
            shutil.move(str(legacy), path)
    return path
