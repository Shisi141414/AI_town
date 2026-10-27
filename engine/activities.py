"""活动注册表 v2 —— 场景化活动 + 物品 + 协同增益

新机制：
1. 每个活动标注 scene（house/town/any），决策时跨场景需先出门
2. 协同增益：同设施内做同一活动的每多 1 人，效果 ×(1+0.35)；
   好感调制：增益倍率乘以好感系数 aff_mod = (aff-50)/50，
   aff=100 → ×1.5，aff=50 → ×1.0，aff=0 → ×0.5（低好感增益变减损时
   由 synergy() 返回负倍率附加）
3. 物品系统：鲜花/盆栽（商店购买），可布置（提升区域装饰度→持续心情加成）
   或送礼（大幅提升好感）
4. 新活动：看电影/洗澡/洗衣/买花/买盆栽/布置房间/布置公共区/送礼
"""
from __future__ import annotations

from .models import Stats

# ------------------------------------------------------------------ 被动消耗
PASSIVE_DRAIN = {"satiety": -0.8, "energy": -0.35, "mood": -0.1}
MOVE_EXTRA_ENERGY = -0.25
STARVE_HP_DAMAGE = -2.0

# ------------------------------------------------------------------ 物品
ITEMS: dict[str, dict] = {
    "flower": {"name": "鲜花", "emoji": "💐", "price": 12, "decor": 6},
    "plant":  {"name": "盆栽", "emoji": "🪴", "price": 20, "decor": 10},
}

