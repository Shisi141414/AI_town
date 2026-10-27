"""决策层 —— LLM 提示词 + 规则引擎兜底

提示词设计原则（用户要求：所有玩家完全相同的提示词）：
- 一份统一模板，不写任何角色扮演人设
- 个体差异完全由三部分涌现：①自身数值 ②记忆流 ③他人好感表
- 这正是斯坦福 Generative Agents 的核心发现：同一提示词 + 不同经历
  会自然涌现出稳定的行为差异（「人格」是经历的沉淀，不是设定的产物）

输出契约：要求 LLM 返回严格 JSON {"action","target","reason"}
- 解析失败 → 重试一次（附带纠错提示）→ 再失败 → 规则引擎接管本 tick
- 规则引擎：基于数值优先级的 if-else 链，保证游戏永远能跑
"""
from __future__ import annotations

import json
import random
import re

from . import activities as act
from . import world
from .llm import LLMError, chat
from .models import ApiProfile, Decision, Stats

# ------------------------------------------------------------------ 统一提示词
SYSTEM_PROMPT = """你是一个住在小镇上的普通居民，自主决定自己的生活。
你的身体状态、记忆和人际关系会实时变化，请根据它们做出当下最合理的决定。
没有标准答案，像活人一样权衡：饿了要吃饭、累了要休息、缺钱要工作、
也可以娱乐、社交、恋爱，甚至发脾气搞破坏——一切由你。"""

USER_TEMPLATE = """【现在是第{day}天 {clock}（{daypart}）】
【我的状态】生命{hp:.0f}/100 饱食{satiety:.0f}/100 精力{energy:.0f}/100 心情{mood:.0f}/100 金钱{money:.0f}元
【我的位置】{location}
【我能看见的人】{nearby}
【我对别人的好感】{affinity}
【我最近的记忆】
{memories}
【可选行动】
{menu}

请决定接下来做什么，只输出一行 JSON（不要markdown代码块）：
{{"action": "动作key", "target": "目标玩家名或空", "reason": "15字内的理由"}}"""

DAYPARTS = ((5, "凌晨"), (8, "早晨"), (11, "上午"), (13, "中午"),
            (17, "下午"), (20, "傍晚"), (23, "晚上"), (25, "深夜"))

JSON_FIX_PROMPT = "你上次的输出无法解析为 JSON。请严格只输出一行 {{\"action\":..., \"target\":..., \"reason\":...}} 形式的 JSON，不要任何其他文字。"


def daypart(hour: int) -> str:
    for start, name in DAYPARTS:
        if hour < start:
            return name
    return "深夜"


def build_user_prompt(p, others_nearby: list[str], clock: str) -> str:
    """p: Player 实例。所有玩家共用此模板，无任何个性化字段。"""
    aff = "、".join(f"{name}{v:.0f}" for name, v in
                    sorted(p.affinity.items(), key=lambda kv: -kv[1])[:6]) or "（还不认识谁）"
    nearby = "、".join(others_nearby) if others_nearby else "（附近没人）"
    mem = p.memory.brief(p.world_tick, k=8)
    return USER_TEMPLATE.format(
        day=p.day, clock=clock, daypart=daypart(p.hour),
        hp=p.stats.hp, satiety=p.stats.satiety,
        energy=p.stats.energy, mood=p.stats.mood, money=p.stats.money,
        location=world.facility_at(p.x, p.y)["name"] if world.facility_at(p.x, p.y) else "小镇中",
        nearby=nearby, affinity=aff, memories=mem, menu=act.activity_menu())


def parse_decision(text: str) -> Decision | None:
    """宽容解析：剥离markdown围栏、截取首个 {...} 块"""
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    action = str(d.get("action", "")).strip()
    if not action:
        return None
    return Decision(action=action, target=str(d.get("target", "")).strip(),
                    reason=str(d.get("reason", ""))[:60], source="llm")


def _norm_action(raw: str) -> str:
    """LLM 可能返回中文活动名或 key，统一映射到 key"""
    raw = raw.strip().lower()
    if raw in act.ACTIVITIES:
        return raw
    for k, a in act.ACTIVITIES.items():
        if a["name"] == raw or a["emoji"] == raw:
            return k
    return ""


