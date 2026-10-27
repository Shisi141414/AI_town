"""决策层 v2 —— 可编辑提示词 + 身份自选 + LLM 好感增减

三大新机制：
1. 统一提示词可由用户在网页上编辑（存 config.system_prompt，空则用默认）
2. 开局身份阶段：每个玩家的模型自选名字（小x 格式）与颜色（调色板）
3. 互动后好感结算：不再纯规则——LLM 以该玩家身份决定对互动对象的好感增减
   （决定权在模型，规则只给边界：-5 ~ +5）
"""
from __future__ import annotations

import json
import random
import re

from . import activities as act
from . import world
from .llm import LLMError, chat
from .models import ApiProfile, Decision

# ================================================================== 默认提示词
DEFAULT_SYSTEM_PROMPT = """你是一个和几位室友合租的普通年轻人，住在一个小镇上，自主决定自己的生活。
你的身体状态、记忆和人际关系会实时变化，请根据它们做出当下最合理的决定。
没有标准答案，像活人一样权衡：饿了要吃饭、累了要休息、缺钱要工作、
也可以娱乐、社交、恋爱，甚至发脾气搞破坏——一切由你。
和室友处好关系有实际好处：一起吃饭/打游戏/工作都有加成，关系差则会互相拖累。"""

USER_TEMPLATE = """【现在是第{day}天 {clock}（{daypart}），你在{scene_name}】
【我的状态】生命{hp:.0f}/100 饱食{satiety:.0f}/100 精力{energy:.0f}/100 心情{mood:.0f}/100 金钱{money:.0f}元
【我的位置】{location}
【我的物品】{items}
【身边的人（同一区域）】{nearby}
【我对别人的好感（0~100）】{affinity}
【家里公共区域装饰度】{decor_common:.0f}（越高大家心情越好）
【我最近的记忆】
{memories}
【可选行动】（场景不符的行动需要先出门走到对应地点）
{menu}

请决定接下来做什么，只输出一行 JSON（不要markdown代码块）：
{{"action": "动作key", "target": "目标玩家名或空", "reason": "15字内的理由"}}"""

DAYPARTS = ((5, "凌晨"), (8, "早晨"), (11, "上午"), (13, "中午"),
            (17, "下午"), (20, "傍晚"), (23, "晚上"), (25, "深夜"))

JSON_FIX_PROMPT = ("你上次的输出无法解析。请严格只输出一行 "
                   "{{\"action\":..., \"target\":..., \"reason\":...}} JSON，不要任何其他文字。")

# ================================================================== 身份选择
IDENTITY_TEMPLATE = """你即将入住一个合租之家，开始自己在小镇的生活。
请给自己起个名字并选一个代表色。

要求：
- 名字必须是「小+一个汉字」，如：小满、小舟、小鹿、小石（体现你想要的气质）
- 颜色从以下选项中选一个：{palette}

只输出一行 JSON：{{"name": "小x", "color": "#hex", "why": "10字内的理由"}}"""

PALETTE = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
           "#3498db", "#9b59b6", "#e84393", "#636e72", "#2d3436"]

IDENTITY_EMOJI = ["🧑‍🎓", "👩‍🎨", "🧑‍🌾", "👩‍🔬", "🧑‍🍳", "🧑‍🚀", "👩‍⚕️", "🧑‍🎸", "🐱", "🦊"]


def daypart(hour: int) -> str:
    for start, name in DAYPARTS:
        if hour < start:
            return name
    return "深夜"


def get_system_prompt(config_system: str = "") -> str:
    return config_system.strip() or DEFAULT_SYSTEM_PROMPT