# ------------------------------------------------------------------ 活动表
ACTIVITIES: dict[str, dict] = {
    # ============ 室内（house） ============
    "cook": {
        "name": "做饭", "emoji": "🍳", "duration": 4, "need": "facility:K",
        "scene": "house", "cost": 4,
        "effects": {"satiety": 14, "mood": 3},
        "synergy": True, "synergy_stat": "mood",
        "desc": "在厨房做饭（便宜且能和室友一起做）",
    },
    "eat": {
        "name": "在家吃饭", "emoji": "🍚", "duration": 3, "need": "facility:D",
        "scene": "house", "cost": 0,
        "effects": {"satiety": 13, "mood": 2},
        "synergy": True, "synergy_stat": "mood",
        "desc": "在餐厅吃饭（需先有饭：做饭后60 tick内有效，否则去咖啡馆）",
    },
    "eat_out": {
        "name": "下馆子", "emoji": "🍜", "duration": 3, "need": "facility:C",
        "scene": "town", "cost": 10,
        "effects": {"satiety": 15, "mood": 3},
        "synergy": True, "synergy_stat": "mood",
        "desc": "去镇上咖啡馆吃饭（花 10 元，不用自己做）",
    },
    "watch_tv": {
        "name": "看电视", "emoji": "📺", "duration": 3, "need": "facility:L",
        "scene": "house", "cost": 0,
        "effects": {"mood": 3, "energy": 0.8},
        "synergy": True, "synergy_stat": "mood",
        "desc": "客厅大沙发看电视，人多更好看",
    },
    "game": {
        "name": "打游戏", "emoji": "🎮", "duration": 3, "need": "facility:L",
        "scene": "house", "cost": 0,
        "effects": {"mood": 5, "energy": -2},
        "synergy": True, "synergy_stat": "mood",
        "desc": "客厅打游戏，多人组队快乐加倍",
    },
    "sleep": {
        "name": "睡觉", "emoji": "😴", "duration": 6, "need": "facility:h",
        "scene": "house", "cost": 0,
        "effects": {"energy": 7, "hp": 0.6, "mood": 0.5},
        "night_bonus": {"energy": 3.0},
        "desc": "回自己卧室睡觉（夜间恢复更高）",
    },
    "shower": {
        "name": "洗澡", "emoji": "🚿", "duration": 2, "need": "facility:b",
        "scene": "house", "cost": 0,
        "effects": {"mood": 3, "energy": 1.5},
        "desc": "洗个热水澡放松",
    },
    "laundry": {
        "name": "洗衣", "emoji": "🧺", "duration": 3, "need": "facility:w",
        "scene": "house", "cost": 0,
        "effects": {"mood": 1, "energy": -1},
        "desc": "用共用洗衣机洗衣服",
    },
    "balcony": {
        "name": "阳台发呆", "emoji": "🌿", "duration": 3, "need": "facility:a",
        "scene": "house", "cost": 0,
        "effects": {"mood": 3, "energy": 0.5},
        "synergy": True, "synergy_stat": "mood",
        "desc": "在大阳台吹风看景",
    },
    "decorate_room": {
        "name": "布置房间", "emoji": "✨", "duration": 2, "need": "facility:h",
        "scene": "house", "cost": 0, "needs_item": "any",
        "effects": {"mood": 4},
        "desc": "用自己的鲜花/盆栽装饰卧室（消耗物品，提升房间装饰度）",
    },
    "decorate_common": {
        "name": "布置公共区", "emoji": "🌸", "duration": 2, "need": "any_house_common",
        "scene": "house", "cost": 0, "needs_item": "any",
        "effects": {"mood": 3},
        "desc": "把鲜花/盆栽放到客厅/餐厅/厨房/阳台（大家都能享受装饰加成）",
    },
    # ============ 室外（town） ============
    "work": {
        "name": "工作", "emoji": "💼", "duration": 4, "need": "facility:O|F",
        "scene": "town", "cost": 0,
        "effects": {"energy": -3, "mood": -1, "money": 7},
        "synergy": True, "synergy_stat": "money",
        "desc": "去办公楼上班或田地劳作（同事一起干，效率更高）",
    },
    "build": {
        "name": "建筑", "emoji": "🔨", "duration": 5, "need": "facility:~",
        "scene": "town", "cost": 0,
        "effects": {"energy": -4, "mood": 1.5, "money": 8},
        "synergy": True, "synergy_stat": "money",
        "desc": "在施工地干活，工钱高但累",
    },
    "exercise": {
        "name": "运动", "emoji": "💪", "duration": 3, "need": "facility:G",
        "scene": "town", "cost": 0,
        "effects": {"energy": -5, "mood": 4, "hp": 1.2},
        "synergy": True, "synergy_stat": "mood",
        "desc": "健身房锻炼",
    },
    "movie": {
        "name": "看电影", "emoji": "🎬", "duration": 3, "need": "facility:M",
        "scene": "town", "cost": 12,
        "effects": {"mood": 6, "energy": -0.5},
        "synergy": True, "synergy_stat": "mood",
        "desc": "去电影院看电影（12元，结伴观影效果更佳）",
    },
    "park": {
        "name": "逛公园", "emoji": "🌳", "duration": 3, "need": "facility:P",
        "scene": "town", "cost": 0,
        "effects": {"mood": 3.5, "energy": -0.5},
        "synergy": True, "synergy_stat": "mood",
        "desc": "公园散步，遇上熟人一起逛更惬意",
    },
    "shop": {
        "name": "购物", "emoji": "🛒", "duration": 2, "need": "facility:S",
        "scene": "town", "cost": 6,
        "effects": {"mood": 3},
        "desc": "商店买零食杂物（6元）",
    },
    "buy_flower": {
        "name": "买鲜花", "emoji": "💐", "duration": 1, "need": "facility:S",
        "scene": "town", "cost": 12,
        "effects": {"mood": 2},
        "gives_item": "flower",
        "desc": "买一束鲜花（12元）：可布置房间/公共区，或作为礼物送人",
    },
    "buy_plant": {
        "name": "买盆栽", "emoji": "🪴", "duration": 1, "need": "facility:S",
        "scene": "town", "cost": 20,
        "effects": {"mood": 2},
        "gives_item": "plant",
        "desc": "买一盆绿植（20元）：装饰度更高",
    },
    "think": {
        "name": "思考", "emoji": "🤔", "duration": 3, "need": None,
        "scene": "any", "cost": 0,
        "effects": {"mood": 2, "energy": -0.5},
        "desc": "原地沉思（任意地点）",
    },
    "wander": {
        "name": "闲逛", "emoji": "🚶", "duration": 2, "need": None,
        "scene": "any", "cost": 0,
        "effects": {"mood": 0.5},
        "desc": "附近走走",
    },
    # ============ 社交（需目标玩家） ============
    "chat": {
        "name": "聊天", "emoji": "💬", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 2.5},
        "desc": "找身边的人聊天",
    },
    "love": {
        "name": "恋爱", "emoji": "💗", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 6},
        "consent_threshold": 55,
        "desc": "向好感足够的人表白/约会",
    },
    "gift": {
        "name": "送礼", "emoji": "🎁", "duration": 1, "need": "player",
        "scene": "any", "cost": 0, "needs_item": "any",
        "effects": {"mood": 1},
        "desc": "把手里的鲜花/盆栽送给对方（大幅提升好感）",
    },
    "attack": {
        "name": "攻击", "emoji": "⚔️", "duration": 1, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 1, "energy": -4},
        "damage": 16.0,
        "desc": "攻击一名玩家（对方掉血、好感暴跌）",
    },
    "destroy": {
        "name": "破坏", "emoji": "💥", "duration": 2, "need": "facility:ANY",
        "scene": "any", "cost": 0,
        "effects": {"mood": 4, "energy": -6},
        "desc": "破坏脚边的公共设施",
    },
    # ============ 治疗 ============
    "treat": {
        "name": "治疗", "emoji": "🏥", "duration": 3, "need": "facility:B",
        "scene": "town", "cost": 15,
        "effects": {"hp": 14, "mood": 1},
        "desc": "去医院治疗（15元）",
    },
}

