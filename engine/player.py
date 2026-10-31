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
        self.driver_license = False
        self.info_feedback = ""
        self.pending_memory_activity = ""
        self.sex_invitation = "无"
        self.skills = {}
        self.stock_shares = 0
        self.stock_cost = 0.0
        self.stock_trades = []
        self.stock_portfolio: dict[str, dict] = {}   # 股票代码 → {shares, cost}
        self.service_spec = None
        self.interaction_slot = None
        self._bed_partner_pid = None
        self._bed_initiator_pid = None
        self.interaction_resume = False
        self.service_completed = False
        self.dining_tick = -1
        self._hunger_alerted = False             # 饥饿预警去重（吃饱后重置）
        self._suspended_activity = None     # 被中断活动的剩余进度（同key继续时恢复）
        self._period_hp_at = -1             # 本刻生理期失血标记（归因用）
        self.work_paid = 0.0
        self.phone_paid = 0.0
        self.injury_alert = ''
        self.last_health_alert_tick = -1
        # 同种持续伤害（疾病/饥饿/低心情）只打断一次活动；伤害结束后清除，
        # 保证下一轮同类伤害仍能正常唤醒角色。存 JSON 兼容用 list 存储。
        self._injury_break_done = []
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
        self.hospitalized = False          # 住院中
        self.hospital_fee_ticks = 0        # 住院房费计时（每72刻一结算）
        self.awaiting_admission = False    # 已送医、正在借款凑钱
        self.ask_stay = False              # 住院后是否需要询问续住
        self.debt_goal = 0.0               # 需借金额
        self.loans: dict[str, float] = {}  # 债主pid → 累计借款
        self.affinity: dict[str, float] = {}  # 键改为 pid
        self.scene = "house"
        self.travel_mode = "walk"
        self.transport = "walk"
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
        self._rejection = ''            # 本次行动被拒原因（系统或对象给出）
        self._block_notified = False    # 本轮阻挡是否已写入记忆
        self.decision: Decision | None = None
        self.plan = ""
        self.last_system_prompt = ''
        self.last_user_prompt = ''
        self.last_raw_reply = ''
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
        self.items: dict[str, int] = {"flower": 0, "plant": 0, "meal": 0, "medicine": 0, "ingredient": 0}
        # 生理周期（cycle.py）：周期锚点/黄体期满足标记/经期提示标记
        # 初始锚点随机偏移（负值=已进行到周期中某阶段），避免全员周期同步
        from .cycle import CYCLE_TICKS
        self.cycle_anchor = -random.randrange(CYCLE_TICKS)
        self.cycle_satisfied_anchor = -1
        self.cycle_noticed = -1
        self.meal_qualities: list[int] = []
        self.meal_expiry: list[int] = []      # 每份饭菜的到期tick（与数量FIFO对应）
        self.eating_quality = 0
        self.eating_from_inventory = False
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
    def snapshot(self, name_of: dict[str, str] | None = None, sim=None) -> PlayerSnapshot:
        name_of = name_of or {}
        aff_named = {name_of.get(k, k): v for k, v in self.affinity.items()}
        from . import cycle
        relationships=[]
        if sim is not None:
            from . import relations
            for relation in relations.all_of(sim,self.pid):
                other=relation['b'] if relation['a']==self.pid else relation['a']
                relationships.append({'other_pid':other,'name':name_of.get(other,other),'type':relation['type'],
                                      'days':(sim.global_tick-relation['since'])//144+1})
        return PlayerSnapshot(
            pid=self.pid, name=self.name, color=self.color, emoji=self.emoji, gender=self.gender,
            scene=self.scene, x=self.x, y=self.y, stats=self.stats,
            state=self.state,
            activity=(self.activity["name"] if self.activity else ""),
            activity_emoji=(self.activity["emoji"] if self.activity else ""),
            target_name=(self.target_player.name if self.target_player else ""),
            plan=self.plan, model=self.model, skills=dict(self.skills), stock_shares=self.stock_shares, stock_cost=self.stock_cost, stock_trades=self.stock_trades, stock_portfolio={k: dict(v) for k, v in self.stock_portfolio.items()}, meal_expiry=[max(0, t - self.world_tick) for t in self.meal_expiry], computer_files=self.computer_files, transactions=self.transactions,
            home=world.zone_of(self.scene, self.x, self.y),
            speech=(self.speech if self.world_tick - self.speech_tick <= 2 else ""),
            affinity=aff_named, items=dict(self.items),
            decor_room=self.decor_room, identity_done=self.identity_done,
            travel_mode=self.travel_mode, driver_license=self.driver_license, transport=self.transport,
            vehicle_driver=self.vehicle_driver, passengers=list(self.passengers), car_id=self.car_id,
            activity_minutes_left=self.activity_left*10, activity_minutes_total=self.activity_total*10, decision_status=self.decision_status,
            last_system_prompt=self.last_system_prompt,last_user_prompt=self.last_user_prompt,last_raw_reply=self.last_raw_reply,
            memory_counts=self.memory.counts(), illness=self.health_description, illness_severity=self.illness_severity,
            cycle_status=cycle.description(self,sim),relationships=relationships,
            hospitalized=self.hospitalized, awaiting_admission=self.awaiting_admission)

    @property
    def health_description(self):
        from .random_events import SEVERITY_NAMES
        if not self.illness_severity:
            return "未患疾病"
        return f"{self.illness_name}（{SEVERITY_NAMES[self.illness_severity]}，预计还需{max(0,self.illness_until-self.world_tick)}刻恢复，已照护{self.care_minutes}分钟，可去诊所治疗）"

    # ------------------------------------------------------------- 记忆助手
    def remember(self, content: str, importance: float = 3.0, sim=None):
        self.memory.add(content, self.day, self.world_tick, importance)

    # ------------------------------------------------------------- 出行
    def travel_path(self, sim, tx, ty):
        car = next((v for v in sim.vehicles if v.id == self.car_id), None)
        if car and self.scene == "town":
            # 驾驶仍避让行人（车快、撞人危险）；步行寻路不避人，只走静态地图
            humans=sim.human_cells('town',self)|sim.occupied('town',self)
            approach=world.bfs_path('town',self.x,self.y,car.x,car.y,blocked=humans)
            if (self.x,self.y)!=(car.x,car.y) and not approach:return []
            onward=world.travel_path('town',car.x,car.y,tx,ty,'car',blocked=humans)
            if (car.x,car.y)!=(tx,ty) and not onward:return []
            return approach+onward
        from .interaction_points import access_cells
        return world.travel_path(self.scene, self.x, self.y, tx, ty, self.travel_mode,allowed=access_cells(self,self.scene))

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
            at_home=[p.pid for p in sim.players if p is not self and p.scene=='house']
            sim.log("social", f"🚪 {self.name} 出门去了小镇", [self.pid,*at_home])
        else:
            sim.drop_passengers(self)
            sim.release_car(self)
            self.travel_mode = "walk"
            self.x,self.y=sim.free_position("house",24,12,self)
            self.scene = "house"
            self.last_home_tick = self.world_tick
            at_home=[p.pid for p in sim.players if p is not self and p.scene=='house']
            sim.log("social", f"🏠 {self.name} 回到家", [self.pid,*at_home])
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
        self._rejection = ''
        from .town_services import action_spec
        from .activity_rules import registry
        a = action_spec(sim,self,key) if key in registry(sim) else None
        if not a:
            self._rejection='这项活动不存在或已被关闭，请改选其他行动'
            return False
        if key == 'self_relief' and not sim.settings.cycle_enabled:
            self._rejection='生理周期机制已关闭'
            return False
        if a.get('male_only') and self.gender != '男':
            self._rejection='这项活动只有男性居民可以进行'
            return False
        if key in ('eat', 'eat_out') and self.stats.satiety >= sim.mechanics.get('meal_block_satiety', 95.0):
            # 家中用餐与饭店用餐同一饱食上限：避免在饭店相遇后饱着肚子无休止聚餐
            cap = sim.mechanics.get('meal_block_satiety', 95.0)
            self._rejection = f'我吃得很饱（饱食{self.stats.satiety:.0f}，达到{cap:.0f}上限），等饿了再吃'
            self.remember('我吃得很饱，暂时吃不下饭', 3)
            return False
        if key == 'sleep' and self.stats.satiety < sim.mechanics.get('hunger_alert_satiety', 10.0):
            # 饿到睡不着：先去吃饭，别饿着肚子入睡
            cap = sim.mechanics.get('hunger_alert_satiety', 10.0)
            self._rejection = f'太饿了睡不着（饱食{self.stats.satiety:.0f}，低于{cap:.0f}），去吃点东西再睡吧'
            self.remember('肚子饿得咕咕叫，躺着翻来覆去睡不着，还是先吃点东西', 5)
            return False
        if key == 'cook':
            meals = act.cook_meal_output(self.requested_duration)
            have = self.items.get('ingredient', 0) + sim.fridge.get('ingredient', 0)
            if have <= 0:
                self._rejection = '缺少食材：做饭需要食材（1份食材出1份饭），商店S有售'
                return False
            if have < meals:
                # 食材不够原计划：自动降档到现有的量（1→3刻/2→5刻/3→6刻），不整单拒绝
                ticks_for = {1: 3, 2: 5, 3: 6}
                self.requested_duration = ticks_for.get(have, 8)
                meals = have
                self.remember(f'食材只够做{meals}份饭，就先做这些', 3)
            self._cook_meals = meals
        if a.get('need') is None and a.get('scene') not in ('any',self.scene):
            self._rejection=f"这项活动不能在{'室内' if self.scene=='house' else '室外'}进行"
            return False
        if a.get('_target_mode')=='required' and target is None:
            self._rejection='这项活动需要选择一位室友作为对象'
            return False
        if a.get('_place_join')=='or' and target is None and a.get('need')=='player' and a.get('_target_mode')!='required':
            from .activity_rules import choose_place,need_for_location,_location
            concrete={scene:[loc for loc in locations if loc not in ('near_player','house_patient')]
                      for scene,locations in a['_places'].items()}
            concrete={scene:locations for scene,locations in concrete.items() if locations}
            if concrete:
                scene,location=choose_place({'places':concrete},self)
                a['scene'],a['_chosen_location']=scene,location
                a['need']=need_for_location(location,a.get('_target_mode','none'))
                a['_use_interaction_points']=location==_location(key,a)
                if location.startswith('town:'):a['_building']=location[5:]
                else:a.pop('_building',None)
        if a.get('_place_join')=='and':
            from .activity_rules import location_matches
            candidates=[(scene,location) for scene,locations in a['_places'].items() for location in locations]
            options=a.get('_position_options',[])
            staying=next(((scene,location) for scene,location in candidates
                          if 'here' in options and location_matches(self,scene,location)),None)
            meeting=next(((scene,location) for scene,location in candidates
                          if 'near_player' in options and target is not None
                          and location_matches(target,scene,location)),None)
            can_stay=staying if (a.get('_target_mode')!='required' or target is not None
                                and target.scene==self.scene and world.same_place(self,target)) else None
            chosen=can_stay or meeting
            if chosen is None:
                self._rejection='这项活动要求在所选地点原地进行，或在该地点的目标角色旁进行'
                return False
            a['scene'],a['_chosen_location']=chosen
            a['need']='player' if chosen==meeting else None
            if a.get('_target_mode')=='required' and chosen==staying and target is not None:
                if target.scene!=self.scene or not world.same_place(self,target):
                    self._rejection='目标室友不在所选地点，无法原地一起进行'
                    return False
            if chosen[1].startswith('town:'):a['_building']=chosen[1][5:]
            else:a.pop('_building',None)
            a['_use_interaction_points']=False
        need = a.get("need", "")
        self.target_player = target
        self._pending_target_pid = None
        from .interaction_points import LOCAL_ACTIONS,route,release,route_bed_pair
        if self.interaction_slot:release(sim,self)

        if key in LOCAL_ACTIONS and a.get('_use_interaction_points',True):return route(sim,self,key)

        if key=='dating' and self.decision and self.decision.location=='bed':
            # 防御路径：正常邀请流程在 simulation._consent_decision；直接调用时保留门槛
            from . import relations as rel_mod
            if target is None or (self.affinity.get(target.pid,0)<55
                                  and not rel_mod.between(sim,self.pid,target.pid)):
                self._rejection='我们还不是恋爱关系，好感也还不到可以同床共枕的程度'
                return False
            return route_bed_pair(sim,self,target)

        # 需要物品
        needed=a.get('needs_item')
        if needed and not (any(self.items.get(k,0)>0 for k in ('flower','plant')) if needed=='any' else self.items.get(needed,0)>0):
            missing='鲜花或盆栽' if needed=='any' else act.ITEMS[needed]['name']
            self._rejection=f'缺少{missing}，没法进行'
            return False

        if key=='home_treat' or need=='home_patient':
            target=target or self
            self.target_player=target;self._pending_target_pid=target.pid;self._pending_key=key
            self._plan_route(sim,'house',target.x,target.y,adjacent=target is not self)
            return True

        # 玩家目标类
        if need == "player":
            if target is None or target.scene != self.scene:
                where = (f'{target.name}在'
                         f'{world.SCENE_NAMES.get(target.scene, target.scene)}·'
                         f'{world.zone_of(target.scene, target.x, target.y)}'
                         if target is not None else '对方不在')
                self._rejection = (f'对方不在我的身边（不同场景；{where}）；'
                                   f'赶不过去可先用 phone 电话跨地点联系')
                return False
            if max(abs(target.x - self.x), abs(target.y - self.y)) > 6 \
                    and not world.same_place(self, target):
                dist = max(abs(target.x - self.x), abs(target.y - self.y))
                self._rejection = (f'对方离我太远，够不着；你们需在同一地点（同一设施区域）才能对话或互动。'
                                   f'{target.name}现在在'
                                   f'{world.SCENE_NAMES.get(target.scene, target.scene)}·'
                                   f'{world.zone_of(target.scene, target.x, target.y)}'
                                   f'（约{dist}格外）；可先走过去，或用 phone 电话跨地点联系')
                return False
            if key=='use_medicine' and target.stats.hp>=100 and not target.illness_severity:
                self._rejection=f'{target.name}身体很健康，用不上药品'
                return False
            if key=='give_meal' and target.stats.satiety >= sim.mechanics.get('meal_block_satiety', 95.0):
                self._rejection=f'{target.name}吃得很饱（饱食95以上），现在不需要饭菜'
                return False
            if key == "dating":
                from . import relations as rel_mod
                rel = rel_mod.between(sim, self.pid, target.pid)
                mutual = (self.affinity.get(target.pid, 0) >= a["consent_threshold"]
                          and target.affinity.get(self.pid, 0) >= a["consent_threshold"])
                if not (rel or mutual):
                    self._rejection=(f'我们还不是恋爱关系，且好感未互达{a["consent_threshold"]}，约会没有成'
                                     f'（好感足够可先 propose_love 建立关系）')
                    return False
            self._pending_key = key
            self._pending_target_pid = target.pid
            if a.get('_place_join')=='and' and 'here' in a.get('_position_options',[]) and a.get('_chosen_location') and location_matches(self,*a['_chosen_location']):
                self._start_social(key,sim,target)
                return True
            self._plan_route(sim, self.scene, target.x, target.y, adjacent=True)
            return True

        if need=='own_computer':
            from .resident_tools import computer_seat
            self._pending_key=key
            self._plan_route(sim,"house",*computer_seat(self))
            return True

        if need=='pond_shore':
            taken=sim.occupied('town',self)|sim.human_cells('town',self)
            shores={(x+dx,y+dy) for x,y in world.find_tiles('town','W')
                    if key!='swim' or (x,y) not in taken
                    for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                    if sim.position_free(self,'town',x+dx,y+dy)}
            if not shores:return False
            ref=(self.x,self.y) if self.scene=='town' else (1,12)
            shore=min(shores,key=lambda q:abs(q[0]-ref[0])+abs(q[1]-ref[1]))
            self._pending_key=key;self._plan_route(sim,'town',*shore)
            return True

        # 设施类
        chars = need.split(':',1)[1].split('|') if isinstance(need,str) and need.startswith('facility:') else []
        if need=='own_bedroom':
            self._pending_key=key
            self._plan_route(sim,'house',*self.bedroom)
            return True
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
        if a.get('_building'):
            from .mechanics import open_now
            code=a['_building']
            if not open_now(sim.opening_hours[code], sim.hour):
                self.state,self.activity,self.activity_key,self._pending_key='idle',None,'',''
                hours=sim.opening_hours[code]
                self.info_feedback=f'{a["name"]}现在没营业（营业{hours[0]:02d}:00–{hours[1]:02d}00），请改选别的活动或稍后再来'
                self.remember(f'{a["name"]}到达时未营业',3)
                return
        from .interaction_points import LOCAL_ACTIONS,options,active_slot
        if key in LOCAL_ACTIONS and a.get('_use_interaction_points',True):
            if not self.interaction_slot or self.interaction_slot['key']!=key:
                from .interaction_points import route
                route(sim,self,key);return
            point=tuple(self.interaction_slot['position'])
            # 占用者只算「正在使用中」的人——同在等位的对手不算，
            # 否则两个等位者会把对方当占用者互相死等
            holder=next((o for o in sim.players if o is not self and o.state=='doing'
                         and o.interaction_slot is not None
                         and tuple(o.interaction_slot['position'])==point),None)
            if holder is not None:
                # 使用位仍被占用：退到旁边空格排队（等位上限24刻，防无限等待），
                # 不与正在使用的人重叠上桌
                self._wait_slot_ticks=getattr(self,'_wait_slot_ticks',0)+1
                if self._wait_slot_ticks>=24:
                    self._abandon_pending(sim,f'{holder.name}一直占着位置')
                    return
                self._stall_ticks=0          # 排队等位不算路阻停滞
                if (self.x,self.y)==point:
                    side=next((q for q in ((point[0]+dx,point[1]+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0)))
                               if sim.position_free(self,'house',*q)),None)
                    if side:
                        self.path=[side];self.route_goal=side
                self.state='moving';return
            self._wait_slot_ticks=0
            if self.scene!='house' or (self.x,self.y)!=point:
                self.state='moving';return
        cost = a.get("cost", 0)
        energy_need=max(0,-a.get('effects',{}).get('energy',0))
        if a.get('_kind') in ('course','license_course','job') and self.stats.energy+1e-8<energy_need:
            self.state,self.activity,self.activity_key,self._pending_key='idle',None,'',''
            self.info_feedback=f'抵达后精力不足（{self.stats.energy:.0f}，{a["name"]}至少需要{energy_need:.0f}），先睡觉或休息再来'
            self.remember('抵达后精力不足，需先休息再上课或工作',3)
            return
        if self.stats.money < cost:
            self.state, self.activity, self.activity_key, self._pending_key = "idle", None, "", ""
            self.info_feedback=f"抵达后现金不足（{a['name']}需{cost:g}元，只有{self.stats.money:.1f}元）；可先去挣钱"
            self.remember(f"抵达后钱不够，无法{a['name']}", 3)
            return
        if key in ('fish','swim'):
            waters=[(self.x+dx,self.y+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                    if self.scene=='town' and world.tile('town',self.x+dx,self.y+dy)=='W']
            if not waters:
                self.state='idle';self._pending_key=''
                self.info_feedback='这里挨不到水面，请到池塘边的空地再试'
                return
            if key=='swim':
                # 下水格独占：避让已在水中的人与其他下水点
                taken=sim.occupied('town',self)|sim.human_cells('town',self)
                waters=[q for q in waters if q not in taken]
                if not waters:
                    self.state='idle';self._pending_key='';return
                sim.drop_passengers(self);sim.release_car(self)
                self.x,self.y=waters[0];self.transport='walk'
        # 一次性付款，避免获得物品后通过上车/中断活动绕过剩余费用。
        self.stats.money -= cost
        if a.get('_building'):
            sim.venue_revenue[a['_building']] = sim.venue_revenue.get(a['_building'], 0.0) + cost
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
        sus = getattr(self, '_suspended_activity', None)
        if sus and sus.get('key') == key:
            # 从中断处继续：保留剩余时长与中间量（如做饭的产出计划）
            self.activity_left = sus['left']
            self.activity_total = max(sus.get('total', 0), self.activity_left)
            if sus.get('cook_meals') is not None:
                self._cook_meals = sus['cook_meals']
            self.info_feedback = f'{a["name"]}从中断处继续（剩{self.activity_left * 10:g}分钟）'
        else:
            self.activity_left = self.requested_duration or a["duration"]
            self.activity_total = self.activity_left
        self._suspended_activity = None     # 选了其他活动即放弃剩余进度
        self.requested_duration = None
        self.state = "doing"
        # 物品类活动：购买获得/布置消耗
        if a.get("gives_item"):
            self.items[a["gives_item"]] = self.items.get(a["gives_item"], 0) + 1
            it = act.ITEMS.get(a["gives_item"],{'name':a['name']})
            self.remember(f"我买了{it['name']}", 3)
            if a["gives_item"]=='ingredient' and self.items['ingredient']>2:
                move=self.items['ingredient']-2
                room=max(0,act.FRIDGE_LIMITS['ingredient']-sim.fridge.get('ingredient',0))
                move=min(move,room)
                if move>0:
                    self.items['ingredient']-=move
                    sim.fridge['ingredient']=sim.fridge.get('ingredient',0)+move
                    self.remember(f'{move}份食材放进了厨房冰箱', 3)
                if self.items['ingredient']>2:
                    self.remember(f'背包里还有{self.items["ingredient"]}份食材（冰箱已满）', 3)
        if a.get("needs_item"):
            if a['needs_item']=='any':self._consume_decor_item(key, sim)
        if key=='eat':
            self.eating_from_inventory=self.items.get('meal',0)>0
            self.eating_quality=0
            if self.eating_from_inventory:
                self.items['meal']-=1
                self.eating_quality=self.meal_qualities.pop(0) if self.meal_qualities else sim.meal_quality
                if self.meal_expiry:self.meal_expiry.pop(0)
            elif sim.fridge.get('meals'):
                # 冰箱有饭：自动取出（最早存入的先吃）
                chilled=sim.fridge_take_meal()
                self.eating_from_inventory=True
                self.eating_quality=chilled['quality']
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
            # 床伴活动等对方到位：等太久（12刻）就放弃，避免一方卡死时双方僵持
            self._wait_ticks=getattr(self,'_wait_ticks',0)+1
            if self._wait_ticks>=12:
                self._abandon_pending(sim,'对方一直没到')
                return
            from .interaction_points import arrive_bed
            arrive_bed(sim,self);return
        if self.state != 'waiting_activity':
            self._wait_ticks=0
        if self.state == "moving":
            # 停滞看护：以「到目标的距离是否创新低」衡量进展——
            # 单纯看位置变化会被侧身让路的来回摇摆骗过（位置在变但永远没接近目标）。
            goal_now=self.route_goal or (self.path[-1] if self.path else None)
            if goal_now:
                dist=abs(self.x-goal_now[0])+abs(self.y-goal_now[1])
                best=getattr(self,'_best_goal_dist',None)
                if best is None or goal_now!=getattr(self,'_best_goal_point',None):
                    self._best_goal_dist=dist;self._best_goal_point=goal_now;self._stall_ticks=0
                elif dist<best:
                    self._best_goal_dist=dist;self._stall_ticks=0
                # 等位/到达后的清零由 _start_local 负责；此处持平即视为无进展
                else:
                    self._stall_ticks=getattr(self,'_stall_ticks',0)+1
            else:
                self._stall_ticks=getattr(self,'_stall_ticks',0)+1
            if self._stall_ticks>=6 and self._pending_key:
                from .interaction_points import LOCAL_ACTIONS,route
                if self._pending_key in LOCAL_ACTIONS and self.interaction_slot:
                    # 等交互位超过1小时：重新挑一个当前空闲的使用位；
                    # 只有真的换到了别的点位才重置停滞计数（全忙时继续等到上限放弃）
                    old_point=tuple(self.interaction_slot['position'])
                    route(sim,self,self._pending_key)
                    new_point=tuple(self.interaction_slot['position']) if self.interaction_slot else None
                    if new_point and new_point!=old_point:
                        self._stall_ticks=0;self._best_goal_dist=None
            elif self._stall_ticks>=15:
                self._abandon_pending(sim,'路上耽搁太久')
                return
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
                if self.blocked_ticks>=3 and not self._block_notified and obstruction:
                    self._block_notified=True
                    blocker=next((q for q in sim.players if q is not self and q.scene==self.scene
                                  and (q.x,q.y)==obstruction and not q.vehicle_driver),None)
                    what=(f"{blocker.name}（{blocker.plan or '正在忙'}）" if blocker else '家具或墙壁')
                    zone=world.zone_of(self.scene,self.x,self.y)
                    self.remember(f"我在{zone}被{what}挡住去路，只能等待或绕行",4)
                    sim.log('activity',f'🚧 {self.name}在{zone}被{what}挡住了',[self.pid],resident_memory=False)
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
            # 室内一次移动在一刻内完成；逐格检查仍负责墙体、家具和角色碰撞。
            # 室外继续按各出行方式的每刻格数使用时间预算。
            budget = float('inf') if self.scene == 'house' else 1.0
            car_fee_paid = False
            while self.path:
                dest = self.path[0]
                if abs(dest[0]-self.x)+abs(dest[1]-self.y)!=1 or not sim.position_free(self,self.scene,*dest):break
                mode = world.movement_mode(self.scene, (self.x, self.y), dest,
                                           self.travel_mode, self.stats.money,
                                           0 if car_fee_paid else sim.mechanics['car_fuel_per_tick'])
                car = next((v for v in sim.vehicles if v.id == self.car_id and v.driver_id == self.pid), None)
                if mode == "car" and (not car or max(abs(car.x-self.x), abs(car.y-self.y)) > 1):
                    mode = "walk"
                if mode != "car" and self.passengers:
                    sim.drop_passengers(self)
                spec = world.TRAVEL_MODES[mode]
                time_cost = 1.0 / sim.mechanics[mode+'_speed']
                if time_cost > budget + 1e-9:
                    break
                budget -= time_cost
                self.x, self.y = self.path.pop(0)
                self.blocked_ticks=0
                self._blocked_cell=None
                self._block_notified=False
                self.transport = mode
                self.stats.energy -= sim.mechanics.get(mode+'_energy_per_tile',0)
                if mode == 'car' and not car_fee_paid:
                    self.stats.money = max(0.0, self.stats.money - sim.mechanics['car_fuel_per_tick'])
                    car_fee_paid = True
                elif mode != 'car':
                    self.stats.money = max(0.0, self.stats.money - spec['cost'])
                if mode == "car":
                    car.x, car.y = self.x, self.y
                if mode != "car":
                    self.stats.satiety = max(0.0, self.stats.satiety - sim.mechanics[mode+'_satiety_per_tile'])
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
            if self.activity_key in ("chat", "dating", "phone") and self._conversation is not None:
                return
            self._step_activity(sim)

    def _at_gate(self) -> bool:
        if self.scene == "house":
            return abs(self.x - 25) + abs(self.y - 12) <= 1
        return abs(self.x - 1) + abs(self.y - 12) <= 1

    @property
    def pending_key(self) -> str:
        return self._pending_key

    def _abandon_pending(self, sim: "Simulation", why: str):
        """放弃排定/等待中的行动（超时兜底）：释放使用位与路线，回到空闲。
        触发场景：设施使用位被长期占用、床伴迟迟不到、路线长期走不通。"""
        from .interaction_points import release
        key = self._pending_key
        label = (act.ACTIVITIES.get(key, {}).get('name') if key else '') or self.plan or '原定行动'
        release(sim, self)
        self.path = []
        self.route_goal = None
        self._final_goal = None
        self._final_scene = None
        self._pending_key = ""
        self._pending_target_pid = None
        self.activity = None
        self.activity_key = ""
        self.activity_left = 0
        self.activity_total = 0
        self.target_player = None
        self.requested_duration = None
        self.state = "idle"
        self._stall_ticks = 0
        self._wait_slot_ticks = 0
        self._best_goal_dist = None
        self.blocked_ticks = 0
        self.remember(f'{label}（{why}）一直没法进行，我先算了，做点别的', 4)
        sim.log('activity', f'⏱ {self.name} {why}，放弃{label}，重新安排',
                [self.pid], scope='game')

    def _start_social(self, key: str, sim: "Simulation", t: "Player"):
        if t.scene != self.scene or (max(abs(t.x - self.x), abs(t.y - self.y)) > 2
                                     and not world.same_place(self, t)):
            self.state = "idle"
            self._pending_key = ""
            self.info_feedback=f'{t.name}已经不在身边了（活动开始时走散）；可改用phone联系或先做别的'
            return
        self._pending_key = ""
        self.target_player = t
        self._start_local(key, sim)
        if key == "attack":
            self._resolve_attack(sim)
        if key in ("chat", "dating"):
            sim.start_conversation(self, t, key)
        if key == "gift":
            self._resolve_gift(sim, t)
        if key == "give_meal":
            self._resolve_give_meal(sim,t)

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

    def _resolve_give_meal(self,sim:"Simulation",t:"Player"):
        """送饭菜：对方当场吃掉（不进背包，杜绝饥饿两人互送饭菜的死循环），
        数值与坐下吃一份饭等值：N刻×(基础效果+烹饪品质加成)。"""
        if self.items.get('meal',0)<=0:return
        if t.stats.satiety >= sim.mechanics.get('meal_block_satiety', 95.0):
            self.remember(f'{t.name}吃得很饱，饭菜送不出去，我先留着', 4)
            return
        self.items['meal']-=1
        if self.meal_expiry:self.meal_expiry.pop(0)
        quality=self.meal_qualities.pop(0) if self.meal_qualities else sim.meal_quality
        eat=act.ACTIVITIES.get('eat',{})
        n=eat.get('duration',3) or 3
        eff=eat.get('effects',{})
        sat=n*(eff.get('satiety',13)+quality*.8)
        mood=n*(eff.get('mood',2)+quality*.3)
        energy=n*(quality*.3)
        t.stats.satiety=min(100.0,t.stats.satiety+sat)
        t.stats.mood=min(100.0,t.stats.mood+mood)
        t.stats.energy=min(100.0,t.stats.energy+energy)
        t.stats.clamp()
        t.remember(f"收到了{self.name}送的饭菜，当场吃了，暖胃又暖心",6)
        self.remember(f"我把做好的饭菜送给{t.name}，TA当场吃了",5)
        t.affinity[self.pid]=min(100.0,t.affinity.get(self.pid,50)+2)
        sim.log('social',f'🍱 {self.name}把饭菜送给{t.name}，当场吃了（饱食+{sat:.0f}）',[self.pid,t.pid])
        t.speech='谢谢！'
        t.speech_tick=t.world_tick

    def _resolve_attack(self, sim: "Simulation"):
        t = self.target_player
        if t is None:
            return
        dmg = act.ACTIVITIES["attack"]["damage"]
        t.stats.hp -= dmg
        # 攻击是单次事件，不参与"同种伤害只打断一次"去重：cause='attack' 不入标记集合
        sim._alert_health_loss(t,dmg,force=True,cause='attack')
        self.affinity[t.pid] = self.affinity.get(t.pid, 30) - 12
        t.affinity[self.pid] = t.affinity.get(self.pid, 30) - 15
        t.remember(f"被{self.name}攻击了！损失{dmg:.0f}生命", 9)
        self.remember(f"我攻击了{t.name}", 7)
        sim.log("combat", f"⚔️ {self.name} 攻击了 {t.name}（-{dmg:.0f} HP）",
                [self.name, t.name])
        t.speech = "好痛！"
        t.speech_tick = t.world_tick

    # ------------------------------------------------------------- 活动结算
    def _step_activity(self, sim: "Simulation"):
        key = self.activity_key
        a = self.activity or act.ACTIVITIES.get(key, {})
        if self.activity_left <= 0:
            self._finish_activity(sim,a)
            return
        if a.get('_kind') in ('course','license_course','job') and self.stats.energy+1e-8<max(0,-a.get('effects',{}).get('energy',0)):
            self.remember('精力不足，结束活动，保留已获得的经验和工资',3)
            self._finish_activity(sim,a);return
        eff: dict = dict(a.get("effects", {}))
        if a.get('_building'):
            from .mechanics import open_now
            if not open_now(sim.opening_hours[a['_building']],sim.hour):
                self.remember('营业时间结束，活动提前结束',3)
                self._finish_activity(sim,a)
                return
        from .town_services import step_effects
        eff=step_effects(sim,self,key,eff)
        # 生理周期：负面精力/饱食/心情效果按当前倍率加重
        # （「自行解决」是释放动作本身，代价固定不放大）
        from . import cycle as physio
        if key != 'self_relief':
            _pm = physio.multipliers(self, sim)
            for _s in ('satiety', 'energy', 'mood'):
                if eff.get(_s, 0) < 0:
                    eff[_s] = eff[_s] * _pm[_s]
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
        affinity_rate=a.get('_joint_affinity_per_tick',0)
        if affinity_rate and key not in ('coffee','eat_out'):
            for other in sim.players:
                if other is not self and other.activity_key==key and other.scene==self.scene and world.same_place(self,other):
                    self.affinity[other.pid]=max(0,min(100,self.affinity.get(other.pid,50)+affinity_rate))
        target=self.target_player
        if target is not None and target is not self and target.scene==self.scene and world.same_place(self,target):
            own=a.get('_self_affinity_per_tick',0)
            theirs=a.get('_target_affinity_per_tick',0)
            if own:self.affinity[target.pid]=max(0,min(100,self.affinity.get(target.pid,50)+own))
            if theirs:target.affinity[self.pid]=max(0,min(100,target.affinity.get(self.pid,50)+theirs))

        # ---- 餐厅吃饭需要家里有饭（做饭后 60 tick 内有效）----
        if key == "eat" and not self.eating_from_inventory and sim.meal_until < self.world_tick:
            # 没饭了：转为去咖啡馆
            self.activity = None
            self.activity_key = ""
            self.state = "idle"
            self.info_feedback='家里没现成饭了（做饭后15刻内有效）：请选cook（需食材）、去冰箱取或下馆子'
            self.remember("家里没饭了，得下馆子或自己做", 4)
            return

        for stat_name, delta in eff.items():
            setattr(self.stats, stat_name, getattr(self.stats, stat_name) + delta)
        self.stats.clamp()

        # ---- 装饰度心情加成 ----
        decor_bonus = (self.decor_room + sim.decor_common) / 200.0 * sim.mechanics['decor_mood_per_200']
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
            from .resident_tools import use_computer, remember_computer_use
            use_computer(sim,self,self.decision)
            remember_computer_use(self)
        if key=='cook':
            quality=min(5,int(self.skills.get('cooking',0)//100))
            planned=getattr(self,'_cook_meals',0) or act.cook_meal_output(self.activity_total)
            elapsed=max(0,self.activity_total-self.activity_left)
            # 中断时按实际进度折算产出；食材按实际产出消耗（不白扣）
            produced=min(planned,int(planned*elapsed/max(1,self.activity_total)),
                         self.items.get('ingredient',0)+sim.fridge.get('ingredient',0))
            if produced>0:
                # 食材消耗：先背包后冰箱
                from_bag=min(produced,self.items.get('ingredient',0))
                if from_bag:
                    self.items['ingredient']-=from_bag
                rest=produced-from_bag
                if rest:
                    sim.fridge['ingredient']-=rest
                # 优先随身（最多2份），多出的放冰箱（保鲜=保质期×3，容量有限）；
                # 冰箱放不下的仍随身带走，不浪费
                fridge_room=max(0,act.FRIDGE_LIMITS['meals']-len(sim.fridge.get('meals',[])))
                keep=min(produced,max(2-self.items.get('meal',0),produced-fridge_room))
                keep=max(0,keep)
                if keep>0:
                    self.items['meal']=self.items.get('meal',0)+keep
                    self.meal_qualities.extend([quality]*keep)
                    self.meal_expiry.extend([sim.global_tick+int(sim.mechanics.get('meal_shelf_life_ticks',36))]*keep)
                chilled=produced-keep
                if chilled>0:
                    sim.fridge_add_meal(quality,chilled)
                sim.log('activity',f'🍱 {self.name}做好{produced}份饭（随身{keep}份、冰箱{chilled}份，消耗{produced}份食材）',[self.pid])
            else:
                sim.log('activity',f'🍳 {self.name}的做饭中途结束，没有产出饭菜',[self.pid])
        from . import cycle as physio
        if key == 'self_relief' and self.gender == '男':
            physio.release_male(self, sim)
        if key == 'sex':
            if self.gender == '男':
                physio.release_male(self, sim, how='互动')
            else:
                physio.satisfy_female(self, sim, how='亲密')
        if key == 'dating' and self.gender == '女':
            physio.satisfy_female(self, sim, how='约会')
        if key=='use_medicine' and self.target_player:
            target=self.target_player
            if self.items.get('medicine',0)>0 and self.scene==target.scene and max(abs(self.x-target.x),abs(self.y-target.y))<=3:
                self.items['medicine']-=1
                target.stats.hp=min(100,target.stats.hp+20)
                if target.illness_severity:
                    if target.illness_severity==1:sim.random_events.recover(sim,target,treated=True)
                    else:target.illness_severity-=1
                target.affinity[self.pid]=min(100,target.affinity.get(self.pid,50)+2)
                target.remember(f"{self.name}给我用了药品，伤情好多了",7)
                self.remember(f"我给{target.name}用了药品",5)
                sim.log('social',f'💊 {self.name}给{target.name}用了药品',[self.pid,target.pid])
        if key == "destroy" and world.tile(self.scene, self.x, self.y) in act.facility_chars("destroy"):
            world.grid(self.scene)[self.y][self.x] = "X"
        # 社交/协同互动的好感结算：LLM 决定增减
        if key in ("chat", "dating"):
            # 对话结束时 Conversation 已写记忆/好感，这里不重复处理
            pass
        elif key == 'sex' and self.target_player:
            sim.request_affinity(self,self.target_player,'sex')
        elif key in ("watch_tv", "work", "movie",
                     "park", "balcony", "cook", "eat", "exercise", "build"):
            partners = [o for o in sim.players
                        if o is not self and o.scene == self.scene
                        and world.same_place(self, o)
                        and o.activity_key == key]
            for o in partners:
                sim.request_affinity(self, o, key)
        self.remember(f"我结束了{a['name']}，实际进行了{max(0,self.activity_total-self.activity_left)*10}分钟",2)
        self.pending_memory_activity = f"刚结束{a['name']}，进行了{max(0,self.activity_total-self.activity_left)*10}分钟"
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
    def _expire_meals(self, sim: "Simulation") -> None:
        """饭菜保质期：到期自动消失并留下记忆；到期列表与数量保持对齐。"""
        shelf = int(sim.mechanics.get('meal_shelf_life_ticks', 20))
        while self.items.get('meal', 0) > 0 and self.meal_expiry and self.meal_expiry[0] <= sim.global_tick:
            self.meal_expiry.pop(0)
            if self.meal_qualities:
                self.meal_qualities.pop(0)
            self.items['meal'] -= 1
            self.remember('我带的饭菜过期变质了，只能扔掉', 5)
            sim.log('activity', f'🗑 {self.name}的饭菜过期变质，扔掉了', [self.pid], scope='game')
        while len(self.meal_expiry) < self.items.get('meal', 0):
            self.meal_expiry.append(sim.global_tick + shelf)
        del self.meal_expiry[self.items.get('meal', 0):]

    def passive_drain(self, sim: "Simulation") -> None:
        from . import hospital
        self._expire_meals(sim)
        if getattr(self, 'hospitalized', False):
            # 住院：不做日常消耗；生命每刻按自然恢复的倍率恢复，每半天结算房费
            self.stats.hp = min(100.0, self.stats.hp
                                + sim.mechanics['hospital_heal_multiplier']
                                * sim.mechanics['high_satiety_hp_gain'])
            self.hospital_fee_ticks = self.hospital_fee_ticks + 1
            if self.hospital_fee_ticks >= hospital.HALF_DAY_TICKS:
                self.hospital_fee_ticks = 0
                hospital.halfday_tick(sim, self)
            self.stats.clamp()
            return
        if getattr(self, 'awaiting_admission', False):
            # 待收治（借款中）：不做消耗，等待结果
            self.stats.clamp()
            return
        from . import cycle as physio
        physio.tick_female_period(self, sim)
        _mults = physio.multipliers(self, sim)
        hp_before=self.stats.hp
        for k, v in act.PASSIVE_DRAIN.items():
            if k == 'mood':v = -sim.mechanics['base_mood_drain'] * _mults['mood']
            if k == 'satiety':v = -sim.mechanics['base_satiety_drain'] * _mults['satiety']
            if k == 'energy':v = sim.mechanics['base_energy_regen']
            if k == "satiety" and (self.vehicle_driver or self.transport == "car"):
                continue
            if v:
                setattr(self.stats, k, getattr(self.stats, k) + v)
        if self.stats.satiety <= 0:
            self.stats.hp -= sim.mechanics['starvation_hp_loss']
        else:
            # 饱食离开 0：饥饿伤害结束，解除打断标记
            sim.clear_injury_break(self, 'starvation')
        # 饥饿预警（默认饱食<10）：不等扣血就唤醒；饿醒正在睡觉的人
        threshold = sim.mechanics.get('hunger_alert_satiety', 10.0)
        if self.stats.satiety < threshold:
            if self.state == 'doing' and self.activity_key == 'sleep':
                sim._cancel_timed_action(self)
                self.info_feedback = '太饿了睡不着，去吃点东西再睡吧'
                self.remember('睡到一半饿醒了，肚子空得发慌，得先去吃点东西', 6)
                sim.log('activity', f'🍜 {self.name}饿醒了（饱食{self.stats.satiety:.0f}），先去吃饭', [self.pid])
            elif not getattr(self, '_hunger_alerted', False):
                self._hunger_alerted = True
                self.injury_alert = (f'很饿了（饱食{self.stats.satiety:.0f}/100），'
                                     '尽快吃饭——吃随身饭菜、在家做饭、下馆子或请室友送饭')
                self.remember('我饿得发慌，得赶紧吃点东西', 5)
        elif self.stats.satiety > threshold + 20:
            self._hunger_alerted = False
        if self.stats.satiety > sim.mechanics['high_satiety_hp_threshold']:
            self.stats.hp = min(100.0, self.stats.hp + sim.mechanics['high_satiety_hp_gain'])
        mood_delta = 0.0
        if self.illness_severity:
            mood_delta -= sim.mechanics['illness_mood_drain']
        if self.stats.energy < sim.mechanics['low_energy_threshold']:
            mood_delta -= (sim.mechanics['low_energy_threshold'] - self.stats.energy) * sim.mechanics['low_energy_mood_per_point']
        for other in self.same_zone_players(sim):
            mood_delta += act.relationship_mood_per_tick(self.affinity.get(other.pid, 50.0),sim.mechanics)
        self.stats.mood += mood_delta
        self.stats.clamp()

        self.low_mood_ticks = self.low_mood_ticks + 1 if self.stats.mood < sim.mechanics['low_mood_threshold'] else 0
        if not self.low_mood_ticks:
            # 心情回到阈值之上：低心情伤害期结束，解除打断标记
            sim.clear_injury_break(self, 'low_mood')
        started_harm = self.low_mood_ticks == sim.mechanics['low_mood_delay_ticks']
        if self.low_mood_ticks >= sim.mechanics['low_mood_delay_ticks']:
            self.stats.hp -= sim.mechanics['low_mood_hp_loss']
        # ---- 紧急送医 / 死亡 ----
        hospital.clear_refused(sim, self)   # 生命回升过阈值的拒收者解除标记
        if hospital.trigger(sim, self):
            sim._alert_health_loss(self, max(0, hp_before - self.stats.hp))
            hospital.start_emergency(sim, self)
        elif self.stats.hp <= 0 and not getattr(self, 'awaiting_admission', False) \
                and not getattr(self, 'hospitalized', False):
            sim._alert_health_loss(self, max(0, hp_before - self.stats.hp))
            hospital.death(sim, self)
            return
        self.stats.clamp()
        if started_harm:
            sim.log('random', f'😞 {self.name}连续心情低落，健康开始受影响', [self.pid])
