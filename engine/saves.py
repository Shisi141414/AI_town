"""按时间命名的独立世界存档；记忆随存档隔离，JSON检查点原子替换。"""
from datetime import datetime
import json
from pathlib import Path
import random
import re
import shutil

from .models import Stats, Decision, GameEvent, Vehicle, MemoryItem
from .player import Player
from .conversation import Conversation
from .simulation import Simulation
from . import world

ID_PATTERN=re.compile(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}')
MODELS={c.__name__:c for c in (Stats,Decision,GameEvent,Vehicle)}


def pack(value):
    if isinstance(value,Player):return {'$player':value.pid}
    if type(value).__name__ in MODELS and hasattr(value,'model_dump'):
        return {'$model':type(value).__name__,'data':value.model_dump()}
    if isinstance(value,tuple):return {'$tuple':[pack(v) for v in value]}
    if isinstance(value,list):return [pack(v) for v in value]
    if isinstance(value,dict):return {k:pack(v) for k,v in value.items()}
    return value


def unpack(value,players):
    if isinstance(value,list):return [unpack(v,players) for v in value]
    if isinstance(value,dict):
        if '$player' in value:return players.get(value['$player'])
        if '$tuple' in value:return tuple(unpack(v,players) for v in value['$tuple'])
        if '$model' in value:return MODELS[value['$model']].model_validate(value['data'])
        return {k:unpack(v,players) for k,v in value.items()}
    return value


def recording_segments(sim):
    segments={s['id']:dict(s) for s in getattr(sim,'recording_segments',[])}
    if sim.recorder:
        rid=sim.recorder.id
        if not sim.recorder.closed:
            with sim.recorder.lock:
                frames=sim.recorder.db.execute("SELECT COUNT(*) FROM records WHERE kind='frame'").fetchone()[0]
            segments[rid]={'id':rid,'frames':frames}
        elif rid not in segments:
            segments[rid]={'id':rid,'frames':getattr(sim.recorder,'frame_count',0)}
    return list(segments.values())


def capture(sim,reason='状态帧'):
    players=[pack({k:v for k,v in vars(p).items() if k not in ('memory','_conversation')}) for p in sim.players]
    attrs=('day','tick','global_tick','speed','decor_common','meal_until','dialogue_log','affinity_queue','sex_proposals','_llm_error_count','model_of','binding_of','names','town_config','mechanics','opening_hours','activity_config','activity_descriptions','activity_overrides','disabled_activities','custom_activities','user_prompt_template','meal_quality','market_price','market_last_tick','stocks','venue_revenue')
    data={'version':1,'id':sim.save_id,'name':(sim.save_id or datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))[:19].replace('_',' '),'updated':datetime.now().isoformat(),
        'reason':reason,'state':pack({k:getattr(sim,k) for k in attrs}),'players':players,
        'events':[e.model_dump() for e in sim.events],
        'maps':{s:[''.join(r) for r in world.grid(s)] for s in ('house','town')},
        'vehicles':[v.model_dump() for v in sim.vehicles],
        'conversations':[pack({k:v for k,v in vars(c).items() if k!='sim'}) for c in sim.conversations],
        'memories':{p.pid:[m.model_dump() for m in p.memory.items] for p in sim.players},
        'memory_states':{p.pid:pack({'rng':p.memory.rng.getstate(),'selection':p.memory.last_selection}) for p in sim.players},
        'random':pack({'global':random.getstate(),'events':sim.random_events.rng.getstate(),
                      'disease_ready':sim.random_events.disease_ready,'affinity_ready':sim.random_events.affinity_ready}),
        'display_frame':sim.process_frame(),
        'runtime_context':{'settings':sim.settings.model_dump(),'system_prompt':sim.system_prompt,
                'active_profile_id':sim.profile.id if sim.profile else None,
                'bindings':{pid:{'profile_id':sim.binding_of.get(pid,''),'model':sim.model_of.get(pid,'')} for pid in set(sim.binding_of)|set(sim.model_of)},
                'profiles':[p.model_dump(exclude={'api_key'}) for p in {**sim.profiles_by_id,**({sim.profile.id:sim.profile} if sim.profile else {})}.values()]},
        'recording_segments':recording_segments(sim),
        'recording_id':sim.recorder.id if sim.recorder else None}
    return data


