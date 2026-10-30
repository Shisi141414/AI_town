"""模拟主循环 v2 —— 身份阶段 + 好感结算队列 + 双场景世界

启动流程：
  1. spawn 5 个「无名」玩家（占卧室、给预设颜色）
  2. 身份阶段：若配了 API → 每个玩家由自己的模型选名字/颜色；
     无 API → 用预设身份
  3. 进入正常 tick 循环

好感 LLM 结算：互动结束后 request_affinity() 入队，
  由 _drain_affinity() 在 tick 间异步消费（不阻塞主循环）。
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from pathlib import Path

from .random_events import RandomEvents
from .recording import Recorder, REQUEST_TRACE

from . import activities as act
from . import brain, world
from .conversation import Conversation
from .llm import LLMError
from .models import ApiProfile, Decision, GameEvent, Settings, Vehicle
from .player import Player

BEDROOM_ANCHORS = world.BEDROOM_ANCHORS
DEFAULT_PLAYER_COUNT = 3            # 初始玩家数（网络压力考虑，用户要求）

PRESET_IDENTITIES = [
    {"name": "小满", "color": "#e74c3c", "emoji": "🧑‍🎓"},
    {"name": "小舟", "color": "#3498db", "emoji": "👩‍🎨"},
    {"name": "小鹿", "color": "#2ecc71", "emoji": "🧑‍🌾"},
    {"name": "小石", "color": "#9b59b6", "emoji": "👩‍🔬"},
    {"name": "小枫", "color": "#e67e22", "emoji": "🧑‍🍳"},
]


class Simulation:
    def __init__(self, memory_dir: Path | None = None) -> None:
        self.save_id = None
        self.memory_dir = memory_dir
        self.replay_dir = None
        self.recorder = None
        self.recording_segments = []
        self.random_events = RandomEvents()
        self.players: list[Player] = []
        self.vehicles = [Vehicle(id="shared-car")]
        self.day = 1
        self.tick = 48  # 第一天早上08:00；global_tick仍是从开局起的已过刻数
        self.global_tick = 0
        from .town_services import defaults
        self.town_config = defaults()
        from .mechanics import DEFAULT_RULES, DEFAULT_HOURS, activity_defaults
        from .activities import activity_description_defaults
        self.mechanics = dict(DEFAULT_RULES)
        self.opening_hours = {k: v[:] for k, v in DEFAULT_HOURS.items()}
        self.activity_config = activity_defaults()
        self.activity_descriptions = activity_description_defaults()
        self.activity_overrides = {}
        self.disabled_activities = []
        self.custom_activities = {}
        self.shop_car = {'name':'私家汽车','emoji':'🚗','enabled':True}
        self.user_prompt_template = ""
        self.meal_quality = 0
        from .town_services import STOCKS
        self.market_price = 10.0
        self.stocks: dict = {k: dict(v) for k, v in STOCKS.items()}      # 小镇股市
        self.venue_revenue: dict = {}                                    # 场所近期营业额（衰减累计）
        self.market_last_tick = 0
        self.running = False
        self.speed = 1.0
        self.events: list[GameEvent] = []
        self.event_id = 0
        self.settings = Settings()
        self.profile: ApiProfile | None = None     # 兼容：默认档案（诊断/无绑定玩家）
        self.profiles_by_id: dict[str, ApiProfile] = {}   # 全部档案（server 同步）
        self.system_prompt: str = ""            # 用户自定义统一提示词
        self.decor_common = 0.0                 # 公共区装饰度
        self.meal_until = 0                     # 家里有饭的截止 tick
        self.dialogue_log: list[dict] = []      # 兼容保留
        self.conversations: list[Conversation] = []   # 活跃对话
        self._dialogue_released = 0                  # 已因对话放行的世界刻数（3轮对话=1刻）
        self._dialogue_cooldown: dict = {}            # 'pidA|pidB' → 对话结束刻（防门口无限连聊）
        self.affinity_queue: list[tuple] = []   # (a_pid, o_pid, activity_key)
        self.loan_queue: list[tuple] = []       # (borrower_pid, lender_pid) 急救借款
        self.sex_proposals: dict[str, dict] = {}
        self._affinity_busy = False
        self._loan_busy = False
        self._loop_task: asyncio.Task | None = None
        self._tick_lock = asyncio.Lock()
        self._affinity_task: asyncio.Task | None = None
        self._loan_task: asyncio.Task | None = None
        self._llm_error_count = 0
        self.identity_phase = False             # 是否在开局身份阶段
        self.model_of: dict[str, str] = {}      # pid → 模型（server 同步进来）
        self.binding_of: dict[str, str] = {}    # pid → profile_id（server 同步）
        self.names = {}                         # pid → 当前名字（广播用）

    def process_frame(self):
        from .shop_products import catalog as shop_catalog
        self.refresh_bedroom_names()
        names={p.pid:p.name for p in self.players}
        return {"type":"tick","day":self.day,"clock":self.clock,"tick":self.global_tick,
            "running":self.running,"speed":self.speed,"identity_phase":self.identity_phase,
            "decor_common":self.decor_common,"meal_until":self.meal_until,
            "town_config":self.town_config,"market_price":self.market_price,"stocks":{k:dict(v) for k,v in self.stocks.items()},"shop_products":shop_catalog(self),
            "players":[p.snapshot(names).model_dump() for p in self.players],
            "vehicles":[v.model_dump() for v in self.vehicles],
            "maps":{scene:["".join(row) for row in world.grid(scene)] for scene in ("house","town")},
            "execution":[{"pid":p.pid,"path":p.path,"pending":p._pending_key,
                          "decision":p.decision.model_dump() if p.decision else None} for p in self.players],
            "conversations":[{"a":c.a.pid,"b":c.b.pid,"phase":c.phase,"history":c.history} for c in self.conversations]}

    def ensure_recording(self):
        if self.replay_dir and (not self.recorder or self.recorder.closed):
            initial={"state":self.process_frame(),"memories":{p.pid:[m.model_dump() for m in p.memory.items] for p in self.players},
                     "settings":self.settings.model_dump()}
            self.recorder=Recorder(self.replay_dir,initial)
            for p in self.players:
                p.memory.sink=lambda data,pid=p.pid:self.record("memory",{"pid":pid,**data})
            self.record_frame()

    def record(self,kind,data):
        if self.recorder:self.recorder.append(kind,data)

    def record_frame(self):
        if self.recorder and not self.recorder.closed:
            from .saves import capture,recording_segments
            frame=self.process_frame();frame['checkpoint']=capture(self)
            for seg in frame['checkpoint']['recording_segments']:
                if seg['id']==self.recorder.id:seg['frames']+=1
            self.record('frame',frame)
            self.recording_segments=recording_segments(self)

    def finish_recording(self):
        if self.recorder and not self.recorder.closed:
            self.record_frame()
            from .saves import recording_segments
            self.recording_segments=recording_segments(self)
            self.recorder.close()
        for p in self.players:p.memory.sink=None

    # ------------------------------------------------------------ 每玩家档案
    def profile_for(self, pid: str) -> ApiProfile | None:
        """玩家实际使用的档案：自己绑定的 > 激活的"""
        pid_prof = self.profiles_by_id.get(self.binding_of.get(pid, ""))
        if pid_prof and pid_prof.api_key:
            return pid_prof
        if self.profile and self.profile.api_key:
            return self.profile
        return None

    # ------------------------------------------------------------ 时钟
    @property
    def hour(self) -> int:
        return self.tick // 6

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:{(self.tick % 6) * 10:02d}"

    def by_pid(self, pid: str) -> Player | None:
        return next((p for p in self.players if p.pid == pid), None)

    def occupied(self,scene,ignore=None):
        """动态占位（仅决策层使用）：活动使用位预约 + 车辆位置。
        角色身体不再算障碍——室内通道狭窄，互为障碍时寻路极易互相卡死；
        走路与寻路只看静态墙/家具（position_free、travel_path），
        只有分配活动使用位（route）、落点（free_position）等决策
        仍用本集合避免抢占。"""
        cells=set()
        from .interaction_points import active_slot
        if scene=='house':
            cells.update(tuple(slot['position']) for p in self.players if p is not ignore and (slot:=active_slot(p)) and slot.get('reserved',True))
        cells.update((v.x,v.y) for v in self.vehicles if scene=="town" and (ignore is None or v.id!=ignore.car_id))
        return cells

    def human_cells(self,scene,ignore=None):
        """角色身体格集合（供车辆驾驶避让等少数场景使用，不用于步行）。"""
        return {(p.x,p.y) for p in self.players if p is not ignore and p.scene==scene and not p.vehicle_driver}

    def position_free(self,p,scene,x,y):
        """步行可达判定：只看静态地图（墙、家具、水）与活动者自己的家具豁免格。
        不检测其他角色——狭窄通道里对向行走互相穿透，不再卡死。"""
        from .interaction_points import access_cells
        swimming=(p is not None and scene=='town' and p.scene==scene and p.state=='doing'
                  and p.activity_key=='swim' and (x,y)==(p.x,p.y) and world.tile(scene,x,y)=='W')
        return world.walkable(scene,x,y) or swimming or (p is not None and (x,y) in access_cells(p,scene))

    def free_position(self,scene,x,y,p=None):
        # 决策层落点：优先避开活动占位与其他角色（尽量不重叠出生/进门），
        # 极端拥挤时退化为只避活动占位，绝不因找位置失败而卡死流程。
        ch=world.tile(scene,x,y)
        def candidates(blocked):
            return [(a,b) for b in range(world.SIZE) for a in range(world.SIZE)
                    if world.walkable(scene,a,b) and (a,b) not in blocked]
        pool=candidates(self.occupied(scene,p)|self.human_cells(scene,p)) or candidates(self.occupied(scene,p))
        if not pool:raise ValueError('场景没有可用的空位')
        return min(pool,key=lambda q:(0 if q==(x,y) else 1,
                   0 if world.tile(scene,*q)==ch else 1,abs(q[0]-x)+abs(q[1]-y),q))

    def yield_position(self,p,requester):
        if p.state not in ('idle','waiting_model') or p._conversation or p.car_id or p.vehicle_driver:return False
        candidates=[(p.x+dx,p.y+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))
                    if self.position_free(p,p.scene,p.x+dx,p.y+dy)]
        if not candidates:return False
        p.x,p.y=max(candidates,key=lambda q:sum(self.position_free(p,p.scene,q[0]+dx,q[1]+dy) for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))))
        return True

    def repair_positions(self):
        for p in self.players:
            if p.vehicle_driver:continue
            if not self.position_free(p,p.scene,p.x,p.y):
                p.x,p.y=self.free_position(p.scene,p.x,p.y,p)
                if p.state=='moving':
                    goal=getattr(p,'route_goal',None) or (p.path[-1] if p.path else None)
                    if goal:p.route_goal=goal;p.path=p.travel_path(self,*goal)
        for p in self.players:
            if p.vehicle_driver:
                driver=self.by_pid(p.vehicle_driver)
                if driver:p.scene,p.x,p.y=driver.scene,driver.x,driver.y

    # ------------------------------------------------------------ 玩家管理
    def refresh_bedroom_names(self) -> None:
        """把各卧室主人的名字同步给 world（区域名显示为「XX的卧室」）。"""
        owners = [None] * len(world.BEDROOM_RECTS)
        for p in self.players:
            i = world.bedroom_index_at(*p.bedroom)
            if i is not None and owners[i] is None:
                owners[i] = p.name
        world.BEDROOM_OWNERS[:] = owners

    def spawn_players(self, n: int = DEFAULT_PLAYER_COUNT) -> list[Player]:
        """生成 n 个无名玩家（身份待定）"""
        out = []
        for i in range(n):
            bedroom = BEDROOM_ANCHORS[i % len(BEDROOM_ANCHORS)]
            bx, by = self.free_position("house", *bedroom)
            preset = PRESET_IDENTITIES[i % len(PRESET_IDENTITIES)]
            p = Player(pid=f"resident-{i + 1}", name=f"居民{i + 1}",
                       color=preset["color"], emoji=preset["emoji"],
                       bedroom=(bx, by), memory_path=(self.memory_dir / f"resident-{i+1}.sqlite3") if self.memory_dir else None)
            p.model = self.model_of.get(p.pid, "")
            self.players.append(p)
            out.append(p)
        return out

    def add_player_manual(self, name: str, color: str, emoji: str, join_identity: bool = True) -> Player:
        """未运行时手动添加玩家。join_identity=True 时保留占位名，开局身份阶段
        仍由模型自选姓名/性别/颜色（与默认居民对称）；False 则固定手动身份。"""
        if any(p.name == name for p in self.players):
            raise ValueError(f"玩家 {name} 已存在")
        import random as _r
        used = [p.bedroom for p in self.players]
        free = [a for a in BEDROOM_ANCHORS if a not in used]
        anchor = _r.choice(free) if free else _r.choice(BEDROOM_ANCHORS)
        bx, by = self.free_position("house", *anchor)
        pid = uuid.uuid4().hex[:8]
        p = Player(pid=pid, name=name, color=color,
                   emoji=emoji or "🙂", bedroom=(bx, by), memory_path=(self.memory_dir / f"{pid}.sqlite3") if self.memory_dir else None)
        p.identity_done = not join_identity
        self.players.append(p)
        if self.recorder and not self.recorder.closed:
            p.memory.sink=lambda data:self.record("memory",{"pid":p.pid,**data})
        self.log("system", f"🎉 {name} {p.emoji} 入住小镇" + ("，等待自选身份" if join_identity else ""), [p.pid],scope="game")
        return p

    def remove_player(self, pid: str) -> Player | None:
        """移除玩家（对话一并清理）"""
        p = self.by_pid(pid)
        if not p:
            return None
        self.leave_vehicle(p)
        self.drop_passengers(p)
        self.release_car(p)
        self.vehicles = [v for v in self.vehicles if v.owner_id != p.pid]
        # 清理其参与的对话
        for c in list(self.conversations):
            if p in (c.a, c.b):
                c._end("leave")
        self.conversations = [c for c in self.conversations
                              if c.phase != "ended" and p not in (c.a, c.b)]
        self.players.remove(p)
        self.names.pop(pid, None)
        for other in self.players:
            other.affinity.pop(pid, None)
        self.refresh_bedroom_names()
        self.log("system", f"👋 {p.name} 离开了小镇", [p.pid],scope="game")
        return p

    def leave_vehicle(self, p):
        if p.vehicle_driver:
            driver = self.by_pid(p.vehicle_driver)
            if driver:
                p.scene=driver.scene
                p.x,p.y=self.free_position(driver.scene,driver.x,driver.y,p)
                driver.passengers = [pid for pid in driver.passengers if pid != p.pid]
            p.vehicle_driver = None
            p.transport = "walk"
            p.state = "idle"

    def release_car(self, p):
        for car in self.vehicles:
            if car.driver_id == p.pid:
                car.driver_id = None
        p.car_id = None
        if p.scene=='town' and not self.position_free(p,p.scene,p.x,p.y):
            p.x,p.y=self.free_position(p.scene,p.x,p.y,p)

    def reserve_car(self, p):
        cars = sorted((v for v in self.vehicles if v.owner_id in (None, p.pid)
                       and v.driver_id in (None, p.pid)),
                      key=lambda v: (v.owner_id is None, abs(v.x - p.x) + abs(v.y - p.y)))
        for car in cars:
            if p.scene == "house":
                nearby = abs(car.x - 2) + abs(car.y - 12) <= 2
            else:
                nearby = p.scene == 'town'  # 先预约空车，寻路会走到停车点再驾驶。
            if nearby:
                car.driver_id, p.car_id = p.pid, car.id
                return car
        return None

    def set_travel_mode(self, p, mode):
        if p.vehicle_driver:
            raise ValueError("请先下车再选择出行方式")
        if mode not in world.TRAVEL_MODES:
            raise ValueError("未知出行方式")
        if mode == "car" and not p.driver_license:
            raise ValueError("驾驶汽车需要先在学校取得驾照；没有驾照仍可乘车")
        if mode == "car" and not p.car_id and not self.reserve_car(p):
            raise ValueError("附近没有可用汽车；共用车可能被占用或停在其他地点")
        if mode != "car":
            self.drop_passengers(p)
            self.release_car(p)
        p.travel_mode = mode
        if p.path:
            tx, ty = p.path[-1]
            # 保留跨场景最终目标，重算当前场景至停车点/目的地的路线。
            car = next((v for v in self.vehicles if v.id == p.car_id), None)
            if car and p.scene == "town":
                p.path = (world.bfs_path("town", p.x, p.y, car.x, car.y)
                          + world.travel_path("town", car.x, car.y, tx, ty, mode))
            else:
                p.path = world.travel_path(p.scene, p.x, p.y, tx, ty, mode)
        self.log("system", f"{p.name} 的室外出行方式改为{world.TRAVEL_MODES[mode]['name']}", [p.pid],scope="game")

    def buy_car(self, p):
        if not self.shop_car.get('enabled',True):
            raise ValueError('汽车已下架')
        if any(v.owner_id == p.pid for v in self.vehicles):
            raise ValueError("已经拥有一辆私家车")
        price=self.town_config["S"]["car"]["price"]
        if p.stats.money < price:
            raise ValueError(f"购买汽车需要 {price:g} 金钱")
        occupied = self.occupied("town")
        parking = [q for q in world.find_tiles("town", "#") if q not in occupied and world.walkable("town",*q)]
        if not parking:raise ValueError("附近没有可用停车位")
        origin = (p.x, p.y) if p.scene == "town" else (2, 12)
        road = min(parking,
                   key=lambda q: abs(q[0]-origin[0])+abs(q[1]-origin[1]))
        p.stats.money -= price
        car = Vehicle(id=uuid.uuid4().hex[:8], owner_id=p.pid, x=road[0], y=road[1])
        self.vehicles.append(car)
        self.log("system", f"{p.name} 花费 {price:g} 金钱购买了私家车", [p.pid],scope="game")
        return car

    def board_car(self, p, driver):
        car = next((v for v in self.vehicles if driver and v.id == driver.car_id and v.driver_id == driver.pid), None)
        if (not driver or driver is p or driver.travel_mode != "car" or not car or driver.vehicle_driver
                or p.scene != "town" or driver.scene != "town"
                or max(abs(p.x-driver.x), abs(p.y-driver.y)) > 2
                or (car.x, car.y) != (driver.x, driver.y)):
            raise ValueError("请选择身边两格内、位于室外且在车旁的司机")
        if p.vehicle_driver or p.passengers or len(driver.passengers) >= 3 or p._conversation or driver._conversation:
            raise ValueError("车辆最多载三名乘客；请先结束对话或当前乘车")
        self.release_car(p)
        p.path.clear()
        p.activity, p.activity_key, p._pending_key = None, "", ""
        p._pending_target_pid, p._final_scene, p._final_goal = None, None, None
        p.vehicle_driver, p.state, p.transport = driver.pid, "riding", "passenger"
        p.travel_mode = "walk"
        p.activity_left = 0
        p.plan = "搭乘室友的汽车"
        p.x, p.y = driver.x, driver.y
        driver.passengers.append(p.pid)
        self.log("system", f"{p.name} 搭乘了 {driver.name} 的汽车", [p.pid, driver.pid],scope="game")

    def travel_context(self, p):
        names = {o.pid: o.name for o in self.players}
        cars = "；".join(f"{'共用车' if not v.owner_id else names.get(v.owner_id,'已离开室友')+'的私家车'}停在({v.x},{v.y})，"
                        + (f"由{names.get(v.driver_id,'室友')}使用" if v.driver_id else "空闲") for v in self.vehicles)
        # 出行引导：让「学驾照→开共用车→买私车」这条路对模型可见
        tips = []
        shared_free = any(not v.owner_id and not v.driver_id for v in self.vehicles)
        own_car = any(v.owner_id == p.pid for v in self.vehicles)
        license_fee = self.town_config.get('U', {}).get('license_driving', {}).get('price', 80)
        car_price = self.town_config.get('S', {}).get('car', {}).get('price', 1000)
        if not p.driver_license:
            tips.append(f"我还没有驾照：到学校U上驾照课程（{license_fee:g}元），取得后即可驾驶共享汽车（无需自己买车）")
        elif not own_car and shared_free:
            tips.append("我有驾照，附近空闲的共用车可直接用 travel_mode=car 驾驶")
        if not own_car and p.stats.money >= car_price + 200:
            tips.append(f"现金充足（{p.stats.money:.0f}元）：可到商店S购买私家汽车（{car_price:g}元，action=buy_car）")
        return (f"当前位置({p.x},{p.y})。"
                + (f"正在搭乘{names.get(p.vehicle_driver, '室友')}的车。" if p.vehicle_driver else "") + f"{cars}。"
                + "".join(f"💡{t}。" for t in tips))

    def drop_passengers(self, driver):
        for pid in list(driver.passengers):
            p = self.by_pid(pid)
            if p:
                self.leave_vehicle(p)
        driver.passengers.clear()

    def apply_identity(self, p: Player, name: str, color: str, why: str = "", gender: str = "男"):
        """应用身份（LLM 选的或预设的）"""
        old = p.name
        p.name = name
        p.color = color
        p.gender = gender if gender in ("男", "女") else "男"
        p.identity_done = True
        self.names[p.pid] = name
        self.refresh_bedroom_names()
        self.log("system", f"🎭 {p.emoji} {name} 确定了自己的身份"
                 + (f"（{why}）" if why else ""), [p.pid],scope="game")
        p.remember(f"我叫{name}，性别{p.gender}，住进了合租之家", 8)

    async def run_identity_phase(self) -> None:
        self.ensure_recording()
        recorder=self.recorder
        token=REQUEST_TRACE.set(lambda data:recorder.append("model",data) if recorder else None)
        try:
            await self._run_identity_phase()
        finally:
            REQUEST_TRACE.reset(token)
            self.record_frame()

    async def _run_identity_phase(self) -> None:
        """开局身份阶段：并行让每个玩家（有档案的）自选身份"""
        # 任一玩家有可用档案就走 LLM 身份（每玩家各自的档案）
        any_api = any(self.profile_for(p.pid) for p in self.players)
        if not any_api:
            for i, p in enumerate(self.players):
                if p.identity_done:continue
                preset = PRESET_IDENTITIES[i % len(PRESET_IDENTITIES)]
                self.apply_identity(p, preset["name"], preset["color"])
            return
        taken: list[str] = [p.name for p in self.players if p.identity_done]
        self.identity_phase = True
        self.log("system", "🎪 新居民们正在选择名字、性别与颜色…")

        async def one(p: Player):
            if p.identity_done:return
            prof = self.profile_for(p.pid)
            model = self.model_of.get(p.pid) or (prof.default_model if prof else "")
            if not prof or not model:
                preset = PRESET_IDENTITIES[len(taken) % len(PRESET_IDENTITIES)]
                self.apply_identity(p, preset["name"], preset["color"])
                taken.append(preset["name"])
                return
            ident = await brain.choose_identity_llm(
                prof, model, list(taken),
                timeout=self.settings.decision_timeout)
            if ident and ident["name"] not in taken:
                taken.append(ident["name"])
                self.apply_identity(p, ident["name"], ident["color"], ident.get("why", ""), gender=ident.get("gender", "男"))
            else:
                preset = PRESET_IDENTITIES[len(taken) % len(PRESET_IDENTITIES)]
                if preset["name"] in taken:
                    preset = {"name": f"小{chr(0x91cc + len(taken))}",
                              "color": preset["color"], "emoji": preset["emoji"]}
                taken.append(preset["name"])
                self.apply_identity(p, preset["name"], preset["color"], gender=ident.get("gender", "男") if ident else "男")

        await asyncio.gather(*[one(p) for p in self.players])
        self.identity_phase = False
        self.log("system", "🏘️ 所有居民就位，小镇生活开始！")

    # ------------------------------------------------------------ 日志
    def log(self, type_: str, text: str, players: list | None = None, *, resident_memory: bool = True, scope: str | None = None) -> GameEvent:
        scope=scope or ('game' if type_ in ('activity','social','combat','dialogue','random') else 'user')
        if not resident_memory:scope='user'
        self.event_id += 1
        e = GameEvent(id=self.event_id, day=self.day, tick=self.tick,
                      clock=self.clock, wall=time.time(), type=type_,
                      text=text, players=players or [], scope=scope)
        self.events.append(e)
        if e.scope=="game" and resident_memory and players:
            for p in self.players:
                if p.pid in players or p.name in players:
                    p.remember(text, 6 if type_ in ("combat", "random") else 3)

        if len(self.events) > 5000:
            self.events = self.events[-3000:]
        self.record("event", e.model_dump())
        if self.recorder:self.record_frame()
        return e

    # ------------------------------------------------------------ 主循环
    async def run(self) -> None:
        while self.running:
            t0 = time.time()
            await self.step_tick()
            interval = 1.0 / max(0.1, self.speed)
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.05, interval - elapsed))

    def start(self):
        self.ensure_recording()
        if not self.running:
            self.running = True
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_event_loop().create_task(self.run())

    def pause(self):
        self.running = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        for p in self.players:
            if p.state == "deciding":
                p.state = "idle"
        self.log("system", "⏸ 模拟已暂停")

    def resume(self):
        if not self.running:
            self.log("system", "▶ 模拟继续")
            self.start()

    def stop(self):
        self.running = False
        if self._affinity_task:
            self._affinity_task.cancel()
            self._affinity_task = None
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        for p in self.players:
            if p.state == "deciding":
                p.state = "idle"
        self.log("system", "⏹ 模拟已停止")

    # ------------------------------------------------------------ 单 tick
    async def step_tick(self) -> None:
        self.ensure_recording()
        async with self._tick_lock:
            recorder=self.recorder
            token=REQUEST_TRACE.set(lambda data:recorder.append("model",data) if recorder else None)
            try:
                await self._step_tick()
            finally:
                REQUEST_TRACE.reset(token)
                self.record_frame()

    async def _step_tick(self) -> None:
        if self.identity_phase:
            return
        self.refresh_bedroom_names()
        for target_pid,proposal in list(self.sex_proposals.items()):
            if self.global_tick >= proposal['expires']:
                self._decline_sex(target_pid,'邀请已过期')
        await self.maintain_memories()

        # 1. 决策（idle 且不在对话中的玩家；住院/待收治者走住院专属决策）
        idlers = [p for p in self.players
                  if (p.state == "idle" or (p.state == "waiting_model" and time.monotonic() >= p.next_decision_at) or (p.vehicle_driver and self.global_tick % 6 == 0)) and p.identity_done
                  and p._conversation is None
                  and not getattr(p, 'hospitalized', False)
                  and not getattr(p, 'awaiting_admission', False)]
        for p in self.players:
            p.day, p.world_tick, p.hour = self.day, self.global_tick, self.hour
        if idlers:
            await self._decide_batch(idlers)

        # 1.5 住院决策：是否继续住院（每半天结算一次后询问，平时不问）
        inpatients = [p for p in self.players
                      if getattr(p, 'hospitalized', False) and p.identity_done]
        for p in inpatients:
            if p.state == 'idle' and getattr(p, 'ask_stay', False):
                p.ask_stay = False
                prof = self.profile_for(p.pid)
                if prof:
                    model = self.model_of.get(p.pid) or prof.default_model
                    p.state = 'deciding'
                    p.decision_status = '考虑是否继续住院'
                    try:
                        d = await brain.decide_llm(
                            prof, model, p, [], self.clock,
                            brain.get_system_prompt(self.system_prompt),
                            self.settings.temperature, self.settings.decision_timeout,
                            self.decor_common,
                            progress=lambda status, _p=p: self._decision_progress(_p, status))
                        if p.state == 'deciding' and p in self.players:
                            self.record("decision", {"pid": p.pid, **d.model_dump()})
                            self._apply_decision(p, d)
                    except Exception:                     # noqa: BLE001
                        p.state = 'idle'                 # 失败则默认继续住院
                        p.next_decision_at = time.monotonic() + 15
                else:
                    # 规则模式：钱够且生命没满就续住
                    if p.stats.hp >= 95 or p.stats.money < self.mechanics['hospital_fee_halfday']:
                        from . import hospital
                        hospital.discharge(self, p, reason="自动出院")
                    else:
                        p.plan = '住院休养中'

        # 有绑定模型但尚未得到可执行回答时，暂缓整个逻辑刻；不扣生命和饥饿。
        if any(p.state in ('waiting_model','deciding') for p in self.players):
            return

        # 1.5 推进活跃对话（对话很快：10轮以内不消耗世界时间）
        if self.conversations:
            for conv in list(self.conversations):
                if conv.phase == "ended":
                    self.conversations.remove(conv)
                    continue
                # 双方还在同一地点才继续；走散则结束
                if conv.kind != 'phone' and (conv.a.scene != conv.b.scene or (
                        max(abs(conv.a.x - conv.b.x), abs(conv.a.y - conv.b.y)) > 3
                        and not world.same_place(conv.a, conv.b))):
                    conv._end("separated")
                    self.conversations.remove(conv)
                    continue
                before={p.pid:p.stats.hp for p in self.players}
                await conv.advance()
                self._interrupt_health_losses(before)
            # 时间冻结：每满3轮对话才放行1刻世界时间（3轮≈1刻）
            active = [c for c in self.conversations if c.phase != 'ended']
            if active:
                owed = sum(c.turn_count for c in active) // 3
                if owed <= self._dialogue_released:
                    return
                self._dialogue_released = owed
        self.tick += 1
        self.global_tick += 1
        from .town_services import update_market
        update_market(self)
        if self.tick >= 144:
            self.tick = 0
            self.day += 1
            self.log("system", f"🌅 第 {self.day} 天开始了",scope="game")
        tick_health_start={p.pid:p.stats.hp for p in self.players}


        # 2. 推进状态机：循环轮换先行者，避免固定顺序长期抢占门口。
        self.repair_positions()
        order=self.players[self.global_tick%len(self.players):]+self.players[:self.global_tick%len(self.players)] if self.players else []
        for p in order:
            p.day, p.world_tick, p.hour = self.day, self.global_tick, self.hour
            before={q.pid:q.stats.hp for q in self.players}
            p.tick(sim=self)
            self._interrupt_health_losses(before)

        self.random_events.tick(self)

        # 3. 被动消耗
        for p in self.players:
            p.passive_drain(self)
        self._interrupt_health_losses(tick_health_start)

        # 4. 相遇播报（每 6 tick 检查一次，避免刷屏）
        if self.tick % 6 == 0:
            self._report_encounters()

        # 5. 状态记忆
        if self.tick % 12 == 0:
            for p in self.players:
                z = world.zone_of(p.scene, p.x, p.y)
                doing = f"，正在{p.activity['name']}" if p.activity else ""
                p.remember(f"我在{z}{doing}", 1)

        await self.maintain_memories()

        # 6. 好感队列消化（不阻塞）
        self._enqueue_periodic_affinity()
        if self._affinity_task is None or self._affinity_task.done():
            self._affinity_task = asyncio.create_task(self._drain_affinity())
        # 6.5 借款队列消化（不阻塞；离线入队的请求在这里补启动）
        if self.loan_queue:
            self._kick_loan_queue()

    def _injury_causes(self, p):
        """按当前状态推断 hp 下降的可能来源（用于同种伤害去重）。"""
        causes = set()
        if p.illness_severity:
            causes.add('illness')
        if p.stats.satiety <= 0:
            causes.add('starvation')
        if p.low_mood_ticks >= self.mechanics['low_mood_delay_ticks']:
            causes.add('low_mood')
        return causes

    def _interrupt_health_losses(self,before):
        for p in self.players:
            previous=before.get(p.pid,p.stats.hp)
            if p.stats.hp < previous-1e-8:
                self._alert_health_loss(p,previous-p.stats.hp)

    def _alert_health_loss(self,p,lost,*,force=False,cause=None):
        """受伤结束已排定的行动，让下一刻进入正常的模型决策流程。

        同种持续伤害（疾病/饥饿/低心情）只打断一次活动：首次打断后记入
        p._injury_break_done，此后同类伤害不再打断，角色可以安心赶路求医
        或吃饭。伤害结束（恢复/吃饱/心情回升）时由 clear_injury_break
        清除标记，下一轮同类伤害仍能正常唤醒。
        """
        if getattr(p,'last_health_alert_tick',-1)==self.global_tick:return
        # 持续疾病/饥饿不反复打断赶往诊所或餐厅的路，否则角色永远无法求助。
        active=p.state=='doing' or (force and p.state in ('moving','waiting_activity','waiting_consent','riding'))
        if not active:return
        causes = {cause} if cause else self._injury_causes(p)
        if cause != 'attack' and causes & set(getattr(p,'_injury_break_done',[]) or []):
            return               # 该种伤害已打断过一次，放行后续活动
        if causes and cause != 'attack':
            marks = [m for m in (getattr(p,'_injury_break_done',[]) or []) if m != 'attack']
            p._injury_break_done = list(dict.fromkeys(marks + [c for c in causes if c != 'attack']))
        p.last_health_alert_tick=self.global_tick
        prior=(p.activity or {}).get('name') or p._pending_key or p.plan or '当前行动'
        p.injury_alert=f'刚失去{lost:.2f}点生命，{prior}已中断；请先评估伤情，再决定下一步。'
        if p.pid in self.sex_proposals:self._decline_sex(p.pid,'受伤后中断')
        if p._conversation and p._conversation.phase!='ended':p._conversation._end('injury')
        partner=self.by_pid(p._bed_partner_pid) if p._bed_partner_pid else None
        if partner and partner._bed_partner_pid==p.pid and partner._conversation is None:
            self._cancel_timed_action(partner)
            partner.remember(f'{p.name}受伤，我们的共同活动中断了',5)
        self._cancel_timed_action(p)
        self.log('random',f'⚠️ {p.name}生命减少了{lost:.2f}点，已中断{prior}并重新考虑行动',[p.pid])

    def clear_injury_break(self,p,cause):
        """某种伤害结束时清除其打断标记；下次同种伤害重新开始计数。"""
        marks=getattr(p,'_injury_break_done',None)
        if marks and cause in marks:
            p._injury_break_done=[m for m in marks if m!=cause]

    def _cancel_timed_action(self,p):
        from .interaction_points import release
        from .town_services import finish
        if p.activity and p.activity.get('_kind') in ('job','course','license_course'):
            finish(self,p,p.activity_key)
        if p.vehicle_driver:self.leave_vehicle(p)
        if p.car_id:
            self.drop_passengers(p);self.release_car(p)
            p.transport='walk';p.travel_mode='walk'
        p.state='idle';p.activity=None;p.activity_key='';p.activity_left=0
        p.requested_duration=None;p.target_player=None;p._pending_key='';p._pending_target_pid=None
        p.path=[];p.route_goal=None;p._final_goal=None;p._final_scene=None
        p.interaction_resume=False
        release(self,p)
        if p.scene=='town' and world.tile('town',p.x,p.y)=='W':
            p.x,p.y=self.free_position('town',p.x,p.y,p)

    async def maintain_memories(self):
        async def one(p):
            if not any(p.memory.needs_compaction(tier) for tier in (1,2)):
                return
            profile = self.profile_for(p.pid)
            model = self.model_of.get(p.pid) or (profile.default_model if profile else "")
            async def selector(candidates, count):
                roommates = "、".join(f"{o.name}（{o.gender}）" for o in self.players if o is not p)
                return await brain.select_memories_llm(profile, model, p.name, candidates, count,
                    timeout=self.settings.decision_timeout,
                    gender_note=f"室友性别：{roommates}" if roommates else "")
            promotions = await p.memory.maintain(selector if profile and model else None)
            for tier, before, after, mode in promotions:
                self.log("memory", f"🧠 {p.name} 将{before}条{tier}级记忆整理为{after}条{tier+1}级记忆（{mode}提名后抽取80%＋全池随机20%）", [p.pid])
        await asyncio.gather(*(one(p) for p in self.players))

    def _report_encounters(self):
        """同场馆相遇播报（进入新场所遇到人时记事件+记忆）"""
        reported = set()
        for a in self.players:
            for b in self.players:
                if a.pid >= b.pid or not world.same_place(a, b):
                    continue
                if a.scene != "town":
                    continue          # 家里天天见，只在室外播报相遇
                zone = world.zone_of(a.scene, a.x, a.y)
                if zone == "路上":
                    continue
                key = (zone, a.pid, b.pid)
                if key in reported:
                    continue
                reported.add(key)
                # 只在两人都刚到达（上一个 tick 在移动）时播报，避免重复
                self.log("social", f"👋 {a.name} 和 {b.name} 在{zone}相遇了",
                         [a.pid, b.pid])

    # ------------------------------------------------------------ 决策
    async def _decide_batch(self, idlers: list[Player]) -> None:
        if not self.identity_phase and any(self.profile_for(p.pid) for p in idlers):
            await asyncio.gather(*[self._decide_one(p) for p in idlers])
        else:
            for p in idlers:
                self._apply_rule(p)

    def _observe_nearby(self, observer: Player) -> list[Player]:
        nearby = [other for other in self.players if other is not observer
                  and other.scene == observer.scene
                  and abs(other.x-observer.x)+abs(other.y-observer.y) <= 8]
        if nearby:
            observed = brain.nearby_description(nearby)
            observer.remember(f"第{self.day}天 {self.clock}，我观察到附近室友：{observed}", 4)
        return nearby

    async def _decide_one(self, p: Player) -> None:
        prof = self.profile_for(p.pid)
        if not prof:
            self._apply_rule(p)
            return
        others = [o for o in self.players if o is not p]
        nearby = self._observe_nearby(p)
        p.state = "deciding"
        p.decision_status = "等待模型回复"
        self.log("system",f"🧠 {p.name} 正在等待模型决定下一项活动",[p.pid],resident_memory=False)
        riding_before = p.vehicle_driver
        from .town_services import prompt_context
        p.town_context = prompt_context(self,p)
        p.transport_context = self.travel_context(p)
        p.user_prompt_template = self.user_prompt_template
        p.activity_descriptions = self.activity_descriptions
        from .activity_rules import registry
        p.activity_registry = registry(self)
        from .shop_products import catalog as shop_catalog
        p.shop_product_names={'item_'+key[4:]:row['name'] for key,row in shop_catalog(self).items() if not row['builtin']}
        proposal=self.sex_proposals.get(p.pid)
        sender=self.by_pid(proposal['from']) if proposal else None
        p.sex_invitation=(f'{sender.name}邀请你亲密互动；若愿意，选择action=intimate、target={sender.name}；也可以选择其他行动拒绝。' if sender else '无')
        p.known_names = {o.pid: o.name for o in self.players}
        p.known_roommates = [(o.name, o.gender) for o in others]
        from .activity_rules import catalog as activity_catalog
        p.phone_fee_per_tick = max(0, -activity_catalog(self)['phone']['effects'].get('money', -self.mechanics['phone_fee_per_tick']))
        model = self.model_of.get(p.pid) or prof.default_model
        try:
            d = await brain.decide_llm(
                prof, model, p, nearby, self.clock,
                brain.get_system_prompt(self.system_prompt),
                self.settings.temperature, self.settings.decision_timeout,
                self.decor_common, progress=lambda status:self._decision_progress(p,status))
            if p.state == "deciding" and p.vehicle_driver == riding_before and p in self.players:
                p.decision_status = "模型已回复"
                if p.pending_memory_activity:
                    if d.memory_note.strip():p.remember('我的活动后感想：'+d.memory_note.strip(),6)
                    p.pending_memory_activity=''
                p.info_feedback=''
                self.record("decision",{"pid":p.pid,**d.model_dump()})
                self._apply_decision(p, d)
                if p.state!='waiting_model':p.injury_alert=''
        except Exception as e:                 # noqa: BLE001
            if p.state != "deciding" or p.vehicle_driver != riding_before or p not in self.players:
                return
            self._llm_error_count += 1
            p.state="waiting_model"
            p.next_decision_at=time.monotonic()+(15 if getattr(e,'retryable',True) else 60)
            p.decision_status=f"等待重试：{str(e)[:80]}"
            self.log("error",f"⏳ {p.name} 暂未获得有效模型决策，稍后重试（{str(e)[:80]}）",[p.pid])

    def _notify_rejection(self, p: Player, key: str, target: Player | None):
        """行动被拒：把系统/对象给出的原因告知发出者，写入双方记忆。"""
        reason = getattr(p, '_rejection', '')
        p._rejection = ''
        if not reason:
            return
        from .activities import ACTIVITIES
        name = ACTIVITIES.get(key, {}).get('name', key)
        tname = f'（对象：{target.name}）' if target else ''
        p.remember(f'我想{name}（{target.name if target else "他人"}），但被拒绝：{reason}', 5)
        if target is not None and target is not p:
            target.remember(f'{p.name}想和我{name}，没有成行：{reason}', 4)
        p.info_feedback = f'{name}被拒绝：{reason}（请换一种做法）'
        self.log('social', f'🚫 {p.name} 的{name}{tname}被拒：{reason}',
                 [p.pid] + ([target.pid] if target else []), scope='game')

    def _decision_progress(self,p,status):
        p.decision_status=status
        self.log("system",f"🧠 {p.name}：{status}",[p.pid],resident_memory=False)

    def _apply_rule(self, p: Player, depth: int = 0) -> None:
        from .activity_rules import registry
        available=registry(self)
        if depth == 0:
            self._observe_nearby(p)
        p.injury_alert=''
        if p.vehicle_driver:
            driver = self.by_pid(p.vehicle_driver)
            if driver and driver.state == "moving" and p.stats.satiety > 25:
                p.state = "riding"
                return
            self.leave_vehicle(p)
        if depth > 3:                         # 防递归：直接原地思考
            if 'think' in available:p.begin_activity("think", self)
            else:p.state='idle'
            return
        others = [o for o in self.players if o is not p]
        d = brain.decide_rule(p, p.nearby_players(others), self.hour)
        if d.action not in available:
            fallback=next((key for key,a in available.items() if a.get('need') is None and a.get('scene') in ('any',p.scene)),None)
            if fallback is None:p.state='idle';return
            d=Decision(action=fallback,reason='选择当前可用活动',source='rule')
        self._apply_decision(p, d, depth)

    def _reject_decision(self,p,d,depth):
        p.requested_duration=None
        if d.source=='llm':
            p.state='waiting_model'
            p.next_decision_at=time.monotonic()+1
            p.decision_status='模型行动暂不可执行，等待重新选择'
            p.remember(f"我想{act.ACTIVITIES.get(d.action,{}).get('name',d.action)}，但当时条件不合适，没能进行",4)
            self.log('error',f'⏳ {p.name} 的{d.action}暂不可执行，将请模型重新选择',[p.pid])
        else:
            self._apply_rule(p,depth+1)

    def _apply_decision(self, p: Player, d: Decision, depth: int = 0) -> None:
        p.requested_duration = None
        key = d.action
        if key in self.disabled_activities:
            self._reject_decision(p,d,depth)
            return
        if key != 'computer':
            p.computer_feedback = ''
        # ---- 住院专属动作：住院中只认 stay/leave，其他行动一律视为继续住院 ----
        from . import hospital
        if getattr(p, 'hospitalized', False):
            if key == 'hospital_leave':
                hospital.discharge(self, p, reason="办理出院")
                p.state = "idle"
                return
            if key != 'hospital_stay':
                p.remember(f"我正在住院，想做{act.ACTIVITIES.get(key, {}).get('name', key)}但被护士拦下了", 4)
            else:
                p.remember("我决定继续住院休养", 3)
                self.log("system", f"🛏 {p.name} 决定继续住院", [p.name], scope="game")
            p.state = "idle"
            return
        if key != 'sex' and p.pid in self.sex_proposals:
            self._decline_sex(p.pid,('我选择了别的安排'+(f'（{d.reason}）' if getattr(d,'reason','') else '')),by_target=True)
        if key in ('sex','phone'):
            from .activity_rules import available_here
            if not available_here(self,p,key):
                p.info_feedback='当前不在这项活动允许的地点';p.remember(p.info_feedback,3);p.state='idle';return
        if key == 'sex':
            self._sex_decision(p,d)
            return
        if key == 'inspect':
            from .town_services import inspect
            try:
                p.info_feedback=inspect(self,p,d.target)
                p.state='idle';p.plan='查询'+d.target
            except ValueError as exc:
                p.info_feedback=str(exc);p.state='idle'
            return
        if key == 'visit':
            from .town_services import NAMES
            code=next((c for c,name in NAMES.items() if d.target in (c,name)),None)
            if not code:
                p.info_feedback='请选择目录中的场所代码或名称';p.state='idle';return
            if code=='W':
                positions=[(x+dx,y+dy) for x,y in world.find_tiles('town','W') for dx,dy in ((0,1),(0,-1),(1,0),(-1,0)) if world.walkable('town',x+dx,y+dy)]
            else:positions=[q for q in world.find_tiles('town',code) if world.walkable('town',*q)]
            if not positions:p.info_feedback='场所暂不可到达';p.state='idle';return
            ref=(p.x,p.y) if p.scene=='town' else (1,12)
            tx,ty=min(positions,key=lambda q:abs(q[0]-ref[0])+abs(q[1]-ref[1]))
            try:
                if d.travel_mode and d.travel_mode!=p.travel_mode:self.set_travel_mode(p,d.travel_mode)
                p._pending_key='';p.plan='前往'+NAMES[code]
                p._plan_route(self,'town',tx,ty)
            except ValueError as exc:p.info_feedback=str(exc);p.state='idle'
            return
        if key in ("emoji","transfer"):
            from .resident_tools import instant_action
            try:
                instant_action(self,p,d)
                p.state="riding" if p.vehicle_driver else "idle";p.plan=d.reason;p.decision=d
            except ValueError as exc:
                p.remember(str(exc),3);self._reject_decision(p,d,depth)
            return
        if key == 'phone':
            target=next((o for o in self.players if o is not p and d.target in (o.pid,o.name)),None)
            from .activity_rules import catalog as activity_catalog
            per_tick=max(0,-activity_catalog(self)['phone']['effects'].get('money',-self.mechanics['phone_fee_per_tick']))
            per_round=round(per_tick/10,2)          # 每轮话费（10轮≈1刻）
            if not target or target.state not in ('idle','waiting_model') or p._conversation or target._conversation or p.vehicle_driver or target.vehicle_driver or p.car_id or target.car_id:
                why='对方正忙或不在空闲状态，无法接电话' if target else '没有找到这位室友'
                p.remember(f'我想给{d.target}打电话，但{why}',4)
                if target:target.remember(f'{p.name}想给我打电话，但我正忙没能接',3)
                p.info_feedback=f'打电话被拒：{why}';p.state='idle';return
            if p.stats.money + 1e-8 < per_round:
                p.remember(f'我想给{target.name}打电话，但付不起一轮{per_round:g}元话费',4)
                p.info_feedback='话费不足，暂时无法拨打';p.state='idle';return
            p.decision=d;p.target_player=target;p.plan=d.reason or '给室友打电话'
            p.requested_duration=(d.duration_minutes+9)//10 if d.duration_minutes else act.ACTIVITIES['phone']['duration']
            p._start_local('phone',self)
            target.target_player=p;target.requested_duration=p.activity_total
            target._start_local('phone',self)
            p.phone_paid=0.0
            self.start_conversation(p,target,'phone')
            return
        if p.vehicle_driver:
            if key == "stay_in_car":
                p.state = "riding"
                return
            self.leave_vehicle(p)
        if key in ("leave_car", "stay_in_car"):
            p.begin_activity("think", self)
            return
        try:
            if d.travel_mode and d.travel_mode != p.travel_mode:
                self.set_travel_mode(p, d.travel_mode)
            if key == "buy_car":
                if p.scene!='town' or world.tile(p.scene,p.x,p.y)!='S':
                    raise ValueError('请先到商店再购买汽车')
                self.buy_car(p)
                p.begin_activity("think", self)
                return
            if key == "ride":
                driver = next((o for o in self.players if o.pid == d.target or o.name == d.target), None)
                self.board_car(p, driver)
                return
        except ValueError as exc:
            p.remember(str(exc), 3)
            self._reject_decision(p,d,depth)
            return
        if key=="eat" and p.items.get('meal',0)<=0 and self.meal_until < self.global_tick:
            d=d.model_copy(update={"action":"eat_out"});key="eat_out"
        from .town_services import prepare
        try:
            a=prepare(self,p,d);key=d.action
        except ValueError as exc:
            p.remember(str(exc),3);self._reject_decision(p,d,depth);return
        if not a:
            self._reject_decision(p,d,depth)
            return
        p.plan = d.reason
        p.decision = d
        if key=="computer":p.computer_operation_done=False

        if a.get("cost", 0) > p.stats.money:
            p.remember(f"我想{a['name']}但钱不够", 5)
            self._reject_decision(p,d,depth)
            return

        target = None
        if key=='home_treat':
            target=next((o for o in self.players if o.name==d.target or o.pid==d.target),p)
        elif a.get("need") == "player" or a.get('_target_mode') in ('optional','required') and d.target:
            target = next((o for o in self.players
                           if (o.name == d.target or o.pid == d.target)
                           and o is not p), None)
            if target is None and (a.get('need')=='player' or a.get('_target_mode')=='required'):
                self._reject_decision(p,d,depth)
                return

        p.requested_duration = (d.duration_minutes+9)//10 if d.duration_minutes and not a.get("fixed_duration") else None
        if a.get('_kind')=='job' and p.requested_duration is not None:
            p.requested_duration=max(a['duration'],p.requested_duration)
        ok = p.begin_activity(key, self, target=target)
        if not ok:
            p.requested_duration = None
            self._notify_rejection(p, key, target)
            self._reject_decision(p,d,depth)
        elif d.source == "llm":
            self.log("activity",
                     f"🧠 {p.name} 决定{a['name']}"
                     + (f"（对象:{d.target}）" if d.target else "")
                     + f" —— {d.reason}", [p.pid])

    def _decline_sex(self,target_pid,reason,by_target=False):
        proposal=self.sex_proposals.pop(target_pid,None)
        if not proposal:return
        sender=self.by_pid(proposal['from']);target=self.by_pid(target_pid)
        if sender and sender.state=='waiting_consent':
            sender.state='idle';sender.plan='亲密邀请结束'
        if sender and target:
            sender.remember(f'我向{target.name}发出亲密邀请被拒绝：{reason}',7)
            if by_target:
                target.remember(f'我拒绝了{sender.name}的亲密邀请：{reason}',5)
            sender.info_feedback=f'亲密邀请被{target.name}拒绝：{reason}'
            self.log('social',f'{sender.name}向{target.name}发出的亲密邀请未继续：{reason}',[sender.pid,target.pid])

    def _sex_decision(self,p,d):
        target=next((o for o in self.players if o is not p and d.target in (o.pid,o.name)),None)
        if not target or p.scene!=target.scene or p.vehicle_driver or target.vehicle_driver:
            p.state='idle';p.info_feedback='请指定同场景、未乘车的室友';return
        proposal=self.sex_proposals.get(p.pid)
        if proposal and proposal['from']==target.pid:
            sender=target
            if sender.state!='waiting_consent' or self.global_tick>=proposal['expires']:
                self._decline_sex(p.pid,'邀请已过期');p.state='idle';return
            location=proposal['location']
            if location=='here' and max(abs(p.x-sender.x),abs(p.y-sender.y))>2:
                self._decline_sex(p.pid,'双方尚未靠近');p.state='idle';p.info_feedback='请先靠近对方';return
            if location=='bed' and p.scene!='house':
                self._decline_sex(p.pid,'双方尚未回家');p.state='idle';return
            sender.requested_duration=proposal['duration'];p.requested_duration=proposal['duration']
            sender.target_player=p;p.target_player=sender;p.decision=d
            self.sex_proposals.pop(p.pid,None)
            if location=='bed':
                from .interaction_points import route_bed_pair
                p.state='idle'
                if not route_bed_pair(self,sender,p,'sex'):
                    sender.state='idle';p.state='idle';p.info_feedback='床位暂不可用';return
            else:
                sender._start_local('sex',self);p._start_local('sex',self)
            self.log('social',f'{sender.name}与{p.name}互相同意亲密相处',[sender.pid,p.pid])
            return
        if p.pid in self.sex_proposals:self._decline_sex(p.pid,'选择了另一位室友')
        if target.state not in ('idle','waiting_model') or target._conversation or p._conversation:
            p.state='idle';p.info_feedback='对方现在没有空回应邀请';return
        location=d.location or 'here'
        if location=='bed' and p.scene!='house':
            p.state='idle';p.info_feedback='床上活动需要双方先回家';return
        if location=='here' and max(abs(p.x-target.x),abs(p.y-target.y))>2:
            p.state='idle';p.info_feedback='当前位置需要双方先靠近';return
        self.sex_proposals[target.pid]={'from':p.pid,'location':location,'duration':(d.duration_minutes+9)//10 if d.duration_minutes else 3,'expires':self.global_tick+12}
        p.state='waiting_consent';p.decision=d;p.plan=f'等待{target.name}回应亲密邀请'
        self.log('social',f'{p.name}邀请{target.name}亲密相处，等待对方自己决定',[p.pid,target.pid])

    # ------------------------------------------------------------ 对话
    DIALOGUE_COOLDOWN_TICKS = 6      # 同一对居民两场对话的最小间隔（刻）

    def _step_off_gate(self, p: Player) -> None:
        """对话开始前把站在大门格上的人挪到旁边，避免堵门。"""
        if world.tile(p.scene, p.x, p.y) == 'E':
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nx, ny = p.x + dx, p.y + dy
                if world.tile(p.scene, nx, ny) not in ('E',) and world.walkable(p.scene, nx, ny) \
                        and self.position_free(p, p.scene, nx, ny):
                    p.x, p.y = nx, ny
                    return

    def start_conversation(self, initiator: Player, target: Player, kind: str):
        """发起回合制对话（由 player._start_social 调用）"""
        # 任一方已在对话中则不再开新对话
        if initiator._conversation or target._conversation:
            return
        # 冷却：同一对居民刚结束对话不久，不再连聊（电话不受限）
        if kind == 'chat':
            pair = '|'.join(sorted((initiator.pid, target.pid)))
            last_end = self._dialogue_cooldown.get(pair, -999)
            if self.global_tick - last_end < self.DIALOGUE_COOLDOWN_TICKS:
                initiator.remember(f'我和{target.name}刚聊过，先各忙各的吧', 4)
                initiator.info_feedback = f'与{target.name}刚聊过，过一会儿再聊'
                if initiator.state == 'doing' and initiator.activity_key == 'chat':
                    initiator.activity = None; initiator.activity_key = ''
                    initiator.state = 'idle'
                if target.state == 'doing' and target.activity_key == 'chat':
                    target.activity = None; target.activity_key = ''
                    target.state = 'idle'
                self.log('social', f'💬 {initiator.name} 想再找 {target.name} 聊，两人刚聊过先各自行动',
                         [initiator.pid, target.pid])
                return
        # love 需要双方好感达标（在 player 层校验过，这里再保险一次）
        if kind == "love":
            a_ok = initiator.affinity.get(target.pid, 0) >= 55
            b_ok = target.affinity.get(initiator.pid, 0) >= 55
            if not (a_ok and b_ok):
                return
        if kind != 'phone':
            self._step_off_gate(initiator)
            self._step_off_gate(target)
        conv = Conversation(initiator, target, kind, self)
        initiator._conversation = conv
        target._conversation = conv
        self.conversations.append(conv)
        kind_cn = {"chat": "对话", "love": "约会", "phone": "打电话"}[kind]
        self.log("social", f"💬 {initiator.name} 找 {target.name} {kind_cn}",
                 [initiator.pid, target.pid])

    def on_conversation_end(self, conv: Conversation):
        """对话结束：好感结算（LLM）+ 基础增量"""
        a, b = conv.a, conv.b
        key = "love" if conv.kind == "love" else "phone" if conv.kind == "phone" else "chat"
        # LLM 决定的浮动增减
        self.request_affinity(a, b, key,conv.elapsed*10)
        self.request_affinity(b, a, key,conv.elapsed*10)

    # ------------------------------------------------------------ 好感队列
    def request_affinity(self, a: Player, o: Player, activity_key: str, duration_minutes=None):
        minutes=duration_minutes if duration_minutes is not None else max(0,a.activity_total-a.activity_left)*10
        self.affinity_queue.append((a.pid,o.pid,activity_key,minutes))

    def _enqueue_periodic_affinity(self):
        budget=self.mechanics['affinity_decisions_per_tick']
        if budget<=0 or sum(q[0]=='periodic' for q in self.affinity_queue if q)>8:return
        eligible=[p for p in self.players if self.profile_for(p.pid)]
        if len(self.players)<2:return
        for offset in range(min(budget,len(eligible))):
            actor=eligible[(self.global_tick+offset)%len(eligible)]
            others=[p for p in self.players if p is not actor]
            target=others[(self.global_tick+offset)%len(others)]
            self.affinity_queue.append(('periodic',actor.pid,target.pid))

    async def _drain_affinity(self):
        """异步消化好感请求：LLM 决定 a 对 o 的好感增减"""
        if self._affinity_busy or not self.affinity_queue:
            return
        self._affinity_busy = True
        try:
            while self.affinity_queue:
                # 优先消化真实互动（对话/协同）结算；周期性自主判断排在其后，
                # 避免高频轮询把互动好感挤到长队尾甚至落空。
                idx = next((i for i, q in enumerate(self.affinity_queue) if q[0] != 'periodic'), None)
                request = self.affinity_queue.pop(idx if idx is not None else 0)
                if request[0]=='periodic':
                    a,o=self.by_pid(request[1]),self.by_pid(request[2])
                    if not a or not o:continue
                    prof=self.profile_for(a.pid)
                    if not prof:continue
                    before=a.affinity.get(o.pid,50)
                    model=self.model_of.get(a.pid) or prof.default_model
                    try:delta=await brain.affinity_tick_llm(prof,model,a,o,timeout=min(20.0,self.settings.decision_timeout))
                    except asyncio.CancelledError:
                        self.affinity_queue.insert(0,request)
                        raise
                    a.affinity[o.pid]=max(0,min(100,before+delta))
                    if delta:
                        a.remember(f'我对{o.name}的好感改变了{delta:+d}点',1)
                        self.log('social',f'💗 {a.name} 对 {o.name} 的好感自主变化 {delta:+d}',[a.pid,o.pid])
                    continue
                a_pid,o_pid,key=request[:3]
                hours=(request[3] if len(request)>3 else 30)/60
                a, o = self.by_pid(a_pid), self.by_pid(o_pid)
                if not a or not o:
                    continue
                before = a.affinity.get(o.pid, 50)
                delta = 0
                prof = self.profile_for(a.pid)
                if prof:
                    model = self.model_of.get(a.pid) or prof.default_model
                    what = act.ACTIVITIES.get(key, {}).get("name", key)
                    try:
                        delta = await brain.affinity_change_llm(
                            prof, model, a.name, o.name, what, f"一起{what}，请评估每小时的好感变化率", before,
                            timeout=min(20.0, self.settings.decision_timeout))
                    except asyncio.CancelledError:
                        self.affinity_queue.insert(0,request)
                        raise
                else:
                    delta = random.choice([1, 1, 2, 2, 3])   # 规则模式：温和正增长
                delta*=hours
                new_val = max(0.0, min(100.0, before + delta))
                a.affinity[o.pid] = new_val
                if delta != 0 and prof:
                    sign = "+" if delta > 0 else ""
                    self.log("social",
                             f"💗 {a.name} 对 {o.name} 的好感变化 {sign}{delta}"
                             f"（{before:.0f}→{new_val:.0f}）", [a.pid, o.pid])
                    o.remember(f"和{a.name}一起{act.ACTIVITIES.get(key, {}).get('name', key)}",
                               3)
        finally:
            self._affinity_busy = False

    def _kick_loan_queue(self):
        """有新借款请求时启动异步消化（不阻塞主循环）。
        无运行事件循环（测试/离线调用）时只入队，由主循环下轮消化。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loan_task is None or self._loan_task.done():
            self._loan_task = loop.create_task(self._drain_loans())
            def _report(task):
                if not task.cancelled() and task.exception():
                    self.log('error', f'⚠️ 借款队列任务异常退出：{task.exception()!r}')
            self._loan_task.add_done_callback(_report)

    async def _drain_loans(self):
        """异步消化急救借款：室友的模型决定借多少，逐笔入账。
        单个请求的任何异常都只按「借 0 元」处理，绝不让任务中途崩溃——
        否则待收治者会永远卡在 awaiting_admission，被排除在所有决策之外。"""
        from . import hospital
        if self._loan_busy or not self.loan_queue:
            return
        self._loan_busy = True
        try:
            while self.loan_queue:
                request = self.loan_queue.pop(0)
                try:
                    borrower_pid, lender_pid = request
                    p, lender = self.by_pid(borrower_pid), self.by_pid(lender_pid)
                    if not p or not lender or not getattr(p, 'awaiting_admission', False):
                        continue
                    need = max(0.0, p.debt_goal - sum(p.loans.values()))
                    if need <= 0:
                        continue
                    aff = lender.affinity.get(p.pid, 50.0)
                    amount = 0
                    prof = self.profile_for(lender.pid)
                    if prof:
                        model = self.model_of.get(lender.pid) or prof.default_model
                        try:
                            amount = await brain.loan_amount_llm(
                                prof, model, lender.name, p.name, need, p.stats.money,
                                aff, lender.stats.money,
                                timeout=min(25.0, self.settings.decision_timeout))
                        except asyncio.CancelledError:
                            self.loan_queue.insert(0, request)
                            raise
                    else:
                        # 规则模式兜底：好感越高越肯借，最多借出一半现金
                        amount = min(int(lender.stats.money * aff / 200), int(need))
                    amount = max(0, min(amount, int(lender.stats.money), int(need)))
                    if amount > 0:
                        hospital.pay(self, p, lender, amount)
                        self.log("social", f"💳 {lender.name} 借给 {p.name} {amount:.0f}元急救费",
                                 [lender.pid, p.pid])
                        lender.remember(f"{p.name}生命垂危，我借给TA{amount:.0f}元急救费", 7)
                        if p in self.players and lender in self.players:
                            p.affinity[lender.pid] = min(100.0, p.affinity.get(lender.pid, 50) + 5)
                            lender.affinity[p.pid] = min(100.0, lender.affinity.get(p.pid, 50) + 3)
                    else:
                        lender.remember(f"{p.name}向我借钱急救，我犹豫了，没有借", 6)
                    # 借够了：立即收治，剩余排队请求作废
                    if p in self.players and sum(p.loans.values()) >= p.debt_goal - 1e-9:
                        hospital.admit(self, p, note="（借款补齐费用）")
                        self.loan_queue = [r for r in self.loan_queue if r[0] != borrower_pid]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:                   # noqa: BLE001
                    self.log('error', f'⚠️ 借款请求处理失败（按未借处理）：{str(exc)[:80]}')
            # 队列清空：复查所有待收治者
            for q in self.players:
                if getattr(q, 'awaiting_admission', False):
                    borrowed = sum(q.loans.values())
                    if borrowed >= q.debt_goal - 1e-9:
                        hospital.admit(self, q, note="（借款补齐费用）")
                    else:
                        q.awaiting_admission = False
                        hospital.refuse(self, q, q.debt_goal - borrowed)
        finally:
            self._loan_busy = False
