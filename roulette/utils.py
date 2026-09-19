"""roulette 包共享工具函数。"""

from __future__ import annotations

import random

from roulette.constants import BIG_RED_PACKET_OPTIONS_COUNT


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
