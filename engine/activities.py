"""活动注册表 —— 14+ 种活动的元数据与数值效果

设计约定：
- 每个活动: key(英文)/name(中文)/emoji/duration(tick数)/need(需求类型)
- need 三种: None=任意位置 / "facility:字符"=需站在对应设施 / "player"=需紧邻目标玩家
- effects: 每 tick 结算一次的数值变化（乘 duration 后为总收益）
- can_do: 运行时检查（钱够不够、对象是否 consenting 等）
"""
from __future__ import annotations

from .models import Stats

# ------------------------------------------------------------------ 被动消耗
# 每个 tick（游戏内10分钟）的自然消耗
PASSIVE_DRAIN = Stats(satiety=-0.8, energy=-0.35, mood=-0.1, hp=0.0, money=0.0)
MOVE_EXTRA_ENERGY = -0.25      # 移动额外耗能
STARVE_HP_DAMAGE = -2.0        # 饱食度为 0 时每 tick 掉血


def _need_money(stats: Stats, cost: float) -> bool:
    return stats.money >= cost


# ------------------------------------------------------------------ 活动表
ACTIVITIES: dict[str, dict] = {
    # ---- 饮食 ----
    "eat": {
        "name": "吃饭", "emoji": "🍚", "duration": 3, "need": "facility:C",
        "cost": 8,
        "effects": {"satiety": 14, "mood": 2},
        "desc": "在咖啡馆花 8 元吃一顿，恢复饱食度",
    },
    "cook": {
        "name": "做饭", "emoji": "🍳", "duration": 4, "need": "facility:K",
        "cost": 4,
        "effects": {"satiety": 12, "mood": 3},
        "desc": "在公共厨房自己做，便宜且有成就感",
    },
    "shop": {
        "name": "购物", "emoji": "🛒", "duration": 2, "need": "facility:S",
        "cost": 10,
        "effects": {"mood": 4, "satiety": 4},
        "desc": "在商店买些零食杂物，开心一下",
    },
    # ---- 运动与娱乐 ----
    "exercise": {
        "name": "运动", "emoji": "💪", "duration": 3, "need": "facility:G",
        "cost": 0,
        "effects": {"energy": -5, "mood": 4, "hp": 1.2},
        "desc": "健身房锻炼，耗精力但强身健体",
    },
    "watch_tv": {
        "name": "看电视", "emoji": "📺", "duration": 3, "need": "facility:V",
        "cost": 0,
        "effects": {"mood": 3, "energy": 0.8},
        "desc": "电视房放松",
    },
    "game": {
        "name": "打游戏", "emoji": "🎮", "duration": 3, "need": "facility:H",
        "cost": 0,
        "effects": {"mood": 5, "energy": -2},
        "desc": "宅在小屋里打游戏",
    },
    "think": {
        "name": "思考", "emoji": "🤔", "duration": 3, "need": None,
        "cost": 0,
        "effects": {"mood": 2, "energy": -0.5},
        "desc": "在长椅/湖畔/林间发呆沉思（任意地点可行）",
    },
    # ---- 社交 ----
    "chat": {
        "name": "聊天", "emoji": "💬", "duration": 3, "need": "player",
        "cost": 0,
        "effects": {"mood": 2.5},
        "affinity": 2.0,          # 双方互相好感提升
        "desc": "找一个人聊天（需在对方旁边）",
    },
    "love": {
        "name": "恋爱", "emoji": "💗", "duration": 3, "need": "player",
        "cost": 0,
        "effects": {"mood": 6},
        "affinity": 4.0,
        "consent_threshold": 55,  # 双方好感都需达到
        "desc": "向好感度足够的人表白/约会",
    },
    # ---- 冲突 ----
    "attack": {
        "name": "攻击", "emoji": "⚔️", "duration": 1, "need": "player",
        "cost": 0,
        "effects": {"mood": 1, "energy": -4},
        "damage": 16.0,
        "affinity": -12.0,
        "desc": "攻击一名玩家（对方掉血、好感暴跌）",
    },
    "destroy": {
        "name": "破坏", "emoji": "💥", "duration": 2, "need": "facility:ANY",
        "cost": 0,
        "effects": {"mood": 4, "energy": -6},
        "desc": "破坏脚下的公共设施（该地块损坏若干小时）",
    },
    # ---- 生产与建设 ----
    "work": {
        "name": "工作", "emoji": "💼", "duration": 4, "need": "facility:F|O",
        "cost": 0,
        "effects": {"energy": -3, "mood": -1, "money": 6},
        "desc": "田地劳作或办公室办公，赚的钱不一样多",
    },
    "build": {
        "name": "建筑", "emoji": "🔨", "duration": 5, "need": "facility:~",
        "cost": 0,
        "effects": {"energy": -4, "mood": 1.5, "money": 7},
        "desc": "在施工地干活，工钱高但很累；也能修复被破坏的地面",
    },
    # ---- 恢复 ----
    "sleep": {
        "name": "睡觉", "emoji": "😴", "duration": 6, "need": "facility:H",
        "cost": 0,
        "effects": {"energy": 7, "hp": 0.6, "mood": 0.5},
        "desc": "回小屋睡觉（夜间睡眠效率更高）",
    },
    "treat": {
        "name": "治疗", "emoji": "🏥", "duration": 3, "need": "facility:B",
        "cost": 15,
        "effects": {"hp": 14, "mood": 1},
        "desc": "去医院治疗（花费 15 元）；也可以紧邻队友为其治疗",
    },
    # ---- 移动 ----
    "wander": {
        "name": "闲逛", "emoji": "🚶", "duration": 2, "need": None,
        "cost": 0,
        "effects": {"mood": 0.5},
        "desc": "在镇上随机走走",
    },
}

# 昼夜加成：夜间睡觉额外恢复
NIGHT_BONUS = {"sleep": {"energy": 3.0}}
NIGHT_HOURS = (22, 6)   # 22:00 ~ 次日 06:00


def is_night(hour: int) -> bool:
    return hour >= NIGHT_HOURS[0] or hour < NIGHT_HOURS[1]


def money_cost(key: str) -> float:
    return float(ACTIVITIES.get(key, {}).get("cost", 0))


def facility_chars(key: str) -> list[str]:
    """活动允许的设施字符列表（'F|O' → ['F','O']；ANY → 所有可破坏设施）"""
    need = ACTIVITIES.get(key, {}).get("need") or ""
    if not need.startswith("facility:"):
        return []
    spec = need.split(":", 1)[1]
    if spec == "ANY":
        return ["C", "K", "G", "V", "S", "P", "~"]   # 医院与小屋不可破坏
    return spec.split("|")


def activity_menu() -> str:
    """生成给 LLM 看的活动菜单（紧凑格式）"""
    lines = []
    for k, a in ACTIVITIES.items():
        cost = f", 花费{a['cost']}元" if a.get("cost") else ""
        need = {"player": ", 需站在某玩家旁(填target)"}.get(a.get("need", ""), "")
        lines.append(f"- {k}: {a['name']}{cost}{need}")
    return "\n".join(lines)
