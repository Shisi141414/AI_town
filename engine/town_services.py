"""小镇服务、课程、岗位与技能。参数按世界隔离，活动开始后使用已确认的条款。"""
from copy import deepcopy
import json
import math
import random

SKILLS={'computer':'计算机','science':'理学','literature':'文学','medicine':'医学','economics':'经济学','cooking':'烹饪'}
NAMES={'C':'咖啡馆','R':'饭店','U':'学校','B':'诊所','O':'事务所','S':'商店','M':'小镇影院','G':'健身房','P':'公园','F':'田园','W':'池塘','J':'小镇花园'}
# 可修改数值与固定用途分开，修改参数不能把咖啡馆变成吃饭场所。
DEFS={
 'coffee':('C','喝咖啡',None,'service'), 'cafe_read':('C','咖啡馆看书',None,'service'),
 'eat_out':('R','饭店吃饭',None,'service'), 'treat':('B','诊所治疗',None,'service'),
 'work':('O','事务助理',None,'job'), 'farm_work':('F','田园劳作',None,'job'),
 'job_programmer':('O','软件开发','computer','job'), 'job_analyst':('O','经济分析','economics','job'),
 'job_researcher':('O','科研助理','science','job'), 'job_editor':('O','文字编辑','literature','job'),
 'job_waiter':('R','餐厅服务员',None,'job'), 'job_chef':('R','厨师','cooking','job'),
 'job_reception':('B','诊所接待',None,'job'), 'job_nurse':('B','护理助理','medicine','job'), 'job_doctor':('B','门诊医师','medicine','job'),
 **{'study_'+k:('U','学习'+v,k,'course') for k,v in SKILLS.items()},
 'buy_flower':('S','鲜花',None,'goods'), 'buy_plant':('S','盆栽',None,'goods'), 'car':('S','私家汽车',None,'goods'),
 'shop':('S','零食杂物',None,'service'), 'movie':('M','看电影',None,'service'), 'exercise':('G','健身',None,'service'), 'park':('P','散步',None,'service'),
 'fish':('W','池塘钓鱼',None,'service'),'swim':('W','池塘游泳',None,'service'),
 'garden_walk':('J','花园散步',None,'service'),'garden_flowers':('J','花园赏花',None,'service'),'garden_tend':('J','花园园艺',None,'service'),
}
DEFAULTS={c:{} for c in NAMES}
DEFAULTS['C']={'coffee':dict(minutes=30,price=8,energy_gain=18,mood_gain=10),'cafe_read':dict(minutes=60,price=4,energy_gain=9,mood_gain=12)}
DEFAULTS['R']['eat_out']=dict(minutes=30,price=10,satiety_gain=45,mood_gain=3,affinity_gain=2)
DEFAULTS['B']['treat']=dict(minutes=30,price=15,hp_gain=42,mood_gain=3)
for key,code,mins,energy,wage,level in [
 ('farm_work','F',60,15,32,0),('work','O',60,9,36,0),('job_programmer','O',120,22,90,2),('job_analyst','O',120,18,85,2),
 ('job_researcher','O',120,20,75,1),('job_editor','O',90,12,65,1),
 ('job_waiter','R',60,12,38,0),('job_chef','R',120,24,80,2),
 ('job_reception','B',60,8,35,0),('job_nurse','B',90,18,65,1),('job_doctor','B',120,25,110,3)]:
 DEFAULTS[code][key]=dict(minutes=mins,energy_cost=energy,hourly_wage=wage,min_level=level)
for skill,mins,energy,fee in [('computer',120,18,60),('science',150,22,55),('literature',90,10,30),('medicine',180,26,80),('economics',120,16,65),('cooking',90,15,40)]:
 DEFAULTS['U']['study_'+skill]=dict(minutes=mins,energy_cost=energy,price=fee,xp=100)
