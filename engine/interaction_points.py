"""家具使用位置：仅对前往/使用该家具的角色开放，普通通行仍受碰撞限制。"""
from . import world

LOCAL_ACTIONS={'cook','eat','computer','computer_game','stock','sleep','shower','watch_tv','balcony'}


def bedroom_index(p):
    return min(range(len(world.BEDROOM_ANCHORS)),key=lambda i:abs(world.BEDROOM_ANCHORS[i][0]-p.bedroom[0])+abs(world.BEDROOM_ANCHORS[i][1]-p.bedroom[1]))


def bed_points(p):
    x,y,_,_=world.BEDROOM_RECTS[bedroom_index(p)]
    return [(x+1,y+2),(x,y+2)],[(x+i,y+j) for i in (0,1) for j in (1,2,3)]


def options(p,key):
    if key in ('computer','computer_game','stock'):
        i=bedroom_index(p);x,y,_,_=world.BEDROOM_RECTS[i]
        return [((x+(3 if i==4 else 4),y+(2 if i==4 else 3)),[])]
    if key=='sleep':
        points,cells=bed_points(p);return [(points[0],cells)]
    if key=='cook':return [((13,3),[]),((13,4),[])]
    if key=='eat':return [(q,[q]) for q in ((10,11),(13,11),(10,14),(13,14))]
    if key=='shower':return [(q,[q]) for q in ((13,23),(17,23))]
    if key=='watch_tv':
        cells=[(x,y) for x in range(19,24) for y in (8,9)]
        return [(q,cells) for q in ((20,8),(22,8),(21,8),(23,8))]
    if key=='balcony':return [(q,[]) for q in ((23,23),(24,23),(22,23),(23,24))]
    return []


def active_slot(p):
    slot=getattr(p,'interaction_slot',None)
    if not slot:return None
    if p.state=='doing' and p.activity_key==slot['key']:return slot
    if p.state in ('moving','waiting_activity') and p._pending_key==slot['key']:return slot
    return None


def access_cells(p,scene):
    slot=active_slot(p)
    if scene!='house' or not slot:return set()
    return {tuple(q) for q in slot['cells'] if world.tile(scene,*q) not in (world.WALL,' ')}


def assign(p,key,position,cells):
    p.interaction_slot={'key':key,'position':tuple(position),'cells':[tuple(q) for q in cells]}


def route(sim,p,key):
    p.interaction_slot=None
    choices=options(p,key)
    if not choices:return False
    # 被占用时保留目标并等待，不改成在附近任意地面执行。
    occupied=sim.occupied('house',p)
    ref=(p.x,p.y) if p.scene=='house' else (24,12)
    available=[q for q in choices if q[0] not in occupied]
    point,cells=min(available or choices,key=lambda q:abs(q[0][0]-ref[0])+abs(q[0][1]-ref[1]))
    assign(p,key,point,cells);p.interaction_slot['reserved']=point not in occupied
    p._pending_key=key;p.state='moving'
    p._plan_route(sim,'house',*point)
    return True


def release(sim,p):
    p.interaction_slot=None;p._bed_partner_pid=None;p._bed_initiator_pid=None
    if p.scene=='house' and not world.walkable('house',p.x,p.y):
        p.x,p.y=sim.free_position('house',p.x,p.y,p)


def route_bed_pair(sim,a,b,key='love'):
    if b._conversation or b.state not in ('idle','waiting_model') or b.vehicle_driver:return False
    points,cells=bed_points(a)
    occupied=sim.occupied('house',a)-({(b.x,b.y)} if b.scene=='house' else set())
    if any(q in occupied for q in points):return False
    for p,other,point in ((a,b,points[0]),(b,a,points[1])):
        assign(p,key,point,cells)
        p._bed_partner_pid=other.pid;p._bed_initiator_pid=a.pid
        p._pending_target_pid=None;p._pending_key=key
        p.state='moving'
        p._plan_route(sim,'house',*point)
    return True


def arrive_bed(sim,p):
    other=sim.by_pid(p._bed_partner_pid)
    if not other or other._bed_partner_pid!=p.pid or not active_slot(other):
        p.state='idle';p._pending_key='';release(sim,p);return
    p.state='waiting_activity'
    if other.state!='waiting_activity':return
    a=sim.by_pid(p._bed_initiator_pid);b=other if a is p else p
    key=p._pending_key
    if not a or (key=='love' and (a.affinity.get(b.pid,0)<55 or b.affinity.get(a.pid,0)<55)):
        for q in (p,other):q.state='idle';q._pending_key='';release(sim,q)
        return
    a._start_local(key,sim);b._start_local(key,sim)
    if key=='love':sim.start_conversation(a,b,'love')
