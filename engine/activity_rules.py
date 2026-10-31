"""统一的活动编辑视图。旧活动保留专属逻辑，通用数值在开始时冻结。"""
from __future__ import annotations

from copy import deepcopy
import math
import re

STATS = ('hp', 'satiety', 'energy', 'mood', 'money')
FIELDS = ('name', 'emoji', 'description', 'scene', 'location', 'duration', 'cost',
          'effects', 'joint_affinity_per_tick', 'enabled', 'builtin')
PLACE_SCENES=('house','town','any')


def places_of(row):
    places=row.get('places')
    return deepcopy(places) if places is not None else {row['scene']:[row['location']]}


def position_options_of(row):
    if 'position_options' in row:return deepcopy(row['position_options'])
    options=[]
    for locations in places_of(row).values():
        for location in locations:
            option='near_player' if location in ('near_player','house_patient') else location
            if option in ('near_player','here') and option not in options:options.append(option)
    return options


def location_matches(player,scene,location):
    """检查角色此刻是否处于选中的具体活动地点。"""
    if scene not in ('any',player.scene):return False
    if location=='here':return True
    from . import world
    if location=='own_bedroom':return player.scene=='house' and '卧室' in world.zone_of('house',player.x,player.y)
    if location=='own_computer':
        from .resident_tools import computer_seat
        return player.scene=='house' and (player.x,player.y)==tuple(computer_seat(player))
    if location=='house_common':return player.scene=='house' and world.zone_of('house',player.x,player.y) in ('客厅','餐厅','厨房','阳台')
    if location.startswith('house:'):return player.scene=='house' and world.tile('house',player.x,player.y)==location[6:]
    if location.startswith('town:'):
        from .town_services import onsite
        return player.scene=='town' and onsite(player,location[5:])
    return False


def target_mode_of(row):
    return row.get('target_mode','required' if row.get('need')=='player' or row.get('location')=='near_player' else 'optional' if row.get('need')=='home_patient' or row.get('location')=='house_patient' else 'none')


def need_for_location(location, target_mode='none'):
    if location=='here':return 'player' if target_mode=='required' else None
    if location=='near_player':return 'player'
    if location=='house_patient':return 'home_patient'
    if location=='own_computer':return 'own_computer'
    if location=='own_bedroom':return 'own_bedroom'
    if location=='house_common':return 'any_house_common'
    if location=='town:W':return 'pond_shore'
    return 'facility:'+location.split(':',1)[1]


def choose_place(row,p):
    places=places_of(row)
    candidates=[(scene,loc) for scene,locations in places.items() for loc in locations]
    same=[item for item in candidates if item[0] in (p.scene,'any')]
    if same:candidates=same
    def distance(item):
        scene,loc=item
        if loc in ('here','near_player'):return 0
        if loc=='own_computer' or loc=='own_bedroom':return 0 if p.scene=='house' else 100
        from .world import find_tiles
        code=loc.split(':',1)[1] if ':' in loc else 'L'
        tiles=find_tiles('town' if scene=='town' else 'house',code)
        return min((abs(p.x-x)+abs(p.y-y) for x,y in tiles),default=1000)
    return min(candidates,key=distance)


def available_here(sim,p,key):
    """无需寻路的特殊行动仍须遵守用户编辑的可用地点。"""
    from .town_services import onsite
    from . import world
    row=catalog(sim).get(key)
    if not row:return False
    if row['place_join']=='and':
        return any(location_matches(p,scene,loc) for scene,locations in row['places'].items() for loc in locations)
    for scene,locations in row['places'].items():
        if scene not in ('any',p.scene):continue
        for location in locations:
            if location in ('here','near_player'):return True
            if p.scene=='town' and location.startswith('town:') and onsite(p,location[5:]):return True
            if p.scene=='house' and location=='own_bedroom' and '卧室' in world.zone_of('house',p.x,p.y):return True
            if p.scene=='house' and location.startswith('house:') and world.tile('house',p.x,p.y)==location[6:]:return True
    return False


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
        row['_places']=places_of({**_base(sim,key,value),**override})
        row['_target_mode']=override.get('target_mode',target_mode_of(value))
        row['_place_join']=override.get('place_join','or')
        row['_position_options']=position_options_of({**_base(sim,key,value),**override})
        builtins[key]=row
    return {**builtins,**{k: custom_spec(v) for k,v in sim.custom_activities.items()
                        if k not in sim.disabled_activities and v.get('enabled',True)}}


def custom_spec(row):
    loc=row['location']
    need=need_for_location(loc,row.get('target_mode','none'))
    spec={'name':row['name'],'emoji':row['emoji'],'scene':row['scene'],
            'need':need,'duration':row['duration'],'cost':row['cost'],
            'effects':deepcopy(row['effects']),'desc':row['description'],
            '_custom':True,'_places':places_of(row),'_target_mode':row.get('target_mode','none'),
            '_place_join':row.get('place_join','or'),'_position_options':position_options_of(row),
            '_venue':loc.split(':',1)[1] if loc.startswith('town:') else None,
            '_building':loc.split(':',1)[1] if loc.startswith('town:') else None}
    if row.get('product_item'):
        spec['gives_item']=row['product_item']
        spec['fixed_duration']=True
    return spec


