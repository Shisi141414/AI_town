"""生理周期机制（实验分支）：所有居民 5 天（720刻）为一个周期。

男性——紧张→释放：
  紧张度随周期进度线性上升（第1天0% → 第5天100%），
  精力/饱食/心情的消耗按 ×(1.0→1.4) 同步加重；
  释放方式：卫生间「自行解决」（30分钟，共损耗60精力），
  或与相恋室友的「互动」（双方同意）；释放后消耗恢复基线，开始新周期。

女性——生理期与黄体期：
  周期第1天为生理期：随机轻微腹痛掉血（每刻10%概率-0.5生命）；
  第4~5天为黄体期：渴望「恋爱」与「互动」，未满足时心情消耗×1.5；
  「恋爱」或双方同意的「互动」后恢复基线（本周期内有效）。
"""
from __future__ import annotations

import random

CYCLE_TICKS = 5 * 144          # 720刻 = 5天
MALE_DRAIN_SPAN = 0.4          # 满周期时消耗加成上限（×1.4）
LUTEAL_START = 3 * 144         # 第4天起为黄体期
LUTEAL_MOOD_MULT = 1.5         # 黄体期未满足时心情消耗倍率
PERIOD_HP_CHANCE = 0.10        # 生理期每刻掉血概率
PERIOD_HP_LOSS = 0.5           # 每次掉血量


def _now(p, sim) -> int:
    """当前世界刻：优先 sim，规则引擎等无 sim 场景用玩家同步的 world_tick。"""
    return sim.global_tick if sim is not None else p.world_tick


def ensure(p, sim) -> None:
    """推进周期锚点：跨周期自动翻页（未释放也自然进入下一周期）。"""
    elapsed = _now(p, sim) - p.cycle_anchor
    if elapsed >= CYCLE_TICKS:
        p.cycle_anchor += CYCLE_TICKS * (elapsed // CYCLE_TICKS)


def elapsed_ticks(p, sim) -> int:
    ensure(p, sim)
    return max(0, _now(p, sim) - p.cycle_anchor)


def day_in_cycle(p, sim) -> int:
    return elapsed_ticks(p, sim) // 144 + 1


def male_tension(p, sim) -> float:
    """男性紧张度 0~1（女性恒0）。"""
    if p.gender != '男':
        return 0.0
    return min(1.0, elapsed_ticks(p, sim) / CYCLE_TICKS)


def in_period(p, sim) -> bool:
    return p.gender == '女' and elapsed_ticks(p, sim) < 144


def in_luteal(p, sim) -> bool:
    return p.gender == '女' and elapsed_ticks(p, sim) >= LUTEAL_START


def luteal_satisfied(p, sim) -> bool:
    """本周期内是否已通过恋爱/互动满足渴望。"""
    return p.cycle_satisfied_anchor == p.cycle_anchor


def multipliers(p, sim) -> dict[str, float]:
    """当前消耗倍率（精力/饱食/心情），基线1.0。"""
    result = {'satiety': 1.0, 'energy': 1.0, 'mood': 1.0}
    if p.gender == '男':
        mult = 1.0 + MALE_DRAIN_SPAN * male_tension(p, sim)
        return {k: mult for k in result}
    if in_luteal(p, sim) and not luteal_satisfied(p, sim):
        result['mood'] = LUTEAL_MOOD_MULT
    return result


def release_male(p, sim, how: str = '自行解决') -> None:
    """男性释放：重置周期，消耗恢复基线。"""
    p.cycle_anchor = _now(p, sim)
    p.remember('我释放了积压的身体紧张，整个人轻松了下来，生理消耗恢复正常', 6)
    sim.log('activity', f'🌙 {p.name}独处释放后恢复了状态，开始新的生理周期', [p.pid])


def satisfy_female(p, sim, how: str = '亲密') -> None:
    """女性渴望满足：本周期内心情消耗恢复基线。"""
    p.cycle_satisfied_anchor = p.cycle_anchor
    p.remember(f'{"和心爱的人相处" if how == "恋爱" else "亲密互动"}让我的渴望得到了满足，心情轻松了许多', 6)


def tick_female_period(p, sim) -> None:
    """生理期结算：入期记忆一次 + 随机少量掉血。"""
    if not in_period(p, sim):
        return
    if getattr(p, 'cycle_noticed', -1) != p.cycle_anchor:
        p.cycle_noticed = p.cycle_anchor
        p.remember('我来了生理期，这几天可能腹痛乏力，随机会掉一点血，多注意休息', 4)
    if random.random() < PERIOD_HP_CHANCE:
        p.stats.hp = max(0.0, p.stats.hp - PERIOD_HP_LOSS)
        p.stats.clamp()
        p._period_hp_at = sim.global_tick   # 本刻生理期失血标记，供扣血归因


def description(p, sim) -> str:
    """给模型的简明周期状态说明。"""
    d = day_in_cycle(p, sim)
    if p.gender == '男':
        t = male_tension(p, sim)
        mult = 1.0 + MALE_DRAIN_SPAN * t
        level = '较低' if t < 0.34 else ('偏高' if t < 0.67 else '很高')
        return (f'第{d}/5天，紧张度{level}（{int(t * 100)}%）：精力/饱食/心情的消耗×{mult:.2f}。'
                '释放方式：在卫生间「自行解决」（30分钟、共损耗60精力），'
                '或与相恋室友「互动」（需双方同意）；释放后消耗恢复基线，开始新周期')
    if in_period(p, sim):
        return ('生理期（第1/5天）：会随机轻微腹痛掉血，注意休息和保暖；'
                '第4~5天是黄体期，会渴望恋爱与亲密互动')
    if in_luteal(p, sim):
        if luteal_satisfied(p, sim):
            return f'黄体期（第{d}/5天）：渴望已被满足，心情消耗正常'
        return ('黄体期（第{d}/5天）：渴望恋爱与亲密互动，未满足时心情消耗×'
                f'{LUTEAL_MOOD_MULT:g}；与室友「恋爱」或双方同意的「互动」后恢复基线')
    return f'第{d}/5天，身体状态平稳'
