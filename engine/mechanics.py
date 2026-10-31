"""World-specific tunable rules. All rates are per ten-minute tick unless named per_hour."""
from copy import deepcopy
import math

DEFAULT_RULES = {
    'base_satiety_drain': .4, 'base_mood_drain': .6, 'base_energy_regen': 1.0,
    'illness_mood_drain': 4.0,
    'high_satiety_hp_threshold': 80.0, 'high_satiety_hp_gain': .3,
    'starvation_hp_loss': 2.0, 'decor_mood_per_200': 1.0,
    'disease_hp_loss_mild': .02, 'disease_hp_loss_moderate': .06,
    'disease_hp_loss_severe': .12, 'disease_energy_loss_per_level': .02,
    'work_mood_per_hour': 9.0, 'study_mood_per_hour': 6.0,
    'low_energy_threshold': 20.0, 'low_energy_mood_per_point': .1,
    'low_mood_threshold': 20.0,
    'low_mood_delay_ticks': 18, 'low_mood_hp_loss': .5,
    'walk_speed': 1, 'bicycle_speed': 4, 'car_speed': 12,
    'walk_satiety_per_tile': .6, 'bicycle_satiety_per_tile': .4,
    'walk_energy_per_tile': .65, 'bicycle_energy_per_tile': .35,
    'car_energy_per_tile': .0125, 'car_fuel_per_tick': 2.0,
    'phone_fee_per_tick': 10.0,
    'dialogue_energy_per_round': 0.15,   # 对话每轮双方精力消耗
    'literature_mood_chance_per_level': .1, 'literature_mood_bonus': 2.0,
    'disease_daily_chance': .02, 'affinity_daily_chance': .04,
    'disease_worsen_chance': .03,
    'affinity_decisions_per_tick': 1,
    'positive_affinity_threshold': 60, 'positive_mood_at_threshold': 2,
    'positive_mood_at_80': 6, 'negative_affinity_threshold': 30,
    'negative_mood_at_threshold': 2, 'negative_mood_at_0': 5,
    # ---- 急救与住院 ----
    'meal_block_satiety': 95.0,        # 饱食高于此值不能吃饭/送饭
    'hunger_alert_satiety': 10.0,      # 饱食低于此值：预警提醒、饿醒并禁止入睡
    'meal_shelf_life_ticks': 36,       # 饭菜保质期（刻）＝6小时，过期自动消失
    'hospital_trigger_hp': 10.0,      # 生命低于此值触发紧急送医
    'hospital_cost': 300.0,           # 急救费；资产不足需向室友借钱补齐
    'hospital_fee_halfday': 100.0,    # 住院房费（每半天）
    'hospital_heal_multiplier': 2.0,  # 住院每刻生命恢复 = 倍率×自然恢复
    'hospital_mood_gain': 15.0,       # 每半天住院恢复心情
    'post_hospital_hp': 60.0,         # 出院时恢复到（不低于现有值）
    'post_hospital_satiety': 80.0,
    'post_hospital_energy': 80.0,
    'post_hospital_mood': 40.0,
}

DEFAULT_HOURS = {
    'C': [7, 22], 'R': [10, 22], 'U': [8, 20], 'B': [0, 24],
    'O': [9, 18], 'S': [8, 21], 'M': [10, 23], 'G': [6, 22],
    'P': [0, 24], 'F': [6, 19], 'W': [6, 19], 'J': [6, 21],
}


def validate_rules(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULT_RULES):
        raise ValueError('机制参数不完整')
    out = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} 必须为有限数字')
        if key=='affinity_decisions_per_tick' and (type(value) is not int or not 0<=value<=5):
            raise ValueError('每刻好感自主判断额度应为0～5次整数')
        limit = 1 if 'chance' in key else 24 if key.endswith('speed') else 1440 if key.endswith('ticks') else 5000 if key.startswith('hospital') else 100
        if not 0 <= value <= limit:
            raise ValueError(f'{key} 超出范围 0～{limit}')
        if key.endswith(('speed', 'ticks')) and (int(value) != value or value < 1):
            raise ValueError(f'{key} 必须为正整数')
        out[key] = value
    if out['positive_affinity_threshold'] >= 80 or out['negative_affinity_threshold'] <= 0:
        raise ValueError('好感阈值需要分别低于80、且高于0')
    return out


def validate_hours(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULT_HOURS):
        raise ValueError('营业时间不完整')
    out = {}
    for code, hours in values.items():
        if not isinstance(hours, list) or len(hours) != 2 or any(type(v) is not int for v in hours):
            raise ValueError(f'{code} 营业时间必须为整数小时')
        if not 0 <= hours[0] < 24 or not 1 <= hours[1] <= 24 or hours[0] == hours[1]:
            raise ValueError(f'{code} 营业时间无效')
        out[code] = hours[:]
    return out


def open_now(hours, hour):
    start, end = hours
    return start <= hour < end if start < end else hour >= start or hour < end


def activity_defaults():
    from .activities import ACTIVITIES
    from .town_services import DEFS
    return {key: {'duration': row['duration'], 'cost': row.get('cost', 0),
                  'effects': deepcopy(row.get('effects', {}))}
            for key, row in ACTIVITIES.items() if key not in DEFS and not row.get('legacy')}


def validate_activity_descriptions(values):
    from .activities import activity_description_defaults
    defaults = activity_description_defaults()
    if not isinstance(values, dict):
        raise ValueError('可选行动描述格式错误')
    # 结构迁移：缺键（新增活动）补默认、多余键（已删除活动）丢弃，
    # 旧面板整表提交不再被整单拒绝
    result = {}
    for key in defaults:
        value = values.get(key, defaults[key])
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 240 or any(c in value for c in '\r\n'):
            raise ValueError(f'{key} 的行动描述应为一行，长度不超过240字')
        result[key] = value.strip()
    return result


def validate_activities(values):
    defaults = activity_defaults()
    if not isinstance(values, dict) or set(values) != set(defaults):
        raise ValueError('活动参数不完整')
    out = {}
    for key, row in values.items():
        base = defaults[key]
        if not isinstance(row, dict):
            raise ValueError(f'{key} 活动字段不完整')
        # 结构迁移：以当前默认键集为准——缺失键补默认值、多余键丢弃（如 cook 的
        # effects 键集随版本演进）。旧面板/旧存档整表提交时，用户没动过的活动
        # 行自动升级，不再整单拒绝（否则「改A活动却报B活动字段不完整」）。
        old_effects = row.get('effects') if isinstance(row.get('effects'), dict) else {}
        row = {'duration': row.get('duration', base['duration']),
               'cost': row.get('cost', base['cost']),
               'effects': {stat: old_effects.get(stat, val)
                           for stat, val in base['effects'].items()}}
        for field, value in row.items():
            fields = value if field == 'effects' else {field: value}
            for name, number in fields.items():
                if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or abs(number) > 1000:
                    raise ValueError(f'{key}.{name} 必须为有限数字')
        if type(row['duration']) is not int or not 1 <= row['duration'] <= 144:
            raise ValueError(f'{key} 时长必须为 1～144 刻')
        if row['cost'] < 0:
            raise ValueError(f'{key} 费用不能为负')
        out[key] = row
    return out