NIGHT_HOURS = (22, 6)


def is_night(hour: int) -> bool:
    return hour >= NIGHT_HOURS[0] or hour < NIGHT_HOURS[1]


def money_cost(key: str) -> float:
    return float(ACTIVITIES.get(key, {}).get("cost", 0))


def facility_chars(key: str) -> list[str]:
    need = ACTIVITIES.get(key, {}).get("need") or ""
    if not need.startswith("facility:"):
        return []
    spec = need.split(":", 1)[1]
    if spec == "ANY":
        return ["L", "K", "D", "w", "a", "b", "S", "P", "O", "M", "G", "C", "F", "~"]
    return spec.split("|")


def activity_menu(scene: str = "any") -> str:
    """给 LLM 的活动菜单（按场景过滤；LLM 会拿到两张图的完整菜单）"""
    lines = []
    for k, a in ACTIVITIES.items():
        if a.get("scene") not in ("any", scene):
            continue
        cost = f", 花费{a['cost']}元" if a.get("cost") else ""
        req = []
        if a.get("need") == "player":
            req.append("需目标在身边(填target)")
        if a.get("needs_item"):
            req.append("需持有鲜花或盆栽")
        if a.get("gives_item"):
            req.append(f"获得{ITEMS[a['gives_item']]['name']}")
        extra = ("；" + "，".join(req)) if req else ""
        lines.append(f"- {k}: {a['name']}{cost}{extra}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 协同增益
def synergy_multiplier(n_participants: int, avg_affinity: float) -> float:
    """协同倍率 = 人数增益 × 好感调制

    人数：1人→1.0，2人→1.35，3人→1.70，4+人→2.05（每多一人 +0.35）
    好感调制（对 synergy_stat 生效）：aff=100 → ×1.5；50 → ×1.0；
      0 → ×0.5；低于 25 时调制后可能 <0.3，此时「增益变减损」由调用方判断
    """
    if n_participants <= 1:
        return 1.0
    people = 1.0 + 0.35 * (n_participants - 1)
    aff_mod = 0.5 + max(0.0, min(100.0, avg_affinity)) / 100.0
    return people * aff_mod


def synergy_effects(base_effects: dict, stat: str, n: int, avg_aff: float,
                    as_delta: bool = False) -> dict:
    """计算协同后的效果。

    返回 dict: {stat: (最终值, 相对单独做的增量)}（as_delta=True 时只有增量）
    低好感减损：若倍率 < 1，单独值会被反向放大——即「还不如一个人做」，
    倍率 ≤ 0.75 时额外附加 stat 的负增量（内讧损耗）。
    """
    out: dict = {}
    mult = synergy_multiplier(n, avg_aff)
    for k, v in base_effects.items():
        solo = v
        if k == stat and mult != 1.0:
            together = v * mult
            # 低好感内讧：倍率 <0.75 时，正向效果大打折扣甚至反转
            if mult < 0.75 and v > 0:
                together = v * mult * 0.5       # 减损：比单独差明显一截
            out[k] = (together, together - solo)
        else:
            out[k] = (solo, 0.0)
    return out if not as_delta else {k: d for k, (_, d) in out.items()}
