"""清空所有用户的虚弱（weak）卡牌次数。"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# 确保能导入 roulette 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roulette.constants import GACHA_DB

DB_PATH = Path(os.getenv("GACHA_DB", GACHA_DB))


def clear_weak() -> int:
    """删除所有用户的虚弱卡牌记录，返回影响行数。"""
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return 0

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM gacha_effects WHERE card_key = ?",
            ("weak",),
        )
        return cursor.rowcount


if __name__ == "__main__":
    count = clear_weak()
    print(f"已清空 {count} 条虚弱卡牌记录。")