def build_user_prompt(p, nearby: list[str], clock: str, decor_common: float = 0.0) -> str:
    aff = "、".join(f"{name}{v:.0f}" for name, v in
                    sorted(p.affinity.items(), key=lambda kv: -kv[1])[:6]) or "（还不认识谁）"
    nearby_s = "、".join(nearby) if nearby else "（附近没人）"
    items = "、".join(f"{act.ITEMS[k]['name']}×{v}" for k, v in p.items.items() if v > 0) or "无"
    mem = p.memory.brief(p.world_tick, k=8)
    menu = act.activity_menu(p.scene)
    return USER_TEMPLATE.format(
        day=p.day, clock=clock, daypart=daypart(p.hour),
        scene_name=world.SCENE_NAMES.get(p.scene, p.scene),
        hp=p.stats.hp, satiety=p.stats.satiety,
        energy=p.stats.energy, mood=p.stats.mood, money=p.stats.money,
        location=world.zone_of(p.scene, p.x, p.y),
        items=items, nearby=nearby_s, affinity=aff,
        decor_common=decor_common, memories=mem, menu=menu)


def parse_decision(text: str) -> Decision | None:
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
    raw = raw.strip().lower()
    if raw in act.ACTIVITIES:
        return raw
    for k, a in act.ACTIVITIES.items():
        if a["name"] == raw or a["emoji"] == raw:
            return k
    return ""


def _run_in_thread(func, *args):
    import asyncio
    return asyncio.get_event_loop().run_in_executor(None, func, *args)


async def decide_llm(profile: ApiProfile, model: str, p, nearby: list[str],
                     clock: str, system_prompt: str, temperature: float,
                     timeout: float, decor_common: float = 0.0) -> Decision:
    user = build_user_prompt(p, nearby, clock, decor_common)
    last_err: Exception | None = None
    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\n" + JSON_FIX_PROMPT
        text = await _run_in_thread(chat, profile, model, system_prompt, prompt,
                                    temperature, 250, timeout)
        d = parse_decision(text)
        if d is None:
            last_err = LLMError(f"无法解析: {text[:120]}")
            continue
        d.action = _norm_action(d.action)
        if d.action:
            return d
        last_err = LLMError(f"未知动作: {d.action}")
    raise last_err or LLMError("决策失败")


async def choose_identity_llm(profile: ApiProfile, model: str, taken_names: list[str],
                              timeout: float = 30.0) -> dict | None:
    """开局身份：LLM 选名字与颜色。失败返回 None（由调用方用随机预设）。"""
    prompt = IDENTITY_TEMPLATE.format(
        palette="、".join(PALETTE),
    )
    if taken_names:
        prompt += f"\n\n注意：这些名字已被室友占用，不能重复：{'、'.join(taken_names)}"
    try:
        text = await _run_in_thread(chat, profile, model,
                                    "你在给自己起名字，输出必须符合格式要求。",
                                    prompt, 1.0, 120, timeout)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
        name = str(d.get("name", "")).strip()
        color = str(d.get("color", "")).strip()
        # 校验：小x 格式
        if not re.fullmatch(r"小[一-鿿]", name):
            return None
        if color not in PALETTE:
            color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else None
        if color is None:
            color = random.choice(PALETTE)
        return {"name": name, "color": color,
                "why": str(d.get("why", ""))[:20]}
    except Exception:                        # noqa: BLE001
        return None


# ================================================================== 好感增减
AFFINITY_TEMPLATE = """你是{self_name}。你刚刚和{other_name}一起{what}（{detail}）。
在此之前你对 TA 的好感是 {before:.0f}/100。
结合你自己的记忆和性格，现在你对 TA 的好感会变化多少？

要求：输出 -5 到 +5 之间的整数（可以是0）。只输出一个数字。"""


async def affinity_change_llm(profile: ApiProfile, model: str, self_name: str,
                              other_name: str, what: str, detail: str,
                              before: float, timeout: float = 20.0) -> int:
    """互动后由模型决定好感增减。失败返回 0（不变）。"""
    prompt = AFFINITY_TEMPLATE.format(self_name=self_name, other_name=other_name,
                                      what=what, detail=detail, before=before)
    try:
        text = await _run_in_thread(chat, profile, model,
                                    "你在一个模拟游戏中扮演角色的内心，只输出一个数字。",
                                    prompt, 0.8, 10, timeout)
        m = re.search(r"-?\d+", text)
        if not m:
            return 0
        v = int(m.group(0))
        return max(-5, min(5, v))
    except Exception:                        # noqa: BLE001
        return 0