DEFAULTS['S']={'buy_flower':dict(price=12),'buy_plant':dict(price=20),'car':dict(price=1000),'shop':dict(minutes=20,price=6,mood_gain=6)}
DEFAULTS['M']={'movie':dict(minutes=30,price=12,mood_gain=18,energy_cost=1.5)}
DEFAULTS['G']={'exercise':dict(minutes=30,price=0,mood_gain=12,energy_cost=15,hp_gain=3.6)}
DEFAULTS['P']={'park':dict(minutes=30,price=0,mood_gain=10.5,energy_cost=1.5)}
DEFAULTS['C']['coffee']['affinity_gain']=4
for code,rows in DEFAULTS.items():
 for key,row in rows.items():
  hours=row.get('minutes',10)/60
  for old,new in [('energy_cost','energy_cost_per_hour'),('energy_gain','energy_gain_per_hour'),('mood_gain','mood_per_hour'),('satiety_gain','satiety_per_hour'),('hp_gain','hp_per_hour'),('affinity_gain','affinity_per_hour')]:
   if old in row:row[new]=round(row.pop(old)/hours,2)
  if key.startswith('study_'):
   row.pop('xp');row['xp_per_hour']={'computer':50,'science':40,'literature':70,'medicine':35,'economics':50,'cooking':70}[key[6:]]
DEFAULTS['S']['buy_flower']['mood_per_hour']=12
DEFAULTS['S']['buy_plant']['mood_per_hour']=12
DEFAULTS['W']={'fish':dict(minutes=60,price=0,mood_per_hour=12,energy_cost_per_hour=2),
 'swim':dict(minutes=30,price=0,mood_per_hour=18,hp_per_hour=6,energy_cost_per_hour=24)}
DEFAULTS['J']={'garden_walk':dict(minutes=30,price=0,mood_per_hour=15,energy_cost_per_hour=3),
 'garden_flowers':dict(minutes=30,price=0,mood_per_hour=18,energy_gain_per_hour=6),
 'garden_tend':dict(minutes=60,price=0,mood_per_hour=15,hp_per_hour=3,energy_cost_per_hour=9)}