def _location(key, activity):
    from .town_services import DEFS
    if key=='phone':return 'here'
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
    joint = 8/6 if key=='dating' else 0
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
        base['places']=places_of({**base,**override})
        base['target_mode']=override.get('target_mode',target_mode_of(activity))
        base['self_affinity_per_tick']=override.get('self_affinity_per_tick',0)
        base['target_affinity_per_tick']=override.get('target_affinity_per_tick',0)
        base['place_join']=override.get('place_join','or')
        base['position_options']=position_options_of({**base,**override})
        rows[key]=base
    for key,row in sim.custom_activities.items():
        rows[key]={field:deepcopy(row[field]) for field in FIELDS}
        rows[key]['places']=places_of(row)
        rows[key]['target_mode']=row.get('target_mode','none')
        rows[key]['self_affinity_per_tick']=row.get('self_affinity_per_tick',0)
        rows[key]['target_affinity_per_tick']=row.get('target_affinity_per_tick',0)
        rows[key]['place_join']=row.get('place_join','or')
        rows[key]['position_options']=position_options_of(row)
        if row.get('product_item'):rows[key]['product_item']=row['product_item']
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
        places=places_of(row)
        if not isinstance(places,dict) or not places or not set(places)<=set(PLACE_SCENES):raise ValueError(f'{key} 活动类别无效')
        place_join=row.get('place_join','or')
        if place_join not in ('or','and'):raise ValueError(f'{key} 地点连接方式无效')
        position_options=position_options_of(row)
        if not isinstance(position_options,list) or any(not isinstance(value,str) for value in position_options) or len(set(position_options))!=len(position_options) or any(value not in ('here','near_player') for value in position_options):raise ValueError(f'{key} 独立位置条件无效')
        if place_join=='and' and not position_options:raise ValueError(f'{key} 选择“且”时须勾选原地或目标角色旁')
        for scene,locations in places.items():
            if not isinstance(locations,list) or not locations or len(locations)>24 or len(set(locations))!=len(locations) or any(not isinstance(loc,str) or not _allowed_location(scene,loc) and not (builtin and scene==ACTIVITIES[key].get('scene','any') and loc==_location(key,ACTIVITIES[key])) for loc in locations):
                raise ValueError(f'{key} 地点无效')
            if place_join=='and' and any(loc in ('near_player','house_patient') or loc=='here' and scene!='any' for loc in locations):raise ValueError(f'{key} 选择“且”时须指定具体地点')
        target_mode=row.get('target_mode',target_mode_of(ACTIVITIES[key]) if builtin else 'none')
        if target_mode not in ('none','optional','required'):raise ValueError(f'{key} 对象设置无效')
        if place_join=='and' and position_options==['near_player'] and target_mode!='required':raise ValueError(f'{key} 只选择“目标角色旁”时必须指定室友')
        if key in ('phone','dating','sex') and target_mode!='required':raise ValueError(f'{key} 必须选择一位室友')
        if type(row['duration']) is not int or not 1<=row['duration']<=144:raise ValueError(f'{key} 时长应为1～144刻')
        for field in ('cost','joint_affinity_per_tick','self_affinity_per_tick','target_affinity_per_tick'):
            v=row.get(field,0)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>10000:raise ValueError(f'{key} {field} 无效')
        if row['cost']<0:raise ValueError(f'{key} 一次性费用不能为负')
        if not isinstance(row['effects'],dict) or set(row['effects'])!=set(STATS):raise ValueError(f'{key} 身体与金钱效果不完整')
        for stat,v in row['effects'].items():
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000:raise ValueError(f'{key} {stat} 效果无效')
        normalized={field:deepcopy(row[field]) for field in FIELDS}
        normalized['places']=deepcopy(places)
        normalized['target_mode']=target_mode
        normalized['self_affinity_per_tick']=row.get('self_affinity_per_tick',0)
        normalized['target_affinity_per_tick']=row.get('target_affinity_per_tick',0)
        normalized['place_join']=place_join
        normalized['position_options']=position_options
        normalized['name']=normalized['name'].strip();normalized['description']=normalized['description'].strip()
        if builtin:
            base=_base(sim,key,ACTIVITIES[key])
            if not normalized['enabled']:disabled.add(key)
            change={field:normalized[field] for field in FIELDS if field not in ('enabled','builtin','description','effects') and normalized[field]!=base[field]}
            for field,default in (('places',{base['scene']:[base['location']]}),('target_mode',target_mode_of(ACTIVITIES[key])),('self_affinity_per_tick',0),('target_affinity_per_tick',0)):
                if normalized[field]!=default:change[field]=normalized[field]
            if normalized['place_join']!='or':change['place_join']=normalized['place_join']
            if normalized['place_join']=='and' or normalized['position_options']!=position_options_of({**base,'places':normalized['places']}):change['position_options']=normalized['position_options']
            changed_effects={stat:value for stat,value in normalized['effects'].items() if value!=base['effects'][stat]}
            if changed_effects:change['effects']=changed_effects
            if change:overrides[key]=change
        else:
            if row.get('product_item'):
                item=row['product_item']
                if not re.fullmatch(r'item_[a-z][a-z0-9_]{1,34}',item) or key!='buy_'+item[5:]:
                    raise ValueError(f'{key} 商品代码或售卖地点无效')
                normalized['product_item']=item
            custom[key]=normalized
            if not normalized['enabled']:disabled.add(key)
    return overrides,disabled,custom


def apply(sim,key,activity,p=None):
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
    configured=catalog(sim)[key]
    a['_places']=configured['places']
    a['_target_mode']=configured['target_mode']
    a['_self_affinity_per_tick']=configured['self_affinity_per_tick']
    a['_target_affinity_per_tick']=configured['target_affinity_per_tick']
    a['_place_join']=configured['place_join']
    a['_position_options']=configured['position_options']
    if p is not None:
        scene,location=choose_place(configured,p)
        a['scene']=scene
        a['need']='home_patient' if key=='home_treat' and location=='house_patient' else need_for_location(location,configured['target_mode'])
        a['_chosen_location']=location
        a['_use_interaction_points']=location==_location(key,activity) and key not in sim.custom_activities
        if location.startswith('town:'):a['_building']=location.split(':',1)[1]
        else:a.pop('_building',None)
    return a
