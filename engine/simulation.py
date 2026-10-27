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

from . import activities as act
from . import brain, world
from .conversation import Conversation
from .llm import LLMError
from .models import ApiProfile, Decision, GameEvent, Settings
from .player import Player

BEDROOM_ANCHORS = [(3, 4), (9, 4), (14, 4), (20, 4), (23, 21)]   # 五间卧室的中心
DEFAULT_PLAYER_COUNT = 3            # 初始玩家数（网络压力考虑，用户要求）

PRESET_IDENTITIES = [
    {"name": "小满", "color": "#e74c3c", "emoji": "🧑‍🎓"},
    {"name": "小舟", "color": "#3498db", "emoji": "👩‍🎨"},
    {"name": "小鹿", "color": "#2ecc71", "emoji": "🧑‍🌾"},
    {"name": "小石", "color": "#9b59b6", "emoji": "👩‍🔬"},
    {"name": "小枫", "color": "#e67e22", "emoji": "🧑‍🍳"},
]


class Simulation:
    def __init__(self) -> None:
        self.players: list[Player] = []
        self.day = 1
        self.tick = 0
        self.global_tick = 0
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
        self.affinity_queue: list[tuple] = []   # (a_pid, o_pid, activity_key)
        self._affinity_busy = False
        self._loop_task: asyncio.Task | None = None
        self._llm_error_count = 0
        self.identity_phase = False             # 是否在开局身份阶段
        self.model_of: dict[str, str] = {}      # pid → 模型（server 同步进来）
        self.binding_of: dict[str, str] = {}    # pid → profile_id（server 同步）
        self.names = {}                         # pid → 当前名字（广播用）

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

    # ------------------------------------------------------------ 玩家管理
    def spawn_players(self, n: int = DEFAULT_PLAYER_COUNT) -> list[Player]:
        """生成 n 个无名玩家（身份待定）"""
        out = []
        for i in range(n):
            bedroom = BEDROOM_ANCHORS[i % len(BEDROOM_ANCHORS)]
            bx, by = world.nearest_walkable("house", *bedroom)
            preset = PRESET_IDENTITIES[i % len(PRESET_IDENTITIES)]
            p = Player(pid=uuid.uuid4().hex[:8], name=f"居民{i + 1}",
                       color=preset["color"], emoji=preset["emoji"],
                       bedroom=(bx, by))
            p.model = self.model_of.get(p.pid, "")
            self.players.append(p)
            out.append(p)
        return out

    def add_player_manual(self, name: str, color: str, emoji: str) -> Player:
        """未运行时手动添加玩家（有名字，跳过身份阶段）"""
        if any(p.name == name for p in self.players):
            raise ValueError(f"玩家 {name} 已存在")
        import random as _r
        used = [p.bedroom for p in self.players]
        free = [a for a in BEDROOM_ANCHORS if a not in used]
        anchor = _r.choice(free) if free else _r.choice(BEDROOM_ANCHORS)
        bx, by = world.nearest_walkable("house", *anchor)
        p = Player(pid=uuid.uuid4().hex[:8], name=name, color=color,
                   emoji=emoji or "🙂", bedroom=(bx, by))
        p.identity_done = True
        self.players.append(p)
        self.log("system", f"🎉 {name} {p.emoji} 入住小镇", [p.pid])
        return p

    def remove_player(self, pid: str) -> Player | None:
        """移除玩家（对话一并清理）"""
        p = self.by_pid(pid)
        if not p:
            return None
        # 清理其参与的对话
        for c in list(self.conversations):
            if p in (c.a, c.b):
                c._end("leave")
        self.conversations = [c for c in self.conversations
                              if c.phase != "ended" and p not in (c.a, c.b)]
        self.players.remove(p)
        self.log("system", f"👋 {p.name} 离开了小镇", [p.pid])
        return p

    def apply_identity(self, p: Player, name: str, color: str, why: str = ""):
        """应用身份（LLM 选的或预设的）"""
        old = p.name
        p.name = name
        p.color = color
        p.identity_done = True
        self.names[p.pid] = name
        self.log("system", f"🎭 {p.emoji} {name} 确定了自己的身份"
                 + (f"（{why}）" if why else ""), [p.pid])
        p.remember(f"我叫{name}，住进了合租之家", 8)

    async def run_identity_phase(self) -> None:
        """开局身份阶段：并行让每个玩家（有档案的）自选身份"""
        # 任一玩家有可用档案就走 LLM 身份（每玩家各自的档案）
        any_api = any(self.profile_for(p.pid) for p in self.players)
        if not any_api:
            for i, p in enumerate(self.players):
                preset = PRESET_IDENTITIES[i % len(PRESET_IDENTITIES)]
                self.apply_identity(p, preset["name"], preset["color"])
            return
        taken: list[str] = []
        self.identity_phase = True
        self.log("system", "🎪 新居民们正在给自己起名字…")

        async def one(p: Player):
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
                self.apply_identity(p, ident["name"], ident["color"], ident.get("why", ""))
            else:
                preset = PRESET_IDENTITIES[len(taken) % len(PRESET_IDENTITIES)]
                if preset["name"] in taken:
                    preset = {"name": f"小{chr(0x91cc + len(taken))}",
                              "color": preset["color"], "emoji": preset["emoji"]}
                taken.append(preset["name"])
                self.apply_identity(p, preset["name"], preset["color"])

        await asyncio.gather(*[one(p) for p in self.players])
        self.identity_phase = False
        self.log("system", "🏘️ 所有居民就位，小镇生活开始！")

    # ------------------------------------------------------------ 日志
    def log(self, type_: str, text: str, players: list | None = None) -> GameEvent:
        self.event_id += 1
        e = GameEvent(id=self.event_id, day=self.day, tick=self.tick,
                      clock=self.clock, wall=time.time(), type=type_,
                      text=text, players=players or [])
        self.events.append(e)
        if len(self.events) > 5000:
            self.events = self.events[-3000:]
        return e

    # ------------------------------------------------------------ 主循环
    async def run(self) -> None:
        self.running = True
        while self.running:
            t0 = time.time()
            await self.step_tick()
            interval = 1.0 / max(0.1, self.speed)
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.05, interval - elapsed))

    def start(self):
        if not self.running:
            self.running = True
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_event_loop().create_task(self.run())

    def pause(self):
        self.running = False
        self.log("system", "⏸ 模拟已暂停")

    def resume(self):
        if not self.running:
            self.log("system", "▶ 模拟继续")
            self.start()

    def stop(self):
        self.running = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        self.log("system", "⏹ 模拟已停止")

    # ------------------------------------------------------------ 单 tick
    async def step_tick(self) -> None:
        self.tick += 1
        self.global_tick += 1
        if self.tick >= 144:
            self.tick = 0
            self.day += 1
            self.log("system", f"🌅 第 {self.day} 天开始了")

        # 开局身份阶段未完成时不推进生活
        if self.identity_phase:
            return

        # 1. 决策（idle 且不在对话中的玩家）
        idlers = [p for p in self.players
                  if p.state == "idle" and p.identity_done
                  and p._conversation is None]
        if idlers:
            await self._decide_batch(idlers)

        # 1.5 推进活跃对话（每 tick 每对话推进一步回合）
        if self.conversations:
            for conv in list(self.conversations):
                if conv.phase == "ended":
                    self.conversations.remove(conv)
                    continue
                # 双方还在同一地点才继续；走散则结束
                if conv.a.scene != conv.b.scene or (
                        max(abs(conv.a.x - conv.b.x), abs(conv.a.y - conv.b.y)) > 3
                        and not world.same_place(conv.a, conv.b)):
                    conv._end("separated")
                    self.conversations.remove(conv)
                    continue
                await conv.advance()

        # 2. 推进状态机
        for p in self.players:
            p.day, p.world_tick, p.hour = self.day, self.global_tick, self.hour
            p.tick(sim=self)

        # 3. 被动消耗
        for p in self.players:
            p.passive_drain(self)

        # 4. 相遇播报（每 6 tick 检查一次，避免刷屏）
        if self.tick % 6 == 0:
            self._report_encounters()

        # 5. 状态记忆
        if self.tick % 12 == 0:
            for p in self.players:
                z = world.zone_of(p.scene, p.x, p.y)
                doing = f"，正在{p.activity['name']}" if p.activity else ""
                p.remember(f"我在{z}{doing}", 1)

        # 6. 好感队列消化（不阻塞）
        asyncio.get_event_loop().create_task(self._drain_affinity())

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

    async def _decide_one(self, p: Player) -> None:
        prof = self.profile_for(p.pid)
        if not prof:
            self._apply_rule(p)
            return
        others = [o for o in self.players if o is not p]
        nearby = [o.name for o in p.nearby_players(others)]
        p.state = "deciding"
        model = self.model_of.get(p.pid) or prof.default_model
        try:
            d = await brain.decide_llm(
                prof, model, p, nearby, self.clock,
                brain.get_system_prompt(self.system_prompt),
                self.settings.temperature, self.settings.decision_timeout,
                self.decor_common)
            self._apply_decision(p, d)
        except Exception as e:                 # noqa: BLE001
            self._llm_error_count += 1
            self.log("error", f"⚠️ {p.name} 决策降级（{str(e)[:70]}）", [p.pid])
            self._apply_rule(p)

    def _apply_rule(self, p: Player, depth: int = 0) -> None:
        if depth > 3:                         # 防递归：直接原地思考
            p.begin_activity("think", self)
            return
        others = [o for o in self.players if o is not p]
        d = brain.decide_rule(p, p.nearby_players(others), self.hour)
        self._apply_decision(p, d, depth)

    def _apply_decision(self, p: Player, d: Decision, depth: int = 0) -> None:
        key = d.action
        a = act.ACTIVITIES.get(key)
        if not a:
            self._apply_rule(p, depth + 1)
            return
        p.plan = d.reason
        p.decision = d

        if a.get("cost", 0) > p.stats.money:
            p.remember(f"我想{a['name']}但钱不够", 5)
            self._apply_rule(p, depth + 1)
            return

        target = None
        if a.get("need") == "player":
            target = next((o for o in self.players
                           if (o.name == d.target or o.pid == d.target)
                           and o is not p), None)
            if target is None:
                self._apply_rule(p, depth + 1)
                return

        # eat 特殊：家里没饭时引导去咖啡馆
        if key == "eat" and self.meal_until < self.global_tick:
            key = "eat_out"
            a = act.ACTIVITIES[key]

        ok = p.begin_activity(key, self, target=target)
        if not ok:
            if key == "love":
                t = next((o for o in self.players if o.name == d.target), None)
                if t:
                    p.remember(f"我和{t.name}的好感还不够，恋爱未成", 8)
                    self.log("social", f"💔 {p.name} 向 {t.name} 求爱未成（好感不足）",
                             [p.pid, t.pid])
            self._apply_rule(p, depth + 1)
        elif d.source == "llm":
            self.log("activity",
                     f"🧠 {p.name} 决定{a['name']}"
                     + (f"（对象:{d.target}）" if d.target else "")
                     + f" —— {d.reason}", [p.pid])

    # ------------------------------------------------------------ 对话
    def start_conversation(self, initiator: Player, target: Player, kind: str):
        """发起回合制对话（由 player._start_social 调用）"""
        # 任一方已在对话中则不再开新对话
        if initiator._conversation or target._conversation:
            return
        # love 需要双方好感达标（在 player 层校验过，这里再保险一次）
        if kind == "love":
            a_ok = initiator.affinity.get(target.pid, 0) >= 55
            b_ok = target.affinity.get(initiator.pid, 0) >= 55
            if not (a_ok and b_ok):
                return
        conv = Conversation(initiator, target, kind, self)
        initiator._conversation = conv
        target._conversation = conv
        self.conversations.append(conv)
        kind_cn = {"chat": "聊天", "love": "约会"}[kind]
        self.log("social", f"💬 {initiator.name} 找 {target.name} {kind_cn}",
                 [initiator.pid, target.pid])

    def on_conversation_end(self, conv: Conversation):
        """对话结束：好感结算（LLM）+ 基础增量"""
        a, b = conv.a, conv.b
        key = "chat" if conv.kind in ("chat", "follow") else "love"
        base = 2.0 if key == "chat" else 4.0
        # 基础好感增量
        a.affinity[b.pid] = min(100.0, a.affinity.get(b.pid, 50) + base)
        b.affinity[a.pid] = min(100.0, b.affinity.get(a.pid, 50) + base)
        # LLM 决定的浮动增减
        self.request_affinity(a, b, key)
        self.request_affinity(b, a, key)

    # ------------------------------------------------------------ 好感队列
    def request_affinity(self, a: Player, o: Player, activity_key: str):
        self.affinity_queue.append((a.pid, o.pid, activity_key))

    async def _drain_affinity(self):
        """异步消化好感请求：LLM 决定 a 对 o 的好感增减"""
        if self._affinity_busy or not self.affinity_queue:
            return
        self._affinity_busy = True
        try:
            while self.affinity_queue:
                a_pid, o_pid, key = self.affinity_queue.pop(0)
                a, o = self.by_pid(a_pid), self.by_pid(o_pid)
                if not a or not o:
                    continue
                before = a.affinity.get(o.pid, 50)
                delta = 0
                prof = self.profile_for(a.pid)
                if prof:
                    model = self.model_of.get(a.pid) or prof.default_model
                    what = act.ACTIVITIES.get(key, {}).get("name", key)
                    delta = await brain.affinity_change_llm(
                        prof, model, a.name, o.name, what,
                        f"一起{what}", before,
                        timeout=min(20.0, self.settings.decision_timeout))
                else:
                    delta = random.choice([1, 1, 2, 2, 3])   # 规则模式：温和正增长
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
