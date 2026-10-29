"""居民自主工具：文字电脑、表情和不限定动机的资金渠道。"""
from decimal import Decimal, InvalidOperation
import json
import uuid
import math

from . import world

EMOJIS={'❤️','❤','😊','😂','😢','😭','😠','😡','👍','👋','😮','🥰','💔'}


def computer_seat(p):
    i=min(range(len(world.BEDROOM_ANCHORS)),key=lambda i:abs(world.BEDROOM_ANCHORS[i][0]-p.bedroom[0])+abs(world.BEDROOM_ANCHORS[i][1]-p.bedroom[1]))
    x,y,_,_=world.BEDROOM_RECTS[i]
    # 桌子前方的地面格，家具实体仍然阻挡。
    return world.nearest_walkable('house',x+(3 if i==4 else 4),y+(2 if i==4 else 3))


def computer_context(p):
    if p.scene!='house' or (p.x,p.y)!=computer_seat(p):
        return '不在自己的电脑旁。要查看记录请选择 computer/list 前往电脑；要搜索小镇信息用 computer/search。'
    if p.computer_feedback:
        return '本次电脑操作结果：'+p.computer_feedback
    return ('已在自己的电脑旁。computer/list 查看记录目录（read/file_id 看正文，create/edit/delete 管理）；'
            'computer/search 搜索小镇信息（query 留空看目录，可查"场所与营业时间""工作岗位""技能课程""商品""生活与急救"或具体名称如"咖啡馆""厨师"）。')


# ---------------------------------------------------------------- 电脑搜索引擎
SEARCH_CATALOG = {
    '场所与营业时间': '各场所的营业时间与场内活动；可再查单个场所名（如：咖啡馆）',
    '工作岗位': '全部岗位的时薪、技能要求与精力消耗；可再查单个岗位名（如：厨师）',
    '技能课程': '学校各技能课程与驾照课程的时长、费用与效果',
    '商品': '鲜花、盆栽、药品、私家车的售价',
    '生活与急救': '生存数值消耗、疾病、紧急送医借款与住院规则',
}
_FIELD_LABELS = {
    'minutes': '时长分', 'price': '费用元', 'hourly_wage': '时薪元',
    'energy_cost_per_hour': '精力耗/h', 'energy_gain_per_hour': '精力回/h',
    'mood_per_hour': '心情/h', 'satiety_per_hour': '饱食/h', 'hp_per_hour': '生命/h',
    'affinity_per_hour': '好感/h', 'min_level': '技能等级', 'xp_per_hour': '经验/h',
    'work_xp_per_hour': '工作经验/h',
}


def _hours_text(hours):
    start, end = hours
    return f'{start:02d}:00-{min(end,24):02d}:00' + ('（全天）' if (start, end) == (0, 24) else '')


def _item_params(sim, code, key):
    from .town_services import DEFS, JOB_GRADES, SKILLS
    row = {'名称': DEFS[key][1]}
    for field, value in (sim.town_config.get(code, {}).get(key) or {}).items():
        label = _FIELD_LABELS.get(field)
        if label and value:
            row[label] = value
    skill = DEFS[key][2]
    if skill:
        grade = JOB_GRADES.get(key)
        need = row.pop('技能等级', None)
        row['技能'] = (SKILLS.get(skill, skill)
                     + (f'（需{DEFS[key][2]} Lv{int(need)}）' if need else '')
                     + ('，教师岗' if grade and grade.get('teacher') else ''))
    return row


def _venues_index(sim):
    from .town_services import DEFS, NAMES
    out = []
    for code, name in NAMES.items():
        inside = [DEFS[k][1] for k, d in DEFS.items() if d[0] == code]
        out.append({'代码': code, '名称': name, '营业时间': _hours_text(sim.opening_hours[code]),
                    '场内项目': inside})
    return out


def _venue_detail(sim, code):
    from .town_services import DEFS, NAMES
    kinds = {'service': '活动服务', 'job': '工作岗位', 'course': '技能课程',
             'license_course': '培训课程', 'goods': '出售商品'}
    groups = {}
    for k, d in DEFS.items():
        if d[0] != code:
            continue
        groups.setdefault(kinds.get(d[3], d[3]), []).append(_item_params(sim, code, k))
    return {'名称': NAMES[code], '营业时间': _hours_text(sim.opening_hours[code]), **groups}


