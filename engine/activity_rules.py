"""统一的活动编辑视图。旧活动保留专属逻辑，通用数值在开始时冻结。"""
from __future__ import annotations

from copy import deepcopy
import math
import re

STATS = ('hp', 'satiety', 'energy', 'mood', 'money')
FIELDS = ('name', 'emoji', 'description', 'scene', 'location', 'duration', 'cost',
          'effects', 'joint_affinity_per_tick', 'enabled', 'builtin')


def registry(sim):
    from .activities import ACTIVITIES
    builtins={}
    for key,value in ACTIVITIES.items():
        if value.get('legacy') or key in sim.disabled_activities:continue
        row=deepcopy(value)
        override=sim.activity_overrides.get(key,{})
        for field in ('name','emoji','duration','cost'):
            if field in override:row[field]=override[field]
        row['desc']=sim.activity_descriptions.get(key,row.get('desc',''))
        builtins[key]=row
    return {**builtins,**{k: custom_spec(v) for k,v in sim.custom_activities.items()
                        if k not in sim.disabled_activities and v.get('enabled',True)}}


def custom_spec(row):
    loc=row['location']
    need=(None if loc=='here' else 'own_computer' if loc=='own_computer' else
          'own_bedroom' if loc=='own_bedroom' else 'any_house_common' if loc=='house_common' else
          'player' if loc in ('near_player','house_patient') else 'pond_shore' if loc=='town:W' else
          'facility:'+loc.split(':',1)[1])
    return {'name':row['name'],'emoji':row['emoji'],'scene':row['scene'],
            'need':need,'duration':row['duration'],'cost':row['cost'],
            'effects':deepcopy(row['effects']),'desc':row['description'],
            '_custom':True,'_venue':loc.split(':',1)[1] if loc.startswith('town:') else None,
            '_building':loc.split(':',1)[1] if loc.startswith('town:') else None}


def _location(key, activity):
    from .town_services import DEFS
    if key in DEFS:
        return 'town:'+DEFS[key][0]
    need = activity.get('need')
    if need == 'own_computer':return 'own_computer'
    if need == 'home_patient':return 'house_patient'
    if need == 'any_house_common':return 'house_common'
    if need == 'pond_shore':return 'town:W'
    if need == 'player':return 'near_player'
    if need and need.startswith('facility:'):
        code=need.split(':',1)[1]
        if code == 'h':return 'own_bedroom' if key in ('sleep','decorate_room') else 'house:h'
        return activity['scene']+':'+code
    return 'here'


