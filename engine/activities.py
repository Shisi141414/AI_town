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
PASSIVE_DRAIN = {"satiety": -0.4, "energy": -0.35, "mood": -1.0}
LOW_ENERGY_MOOD_THRESHOLD = 20.0
LOW_MOOD_HP_THRESHOLD = 20.0
LOW_MOOD_HP_DELAY_TICKS = 18  # 连续3小时，每刻10分钟。
LOW_MOOD_HP_LOSS = 0.5


def relationship_mood_per_tick(affinity: float, rules=None) -> float:
    """单向好感对本人心情的影响；锚点：80→+6，0→−5。"""
    score = max(0.0, min(100.0, affinity))
    from .mechanics import DEFAULT_RULES
    r=rules or DEFAULT_RULES
    if score > r['positive_affinity_threshold']:
        span=max(1,80-r['positive_affinity_threshold'])
        return r['positive_mood_at_threshold']+(score-r['positive_affinity_threshold'])*(r['positive_mood_at_80']-r['positive_mood_at_threshold'])/span
    if score < r['negative_affinity_threshold']:
        span=max(1,r['negative_affinity_threshold'])
        return -(r['negative_mood_at_threshold']+(r['negative_affinity_threshold']-score)*(r['negative_mood_at_0']-r['negative_mood_at_threshold'])/span)
    return 0.0
MOVE_EXTRA_ENERGY = -0.25