def _jobs_index(sim):
    from .town_services import DEFS, NAMES
    out = []
    for k, d in DEFS.items():
        if d[3] != 'job':
            continue
        out.append({'岗位': d[1], '场所': NAMES[d[0]], **{
            lb: v for lb, v in _item_params(sim, d[0], k).items() if lb != '名称'}})
    return out


def _courses_index(sim):
    from .town_services import DEFS, NAMES, SKILLS
    out = []
    for k, d in DEFS.items():
        if d[3] not in ('course', 'license_course'):
            continue
        out.append({'课程': d[1], '场所': NAMES[d[0]], **{
            lb: v for lb, v in _item_params(sim, d[0], k).items() if lb != '名称'}})
    return out


def _goods_index(sim):
    from .town_services import DEFS, NAMES
    out = []
    for k, d in DEFS.items():
        if d[3] != 'goods':
            continue
        row = _item_params(sim, d[0], k)
        out.append({'商品': row.pop('名称'), '场所': NAMES[d[0]], **row})
    return out


def _life_rules(sim):
    m = sim.mechanics
    return {
        '生存数值（每10分钟一刻）': {
            '饱食消耗/刻': m['base_satiety_drain'], '心情消耗/刻': m['base_mood_drain'],
            '精力恢复/刻': m['base_energy_regen'],
            '饱食为0': f"每刻扣生命{m['starvation_hp_loss']}",
            '饱食高于80': f"每刻回生命{m['high_satiety_hp_gain']}",
            '精力过低': f"低于{m['low_energy_threshold']}时每刻额外扣心情",
            '心情低落': f"低于{m['low_mood_threshold']}连续{m['low_mood_delay_ticks']}刻后每刻扣生命{m['low_mood_hp_loss']}",
        },
        '疾病': '随机患病，可自然恢复、诊所治疗（15元/次）、医学技能照护或买药',
        '紧急送医与住院': {
            '触发': f"生命≤{m['hospital_trigger_hp']}自动紧急送医",
            '急救费': f"{m['hospital_cost']:.0f}元；现金不足会自动打电话向室友借钱凑齐，凑不齐则不收治",
            '住院': f"房费{m['hospital_fee_halfday']:.0f}元/半天，生命每刻按自然恢复的{m['hospital_heal_multiplier']:.0f}倍恢复，"
                    f"心情每半天+{m['hospital_mood_gain']:.0f}，饱食与精力保持满值；随时可决定出院",
            '出院保底': f"生命{m['post_hospital_hp']:.0f}/饱食{m['post_hospital_satiety']:.0f}/精力{m['post_hospital_energy']:.0f}/心情{m['post_hospital_mood']:.0f}（不低于现有值）",
            '死亡': '未收治者生命归零即死亡',
        },
    }


def search_knowledge(sim, query: str = "") -> dict:
    """电脑搜索引擎：按一级目录组织小镇公开信息，支持目录→分类→具体条目。"""
    from .town_services import DEFS, NAMES
    q = (query or '').strip()
    if not q or q in ('目录', '帮助', '列表', '分类'):
        return {'搜索目录': SEARCH_CATALOG,
                '用法': 'query 填目录名或分类名看详情；也可直接填场所/岗位/课程/商品名'}
    if any(k in q for k in ('场所', '营业', '场馆', '地点')):
        return {'场所与营业时间': _venues_index(sim)}
    if q in ('工作', '岗位', '职业', '打工', '时薪', '招聘'):
        return {'工作岗位': _jobs_index(sim)}
    if q in ('课程', '学习', '技能课程', '学校'):
        return {'技能课程': _courses_index(sim)}
    if q in ('商品', '购物', '价格', '买'):
        return {'商品': _goods_index(sim)}
    if any(k in q for k in ('生活', '急救', '住院', '医院', '规则', '数值', '死亡', '疾病')):
        return {'生活与急救': _life_rules(sim)}
    # 具体条目：场所名/代码 → 详情；岗位/课程/商品名 → 条目
    for code, name in NAMES.items():
        if q in (name, code) or name in q:
            return {'场所详情': _venue_detail(sim, code)}
    hits = {}
    for k, d in DEFS.items():
        if q and q in d[1]:
            hits.setdefault(d[1], _item_params(sim, d[0], k))
    if hits:
        return {'搜索结果': list(hits.values())}
    return {'提示': f'未找到"{q}"', '搜索目录': SEARCH_CATALOG}