# 保留旧活动编号作为一级岗位，旧存档和已接受的合同仍可继续。
JOB_GRADES={}
for key,skill,title,senior_level in [
 ('work','economics','事务助理',2),('farm_work','science','田园劳作员',2),
 ('job_programmer','computer','软件工程师',4),('job_analyst','economics','经济分析师',4),
 ('job_researcher','science','科研助理',3),('job_editor','literature','文字编辑',3),
 ('job_waiter','cooking','餐厅服务员',2),('job_chef','cooking','厨师',4),
 ('job_reception','medicine','诊所接待',2),('job_nurse','medicine','护理助理',3),
 ('job_doctor','medicine','门诊医师',5)]:
 code=DEFS[key][0];base=DEFAULTS[code][key]
 DEFS[key]=(code,'一级'+title,skill,'job');JOB_GRADES[key]={'grade':1,'teacher':False,'family':key}
 senior=key+'_2'
 DEFS[senior]=(code,'二级'+title,skill,'job');JOB_GRADES[senior]={'grade':2,'teacher':False,'family':key}
 DEFAULTS[code][senior]={**deepcopy(base),'minutes':max(10,int(base['minutes']//20)*10),
  'hourly_wage':round(base['hourly_wage']*1.5,2),'min_level':senior_level,
  'energy_cost_per_hour':round(base['energy_cost_per_hour']*.85,2)}
for skill,name in SKILLS.items():
 DEFAULTS['U']['study_'+skill]['work_xp_per_hour']=3
 for grade,required,minutes,wage,energy in [(1,3,120,70,10),(2,5,60,105,8.5)]:
  key=f'job_teacher_{skill}'+('_2' if grade==2 else '')
  DEFS[key]=('U',('一级' if grade==1 else '二级')+name+'教师',skill,'job')
  JOB_GRADES[key]={'grade':grade,'teacher':True,'family':'job_teacher_'+skill}
  DEFAULTS['U'][key]=dict(minutes=minutes,energy_cost_per_hour=energy,hourly_wage=wage,min_level=required)
FIELDS={'minutes':('默认时长 / 岗位最短班次','分钟',10,1440),'price':('一次性费用 / 售价','元/次',0,100000),
 'energy_cost_per_hour':('精力消耗率','点/小时',0,600),
 'energy_gain_per_hour':('精力恢复率','点/小时',0,600),'mood_per_hour':('心情增长率','点/小时',0,600),
 'satiety_per_hour':('饱食恢复率','点/小时',0,600),'hp_per_hour':('生命恢复率','点/小时',0,600),
 'affinity_per_hour':('共同活动好感增长率','点/小时',0,60),'hourly_wage':('时薪','元/小时',0,1000),
 'min_level':('技能等级要求','级',0,5),'xp_per_hour':('学习经验率','经验/小时',1,600),
 'work_xp_per_hour':('该学科普通工作经验率（教师×2）','经验/小时',0,60)}



def defaults():return deepcopy(DEFAULTS)
def level(p,skill):return min(5,int(p.skills.get(skill,0)//100))

def merge_parameters(saved):
 """旧档只补充新增岗位和字段，不覆盖用户已调整的数值。"""
 result=defaults()
 for code,rows in (saved or {}).items():
  if code not in result or not isinstance(rows,dict):continue
  for key,row in rows.items():
   if key in result[code] and isinstance(row,dict):
    for field,value in row.items():
     if field in result[code][key]:result[code][key][field]=deepcopy(value)
 return result

def work_experience_rate(sim,key):
 skill=DEFS[key][2]
 base=sim.town_config['U']['study_'+skill]['work_xp_per_hour']
 return base*(2 if JOB_GRADES[key]['teacher'] else 1)

def validate_parameters(code,data):
 if code not in DEFAULTS or not isinstance(data,dict) or set(data)!=set(DEFAULTS[code]):raise ValueError('场所项目不完整或存在未知项目')
 for key,row in data.items():
  if not isinstance(row,dict) or set(row)!=set(DEFAULTS[code][key]):raise ValueError('参数字段与该项目不一致')
  for field,value in row.items():
   if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('参数必须是有限数字')
   _,_,lo,hi=FIELDS[field]
   if not lo<=value<=hi:raise ValueError(f'{FIELDS[field][0]}应在{lo}～{hi}之间')
   if field in ('minutes','min_level') and int(value)!=value:raise ValueError('时间和等级应为整数')
   if field=='minutes' and value%10:raise ValueError('时长必须是10分钟的倍数')
 return deepcopy(data)

def catalog(sim):
 return {c:{'name':name,'parameters':sim.town_config[c],'defaults':DEFAULTS[c],'services':{k:{'name':DEFS[k][1],'skill':SKILLS.get(DEFS[k][2],''),'skill_key':DEFS[k][2],'kind':DEFS[k][3],**JOB_GRADES.get(k,{})} for k in DEFAULTS[c]},'fields':{k:{'name':v[0],'unit':v[1],'min':v[2],'max':v[3],'step':10 if k=='minutes' else 1 if k=='min_level' else .01} for k,v in FIELDS.items()}} for c,name in NAMES.items()}

def register(activities):
 for key,(code,name,skill,kind) in DEFS.items():
  if key=='car':continue
  if key not in activities:
   activities[key]={'name':name,'emoji':'📚' if kind=='course' else '💼' if kind=='job' else '☕','duration':1,'need':'facility:'+code,'scene':'town','cost':0,'effects':{},'desc':name}
  if kind=='job':activities[key]['name']=name
 activities['home_treat']={'name':'在家行医','emoji':'🩺','duration':3,'need':'home_patient','scene':'house','cost':0,'effects':{'energy':-1},'desc':'医学1级起，可在家治疗自己或target室友，能力取决于医学等级'}
 activities['stock']={'name':'电脑股票交易','emoji':'📈','duration':1,'need':'own_computer','scene':'house','cost':0,'effects':{'energy':-.5},'desc':'经济学1级可在自己电脑买卖虚拟小镇指数，operation=buy/sell，shares填正整数；可能亏损'}
 # 原施工工作仅为旧决策兼容，新的模型菜单不提供。
 activities['build']['legacy']=True
 for key in ('buy_flower','buy_plant','shop','gift','attack','decorate_room','decorate_common'):
  activities[key]['fixed_duration']=True

def action_spec(sim,p,key):
 from .activities import ACTIVITIES
 if p.service_spec and p.service_spec.get('_key')==key:return deepcopy(p.service_spec)
 return deepcopy(ACTIVITIES[key])

def prepare(sim,p,d):
 from .activities import ACTIVITIES
 p.service_spec=None;p.service_completed=False
 key=d.action
 if key=='build':d.action=key='work'
 if key in DEFS and key!='car':
  code,name,skill,kind=DEFS[key];v=sim.town_config[code][key]
  if kind=='job' and skill and level(p,skill)<v['min_level']:raise ValueError(f'{name}需要{SKILLS[skill]}{v["min_level"]}级')
  if key.startswith('study_') and level(p,skill)>=5:raise ValueError('这门技能已经达到5级')
  energy=v.get('energy_cost_per_hour',0)
  if kind=='course':energy*=1-.03*level(p,'science')
  if kind=='job':energy*=1-.02*level(p,skill)
  if kind in ('course','job') and p.stats.energy<energy/6:raise ValueError('当前精力不足以完成这项课程或工作，请先休息')
  n=int(v.get('minutes',10)//10)
  a=deepcopy(ACTIVITIES[key]);a.update(name=name,need='facility:'+code,duration=n,cost=v.get('price',0),effects={},synergy=False,_key=key,_kind=kind,_terms=deepcopy(v),_skill=skill,fixed_duration=kind=='goods')
  if code=='W':a['need']='pond_shore';a['emoji']='🏊' if key=='swim' else '🎣'
  if code=='J':a['emoji']='🌷' if key=='garden_flowers' else '🌿'
  for field,stat in [('energy_gain_per_hour','energy'),('mood_per_hour','mood'),('satiety_per_hour','satiety'),('hp_per_hour','hp')]:
   if v.get(field):a['effects'][stat]=v[field]/6
  if energy:a['effects']['energy']=a['effects'].get('energy',0)-energy/6
  if kind=='job':
   a['effects']['money']=v['hourly_wage']/6
   a['_work_xp_per_hour']=work_experience_rate(sim,key)
  p.service_spec=a
 elif key in ('stock','home_treat'):
  skill='economics' if key=='stock' else 'medicine'
  if level(p,skill)<1:raise ValueError(f'需要{SKILLS[skill]}1级')
  if key=='stock' and (d.operation not in ('buy','sell') or not d.shares):raise ValueError('股票交易请指定buy/sell和正整数shares')
  if key=='home_treat':
   target=next((o for o in sim.players if o.pid==d.target or o.name==d.target),p if not d.target else None)
   if target is None or target.scene!='house':raise ValueError('只能为在家的自己或室友治疗')
   if target.illness_severity>level(p,'medicine'):raise ValueError('病情超过当前医学能力，请去诊所')
   if not target.illness_severity and target.stats.hp>=100:raise ValueError('对方目前不需要治疗')
 return action_spec(sim,p,key) if key in ACTIVITIES else None

def prompt_context(sim,p):
 lines=['技能：'+json.dumps({SKILLS[k]:{'等级':level(p,k),'经验':p.skills.get(k,0)} for k in SKILLS},ensure_ascii=False)]
 for key,(code,name,skill,kind) in DEFS.items():
  v=sim.town_config[code][key]
  info=f'{key} {NAMES[code]}·{name} '+json.dumps(v,ensure_ascii=False)
  if skill:info+=' 技能:'+SKILLS[skill]
  if kind=='job':info+=f' 工作经验率:{work_experience_rate(sim,key):g}/小时，等级:{JOB_GRADES[key]["grade"]}'
  lines.append(info)
 lines.append(f'医学每级可在家处理相应病重等级并恢复生命；烹饪提高自制餐饱食、心情和精力；理学每级降低课程精力消耗3%；文学让阅读更愉快；计算机让电脑游戏更愉快；经济学1级解锁电脑股票交易。每100经验升一级，最高5级。所有数值收益按每小时收益率乘实际活动时间计算，学习中断保留已学经验，每次选课开始时按price一次性付清学费，中途不再扣费。工作可延长，班次不能短于岗位要求；消费和商品一次性价格单独列出，一小时为6刻。')
 lines.append('各职业均在工作时积累对应学科技能经验；教师经验率固定为同学科普通职业的两倍，由学校对应课程的work_xp_per_hour统一设置。教师必须具备所教学科3级（二级教师5级），不需要其他游戏参与者。所有岗位每级相关技能额外节约2%精力，最多10%；二级岗位要求更高技能，但最低班次更短、时薪更高。医学每级每小时恢复16生命、为室友治疗时每级每小时增加4好感；烹饪每级每小时额外恢复4.8饱食、1.8精力和1.8心情；文学阅读及计算机游戏每级每小时额外增加1.2心情。经济学的工作效率随等级提高，虚拟股票仍可能亏损。')
 lines.append(f'虚拟小镇指数现价{sim.market_price:.2f}元/份；我持有{p.stock_shares}份，持仓成本{p.stock_cost:.2f}元；无杠杆，不保证收益。')
 return '\n'.join(lines)

def step_effects(sim,p,key,eff):
 elapsed=p.activity_total-p.activity_left+1
 if (p.activity or {}).get('_kind')=='job':
  due=round(p.activity['_terms']['hourly_wage']*elapsed/6,2)
  # 升级事件会立即记录检查点；工资和已付标记必须先一起落入状态。
  p.stats.money=round(p.stats.money+due-p.work_paid,2);p.work_paid=due;eff['money']=0
 if p.service_effects_applied>=elapsed:return eff
 p.service_effects_applied=elapsed
 if key.startswith('study_') or (p.activity or {}).get('_work_xp_per_hour') is not None:
  skill=key[6:] if key.startswith('study_') else p.activity['_skill']
  rate=p.activity['_terms']['xp_per_hour'] if key.startswith('study_') else p.activity['_work_xp_per_hour']
  credited_ticks=elapsed-p.activity.get('_work_xp_start_elapsed',0)
  due=round(rate*credited_ticks/6,6);delta=max(0,due-p.study_awarded);p.study_awarded=due
  before=level(p,skill);old_xp=p.skills.get(skill,0);p.skills[skill]=min(500,round(old_xp+delta,6))
  p.skill_xp_gained=round(p.skill_xp_gained+p.skills[skill]-old_xp,6)
  if level(p,skill)>before:sim.log('activity',f'📚 {p.name}的{SKILLS[skill]}达到{level(p,skill)}级',[p.pid])
 if key in ('cook','eat'):
  quality=level(p,'cooking') if key=='cook' else sim.meal_quality
  eff['satiety']=eff.get('satiety',0)+quality*.8
  eff['energy']=eff.get('energy',0)+quality*.3
  eff['mood']=eff.get('mood',0)+quality*.3
  if key=='cook':sim.meal_until=max(sim.global_tick,sim.meal_until)+15;sim.meal_quality=quality
 if key in ('decorate_room','decorate_common'):
  if key=='decorate_room':p.decor_room+=p.decor_gain_per_tick
  else:
   sim.decor_common+=p.decor_gain_per_tick
   for other in sim.players:
    if other is not p and other.scene=='house':other.stats.mood=min(100,other.stats.mood+1/2)
 if key=='gift' and p.target_player and world_same(p,p.target_player):
  other=p.target_player
  p.affinity[other.pid]=min(100,p.affinity.get(other.pid,30)+2)
  other.affinity[p.pid]=min(100,other.affinity.get(p.pid,30)+p.gift_affinity_per_tick)
 if key=='cafe_read':eff['mood']=eff.get('mood',0)+level(p,'literature')*.2
 if key=='computer_game':eff['mood']=eff.get('mood',0)+level(p,'computer')*.2
 if key in ('eat_out','coffee'):
  p.social_activity_tick[key]=sim.global_tick
  code='R' if key=='eat_out' else 'C'
  terms=(p.activity or {}).get('_terms',sim.town_config[code][key])
  gain=terms.get('affinity_per_hour',4 if key=='eat_out' else 8)/6
  for other in sim.players:
   if other is not p and other.scene=='town' and world_same(p,other) and (other.activity_key==key or other.social_activity_tick.get(key)==sim.global_tick):
    p.affinity[other.pid]=min(100,p.affinity.get(other.pid,50)+gain)
    if other.pid not in p.activity_companions:p.activity_companions.append(other.pid)
 if key=='home_treat':
  target=p.target_player or p
  if p.scene!='house' or target.scene!='house' or max(abs(p.x-target.x),abs(p.y-target.y))>3 or target.illness_severity>level(p,'medicine'):
   p.activity_left=1;return eff
  if not target.illness_severity and target.stats.hp>=100:p.activity_left=1;return eff
  target.stats.hp=min(100,target.stats.hp+16*level(p,'medicine')/6)
  if target is not p:target.affinity[p.pid]=min(100,target.affinity.get(p.pid,50)+4*level(p,'medicine')/6)
  p.treatment_ticks+=1;target.care_minutes+=10
  if target.care_minutes>=30 and target.illness_severity:sim.random_events.recover(sim,target,treated=True)
 if key=='treat':
  p.treatment_ticks+=1;p.care_minutes+=10
  if p.care_minutes>=p.activity.get('duration',3)*10 and p.illness_severity:sim.random_events.recover(sim,p,treated=True)
 return eff

def world_same(a,b):
 from .world import same_place
 return same_place(a,b)

def finish(sim,p,key):
 if p.service_completed:return
 p.service_completed=True
 if key.startswith('study_'):
  skill=key[6:]
  sim.log('activity',f'📚 {p.name}结束{SKILLS[skill]}学习，本次获得{p.skill_xp_gained:g}经验（{level(p,skill)}级）',[p.pid])
 elif (p.activity or {}).get('_work_xp_per_hour') is not None:
  skill=p.activity['_skill']
  sim.log('activity',f'💼 {p.name}结束{p.activity["name"]}工作，本次获得{p.skill_xp_gained:g}点{SKILLS[skill]}经验（{level(p,skill)}级）',[p.pid])
 elif key in ('coffee','eat_out'):
  for pid in p.activity_companions:
   other=sim.by_pid(pid)
   if other and p.pid<other.pid:sim.log('social',f'{p.name}与{other.name}一起'+('喝了咖啡' if key=='coffee' else '用过餐'),[p.pid,other.pid])
 elif key=='home_treat':
  target=p.target_player or p
  if p.treatment_ticks:sim.log('social',f'🩺 {p.name}在家为{target.name}提供了{p.treatment_ticks*10}分钟照护',[p.pid,target.pid])
 elif key=='stock':
  d=p.decision;count=d.shares;value=round(count*sim.market_price,2)
  if d.operation=='buy':
   if p.stats.money<value:p.remember('交易未完成：余额不足',3);return
   p.stats.money=round(p.stats.money-value,2);p.stock_shares+=count;p.stock_cost=round(p.stock_cost+value,2)
  else:
   if count>p.stock_shares:p.remember('交易未完成：持仓不足',3);return
   p.stock_cost=round(p.stock_cost*(p.stock_shares-count)/p.stock_shares,2);p.stock_shares-=count;p.stats.money=round(p.stats.money+value,2)
  p.stock_trades.append({'tick':sim.global_tick,'side':d.operation,'shares':count,'price':sim.market_price,'value':value})
  sim.log('activity',f'📈 {p.name}'+('买入' if d.operation=='buy' else '卖出')+f'小镇指数{count}份，价格{sim.market_price:.2f}，总额{value:.2f}元',[p.pid])

def update_market(sim):
 if sim.global_tick and sim.global_tick%6==0 and sim.market_last_tick!=sim.global_tick:
  sim.market_price=round(max(1,sim.market_price*(1+random.uniform(-.015,.015))),2);sim.market_last_tick=sim.global_tick