async def decide_llm(profile: ApiProfile, model: str, p, nearby: list[str],
                     clock: str, temperature: float, timeout: float) -> Decision:
    """调用 LLM 决策；带一次纠错重试。任何失败抛 LLMError 由上层降级。"""
    user = build_user_prompt(p, nearby, clock)
    last_err: Exception | None = None
    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\n" + JSON_FIX_PROMPT
        text = await _achat(profile, model, SYSTEM_PROMPT, prompt,
                            temperature, timeout)
        d = parse_decision(text)
        if d is None:
            last_err = LLMError(f"无法解析: {text[:120]}")
            continue
        d.action = _norm_action(d.action)
        if d.action:
            return d
        last_err = LLMError(f"未知动作: {d.action}")
    raise last_err or LLMError("决策失败")


def _run_in_thread(func, *args):
    """在线程池里跑同步 requests（asyncio 环境不阻塞事件循环）"""
    import asyncio
    return asyncio.get_event_loop().run_in_executor(None, func, *args)


async def _achat(profile, model, system, user, temperature, timeout):
    return await _run_in_thread(chat, profile, model, system, user,
                                temperature, 250, timeout)


# ------------------------------------------------------------------ 规则引擎
def decide_rule(p, others: list, clock_hour: int) -> Decision:
    """数值优先级决策（LLM 不可用时的保底，也是 dry-run 模式的引擎）

    优先级链：垂死→饥饿→困倦→没钱→心情差→夜间→随机生活
    """
    s: Stats = p.stats
    name = p.name

    # 1. 濒死 → 治疗
    if s.hp < 35 and s.money >= 15:
        return Decision(action="treat", reason="我快不行了，得去医院",
                        source="rule")
    # 2. 饿 → 找吃的（有钱咖啡馆，没钱厨房做）
    if s.satiety < 30:
        if s.money >= 8:
            return Decision(action="eat", reason="肚子饿了", source="rule")
        return Decision(action="cook", reason="没钱下馆子，自己做", source="rule")
    # 3. 困 → 睡觉（夜间阈值放宽）
    threshold = 30 if act.is_night(clock_hour) else 18
    if s.energy < threshold or (act.is_night(clock_hour) and s.energy < 45):
        return Decision(action="sleep", reason="困了，回去睡觉", source="rule")
    # 4. 穷 → 工作
    if s.money < 20:
        return Decision(action="work", reason="手头紧，去赚点钱", source="rule")
    # 5. 心情差 → 分流：随机宣泄（破坏/攻击）或娱乐
    if s.mood < 25:
        roll = random.random()
        if roll < 0.15 and others:
            victim = random.choice(others)
            return Decision(action="attack", target=victim.name,
                            reason="心情糟透了，想找人出气", source="rule")
        if roll < 0.30:
            return Decision(action="destroy", reason="心里憋屈，想砸点什么",
                            source="rule")
        return Decision(action="watch_tv",
                        reason="心情不好，看电视散心", source="rule")
    # 6. 夜间没困 → 打游戏/睡觉二选一
    if act.is_night(clock_hour):
        return random.choice([
            Decision(action="game", reason="夜深了，打会儿游戏", source="rule"),
            Decision(action="sleep", reason="夜深了，睡觉", source="rule"),
        ])
    # 7. 社交窗口：白天 1/4 概率找人聊天/恋爱
    if others and random.random() < 0.25:
        # 挑好感最高的对象
        best = max(others, key=lambda o: p.affinity.get(o.name, 30))
        if p.affinity.get(best.name, 30) >= act.ACTIVITIES["love"]["consent_threshold"]:
            return Decision(action="love", target=best.name,
                            reason=f"我很喜欢{best.name}", source="rule")
        return Decision(action="chat", target=best.name,
                        reason="找人说说话", source="rule")
    # 8. 常规生活轮盘
    pool = ["work", "exercise", "think", "wander", "shop",
            "watch_tv", "cook", "eat"]
    weights = [3 if s.money < 50 else 1,            # work：缺钱多工作
               1,                                    # exercise
               2 if s.mood < 60 else 1,              # think
               1,                                    # wander
               1,                                    # shop
               2 if s.mood < 50 else 1,              # watch_tv
               1,                                    # cook
               2 if s.satiety < 60 else 1]           # eat
    action = random.choices(pool, weights=weights, k=1)[0]
    return Decision(action=action, reason="过日子", source="rule")