def use_computer(sim,p,d):
    if d is None or d.action!='computer' or p.computer_operation_done:return
    if p.scene!='house' or (p.x,p.y)!=computer_seat(p):return
    p.computer_operation_done=True
    file=next((f for f in p.computer_files if f['id']==d.file_id),None)
    op=d.operation
    if op=='create':
        file={'id':uuid.uuid4().hex[:12],'title':d.title or '未命名记录','content':d.content,'created':sim.global_tick,'updated':sim.global_tick}
        p.computer_files.append(file)
        result={'created':file['id'],'title':file['title']}
    elif op in ('edit','delete','read'):
        if not file:
            p.computer_feedback='记录不存在，请先list查看自己的记录编号。'
            return
        if op=='edit':
            file.update(title=d.title or file['title'],content=d.content,updated=sim.global_tick)
            result={'edited':file['id'],'title':file['title']}
        elif op=='delete':
            p.computer_files.remove(file);result={'deleted':file['id']}
        else:
            result={'id':file['id'],'title':file['title'],'offset':d.offset,'content':file['content'][d.offset:d.offset+4000],'length':len(file['content'])}
    elif op=='list':
        result={'files':[{'id':f['id'],'title':f['title'],'length':len(f['content'])} for f in p.computer_files[d.offset:d.offset+20]],'total':len(p.computer_files),'offset':d.offset}
    elif op=='search':
        result=search_knowledge(sim,d.query)
    else:
        p.computer_feedback='电脑操作无效，请使用list/create/edit/read/delete/search。'
        return
    p.computer_feedback=json.dumps(result,ensure_ascii=False)
    sim.log('activity',f'💻 {p.name}在自己的电脑上'+{'create':'创建了记录','edit':'编辑了记录','read':'查看了记录','delete':'删除了记录','list':'查看了记录目录','search':'搜索了小镇信息'}[op],[p.pid])


def instant_action(sim,p,d):
    target=next((o for o in sim.players if o.pid==d.target or o.name==d.target),None)
    if target is None:raise ValueError('未找到资金或表情接收人')
    if d.action=='emoji':
        if target is p or d.emoji not in EMOJIS:raise ValueError('请选择一位室友并发送支持的简单表情')
        p.speech=f'{d.emoji} → {target.name}';p.speech_tick=p.world_tick
        sim.log('social',f'{p.name} → {target.name}：{d.emoji}',[p.pid,target.pid])
        return
    sender=p if not d.money_from else next((o for o in sim.players if o.pid==d.money_from or o.name==d.money_from),None)
    if sender is None or sender is target or p not in (sender,target):raise ValueError('资金转移需要自己作为付款或收款方，双方必须不同')
    try:
        amount=Decimal(str(d.amount))
        if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('.01')):raise ValueError('金额必须为正数，最多两位小数')
    except (InvalidOperation,TypeError):raise ValueError('金额无效')
    balance=Decimal(str(sender.stats.money));receive=Decimal(str(target.stats.money))
    if balance<amount:raise ValueError('付款方余额不足，资金未转移')
    if not math.isfinite(float(receive+amount)):raise ValueError('金额超出可用范围，资金未转移')
    sender.stats.money=float(balance-amount);target.stats.money=float(receive+amount)
    entry={'id':uuid.uuid4().hex,'day':sim.day,'clock':sim.clock,'tick':sim.global_tick,'actor':p.name,'from':sender.name,'to':target.name,'amount':float(amount),'reason':d.reason}
    sender.transactions.append(dict(entry));target.transactions.append(dict(entry))
    sim.log('social',f'💰 {sender.name} → {target.name}：{amount:.2f}元'+(f'（{p.name}：{d.reason}）' if d.reason else ''),[sender.pid,target.pid])
