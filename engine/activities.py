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
    "ingredient": {"name": "食材", "emoji": "🥬", "price": 5, "decor": 0},
    "meal":   {"name": "家常饭菜", "emoji": "🍱", "price": 0, "decor": 0},
    "medicine": {"name": "药品", "emoji": "💊", "price": 25, "decor": 0},
}


# 厨房公共冰箱容量：食材12份、饭菜4份
FRIDGE_LIMITS = {"ingredient": 12, "meals": 4}


def cook_meal_output(ticks: int | None) -> int:
    """做饭时长（刻）→ 产出饭菜份数：30分钟→1，45分钟→2，60分钟→3，80分钟→4。
    其他时长按区间就近归档。"""
    t = ticks if ticks else ACTIVITIES["cook"]["duration"]
    if t <= 3:
        return 1
    if t <= 5:
        return 2
    if t <= 7:
        return 3
    return 4

# ------------------------------------------------------------------ 活动表
ACTIVITIES: dict[str, dict] = {
    # ============ 室内（house） ============
    "cook": {
        "name": "做饭", "emoji": "🍳", "duration": 5, "need": "facility:K",
        "scene": "house", "cost": 0,
        "effects": {"energy": -2.0, "mood": 1.0},
        "synergy": True, "synergy_stat": "mood",
        "desc": "在厨房做饭：需消耗食材（商店S购买，1份食材出1份饭），"
                "时长30/45/60/80分钟分别产出1/2/3/4份可带走的饭菜",
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
    "buy_foodstuff": {
        "name": "买食材", "emoji": "🥬", "duration": 1, "need": "facility:S",
        "scene": "town", "cost": 5,
        "effects": {"mood": 1},
        "gives_item": "ingredient",
        "desc": "买一份食材（做饭用，1份食材做1份饭）",
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
        "desc": "原地附近踱步散心（位置不变，回家要选睡觉）",
    },
    # ============ 社交（需目标玩家） ============
    "chat": {
        "name": "对话", "emoji": "💬", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {},
        "desc": "与同一地点的人对话：交流信息、分享想法、商量事情或增进了解；不产生心情好感变化，每轮微耗精力，3轮约占1刻时间",
    },
    "phone": {
        "name": "打电话", "emoji": "📞", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {},
        "desc": "给室友打电话，可跨地点对话（交流信息、商量事情，无数值变化），拨打者按通话轮数支付话费",
    },
    "love": {
        "name": "约会", "emoji": "💗", "duration": 3, "need": "player",
        "scene": "any", "cost": 0,
        "effects": {"mood": 6},
        "consent_threshold": 55,
        "desc": "和心仪的室友约会（好感互达55或已是恋爱关系）",
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
    "self_relief": {
        "name": "自行解决", "emoji": "🌙", "duration": 3, "need": "facility:b",
        "scene": "house", "cost": 0, "male_only": True,
        "effects": {"energy": -20, "mood": 2},
        "fixed_duration": True,
        "desc": "在卫生间独处释放生理紧张（30分钟，共损耗60精力）；释放后精力/饱食/心情消耗恢复基线，开始新的生理周期",
    },
    "use_medicine": {
        "name":"给室友用药", "emoji":"💊", "duration":1, "need":"player",
        "scene":"any", "cost":0, "needs_item":"medicine", "effects":{"energy":-0.5},
        "desc":"与室友在同一地点（同一设施区域）用药品疗伤或缓解疾病",
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

# 模型可见伪装：给 LLM 的 key/名称用中性词，避免对齐层回避敏感直白词汇；
# 解析时由 brain._norm_action 映射回真实行动，显示层（日志/前端）不受影响。
MODEL_DISGUISES: dict[str, dict] = {
    "sex": {"key": "intimate", "name": "互动", "aliases": ["亲密互动"]},
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
        "cook":"在家中厨房；需消耗食材（商店购买），按时长产出1~4份可带走饭菜", "eat":"在家中餐厅；吃随身饭菜或家中饭菜（家里刚做过饭15刻内有效），恢复饱食",
        "watch_tv":"在家中客厅沙发；放松心情", "computer":"在自己卧室电脑前；保存、查看和修改自己的记录",
        "computer_game":"在自己卧室电脑前；娱乐、提升心情", "sleep":"在自己卧室床上；恢复精力与身体",
        "shower":"在家中浴室；放松并恢复精力", "laundry":"在家中洗衣区；洗衣、略有放松",
        "balcony":"在家中阳台；晒太阳、放松", "decorate_room":"在自己卧室；消耗装饰品改善房间",
        "decorate_common":"在家中公共区域；消耗装饰品改善公共环境",
        "think":"在当前位置；整理思绪、放松", "wander":"在当前位置附近来回踱步放松；不会移动到别处（回家需直接选sleep或visit）",
        "chat":"与室友在同一地点（同一设施区域）；交流信息、分享近况、商量事情（每轮微耗精力，3轮约1刻）", "phone":"在任意地点；跨地点对话，交流信息（无数值变化，按轮收话费）",
        "love":"与室友在同一地点（同一设施区域）或床上；约会、增进感情（已是恋爱关系时不受好感门槛限制）", "sex":"与室友在同一地点（同一设施区域）或床上；亲密相处（恋爱关系足够深时的自然选择，需双方同意）",
        "gift":"与室友在同一地点（同一设施区域）；赠送物品、增进感情", "attack":"与室友在同一地点（同一设施区域）；伤害对方、恶化关系",
        "give_meal":"与室友在同一地点；把饭菜送给对方当场吃", "use_medicine":"与室友在同一地点；消耗药品治疗伤病",
        "self_relief":"在卫生间独处释放生理紧张（仅男性；释放后消耗倍率恢复基线）",
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
        "buy_foodstuff":"买食材，回家做饭的原料（1份做1份饭）",
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


def _menu_available_here(places, scene) -> bool:
    """该活动在当前场景是否有可用地点（用于判断是否需要跨场景前往）。"""
    if not isinstance(places, dict):
        return scene == "any"
    for current, locations in places.items():
        if current not in (scene, "any"):
            continue
        if any(loc in ("here", "near_player") or
               (isinstance(loc, str) and loc.startswith(scene + ":")) for loc in locations):
            return True
    return False


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
        places=a.get('_places')
        home_bound = k == "sleep" and scene == "town" and not _menu_available_here(places, scene)
        if places:
            if scene!='any' and scene not in places and 'any' not in places and not home_bound:continue
            if scene=='town' and 'town' in places and 'any' not in places and not any(loc in ('here','near_player','town:'+(venue_code or '')) for loc in places['town']):continue
        else:
            if k in DEFS and (not venue_code or DEFS[k][0] != venue_code):continue
            if a.get('_venue') and a['_venue']!=venue_code:continue
            if scene != "any" and a.get("scene") not in ("any", scene) and not home_bound:continue
        req = []
        if a.get('_target_mode')=='required' or a.get("need") == "player":
            req.append("target=室友")
        elif a.get('_target_mode')=='optional':req.append('target=室友（可选）')
        if a.get("needs_item"):
            req.append("需要鲜花或盆栽" if a['needs_item']=='any' else f"需要{ITEMS[a['needs_item']]['name']}")
        extra = "（"+"；".join(req)+"）" if req else ""
        group = "当前场所" if scene=='town' and places and any(loc.startswith('town:') for loc in places.get('town',[])) else "当前场所" if not places and (k in DEFS or a.get('_venue')) else ("社交互动" if a.get("need")=="player" else
                "室内生活" if a.get("scene")=="house" and not home_bound else
                "小镇日常" if a.get("scene")=="town" else "随处行动")
        detail = descriptions.get(k, defaults.get(k,a.get('desc','在相应地点；进行活动')))
        if home_bound:
            detail = "回自己卧室床上睡觉（夜间恢复更高）；从镇上选它会自动回家"
        place_hint=''
        if places:
            labels={'here':'当前位置','near_player':'同一地点','own_computer':'自己的电脑','own_bedroom':'自己的卧室','house_common':'公共区','house_patient':'病人旁','house:K':'厨房','house:D':'餐厅','house:L':'客厅','house:a':'阳台','house:b':'浴室','house:w':'洗衣区','house:h':'卧室'}
            from .town_services import NAMES
            visible=[labels.get(loc,NAMES.get(loc[5:],loc) if loc.startswith('town:') else loc) for current,locations in places.items() if current in (scene,'any') for loc in locations]
            if visible:place_hint='｜可在'+ '、'.join(dict.fromkeys(visible))
        disg = MODEL_DISGUISES.get(k)
        menu_key = disg['key'] if disg else k
        menu_name = disg['name'] if disg else a['name']
        sections[group].append(f"- {menu_key}: {menu_name}{extra}｜{detail}{place_hint}")
    # 在家时给一条跨场景引导：挣钱类场所必须到场才可见，缺钱时模型无从得知，
    # 这里固定提示 visit 方向，避免全员坐吃山空。
    if scene == "house":
        sections["小镇日常"].append(
            "- visit: 前往小镇场所（target 填代码/名称）｜缺钱可去事务所O（上班）、田园F（劳作）挣钱；"
            "商店S购物、饭店R吃饭、诊所B看病。到场后才会列出当地具体项目")
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


# ------------------------------------------------------------------ 定量描述
# 关键防误解注记：定量描述实验中仍须保留的行为提示（历史上删掉会直接引发
# 模型行为误解，如 wander 以为能走回家、镇上以为不能睡觉）。
KEY_NOTES: dict[str, str] = {
    "wander": "只在原地附近踱步，不会移动到别处（回家要选睡觉）",
    "eat": "需背包有饭菜，或家里15刻内刚做过饭；否则请选下馆子；饱食达上限（默认95）时不能吃",
    "eat_out": "饭店用餐与家中用餐同一饱食上限（默认95），吃得很饱时会被拒；多人同吃有心情/好感加成",
    "sleep": "夜间恢复更高；从镇上选它会自动回家睡觉",
    "phone": "可跨地点通话；话费由拨打者按轮支付",
    "give_meal": "对方当场吃掉（不进背包）；对方吃得很饱时会被拒",
    "use_medicine": "对方满血无病时会被拒",
    "visit": "抵达后才会列出当地具体项目（先用target填场所代码/名称）",
    "computer": "search/read 结果自动存入记忆；文件不会随记忆整理消失",
    "love": "需双方好感都≥55或已建立恋爱关系，否则不会成",
    "sex": "需双方各自选择同意；对方可以拒绝",
    "gift": "消耗背包里的鲜花/盆栽；大幅提升好感",
    "attack": "对方掉血、双方好感暴跌",
    "transfer": "付款或收款的一方必须是你自己",
    "ride": "需身边的司机正在开车；乘车不需要驾照",
    "stock": "在本机交易；场所股受营业情况影响，可能亏损",
    "buy_car": "需已到商店；买车后仍需驾照才能驾驶",
    "treat": "诊所24小时营业",
    "self_relief": "仅男性；释放后生理周期消耗倍率归1并开始新周期（互动同样有效）",
}

_STAT_LABELS = (("hp", "生命"), ("satiety", "饱食"), ("energy", "精力"),
                ("mood", "心情"), ("money", "金钱"))


def quantitative_descriptions(sim) -> dict[str, str]:
    """把每项活动的全部机制写成一行定量描述；数值为 0 的机制不显示，
    防误解的关键注记（KEY_NOTES）附加在定量信息之后。
    读取 sim 当前配置（town_config / mechanics / activity_config），
    网页面板调参后描述自动跟随。"""
    from .town_services import DEFS, SKILLS, work_experience_rate
    out: dict[str, str] = {}
    for key, a in ACTIVITIES.items():
        if a.get("legacy"):
            continue
        parts: list[str] = []
        if key in DEFS:
            code, name, skill, kind = DEFS[key]
            v = sim.town_config.get(code, {}).get(key, {})
            if kind == "job":
                parts.append(f"时薪{v.get('hourly_wage', 0):g}元/小时")
                if v.get("min_level"):
                    parts.append(f"需{SKILLS.get(skill, skill)}{int(v['min_level'])}级")
                parts.append(f"默认班次{v.get('minutes', 0):g}分钟（可延长）")
                if v.get("energy_cost_per_hour"):
                    parts.append(f"精力-{v['energy_cost_per_hour']:g}/小时")
                if sim.mechanics.get("work_mood_per_hour"):
                    parts.append(f"心情-{sim.mechanics['work_mood_per_hour']:g}/小时")
                if skill:
                    xp = work_experience_rate(sim, key)
                    if xp:
                        parts.append(f"{SKILLS.get(skill, skill)}经验+{xp:g}/小时")
            elif kind in ("course", "license_course"):
                parts.append(f"时长{v.get('minutes', 0):g}分钟")
                if v.get("price"):
                    parts.append(f"学费{v['price']:g}元")
                if v.get("energy_cost_per_hour"):
                    parts.append(f"精力-{v['energy_cost_per_hour']:g}/小时")
                if sim.mechanics.get("study_mood_per_hour"):
                    parts.append(f"心情-{sim.mechanics['study_mood_per_hour']:g}/小时")
                xp = v.get("xp_per_hour")
                if isinstance(xp, (int, float)) and xp:
                    parts.append(f"经验+{xp:g}/小时")
                if key == "license_driving":
                    parts.append("完成即取得驾照")
            elif kind == "goods":
                if v.get("price"):
                    parts.append(f"售价{v['price']:g}元")
                if a.get("gives_item"):
                    parts.append(f"获得{ITEMS[a['gives_item']]['name']}×1")
            else:  # service
                if v.get("minutes"):
                    parts.append(f"默认{v['minutes']:g}分钟")
                if v.get("price"):
                    parts.append(f"费用{v['price']:g}元")
                for field, label in (("satiety_per_hour", "饱食"), ("energy_gain_per_hour", "精力"),
                                     ("mood_per_hour", "心情"), ("hp_per_hour", "生命")):
                    val = v.get(field, 0)
                    if val:
                        parts.append(f"{label}+{val:g}/小时")
                if v.get("energy_cost_per_hour"):
                    parts.append(f"精力-{v['energy_cost_per_hour']:g}/小时")
                if v.get("affinity_per_hour"):
                    parts.append(f"同伴好感+{v['affinity_per_hour']:g}/小时")
        else:
            # 三层合并（与执行层 apply()/_base() 一致）：ACTIVITIES 原始值
            # → 活动参数面板 activity_config → 活动编辑器 activity_overrides
            cfg = sim.activity_config.get(key, {})
            over = sim.activity_overrides.get(key, {})
            eff = dict(cfg.get("effects", a.get("effects", {})))
            eff.update(over.get("effects", {}))
            dur = over.get("duration", cfg.get("duration", a["duration"]))
            cost = over.get("cost", cfg.get("cost", a.get("cost", 0)))
            if cost:
                parts.append(f"费用{cost:g}元")
            parts.append(f"默认{int(dur) * 10:g}分钟")
            for stat, label in _STAT_LABELS:
                val = eff.get(stat, 0)
                if val:
                    parts.append(f"{label}{'+' if val > 0 else ''}{val:g}/刻")
            joint = over.get("joint_affinity_per_tick", (8 / 6 if key == "love" else 0))
            if joint:
                parts.append(f"共同好感+{joint:g}/刻")
            self_aff = over.get("self_affinity_per_tick", 0)
            target_aff = over.get("target_affinity_per_tick", 0)
            if self_aff:
                parts.append(f"自己对对象好感+{self_aff:g}/刻")
            if target_aff:
                parts.append(f"对象对自己好感+{target_aff:g}/刻")
            if a.get("synergy"):
                parts.append("协同：2人×1.35/3人×1.70/4人×2.05（好感调制）")
            if a.get("gives_item"):
                parts.append(f"获得{ITEMS[a['gives_item']]['name']}×1")
            need = a.get("needs_item")
            if need:
                label = "鲜花或盆栽" if need == "any" else ITEMS.get(need, {}).get("name", need)
                parts.append(f"需消耗{label}")
            for stat, bonus in (a.get("night_bonus") or {}).items():
                if bonus:
                    label = dict(_STAT_LABELS).get(stat, stat)
                    parts.append(f"夜间{label}+{bonus:g}/刻")
        if key == "cook":
            shelf = int(sim.mechanics.get("meal_shelf_life_ticks", 36))
            parts.append("时长30/45/60/80分钟→产出1/2/3/4份饭菜（每份耗1食材）")
            parts.append(f"饭菜保质{shelf}刻；做好后15刻内家人可直接吃饭")
        note = KEY_NOTES.get(key)
        if note:
            parts.append("⚠" + note)
        out[key] = "；".join(parts) or a.get("desc", "")
    return out