# ------------------------------------------------------------------ 物品
ITEMS: dict[str, dict] = {
    "flower": {"name": "鲜花", "emoji": "💐", "price": 12, "decor": 6},
    "plant":  {"name": "盆栽", "emoji": "🪴", "price": 20, "decor": 10},
    "meal":   {"name": "家常饭菜", "emoji": "🍱", "price": 0, "decor": 0},
    "medicine": {"name": "药品", "emoji": "💊", "price": 25, "decor": 0},
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
        "desc": "在餐厅吃饭（需先有饭：做饭后60 tick内有效，否则去饭店）",
    },
    "eat_out": {
        "name": "下馆子", "emoji": "🍜", "duration": 3, "need": "facility:R",
        "scene": "town", "cost": 10,
        "effects": {"satiety": 15, "mood": 3},
        "synergy": True, "synergy_stat": "mood",
        "desc": "去镇上饭店吃饭（花 10 元，不用自己做）",
    },
    "watch_tv": {
        "name": "看电视", "emoji": "📺", "duration": 3, "need": "facility:L",
        "scene": "house", "cost": 0,
        "effects": {"mood": 3, "energy": 0.8},
        "synergy": True, "synergy_stat": "mood",
        "desc": "客厅大沙发看电视，人多更好看",
    },
    "computer": {
        "name":"使用自己的电脑记录", "emoji":"💻", "duration":1, "need":"own_computer",
        "scene":"house", "cost":0, "effects":{"energy":-0.3},
        "desc":"在自己电脑创建、编辑、查看或删除文字记录，可以保存怕忘掉的经历",
    },
    "computer_game": {
        "name":"电脑游戏", "emoji":"🎮", "duration":3, "need":"own_computer",
        "scene":"house", "cost":0, "effects":{"mood":5,"energy":-2},
        "desc":"回自己的电脑前打游戏，可指定游戏时长",
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
        "effects": {"mood": 2, "energy": 3},
        "desc": "原地沉思（任意地点），恢复精力",
    },
    "wander": {
        "name": "闲逛", "emoji": "🚶", "duration": 2, "need": None,
        "scene": "any", "cost": 0,
        "effects": {"mood": 0.5},
        "desc": "附近走走",
    },
    # ============ 社交（需目标玩家） ============
    "chat": {
        "name": "对话", "emoji": "💬", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {},
        "desc": "与身边的人对话：交流信息、分享想法、商量事情或增进了解；对话本身不产生数值变化，10轮以内不消耗时间",
    },
    "phone": {
        "name": "打电话", "emoji": "📞", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {},
        "desc": "给室友打电话，可跨地点对话（交流信息、商量事情，无数值变化），拨打者按通话轮数支付话费",
    },
    "love": {
        "name": "恋爱", "emoji": "💗", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 6},
        "consent_threshold": 55,
        "desc": "向好感足够的人表白/约会",
    },
    "sex": {
        "name": "做爱", "emoji": "💕", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 3, "energy": -1.5},
        "desc": "双方分别选择同意后进行；可选自己的床或当前位置",
    },
    "gift": {
        "name": "送礼", "emoji": "🎁", "duration": 1, "need": "player",
        "scene": "any", "cost": 0, "needs_item": "any",
        "effects": {"mood": 1},
        "desc": "把手里的鲜花/盆栽送给对方（大幅提升好感）",
    },
    "give_meal": {
        "name":"送饭菜", "emoji":"🍱", "duration":1, "need":"player",
        "scene":"any", "cost":0, "needs_item":"meal", "effects":{"mood":1},
        "desc":"把做好的饭菜送给附近室友，对方当场吃掉",
    },
    "use_medicine": {
        "name":"给室友用药", "emoji":"💊", "duration":1, "need":"player",
        "scene":"any", "cost":0, "needs_item":"medicine", "effects":{"energy":-0.5},
        "desc":"在室友身边用药品疗伤或缓解疾病",
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
        return ["L", "K", "D", "w", "a", "b", "S", "P", "O", "M", "G", "C", "F", "R", "U"]
    return spec.split("|")


def activity_description_defaults() -> dict[str, str]:
    """模型行动目录中的默认文字；用户可逐项覆盖，不改变行动规则。"""
    from .town_services import DEFS, NAMES
    effects = {
        "cook":"在家中厨房；备餐，完成后得到可赠送的饭菜", "eat":"在家中餐厅；吃随身饭菜或家中饭菜，恢复饱食",
        "watch_tv":"在家中客厅沙发；放松心情", "computer":"在自己卧室电脑前；保存、查看和修改自己的记录",
        "computer_game":"在自己卧室电脑前；娱乐、提升心情", "sleep":"在自己卧室床上；恢复精力与身体",
        "shower":"在家中浴室；放松并恢复精力", "laundry":"在家中洗衣区；洗衣、略有放松",
        "balcony":"在家中阳台；晒太阳、放松", "decorate_room":"在自己卧室；消耗装饰品改善房间",
        "decorate_common":"在家中公共区域；消耗装饰品改善公共环境",
        "think":"在当前位置；整理思绪、放松", "wander":"在当前位置附近；散步、略有放松",
        "chat":"在室友身边；交流信息、分享近况、商量事情（无数值变化，几乎不耗时间）", "phone":"在任意地点；跨地点对话，交流信息（无数值变化，按轮收话费）",
        "love":"在室友身边或床上；约会、增进感情", "sex":"在双方靠近处或床上；亲密相处",
        "gift":"在室友身边；赠送物品、增进感情", "attack":"在室友身边；伤害对方、恶化关系",
        "give_meal":"在室友身边；把饭菜送给对方当场吃", "use_medicine":"在室友身边；消耗药品治疗伤病",
        "destroy":"在公共设施附近；破坏设施、发泄情绪",
        "home_treat":"在家中；用医学技能照护自己或室友", "stock":"在自己卧室电脑前；交易股票，可能盈亏",
    }
    local_effects = {
        "coffee":"喝咖啡，恢复精力与心情，结伴更亲近", "cafe_read":"阅读，恢复心情与精力",
        "eat_out":"吃饭，恢复饱食，结伴增进感情", "treat":"治疗疾病、恢复身体",
        "movie":"观影，提升心情", "exercise":"锻炼，改善身体与心情",
        "park":"散步，放松心情", "fish":"钓鱼，放松心情", "swim":"游泳，锻炼身体并放松",
        "garden_walk":"散步，放松心情", "garden_flowers":"赏花，恢复心情与精力",
        "garden_tend":"园艺，改善身体与心情", "shop":"购物，改善心情",
        "buy_flower":"买鲜花，用于装饰或送礼", "buy_plant":"买盆栽，用于装饰或送礼",
        "buy_medicine":"买药品，带给室友疗伤",
    }
    descriptions = {}
    for key, activity in ACTIVITIES.items():
        if activity.get('legacy'):
            continue
        if key in DEFS:
            code, _, skill, kind = DEFS[key]
            purpose = local_effects.get(key) or (
                "工作、获得工资并积累相关技能经验" if kind == "job" else
                "学习并提高相应技能" if kind == "course" else
                "学习驾驶，取得驾照" if kind == "license_course" else "购买物品")
            descriptions[key] = f"在{NAMES[code]}；{purpose}"
        else:
            descriptions[key] = effects.get(key, activity.get('desc', '在相应地点；进行活动'))
    return descriptions


def activity_menu(scene: str = "any", venue_code: str | None = None,
                  descriptions: dict[str, str] | None = None,
                  activities: dict[str, dict] | None = None) -> str:
    """只给行动目录；场所项目需到场后才能看到。"""
    sections = {"室内生活": [], "社交互动": [], "随处行动": [], "小镇日常": [], "当前场所": []}
    from .town_services import DEFS
    defaults = activity_description_defaults()
    descriptions = descriptions or {}
    for k, a in (activities if activities is not None else ACTIVITIES).items():
        if a.get("legacy"):continue
        if k in DEFS and (not venue_code or DEFS[k][0] != venue_code):continue
        if a.get('_venue') and a['_venue']!=venue_code:continue
        if scene != "any" and a.get("scene") not in ("any", scene):
            continue
        req = []
        if a.get("need") == "player":
            req.append("target=室友")
        if a.get("needs_item"):
            req.append("需要鲜花或盆栽" if a['needs_item']=='any' else f"需要{ITEMS[a['needs_item']]['name']}")
        extra = "（"+"；".join(req)+"）" if req else ""
        group = "当前场所" if k in DEFS or a.get('_venue') else ("社交互动" if a.get("need")=="player" else
                "室内生活" if a.get("scene")=="house" else
                "小镇日常" if a.get("scene")=="town" else "随处行动")
        detail = descriptions.get(k, defaults.get(k,a.get('desc','在相应地点；进行活动')))
        sections[group].append(f"- {k}: {a['name']}{extra}｜{detail}")
    return "\n\n".join(f"{title}：\n"+"\n".join(rows) for title,rows in sections.items() if rows)


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


# 新场所与技能活动共享原有行走和时长引擎。
from .town_services import register
register(ACTIVITIES)