class SaveStore:
    def __init__(self,root):self.root=Path(root)

    def folder(self,sid):
        if not ID_PATTERN.fullmatch(sid):raise ValueError('无效存档编号')
        return self.root/sid

    def allocate(self):
        now=datetime.now()
        sid=now.strftime('%Y-%m-%d_%H-%M-%S_%f')
        self.folder(sid).mkdir(parents=True)
        return sid

    def activate(self,sid):
        self.folder(sid)
        temp=self.root/'active.tmp'
        temp.write_text(json.dumps({'id':sid}),'utf-8')
        temp.replace(self.root/'active.json')

    def current(self):
        path=self.root/'active.json'
        if not path.exists():return None
        return self.read(json.loads(path.read_text('utf-8'))['id'])

    def read(self,sid):
        data=json.loads((self.folder(sid)/'state.json').read_text('utf-8'))
        if data.get('version')!=1 or data.get('id')!=sid:raise ValueError('无法识别的存档格式')
        return data

    def checkpoint(self,sim,reason='手动保存'):
        data=capture(sim,reason)
        data.update(self.metadata(sim.save_id))
        path=self.folder(sim.save_id)/'state.json'
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),'utf-8')
        temp.replace(path)
        return data

    def restore(self,data,sid=None):
        target_id=sid or data['id']
        maps=data['maps']
        if any(len(maps[s])!=26 or any(len(r)!=26 for r in maps[s]) for s in ('house','town')):
            raise ValueError('存档地图尺寸不正确')
        sim=Simulation(memory_dir=self.folder(target_id)/'memories');sim.save_id=target_id
        for raw in data['players']:
            p=Player(raw['pid'],raw['name'],raw['color'],raw['emoji'],memory_path=sim.memory_dir/(raw['pid']+'.sqlite3'))
            sim.players.append(p)
        players={p.pid:p for p in sim.players}
        for p,raw in zip(sim.players,data['players']):
            p.__dict__.update(unpack(raw,players))
            if 'skill_xp_gained' not in raw:p.skill_xp_gained=p.study_awarded
            p.gender=p.gender if p.gender in ('男','女') else '男'
            if p.state=='deciding':p.state='idle'
            if p.state=='doing' and p.service_completed:p.activity_left=0
            p.next_decision_at=0
            with p.memory.db:
                p.memory.db.execute('DELETE FROM memories')
                for item in reversed(data.get('memories',{}).get(p.pid,[])):p.memory._insert(MemoryItem.model_validate(item))
            if p.pid in data.get('memory_states',{}):
                meta=unpack(data['memory_states'][p.pid],players)
                p.memory.rng.setstate(meta['rng']);p.memory.last_selection=meta['selection']
        sim.__dict__.update(unpack(data['state'],players))
        from .town_services import merge_parameters,work_experience_rate,JOB_GRADES,DEFS
        sim.town_config=merge_parameters(sim.town_config)
        from .mechanics import DEFAULT_RULES,DEFAULT_HOURS,activity_defaults
        sim.mechanics={**DEFAULT_RULES,**{k:v for k,v in getattr(sim,'mechanics',{}).items() if k in DEFAULT_RULES}}
        from .town_services import STOCKS
        if not isinstance(getattr(sim,'stocks',None),dict) or 'index' not in sim.stocks:
            sim.stocks={k:dict(v) for k,v in STOCKS.items()}
            sim.stocks['index']['price']=max(1.0,float(getattr(sim,'market_price',10.0)))
        for key,row in sim.stocks.items():
            row.setdefault('base',STOCKS.get(key,{}).get('price',row['price']))
            row.setdefault('shares',STOCKS.get(key,{}).get('shares',100))
        if not isinstance(getattr(sim,'venue_revenue',None),dict):sim.venue_revenue={}
        for q in sim.players:
            if not isinstance(getattr(q,'stock_portfolio',None),dict):q.stock_portfolio={}
            if not q.stock_portfolio and q.stock_shares>0:
                q.stock_portfolio['index']={'shares':q.stock_shares,'cost':q.stock_cost}
        if getattr(sim,'user_prompt_template',''):
            sim.user_prompt_template=sim.user_prompt_template.replace('话费每小时50元','话费每刻{phone_fee_per_tick:g}元').replace('话费每小时{phone_fee_per_hour:g}元','话费每刻{phone_fee_per_tick:g}元')
        sim.opening_hours={**DEFAULT_HOURS,**getattr(sim,'opening_hours',{})}
        defaults_activity=activity_defaults()
        sim.activity_config={**defaults_activity,**{key:value for key,value in getattr(sim,'activity_config',{}).items() if key in defaults_activity}}
        from .activities import activity_description_defaults
        default_descriptions=activity_description_defaults()
        sim.activity_descriptions={**default_descriptions,**{key:value for key,value in getattr(sim,'activity_descriptions',{}).items() if key in default_descriptions}}
        sim.activity_overrides=getattr(sim,'activity_overrides',{})
        sim.disabled_activities=list(getattr(sim,'disabled_activities',[]))
        sim.custom_activities=getattr(sim,'custom_activities',{})
        # 旧存档的电话费曾混在活动效果中，现统一由基础机制结算。
        if 'phone' in sim.activity_config:
            sim.activity_config['phone']['effects'].pop('money',None)
        for p in sim.players:
            if p._pending_key=='game':
                p._pending_key='';p.interaction_slot=None;p.path=[];p.state='idle'
        for p in sim.players:
            if not p.driver_license and p.travel_mode=='car':
                sim.drop_passengers(p);sim.release_car(p);p.travel_mode='walk'
        for p in sim.players:
            for contract in (p.activity,p.service_spec):
                key=(contract or {}).get('_key')
                if key in JOB_GRADES and '_work_xp_per_hour' not in contract:
                    contract['_skill']=DEFS[key][2]
                    contract['_work_xp_per_hour']=work_experience_rate(sim,key)
                    if contract is p.activity:
                        contract['_work_xp_start_elapsed']=max(0,p.activity_total-p.activity_left)
        context=data.get('runtime_context',{})
        if context:
            from .models import Settings,ApiProfile
            sim.settings=Settings.model_validate(context['settings']);sim.system_prompt=context['system_prompt']
            sim.profiles_by_id={p['id']:ApiProfile(**p) for p in context.get('profiles',[])}
            sim.profile=sim.profiles_by_id.get(context.get('active_profile_id'))
        sim.recording_segments=list(data.get('recording_segments',[]))
        if not sim.recording_segments and data.get('recording_id'):
            sim.recording_segments=[{'id':data['recording_id'],'frames':None}]
        sim.events=[GameEvent.model_validate(e) for e in data['events']]
        sim.event_id=max((e.id for e in sim.events),default=0)
        sim.vehicles=[Vehicle.model_validate(v) for v in data['vehicles']]
        for raw in data['conversations']:
            c=Conversation.__new__(Conversation);c.__dict__.update(unpack(raw,players));c.sim=sim
            sim.conversations.append(c)
            for p in (c.a,c.b):p._conversation=c
        states=unpack(data['random'],players)
        random.setstate(states['global']);sim.random_events.rng.setstate(states['events'])
        sim.random_events.disease_ready=states['disease_ready'];sim.random_events.affinity_ready=states['affinity_ready']
        for scene in ('house','town'):world.grid(scene)[:]=[list(r) for r in maps[scene]]
        for y in range(2,11):
            for x in range(10,17):
                if world.grid('town')[y][x]=='.':world.grid('town')[y][x]='J'
        # 旧世界下方事务所升级为饭店，保留损坏格和其他地图变化。
        school_migrated=False
        for y in range(20,24):
            for x in range(10,14):
                if world.grid("town")[y][x]=="~":world.grid("town")[y][x]="U";school_migrated=True
        if school_migrated:
            for p in sim.players:
                if p.activity_key=='build' or p._pending_key=='build':
                    p.state='idle';p.activity=None;p.activity_key='';p.activity_left=0;p.path=[];p.route_goal=None;p._pending_key='';p._final_goal=None;p._final_scene=None
        migrated=False
        for y in range(7,11):
            for x in range(19,25):
                if world.grid('town')[y][x]=='O':world.grid('town')[y][x]='R';migrated=True
        if migrated:
            for p in sim.players:
                if p.activity_key=='work' and p.scene=='town' and 18<=p.x<=25 and 6<=p.y<=11:
                    p.state='idle';p.activity=None;p.activity_key='';p.activity_left=0
                if p._pending_key=='work' and ((p._final_scene=='town' and p._final_goal and 18<=p._final_goal[0]<=25 and 6<=p._final_goal[1]<=11) or (p.scene=='town' and p.route_goal and 18<=p.route_goal[0]<=25 and 6<=p.route_goal[1]<=11)):
                    p.state='idle';p.path=[];p.route_goal=None;p._pending_key='';p._final_goal=None;p._final_scene=None
        for p in sim.players:p.memory.purge_runtime_notices()
        from .interaction_points import LOCAL_ACTIONS,route
        for p in sim.players:
            if not p.interaction_slot:
                if p.state=='doing' and p.activity_key in LOCAL_ACTIONS and p.activity_left>0 and not p.service_completed:
                    route(sim,p,p.activity_key);p.interaction_resume=True
                elif p.state=='moving' and p._pending_key in LOCAL_ACTIONS:
                    route(sim,p,p._pending_key)
        sim.repair_positions()
        return sim

    def metadata(self,sid):
        path=self.folder(sid)/'metadata.json'
        if path.exists():return json.loads(path.read_text('utf-8'))
        return {'name':sid[:10]+' '+sid[11:19].replace('-',':'),'favorite':False}

    def update_metadata(self,sid,changes):
        self.read(sid)
        meta=self.metadata(sid)
        if 'name' in changes:
            name=str(changes['name']).strip()
            if not 1<=len(name)<=80:raise ValueError('名称长度应为1～80字')
            meta['name']=name
        if 'favorite' in changes:
            if not isinstance(changes['favorite'],bool):raise ValueError('收藏状态必须为布尔值')
            meta['favorite']=changes['favorite']
        target=self.folder(sid)/'metadata.json';temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(meta,ensure_ascii=False),'utf-8');temp.replace(target)
        return meta

    def delete(self,sid):
        self.read(sid)
        active=self.current()
        if active and active['id']==sid:raise ValueError('请先切换到其他存档，再删除当前存档')
        target=self.folder(sid).resolve();root=self.root.resolve()
        if target.parent!=root:raise ValueError('存档路径超出范围')
        shutil.rmtree(target)

    def listing(self):
        if not self.root.exists():return []
        out=[]
        for path in sorted(self.root.glob('*/state.json'),reverse=True):
            data=self.read(path.parent.name);state=unpack(data['state'],{})
            out.append({'id':data['id'],**self.metadata(data['id']),'day':state['day'],
                'clock':f"{state['tick']//6:02d}:{state['tick']%6*10:02d}",
                'reason':data['reason'],'updated':data['updated'],'residents':len(data['players']),
                'memory_count':sum(len(ms) for ms in data.get('memories',{}).values()),
                'context_complete':bool(data.get('runtime_context')),
                'recording_segments':data.get('recording_segments',[]),
                'recording_id':data.get('recording_id')})
        return sorted(out,key=lambda item:(item['favorite'],item['updated']),reverse=True)

    def replay_segments(self,data,directory):
        from .recording import open_recording
        segments=data.get('recording_segments') or ([{'id':data['recording_id'],'frames':None}] if data.get('recording_id') else [])
        out=[]
        for seg in segments:
            try:
                with open_recording(directory,seg['id']) as db:
                    if seg.get('frames') is None:
                        n=db.execute("SELECT COUNT(*) FROM records WHERE kind='frame' AND json_extract(data,'$.tick')<=?",(data['state']['global_tick'],)).fetchone()[0]
                    else:n=min(seg['frames'],db.execute("SELECT COUNT(*) FROM records WHERE kind='frame'").fetchone()[0])
                if n:out.append({'id':seg['id'],'frames':n})
            except (ValueError,FileNotFoundError):continue
        return out

    def replay_frame(self,data,directory,index):
        from .recording import read_frame
        segments=self.replay_segments(data,directory);total=sum(s['frames'] for s in segments)
        if not total:
            if index!=0:raise IndexError('回放位置超出范围')
            frame=data.get('display_frame')
            if frame is None:
                # 兼容早期检查点：仅构造展示用角色，不修改世界或随机状态。
                ps={r['pid']:Player(r['pid'],r['name'],r['color'],r['emoji']) for r in data['players']}
                for raw in data['players']:
                    p=ps[raw['pid']];p.__dict__.update(unpack(raw,ps))
                    if p.gender not in ('男','女'):p.gender='男'
                names={pid:p.name for pid,p in ps.items()};snapshots=[]
                for pid,p in ps.items():
                    snap=p.snapshot(names).model_dump();ms=data.get('memories',{}).get(pid,[])
                    snap['memory_counts']={str(t):sum(m['tier']==t for m in ms) for t in (1,2,3)}
                    snapshots.append(snap);p.memory.close()
                state=unpack(data['state'],ps)
                frame={**state,'clock':f"{state['tick']//6:02d}:{state['tick']%6*10:02d}",
                       'players':snapshots,'vehicles':data['vehicles'],'maps':data['maps']}
            frame={**frame,'events':data['events'],'checkpoint':data,'running':False}
            return {'index':0,'total':1,'frame':frame}
        if not 0<=index<total:raise IndexError('回放位置超出范围')
        local=index
        for seg in segments:
            if local<seg['frames']:
                result=read_frame(directory,seg['id'],local)
                result.update(index=index,total=total)
                return result
            local-=seg['frames']

    def import_legacy(self,export,sid):
        # 首次升级：保留旧服务公开的居民/数值/记忆/地图，未完成路线重新决策。
        from . import activities
        frame=export['frame']
        sim=Simulation(memory_dir=self.folder(sid)/'memories');sim.save_id=sid
        sim.day=frame['day'];sim.tick=int(frame['clock'][:2])*6+int(frame['clock'][3:])//10
        sim.global_tick=frame.get('tick',0)
        sim.speed=frame['speed'];sim.decor_common=frame['decor_common'];sim.meal_until=frame.get('meal_until',0)
        ids={r['name']:r['pid'] for r in frame['players']}
        for i,r in enumerate(frame['players']):
            p=Player(r['pid'],r['name'],r['color'],r['emoji'],bedroom=world.BEDROOM_ANCHORS[i%5],memory_path=sim.memory_dir/(r['pid']+'.sqlite3'))
            for key in ('scene','x','y','plan','model','items','decor_room','identity_done','travel_mode','transport','vehicle_driver','passengers','car_id','speech'):
                if key in r:setattr(p,key,r[key])
            p.gender=r.get('gender') if r.get('gender') in ('男','女') else '男'
            p.stats=Stats.model_validate(r['stats']);p.affinity={ids.get(k,k):v for k,v in r['affinity'].items()}
            p.day=sim.day;p.world_tick=sim.global_tick;p.hour=sim.hour
            p.activity_left=r.get('activity_minutes_left',0)//10;p.activity_total=r.get('activity_minutes_total',0)//10
            if r['state']=='doing':
                key=next((k for k,v in activities.ACTIVITIES.items() if v['name']==r['activity']),None)
                if key:p.activity_key=key;p.activity=activities.ACTIVITIES[key];p.state='doing'
            with p.memory.db:
                for m in reversed(export.get('memories',{}).get(p.pid,[])):p.memory._insert(MemoryItem.model_validate(m))
            sim.players.append(p);sim.names[p.pid]=p.name
        sim.vehicles=[Vehicle.model_validate(v) for v in frame['vehicles']]
        sim.events=[GameEvent.model_validate(e) for e in export.get('events',[])];sim.event_id=max((e.id for e in sim.events),default=0)
        for scene,rows in frame['maps'].items():world.grid(scene)[:]=[list(r) for r in rows]
        for p in sim.players:p.memory.purge_runtime_notices()
        sim.repair_positions()
        return sim