# ================================================================== 回合制对话
CONV_TURN_TEMPLATE = """【背景】你是{self_name}，正在和{other_name}面对面{kind}。
【你对TA的好感】{aff:.0f}/100
【你的状态】心情{mood:.0f}/100 精力{energy:.0f}/100
【对话记录】（"我"是你的话，"{other_name}"是TA的话）
{history}

现在轮到你说话。你可以：
1. 回应TA说的内容——要针对TA刚才那句话，不要自说自话
2. 结束对话离开（你不想聊了，说句告别的话）
3. 如果TA刚才说"要走了"，你也可以挽留或跟上TA继续聊

注意：不要重复自己或TA说过的原话；一句即可，15字以内。
只输出一行 JSON：
{{"say": "你要说的话", "act": "stay 或 leave 或 follow"}}"""

CONV_KIND_CN = {"chat": "聊天", "love": "约会", "follow": "边走边聊"}

# 规则模式对话库（无 API 时兜底）
RULE_OPENERS = ["今天过得怎么样？", "晚饭吃什么好呢", "周末有什么打算吗",
                "最近有点累啊", "阳台的花该浇水了", "你昨晚打游戏到几点？",
                "这条消息看到了吗", "天气真不错"]
RULE_REPLIES = ["嗯嗯，我也是这么想的", "哈哈，确实", "有道理",
                "然后呢？后来怎么样了", "唉，最近事情好多", "说得对",
                "我没意见，都行", "下次一起呗"]
RULE_CLOSERS = ["我先去忙了，回头聊", "好吧，那我先走了", "就聊到这吧，拜拜",
                "想起来我还有事", "改天再聊"]


def parse_conv_turn(text: str) -> dict | None:
    """解析对话回合输出 {"say","act"}；宽容处理纯文本回答"""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            act = str(d.get("act", "stay")).strip().lower()
            if act not in ("stay", "leave", "follow"):
                act = "stay"
            say = str(d.get("say", "")).strip()[:40]
            if act == "stay" and not say:
                return None
            return {"say": say, "act": act}
        except json.JSONDecodeError:
            pass
    # 纯文本：整句当作说话
    if text and len(text) <= 40:
        return {"say": text, "act": "stay"}
    return None


async def conv_turn_llm(profile: ApiProfile, model: str, self_name: str,
                        other_name: str, kind: str, aff: float, mood: float,
                        energy: float, history: list[tuple[str, str]],
                        timeout: float = 25.0) -> dict:
    """一次对话回合：模型看着完整对话历史决定说什么/是否离开。
    失败抛 LLMError（由 Conversation 降级规则库）。"""
    hist_lines = []
    for speaker, line in history[-12:]:      # 最近12条，防超长
        who = "我" if speaker == self_name else speaker
        hist_lines.append(f"{who}：{line}")
    history_s = "\n".join(hist_lines) if hist_lines else "（刚开始，还没有说过话）"
    kind_cn = CONV_KIND_CN.get(kind, "聊天")
    prompt = CONV_TURN_TEMPLATE.format(
        self_name=self_name, other_name=other_name, kind=kind_cn,
        aff=aff, mood=mood, energy=energy, history=history_s)
    text = await _run_in_thread(chat, profile, model,
                                "你在一个生活模拟游戏里扮演角色的对话，"
                                "说话要自然口语化，符合当前语境。只输出要求的 JSON。",
                                prompt, 0.9, 120, timeout)
    d = parse_conv_turn(text)
    if d is None:
        raise LLMError(f"对话回合无法解析: {text[:80]}")
    return d