def _base(sim, key, activity):
    from .town_services import DEFS, work_experience_rate
    if key in DEFS:
        code, name, skill, kind = DEFS[key]
        terms = sim.town_config[code][key]
        effects = {stat: 0.0 for stat in STATS}
        for field, stat in (('energy_gain_per_hour','energy'),('mood_per_hour','mood'),
                            ('satiety_per_hour','satiety'),('hp_per_hour','hp')):
            effects[stat] += terms.get(field,0)/6
        effects['energy'] -= terms.get('energy_cost_per_hour',0)/6
        if kind == 'job':
            effects['money'] = terms.get('hourly_wage',0)/6
            effects['mood'] -= sim.mechanics['work_mood_per_hour']/6
        elif kind in ('course','license_course'):
            effects['mood'] -= sim.mechanics['study_mood_per_hour']/6
        extra = deepcopy(terms)
        if kind == 'job' and skill:extra['work_xp_per_hour'] = work_experience_rate(sim,key)
        return {'name':name,'emoji':activity['emoji'],'description':sim.activity_descriptions.get(key,name),
                'scene':'town','location':'town:'+code,'duration':int(terms.get('minutes',10)//10),
                'cost':terms.get('price',0),'effects':effects,
                'joint_affinity_per_tick':terms.get('affinity_per_hour',0)/6,
                'enabled':key not in sim.disabled_activities,'builtin':True,'special':extra}
    values=sim.activity_config.get(key,{})
    effects={stat:values.get('effects',activity.get('effects',{})).get(stat,0) for stat in STATS}
    if key=='phone':effects['money']=-sim.mechanics['phone_fee_per_tick']
    joint = (8 if key=='love' else 4)/6 if key in ('chat','phone','love') else 0
    return {'name':activity['name'],'emoji':activity['emoji'],
            'description':sim.activity_descriptions.get(key,activity.get('desc',key)),
            'scene':activity.get('scene','any'),'location':_location(key,activity),
            'duration':values.get('duration',activity['duration']),
            'cost':values.get('cost',activity.get('cost',0)), 'effects':effects,
            'joint_affinity_per_tick':joint,'enabled':key not in sim.disabled_activities,
            'builtin':True,'special':{k:deepcopy(v) for k,v in activity.items()
                                         if k not in ('name','emoji','duration','cost','effects','desc','need','scene')}}


def catalog(sim):
    from .activities import ACTIVITIES
    rows={}
    for key, activity in ACTIVITIES.items():
        if activity.get('legacy'):continue
        base=_base(sim,key,activity)
        override=sim.activity_overrides.get(key,{})
        base.update(deepcopy({field:value for field,value in override.items() if field!='effects'}))
        base['effects'].update(deepcopy(override.get('effects',{})))
        rows[key]=base
    for key,row in sim.custom_activities.items():
        rows[key]={field:deepcopy(row[field]) for field in FIELDS}
        rows[key]['special']={}
    return rows


def _allowed_location(scene, location):
    from .town_services import NAMES
    if location == 'here':return True
    if scene == 'house':return location in ('own_computer','own_bedroom','house_common','house_patient','near_player') or location in ('house:K','house:D','house:L','house:a','house:b','house:w','house:h')
    if scene == 'town':return location == 'near_player' or location in {'town:'+code for code in NAMES}
    return location in ('here','near_player')


def validate_catalog(sim, values):
    """返回覆盖、已禁用活动、自建活动；不让自建活动冒充内置动作。"""
    from .activities import ACTIVITIES
    if not isinstance(values,dict):raise ValueError('活动配置格式错误')
    builtins={k for k,v in ACTIVITIES.items() if not v.get('legacy')}
    if not builtins <= set(values):raise ValueError('内置活动列表不完整，请用关闭代替删除')
    if len(values)>len(builtins)+100:raise ValueError('自建活动最多100项')
    overrides,disabled,custom={},set(),{}
    for key,row in values.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',key):raise ValueError('活动代码只能是小写英文、数字和下划线')
        if key in ACTIVITIES and key not in builtins:raise ValueError('不能覆盖旧版兼容活动')
        if not isinstance(row,dict) or not set(FIELDS) <= set(row):raise ValueError(f'{key} 活动字段不完整')
        builtin=key in builtins
        if row['builtin'] is not builtin or type(row['enabled']) is not bool:raise ValueError(f'{key} 活动类型无效')
        if not isinstance(row['name'],str) or not 1<=len(row['name'].strip())<=40:raise ValueError(f'{key} 名称无效')
        if not isinstance(row['emoji'],str) or not 1<=len(row['emoji'])<=8:raise ValueError(f'{key} 图标无效')
        if not isinstance(row['description'],str) or not 1<=len(row['description'].strip())<=240 or any(c in row['description'] for c in '\r\n'):raise ValueError(f'{key} 说明应为一行，最多240字')
        if row['scene'] not in ('house','town','any') or (not builtin and not _allowed_location(row['scene'],row['location'])):raise ValueError(f'{key} 地点无效')
        if type(row['duration']) is not int or not 1<=row['duration']<=144:raise ValueError(f'{key} 时长应为1～144刻')
        for field in ('cost','joint_affinity_per_tick'):
            v=row[field]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>10000:raise ValueError(f'{key} {field} 无效')
        if row['cost']<0:raise ValueError(f'{key} 一次性费用不能为负')
        if not isinstance(row['effects'],dict) or set(row['effects'])!=set(STATS):raise ValueError(f'{key} 身体与金钱效果不完整')
        for stat,v in row['effects'].items():
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000:raise ValueError(f'{key} {stat} 效果无效')
        normalized={field:deepcopy(row[field]) for field in FIELDS}
        normalized['name']=normalized['name'].strip();normalized['description']=normalized['description'].strip()
        if builtin:
            base=_base(sim,key,ACTIVITIES[key])
            if normalized['scene']!=base['scene'] or normalized['location']!=base['location']:
                raise ValueError(f'{key} 的地点与专属功能绑定；可新建活动选择其他地点')
            if not normalized['enabled']:disabled.add(key)
            change={field:normalized[field] for field in FIELDS if field not in ('enabled','builtin','description','effects') and normalized[field]!=base[field]}
            changed_effects={stat:value for stat,value in normalized['effects'].items() if value!=base['effects'][stat]}
            if changed_effects:change['effects']=changed_effects
            if change:overrides[key]=change
        else:
            custom[key]=normalized
            if not normalized['enabled']:disabled.add(key)
    return overrides,disabled,custom


def apply(sim,key,activity):
    """在活动开始时把统一参数写入本次活动合同。"""
    if key in sim.disabled_activities:return None
    a=deepcopy(activity)
    if key in sim.custom_activities:
        row=sim.custom_activities[key]
        a.update(custom_spec(row))
        a['_joint_affinity_per_tick']=row['joint_affinity_per_tick']
    else:
        from .activities import ACTIVITIES
        row=_base(sim,key,ACTIVITIES[key])
        override=sim.activity_overrides.get(key,{})
        for field in ('name','emoji','duration','cost'):
            if field in override:a[field]=override[field]
        a['effects'].update(deepcopy(override.get('effects',{})))
        if key=='phone' and 'money' not in override.get('effects',{}):
            a['effects']['money']=-sim.mechanics['phone_fee_per_tick']
        a['_joint_affinity_per_tick']=override.get('joint_affinity_per_tick',row['joint_affinity_per_tick'])
        if a.get('_kind')=='job' and 'money' in override.get('effects',{}):
            a['_terms']['hourly_wage']=override['effects']['money']*6
    return a
