"""玩家框架 v2 —— 场景感知 + 跨场景出行 + 协同增益 + 物品装饰

生命周期（tick 状态机）：
    idle → deciding(sim 驱动) → [出门 crossing] → moving → doing → idle

跨场景出行：意图目的地在另一场景时：
    1. 走到本场景大门 E
    2. 切换场景（house⇄town），坐标跳到对面大门
    3. 继续寻路到最终目的地
（即真实世界的「先下楼，再走到目的地」）

协同增益：活动开始时统计同区域同活动的人数与平均好感，
    应用 synergy_effects() 到每 tick 结算。

装饰系统：房间/公共区装饰度 → 持续心情加成（每 tick +decor/200）。
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from . import activities as act
from . import brain, world
from .memory import MemoryStream
from .models import Decision, PlayerSnapshot, Stats

if TYPE_CHECKING:
    from .simulation import Simulation


class Player:
    def __init__(self, pid: str, name: str, color: str, emoji: str,
                 bedroom: tuple[int, int] | None = None, memory_path=None):
        self.pid = pid                       # 稳定 ID：配置绑定/好感键
        self.name = name
        self.color = color
        self.emoji = emoji
        self.gender = "男"
        self.skills = {}
        self.stock_shares = 0
        self.stock_cost = 0.0
        self.stock_trades = []
        self.service_spec = None
        self.interaction_slot = None
        self._bed_partner_pid = None
        self._bed_initiator_pid = None
        self.interaction_resume = False
        self.service_completed = False
        self.dining_tick = -1
        self.work_paid = 0.0
        self.tuition_paid = 0.0
        self.study_awarded = 0.0
        self.skill_xp_gained = 0.0
        self.service_effects_applied = 0
        self.treatment_ticks = 0
        self.social_activity_tick = {}
        self.activity_companions = []
        self.decor_gain_per_tick = 0.0
        self.gift_affinity_per_tick = 0.0
        self.computer_files = []
        self.computer_operation_done = False
        self.computer_feedback = ""
        self.transactions = []
        self.stats = Stats()
        self.memory = MemoryStream(memory_path)
        self.care_minutes = 0
        self.low_mood_ticks = 0
        self.illness_severity = 0
        self.illness_until = 0
        self.illness_name = ""
        self.affinity: dict[str, float] = {}  # 键改为 pid
        self.scene = "house"
        self.travel_mode = "walk"
        self.transport = "walk"
        if self.interaction_slot and not self.interaction_slot.get('reserved',True):
            point=tuple(self.interaction_slot['position'])
            if point not in sim.occupied('house',self):self.interaction_slot['reserved']=True
        self.vehicle_driver = None
        self.passengers = []
        self.car_id = None
        # 出生：自己的卧室
        if bedroom:
            self.x, self.y = bedroom
        else:
            self.x, self.y = world.nearest_walkable("house", 12, 12)
        self.bedroom = bedroom or (self.x, self.y)
        self.state = "idle"
        self.activity: dict | None = None
        self.activity_key = ""
        self.activity_left = 0
        self.activity_total = 0
        self.activity_custom_duration = False
        self.requested_duration = None
        self.decision_status = ""
        self.next_decision_at = 0.0
        self.target_player: "Player | None" = None
        self.path: list[tuple[int, int]] = []
        self.route_goal = None
        self.blocked_ticks = 0
        self._blocked_cell = None
        self.decision: Decision | None = None
        self.plan = ""
        self.speech = ""
        self.speech_tick = 0
        self.day = 1
        self.world_tick = 0
        self.hour = 8
        self._pending_key = ""
        self._pending_target_pid: str | None = None
        self._final_goal: tuple[int, int] | None = None   # 跨场景的最终目的地
        self._final_scene: str | None = None
        self.model = ""
        self.items: dict[str, int] = {"flower": 0, "plant": 0}
        self.decor_room = 0.0
        self.identity_done = False           # 开局身份是否已确定
        # 出行统计（规则引擎的跨场景加权用）
        self.last_out_tick = 0
        self.last_home_tick = 0
        # 回合制对话（由 simulation 的对话引擎驱动）
        self._conversation = None

    # ------------------------------------------------------------- 感知
    def nearby_players(self, others: list["Player"], radius: int = 4) -> list["Player"]:
        """同场景内、距离 ≤radius 或同区域（same_place）的玩家"""
        out = []
        for o in others:
            if o.scene != self.scene:
                continue
            if max(abs(o.x - self.x), abs(o.y - self.y)) <= radius or world.same_place(self, o):
                out.append(o)
        return out

    def same_zone_players(self, sim: "Simulation") -> list["Player"]:
        """与我在同一区域（相遇判定）的其他玩家"""
        return [o for o in sim.players if o is not self and world.same_place(self, o)]

    # ------------------------------------------------------------- 快照
    def snapshot(self, name_of: dict[str, str] | None = None) -> PlayerSnapshot:
        name_of = name_of or {}
        aff_named = {name_of.get(k, k): v for k, v in self.affinity.items()}
        return PlayerSnapshot(
            pid=self.pid, name=self.name, color=self.color, emoji=self.emoji, gender=self.gender,
            scene=self.scene, x=self.x, y=self.y, stats=self.stats,
            state=self.state,
            activity=(self.activity["name"] if self.activity else ""),
            activity_emoji=(self.activity["emoji"] if self.activity else ""),
            target_name=(self.target_player.name if self.target_player else ""),
            plan=self.plan, model=self.model, skills=dict(self.skills), stock_shares=self.stock_shares, stock_cost=self.stock_cost, stock_trades=self.stock_trades, computer_files=self.computer_files, transactions=self.transactions,
            home=world.zone_of(self.scene, self.x, self.y),
            speech=(self.speech if self.world_tick - self.speech_tick <= 2 else ""),
            affinity=aff_named, items=dict(self.items),
            decor_room=self.decor_room, identity_done=self.identity_done,
            travel_mode=self.travel_mode, transport=self.transport,
            vehicle_driver=self.vehicle_driver, passengers=list(self.passengers), car_id=self.car_id,
            activity_minutes_left=self.activity_left*10, activity_minutes_total=self.activity_total*10, decision_status=self.decision_status,
            memory_counts=self.memory.counts(), illness=self.health_description, illness_severity=self.illness_severity)

    @property
    def health_description(self):
        from .random_events import SEVERITY_NAMES
        if not self.illness_severity:
            return "健康"
        return f"{self.illness_name}（{SEVERITY_NAMES[self.illness_severity]}，预计还需{max(0,self.illness_until-self.world_tick)}刻恢复，已照护{self.care_minutes}分钟，可去诊所治疗）"

    # ------------------------------------------------------------- 记忆助手
    def remember(self, content: str, importance: float = 3.0, sim=None):
        self.memory.add(content, self.day, self.world_tick, importance)

    # ------------------------------------------------------------- 出行
    def travel_path(self, sim, tx, ty):
        car = next((v for v in sim.vehicles if v.id == self.car_id), None)
        if car and self.scene == "town":
            approach=world.bfs_path('town',self.x,self.y,car.x,car.y,blocked=sim.occupied('town',self))
            if (self.x,self.y)!=(car.x,car.y) and not approach:return []
            onward=world.travel_path('town',car.x,car.y,tx,ty,'car',blocked=sim.occupied('town',self))
            if (car.x,car.y)!=(tx,ty) and not onward:return []
            return approach+onward
        from .interaction_points import access_cells
        return world.travel_path(self.scene, self.x, self.y, tx, ty, self.travel_mode,blocked=sim.occupied(self.scene,self),allowed=access_cells(self,self.scene))

    def _plan_route(self, sim: "Simulation", dest_scene: str, tx: int, ty: int,
                    adjacent: bool = False):
        """规划到 (dest_scene, tx, ty) 的路线；跨场景则先到大门。"""
        if adjacent:
            candidates = [(tx + dx, ty + dy)
                          for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                          if sim.position_free(self,dest_scene,tx+dx,ty+dy)]
            if candidates:
                tx, ty = min(candidates,
                             key=lambda c: abs(c[0] - self.x) + abs(c[1] - self.y))
        if not (self.interaction_slot and dest_scene=='house' and tuple(self.interaction_slot['position'])==(tx,ty)):
            tx, ty = sim.free_position(dest_scene, tx, ty,self)
        if dest_scene == self.scene:
            self.route_goal=(tx,ty)
            self.path = self.travel_path(sim, tx, ty)
            self._final_goal = None
            self._final_scene = None
        else:
            # 先到本场景大门
            if self.scene == "house":
                gate = (25, 12)
            else:
                gate = (1, 12)
            gx, gy = world.nearest_walkable(self.scene, *gate)
            self.route_goal=(gx,gy)
            self.path = self.travel_path(sim, gx, gy)
            self._final_goal = (tx, ty)
            self._final_scene = dest_scene
        self.state = "moving"

    def _cross_gate(self, sim: "Simulation"):
        """到达大门：切换场景，继续向最终目的地走"""
        if self.scene == "house":
            self.x,self.y=sim.free_position("town",2,12,self)
            self.scene = "town"
            self.last_out_tick = self.world_tick
            sim.log("system", f"🚪 {self.name} 出门去了小镇", [self.name],scope="game")
        else:
            sim.drop_passengers(self)
            sim.release_car(self)
            self.travel_mode = "walk"
            self.x,self.y=sim.free_position("house",24,12,self)
            self.scene = "house"
            self.last_home_tick = self.world_tick
            sim.log("system", f"🏠 {self.name} 回到家", [self.name],scope="game")
        # 继续寻路
        if self._final_goal and self._final_scene == self.scene:
            tx, ty = self._final_goal
            self.route_goal=(tx,ty)
            self.path = self.travel_path(sim, tx, ty)
            self._final_goal = None
            self._final_scene = None
        else:
            self.path = []

    # ------------------------------------------------------------- 活动开始
    def begin_activity(self, key: str, sim: "Simulation", target=None) -> bool:
        from .town_services import action_spec
        a = action_spec(sim,self,key) if key in act.ACTIVITIES else None
        if not a:
            return False
        need = a.get("need", "")
        self.target_player = target
        self._pending_target_pid = None
        from .interaction_points import LOCAL_ACTIONS,route,release,route_bed_pair
        if self.interaction_slot:release(sim,self)

        if key in LOCAL_ACTIONS:return route(sim,self,key)

        if key=='love' and self.decision and self.decision.location=='bed':
            if target is None or self.affinity.get(target.pid,0)<55 or target.affinity.get(self.pid,0)<55:return False
            return route_bed_pair(sim,self,target)

        # 需要物品
        if a.get("needs_item") and not any(v > 0 for v in self.items.values()):
            self.remember(f"我想{a['name']}但没有鲜花/盆栽", 4)
            return False

        if key=='home_treat':
            target=target or self
            self.target_player=target;self._pending_target_pid=target.pid;self._pending_key=key
            self._plan_route(sim,'house',target.x,target.y,adjacent=target is not self)
            return True

        # 玩家目标类
        if need == "player":
            if target is None or target.scene != self.scene:
                return False
            if max(abs(target.x - self.x), abs(target.y - self.y)) > 6 \
                    and not world.same_place(self, target):
                return False
            if key == "love":
                mutual = (self.affinity.get(target.pid, 0) >= a["consent_threshold"]
                          and target.affinity.get(self.pid, 0) >= a["consent_threshold"])
                if not mutual:
                    return False
            self._pending_key = key
            self._pending_target_pid = target.pid
            self._plan_route(sim, self.scene, target.x, target.y, adjacent=True)
            return True

        if key in ("computer", "computer_game", "stock"):
            from .resident_tools import computer_seat
            self._pending_key=key
            self._plan_route(sim,"house",*computer_seat(self))
            return True

        if need=='pond_shore':
            shores={(x+dx,y+dy) for x,y in world.find_tiles('town','W')
                    if key!='swim' or (x,y) not in sim.occupied('town',self)
                    for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                    if sim.position_free(self,'town',x+dx,y+dy)}
            if not shores:return False
            ref=(self.x,self.y) if self.scene=='town' else (1,12)
            shore=min(shores,key=lambda q:abs(q[0]-ref[0])+abs(q[1]-ref[1]))
            self._pending_key=key;self._plan_route(sim,'town',*shore)
            return True

        # 设施类
        chars = act.facility_chars(key)
        if need == "any_house_common":
            chars = ["L", "D", "K", "a"]
        if chars:
            if key in ("sleep", "decorate_room"):
                self._pending_key = key
                self._plan_route(sim, "house", *self.bedroom)
                return True
            dest_scene = a.get("scene", "any")
            if dest_scene not in ("house", "town"):
                dest_scene = self.scene
            # decorate_common：公共区在室内找
            if a.get("need") == "any_house_common":
                dest_scene = "house"
                chars = ["L", "D", "K", "a"]
            best, best_d = None, 10**9
            # 家里的活动优先选离自己近的；跨场景则从门口算
            ref = (self.x, self.y) if dest_scene == self.scene else (
                (1, 12) if dest_scene == "town" else (24, 12))
            for ch in chars:
                for (tx, ty) in world.find_tiles(dest_scene, ch):
                    d = abs(tx - ref[0]) + abs(ty - ref[1])
                    if d < best_d:
                        best_d, best = d, (tx, ty)
            if best is None:
                return False
            self._pending_key = key
            self._plan_route(sim, dest_scene, *best)
            return True

        # 任意地点（think/wander）
        if need is None:
            self._pending_key = key
            self._start_local(key, sim)
            return True
        return False

    def _start_local(self, key: str, sim: "Simulation"):
        from .town_services import action_spec
        a = action_spec(sim,self,key)
        from .interaction_points import LOCAL_ACTIONS,options
        if key in LOCAL_ACTIONS:
            if not self.interaction_slot or self.interaction_slot['key']!=key:
                from .interaction_points import route
                route(sim,self,key);return
            if self.scene!='house' or (self.x,self.y)!=tuple(self.interaction_slot['position']):
                self.state='moving';return
        cost = a.get("cost", 0)
        energy_need=max(0,-a.get('effects',{}).get('energy',0))
        if a.get('_kind') in ('course','job') and self.stats.energy+1e-8<energy_need:
            self.state,self.activity,self.activity_key,self._pending_key='idle',None,'',''
            self.remember('抵达后精力不足，需先休息再上课或工作',3)
            return
        if self.stats.money < cost:
            self.state, self.activity, self.activity_key, self._pending_key = "idle", None, "", ""
            self.remember(f"抵达后钱不够，无法{a['name']}", 3)
            return
        if key in ('fish','swim'):
            waters=[(self.x+dx,self.y+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                    if self.scene=='town' and world.tile('town',self.x+dx,self.y+dy)=='W']
            if key=='swim':waters=[q for q in waters if q not in sim.occupied('town',self)]
            if not waters:
                self.state='idle';self._pending_key='';return
            if key=='swim':
                sim.drop_passengers(self);sim.release_car(self)
                self.x,self.y=waters[0];self.transport='walk'
        # 一次性付款，避免获得物品后通过上车/中断活动绕过剩余费用。
        self.stats.money -= cost
        self.activity = a
        self.work_paid = 0.0
        self.tuition_paid = 0.0
        self.study_awarded = 0.0
        self.skill_xp_gained = 0.0
        self.service_effects_applied = 0
        self.treatment_ticks = 0
        self.activity_companions = []
        self.decor_gain_per_tick = 0.0
        self.gift_affinity_per_tick = 0.0
        self.activity_key = key
        self.activity_custom_duration = self.requested_duration is not None
        self.activity_left = self.requested_duration or a["duration"]
        self.activity_total = self.activity_left
        self.requested_duration = None
        self.state = "doing"
        # 物品类活动：购买获得/布置消耗
        if a.get("gives_item"):
            self.items[a["gives_item"]] = self.items.get(a["gives_item"], 0) + 1
            it = act.ITEMS[a["gives_item"]]
            self.remember(f"我买了{it['name']}", 3)
        if a.get("needs_item"):
            self._consume_decor_item(key, sim)
        sim.log("activity", f"{self.name} 开始{a['name']} {a['emoji']}（计划{self.activity_total*10}分钟）", [self.name],scope="game")

    def _consume_decor_item(self, key: str, sim: "Simulation"):
        """布置房间/公共区：消耗物品，提升装饰度"""
        # 优先用装饰度低的物品？用盆栽先（decor 高）——玩家自己决定不了，按顺序
        item = "plant" if self.items.get("plant", 0) > 0 else "flower"
        if self.items.get(item, 0) <= 0:
            return
        self.items[item] -= 1
        self.decor_gain_per_tick=act.ITEMS[item]['decor']/act.ACTIVITIES[key]['duration']
        self.remember(f"我用{act.ITEMS[item]['name']}开始布置",4)

    # ------------------------------------------------------------- tick
    def tick(self, sim: "Simulation") -> None:
        if self.vehicle_driver:
            driver = sim.by_pid(self.vehicle_driver)
            if driver:
                self.scene, self.x, self.y = driver.scene, driver.x, driver.y
                self.transport = "passenger"
                return
            self.vehicle_driver = None
            self.state = "idle"
        # 对话中：位置不动、不做活动结算，说话由 Conversation 驱动
        if self._conversation is not None and self._conversation.phase != "ended":
            return

        self.transport = "walk"
        if self.state=='waiting_activity':
            from .interaction_points import arrive_bed
            arrive_bed(sim,self);return
        if self.state == "moving":
            if self.route_goal is None and self.path:self.route_goal=self.path[-1]
            goal=self.route_goal
            if goal and (self.x,self.y)!=goal and (not self.path or abs(self.path[0][0]-self.x)+abs(self.path[0][1]-self.y)!=1 or not sim.position_free(self,self.scene,*self.path[0])):
                self.blocked_ticks+=1
                obstruction=self.path[0] if self.path else self._blocked_cell
                if obstruction is None:
                    direct=world.travel_path(self.scene,self.x,self.y,*goal,self.travel_mode)
                    occupied=sim.occupied(self.scene,self)
                    obstruction=next((cell for cell in direct if cell in occupied),None)
                self._blocked_cell=obstruction
                # 目标被占时使用相邻空位；途中遇人先绕行，无法绕行再侧身让路。
                if goal in sim.occupied(self.scene,self) and abs(goal[0]-self.x)+abs(goal[1]-self.y)<=2:
                    self.route_goal=goal=sim.free_position(self.scene,*goal,self)
                self.path=self.travel_path(sim,*goal)
                if not self.path and self.blocked_ticks>=2:
                    blocker=next((q for q in sim.players if q is not self and q.scene==self.scene and (q.x,q.y)==obstruction and not q.vehicle_driver),None)
                    if blocker and sim.yield_position(blocker,self):self.path=self.travel_path(sim,*goal)
                if not self.path and self.blocked_ticks>=2:
                    neighbours=[(self.x+dx,self.y+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                                if sim.position_free(self,self.scene,self.x+dx,self.y+dy)]
                    if neighbours:
                        side=max(neighbours,key=lambda q:sum(sim.position_free(self,self.scene,q[0]+dx,q[1]+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))))
                        self.path=[side]
                if not self.path:return
            # 每格扣除时间预算，避免道路边缘超速或穿墙。
            budget = 1.0
            while self.path:
                dest = self.path[0]
                if abs(dest[0]-self.x)+abs(dest[1]-self.y)!=1 or not sim.position_free(self,self.scene,*dest):break
                mode = world.movement_mode(self.scene, (self.x, self.y), dest,
                                           self.travel_mode, self.stats.money)
                car = next((v for v in sim.vehicles if v.id == self.car_id and v.driver_id == self.pid), None)
                if mode == "car" and (not car or max(abs(car.x-self.x), abs(car.y-self.y)) > 1):
                    mode = "walk"
                if mode != "car" and self.passengers:
                    sim.drop_passengers(self)
                spec = world.TRAVEL_MODES[mode]
                time_cost = 1.0 / spec["speed"]
                if time_cost > budget + 1e-9:
                    break
                budget -= time_cost
                self.x, self.y = self.path.pop(0)
                self.blocked_ticks=0
                self._blocked_cell=None
                self.transport = mode
                self.stats.energy += act.MOVE_EXTRA_ENERGY / 3 * spec["energy"]
                self.stats.money = max(0.0, self.stats.money - spec["cost"])
                if mode == "car":
                    car.x, car.y = self.x, self.y
                if mode != "car":
                    self.stats.satiety = max(0.0, self.stats.satiety - (0.18 if mode == "walk" else 0.25))
                for pid in self.passengers:
                    passenger = sim.by_pid(pid)
                    if passenger:
                        passenger.scene, passenger.x, passenger.y = self.scene, self.x, self.y
            if not self.path:
                if self.route_goal and (self.x,self.y)!=self.route_goal:return
                # 到达大门？→ 跨场景
                if self._final_scene is not None and self._at_gate():
                    self._cross_gate(sim)
                    if self.path or (self.route_goal and (self.x,self.y)!=self.route_goal):
                        return
                if self.pending_key:
                    if self.interaction_resume:
                        self.interaction_resume=False;self.state='doing';return
                    if self._bed_partner_pid:
                        from .interaction_points import arrive_bed
                        arrive_bed(sim,self);return
                    if self._pending_target_pid:
                        t = sim.by_pid(self._pending_target_pid)
                        if t:
                            self._start_social(self.pending_key, sim, t)
                            return
                    self._start_local(self.pending_key, sim)
                    return
                self.state = "idle"
            return

        if self.state == "doing":
            # 对话类活动不结算数值效果（效果在对话结束后统一给）
            if self.activity_key in ("chat", "love"):
                return
            self._step_activity(sim)

    def _at_gate(self) -> bool:
        if self.scene == "house":
            return abs(self.x - 25) + abs(self.y - 12) <= 1
        return abs(self.x - 1) + abs(self.y - 12) <= 1

    @property
    def pending_key(self) -> str:
        return self._pending_key

    def _start_social(self, key: str, sim: "Simulation", t: "Player"):
        if t.scene != self.scene or (max(abs(t.x - self.x), abs(t.y - self.y)) > 2
                                     and not world.same_place(self, t)):
            self.state = "idle"
            self._pending_key = ""
            return
        self._pending_key = ""
        self.target_player = t
        self._start_local(key, sim)
        if key == "attack":
            self._resolve_attack(sim)
        if key in ("chat", "love"):
            sim.start_conversation(self, t, key)
        if key == "gift":
            self._resolve_gift(sim, t)

    def _resolve_gift(self, sim: "Simulation", t: "Player"):
        """送礼：物品转手 + 好感大幅提升（LLM 决定的部分在互动结算中）"""
        item = "plant" if self.items.get("plant", 0) > 0 else "flower"
        if self.items.get(item, 0) <= 0:
            return
        self.items[item] -= 1
        t.items[item] = t.items.get(item, 0) + 1
        base = act.ITEMS[item]["decor"]     # flower 6 / plant 10
        self.gift_affinity_per_tick=base
        t.remember(f"{self.name} 送了我{act.ITEMS[item]['name']}！", 8)
        self.remember(f"我送了{t.name}{act.ITEMS[item]['name']}", 6)
        sim.log("social", f"🎁 {self.name} 送 {t.name} {act.ITEMS[item]['emoji']}"
                          f"{act.ITEMS[item]['name']}", [self.name, t.name])
        t.speech = "谢谢！"
        t.speech_tick = t.world_tick

    def _resolve_attack(self, sim: "Simulation"):
        t = self.target_player
        if t is None:
            return
        dmg = act.ACTIVITIES["attack"]["damage"]
        t.stats.hp -= dmg
        self.affinity[t.pid] = self.affinity.get(t.pid, 30) - 12
        t.affinity[self.pid] = t.affinity.get(self.pid, 30) - 15
        t.remember(f"被{self.name}攻击了！损失{dmg:.0f}生命", 9)
        self.remember(f"我攻击了{t.name}", 7)
        sim.log("combat", f"⚔️ {self.name} 攻击了 {t.name}（-{dmg:.0f} HP）",
                [self.name, t.name])
        t.speech = "好痛！"
        t.speech_tick = t.world_tick
        t.state = "idle"
        t.activity = None
        t.activity_left = 0
        t.path = []

    # ------------------------------------------------------------- 活动结算
    def _step_activity(self, sim: "Simulation"):
        key = self.activity_key
        a = self.activity or act.ACTIVITIES.get(key, {})
        if self.activity_left <= 0:
            self._finish_activity(sim,a)
            return
        if a.get('_kind') in ('course','job') and self.stats.energy+1e-8<max(0,-a.get('effects',{}).get('energy',0)):
            self.remember('精力不足，结束活动，保留已获得的经验和工资',3)
            self._finish_activity(sim,a);return
        eff: dict = dict(a.get("effects", {}))
        from .town_services import step_effects
        eff=step_effects(sim,self,key,eff)
        if act.is_night(self.hour) and a.get("night_bonus"):
            for stat, bonus in a["night_bonus"].items():
                eff[stat] = eff.get(stat, 0) + bonus

        # ---- 协同增益 ----
        partners = []
        if a.get("synergy"):
            for o in sim.players:
                if o is not self and o.activity_key == key and o.scene == self.scene \
                        and world.same_place(self, o):
                    partners.append(o)
        if partners:
            n = len(partners) + 1
            affs = [self.affinity.get(o.pid, 50) for o in partners]
            avg_aff = sum(affs) / len(affs)
            stat = a.get("synergy_stat", "mood")
            calc = act.synergy_effects(eff, stat, n, avg_aff)
            eff = {k: v[0] for k, v in calc.items()}
            # 首次触发时播报
            if self.activity_left == self.activity_total:
                names = "、".join(o.name for o in partners)
                if act.synergy_multiplier(n, avg_aff) >= 1.0:
                    sim.log("social", f"🤝 {self.name} 和 {names} 一起{a['name']}（协同加成）",
                            [self.name] + [o.name for o in partners])
                else:
                    sim.log("social", f"😤 {self.name} 和 {names} 一起{a['name']}"
                                      f"但关系不睦（效果打折）",
                            [self.name] + [o.name for o in partners])

        # ---- 餐厅吃饭需要家里有饭（做饭后 60 tick 内有效）----
        if key == "eat" and sim.meal_until < self.world_tick:
            # 没饭了：转为去咖啡馆
            self.activity = None
            self.activity_key = ""
            self.state = "idle"
            self.remember("家里没饭了，得下馆子或自己做", 4)
            return

        for stat_name, delta in eff.items():
            setattr(self.stats, stat_name, getattr(self.stats, stat_name) + delta)
        self.stats.clamp()

        # ---- 装饰度心情加成 ----
        decor_bonus = (self.decor_room + sim.decor_common) / 200.0
        if decor_bonus > 0:
            self.stats.mood = min(100.0, self.stats.mood + decor_bonus)

        self.activity_left -= 1
        if self.activity_left <= 0:
            self._finish_activity(sim, a)

    def _finish_activity(self, sim: "Simulation", a: dict):
        key = self.activity_key
        from .town_services import finish
        finish(sim,self,key)
        if key == "computer":
            from .resident_tools import use_computer
            use_computer(sim,self,self.decision)
        if key == "destroy" and world.tile(self.scene, self.x, self.y) in act.facility_chars("destroy"):
            world.grid(self.scene)[self.y][self.x] = "X"
        # 社交/协同互动的好感结算：LLM 决定增减
        if key in ("chat", "love"):
            # 对话结束时 Conversation 已写记忆/好感，这里不重复处理
            pass
        elif key in ("game", "watch_tv", "work", "movie",
                     "park", "balcony", "cook", "eat", "exercise", "build"):
            partners = [o for o in sim.players
                        if o is not self and o.scene == self.scene
                        and world.same_place(self, o)
                        and o.activity_key == key]
            for o in partners:
                sim.request_affinity(self, o, key)
        self.remember(f"我结束了{a['name']}，实际进行了{max(0,self.activity_total-self.activity_left)*10}分钟",2)
        self.activity = None
        self.activity_key = ""
        self.target_player = None
        self._pending_key = ""
        self._pending_target_pid = None
        self.state = "idle"

        from .interaction_points import release
        release(sim,self)

        if key=='swim' and self.scene=='town' and world.tile('town',self.x,self.y)=='W':
            self.x,self.y=sim.free_position('town',self.x,self.y,self)

    # ------------------------------------------------------------- 被动结算
    def passive_drain(self, sim: "Simulation") -> None:
        for k, v in act.PASSIVE_DRAIN.items():
            if k == "satiety" and (self.vehicle_driver or self.transport == "car"):
                continue
            if v:
                setattr(self.stats, k, getattr(self.stats, k) + v)
        if self.stats.satiety <= 0:
            self.stats.hp += act.STARVE_HP_DAMAGE
        mood_delta = 0.0
        if self.illness_severity:
            mood_delta -= 4.0
        if self.stats.energy < act.LOW_ENERGY_MOOD_THRESHOLD:
            mood_delta -= (act.LOW_ENERGY_MOOD_THRESHOLD - self.stats.energy) / 10.0
        for other in self.same_zone_players(sim):
            mood_delta += act.relationship_mood_per_tick(self.affinity.get(other.pid, 50.0))
        self.stats.mood += mood_delta
        self.stats.clamp()

        self.low_mood_ticks = self.low_mood_ticks + 1 if self.stats.mood < act.LOW_MOOD_HP_THRESHOLD else 0
        started_harm = self.low_mood_ticks == act.LOW_MOOD_HP_DELAY_TICKS
        if self.low_mood_ticks >= act.LOW_MOOD_HP_DELAY_TICKS:
            self.stats.hp -= act.LOW_MOOD_HP_LOSS
        if self.stats.hp <= 0:
            sim.leave_vehicle(self)
            sim.drop_passengers(self)
            sim.release_car(self)
            self.stats.hp = 30
            self.stats.money = max(0.0, self.stats.money - 30)
            # 送到医院（室外）
            self.scene = "town"
            beds = world.find_tiles("town", "B")
            if beds:
                bx, by = random.choice(beds)
                self.x, self.y = sim.free_position("town", bx, by,self)
            self.activity = None
            self.state = "idle"
            self.path = []
            self._pending_key = ""
            self.remember("我晕倒了，被送去医院急救（-30元）", 9)
            sim.log("system", f"🚑 {self.name} 生命归零，被送医急救（-30元）",
                    [self.name],scope="game")
        self.stats.clamp()
        if started_harm:
            sim.log('random', f'😞 {self.name}连续心情低落，健康开始受影响', [self.pid])