# ================================================================== 规则引擎
def decide_rule(p, others: list, clock_hour: int) -> Decision:
    s = p.stats

    if s.hp < 35 and s.money >= 15:
        return Decision(action="treat", reason="我快不行了，得去医院", source="rule")
    if s.satiety < 30:
        # 在家→厨房做/餐厅吃；在镇上→咖啡馆
        if p.scene == "house":
            return Decision(action="cook", reason="饿了，自己做点吃的", source="rule")
        return Decision(action="eat_out", reason="饿了，下馆子", source="rule")
    threshold = 30 if act.is_night(clock_hour) else 18
    if s.energy < threshold or (act.is_night(clock_hour) and s.energy < 45):
        if p.scene == "house":
            return Decision(action="sleep", reason="困了，回房睡觉", source="rule")
        # 夜里在镇上：回家睡觉（寻路自动经大门）
        return Decision(action="sleep", reason="夜深了，回家睡觉", source="rule")
    # 夜间不困但在室外 → 也回家（家里有电视游戏）
    if act.is_night(clock_hour) and p.scene == "town" and random.random() < 0.6:
        return Decision(action="game", reason="回家打游戏", source="rule")
    if s.money < 20:
        return Decision(action="work", reason="手头紧，去赚点钱", source="rule")
    if s.mood < 25:
        roll = random.random()
        if roll < 0.15 and others:
            victim = random.choice(others)
            return Decision(action="attack", target=victim.name,
                            reason="心情糟透了，想找人出气", source="rule")
        if roll < 0.30:
            return Decision(action="destroy", reason="心里憋屈，想砸点什么", source="rule")
        return Decision(action="watch_tv" if p.scene == "house" else "park",
                        reason="心情不好，散散心", source="rule")
    if act.is_night(clock_hour):
        if p.scene == "house":
            # 有花/盆栽且没布置过 → 优先布置
            if (p.items.get("flower", 0) + p.items.get("plant", 0)) > 0:
                return Decision(action="decorate_room", reason="睡前把房间弄漂亮点",
                                source="rule")
            return random.choice([
                Decision(action="game", reason="夜深了，打会儿游戏", source="rule"),
                Decision(action="sleep", reason="夜深了，睡觉", source="rule"),
            ])
        return Decision(action="wander", reason="夜里在外面走走", source="rule")
    # 有礼物潜力：好感>65 且有花 → 送礼
    if others and (p.items.get("flower", 0) > 0 or p.items.get("plant", 0) > 0):
        best = max(others, key=lambda o: p.affinity.get(o.name, 30))
        if p.affinity.get(best.name, 30) > 65:
            return Decision(action="gift", target=best.name,
                            reason=f"想送{best.name}个礼物", source="rule")
    # 社交窗口
    if others and random.random() < 0.25:
        best = max(others, key=lambda o: p.affinity.get(o.name, 30))
        if p.affinity.get(best.name, 30) >= act.ACTIVITIES["love"]["consent_threshold"]:
            return Decision(action="love", target=best.name,
                            reason=f"我很喜欢{best.name}", source="rule")
        return Decision(action="chat", target=best.name,
                        reason="找人说说话", source="rule")
    # 常规轮盘（按场景）
    if p.scene == "house":
        pool = ["work", "exercise", "think", "wander", "shop",
                "watch_tv", "cook", "laundry", "shower", "balcony", "movie"]
        weights = [2, 1, 1, 1, 1,
                   3, 2, 1, 1, 2, 1]
    else:
        pool = ["work", "exercise", "think", "wander", "shop",
                "movie", "park", "eat_out"]
        weights = [3, 1, 1, 1, 1,
                   2, 2, 1]
    # 跨场景加权：长时间没出门→加户外项权重
    if p.scene == "house":
        out_idx = [0, 1, 4, 10]              # work/exercise/shop/movie
        for i in out_idx:
            weights[i] += (1 if p.world_tick - p.last_out_tick > 60 else 0)
    else:
        home_idx = [7]
        for i in home_idx:
            weights[i] += (1 if p.world_tick - p.last_home_tick > 60 else 0)
    action = random.choices(pool, weights=weights, k=1)[0]
    return Decision(action=action, reason="过日子", source="rule")
