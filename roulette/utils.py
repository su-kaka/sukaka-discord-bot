"""roulette 包共享工具函数。"""

from __future__ import annotations

import random

from roulette.constants import BIG_RED_PACKET_OPTIONS_COUNT


def split_random(pool: int, count: int) -> list[int]:
    """把 pool 点随机分成 count 份，每份至少 1 点。"""
    if count <= 0:
        return []
    if count == 1:
        return [pool]
    cuts = sorted(random.sample(range(1, pool), count - 1))
    parts = [b - a for a, b in zip([0] + cuts, cuts + [pool])]
    random.shuffle(parts)
    return parts


def split_random_capped(pool: int, count: int, cap: int) -> list[int]:
    """把 pool 点随机分成 count 份，每份 0-cap 点，总和不超过 pool。"""
    if count <= 0 or pool <= 0:
        return []
    amounts = [random.randint(0, cap) for _ in range(count)]
    total = sum(amounts)
    if total > pool:
        amounts = [a * pool // total for a in amounts]
    return amounts


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
