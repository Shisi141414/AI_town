"""模拟主循环 —— 世界时钟 + tick 调度 + 事件日志

时钟设计：1 tick = 游戏内 10 分钟，一天 = 144 tick
异步设计：所有玩家决策并行 await（5 人 25s 超时不会串行阻塞 2 分钟）
对白生成：聊天/恋爱开始时，若开关开启且有 API，用第三个视角给双方生成台词
"""
from __future__ import annotations

import asyncio
import random
import time

from . import activities as act
from . import brain, world
from .llm import LLMError
from .models import ApiProfile, Decision, GameEvent, Settings
from .player import Player

PRESETS: list[dict] = [
    {"name": "阿澈", "color": "#e74c3c", "emoji": "🧑‍🎓", "home": "H"},
    {"name": "小满", "color": "#3498db", "emoji": "👩‍🎨", "home": "H"},
    {"name": "老周", "color": "#27ae60", "emoji": "🧑‍🌾", "home": "H"},
    {"name": "艾达", "color": "#9b59b6", "emoji": "👩‍🔬", "home": "H"},
    {"name": "大熊", "color": "#e67e22", "emoji": "🧑‍🍳", "home": "H"},
]


class Simulation:
    def __init__(self) -> None:
        self.players: list[Player] = []
        self.day = 1
        self.tick = 0                     # 当天 tick（0~143）
        self.global_tick = 0
        self.running = False
        self.speed = 1.0                  # ticks/sec
        self.events: list[GameEvent] = []
        self.event_id = 0
        self.settings = Settings()
        self.profile: ApiProfile | None = None   # 当前激活 API 档案
        self.dialogue_log: list[dict] = []
        self._loop_task: asyncio.Task | None = None
        self._pending_decisions: dict[str, Decision] = {}
        self._llm_error_count = 0
        self._last_rule_tick: dict[str, int] = {}

    # ------------------------------------------------------------ 时钟
    @property
    def hour(self) -> int:
        return self.tick // 6             # 1 tick = 10 min

    @property
    def minute(self) -> int:
        return (self.tick % 6) * 10

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    # ------------------------------------------------------------ 玩家管理
    def spawn_preset_players(self) -> None:
        for i, p in enumerate(PRESETS):
            self.add_player(**p)

    def add_player(self, name: str, color: str, emoji: str, home: str = "H") -> Player:
        """动态添加玩家（框架开放的入口）"""
        if any(p.name == name for p in self.players):
            raise ValueError(f"玩家 {name} 已存在")
        p = Player(name, color, emoji, home_char=home)
        p.day, p.world_tick, p.hour = self.day, self.global_tick, self.hour
        p.model = ""                      # 交给上层绑定
        self.players.append(p)
        self.log("system", f"🎉 {name} {emoji} 入住小镇", [name])
        return p

    def remove_player(self, name: str) -> None:
        self.players = [p for p in self.players if p.name != name]
        self.log("system", f"👋 {name} 离开了小镇", [name])

    # ------------------------------------------------------------ 日志
    def log(self, type_: str, text: str, players: list[str] | None = None) -> GameEvent:
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
        """常驻循环：按 speed 决定 tick 间隔"""
        self.running = True
        while self.running:
            t0 = time.time()
            await self.step_tick()
            interval = 1.0 / max(0.1, self.speed)
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.05, interval - elapsed))

    def start(self):
        if not self.running:
            self.running = True     # 同步置位，避免响应竞态
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
        """一个游戏 tick 的完整流程"""
        # 0. 世界时钟推进
        self.tick += 1
        self.global_tick += 1
        if self.tick >= 144:
            self.tick = 0
            self.day += 1
            self.log("system", f"🌅 第 {self.day} 天开始了")

        # 1. 给所有 idle 玩家并行发起决策
        idlers = [p for p in self.players if p.state == "idle"]
        if idlers:
            await self._decide_batch(idlers)

        # 2. 推进所有玩家状态机
        for p in self.players:
            p.day, p.world_tick, p.hour = self.day, self.global_tick, self.hour
            p.tick(sim=self)

        # 3. 被动消耗
        for p in self.players:
            p.passive_drain(self)

        # 4. 每 12 tick（游戏内2小时）为每人写一条状态记忆
        if self.tick % 12 == 0:
            for p in self.players:
                p.memory.add(
                    f"我在{world.facility_at(p.x, p.y)['name'] if world.facility_at(p.x, p.y) else '路上'}"
                    f"{('，正在' + p.activity['name']) if p.activity else ''}",
                    p.day, p.world_tick, importance=1)

    # ------------------------------------------------------------ 决策调度
    async def _decide_batch(self, idlers: list[Player]) -> None:
        """并行决策：有 API 用 LLM，否则规则引擎（同步执行）"""
        if self.profile and self.profile.api_key:
            tasks = [self._decide_one(p) for p in idlers]
            await asyncio.gather(*tasks)
        else:
            for p in idlers:
                self._apply_rule(p)

    async def _decide_one(self, p: Player) -> None:
        """单个玩家的 LLM 决策，失败降级规则引擎"""
        others = p.nearby_players([o for o in self.players if o is not p])
        nearby_names = [o.name for o in others]
        p.state = "deciding"
        model = p.model or (self.profile.default_model if self.profile else "")
        try:
            d = await brain.decide_llm(
                self.profile, model, p, nearby_names, self.clock,
                self.settings.temperature, self.settings.decision_timeout)
            self._apply_decision(p, d)
        except (LLMError, Exception) as e:      # noqa: BLE001 —— 决策永不致命
            self._llm_error_count += 1
            self.log("error", f"⚠️ {p.name} 决策降级（{str(e)[:80]}）", [p.name])
            self._apply_rule(p)

    # ------------------------------------------------------------ 决策执行
    def _apply_rule(self, p: Player) -> None:
        others = p.nearby_players([o for o in self.players if o is not p])
        d = brain.decide_rule(p, others, self.hour)
        self._apply_decision(p, d)

    def _apply_decision(self, p: Player, d: Decision) -> None:
        key = d.action
        a = act.ACTIVITIES.get(key)
        if not a:
            self._apply_rule(p)
            return
        p.plan = d.reason
        p.decision = d

        # 校验资金
        if a.get("cost", 0) > p.stats.money:
            p.memory.add(f"我想{a['name']}但钱不够", p.day, p.world_tick, 5)
            self._apply_rule(p)
            return

        # 解析 target
        target = None
        if a.get("need") == "player":
            target = next((o for o in self.players
                           if o.name == d.target and o is not p), None)
            if target is None:
                self._apply_rule(p)
                return

        ok = p.begin_activity(key, self, target=target)
        if not ok:
            # love 被拒等场景 → 记忆 + 回退
            if key == "love":
                t = next((o for o in self.players if o.name == d.target), None)
                if t:
                    p.memory.add(f"我和{t.name}的好感还不够，恋爱未成",
                                 p.day, p.world_tick, 8)
                    self.log("social", f"💔 {p.name} 向 {t.name} 求爱未成（好感不足）",
                             [p.name, t.name])
            self._apply_rule(p)
        else:
            if d.source == "llm":
                self.log("activity",
                         f"🧠 {p.name} 决定{a['name']}"
                         + (f"（对象:{d.target}）" if d.target else "")
                         + f" —— {d.reason}", [p.name])

    # ------------------------------------------------------------ 对白生成
    def request_dialogue(self, a: Player, b: Player, kind: str) -> None:
        """聊天/恋爱开始时排入对白队列（由 server 层的广播器消费）"""
        self.dialogue_log.append({"a": a.name, "b": b.name, "kind": kind,
                                  "tick": self.global_tick, "lines": []})

    async def generate_dialogue(self, a: Player, b: Player, kind: str) -> list[str]:
        """LLM 生成 2~4 句往来对白。失败返回空列表（前端显示通用文案）。"""
        if not (self.settings.dialogue_generation and self.profile and self.profile.api_key):
            return []
        model = a.model or self.profile.default_model
        aff = a.affinity.get(b.name, 30)
        prompt = (f"小镇上，{a.name} 和 {b.name} 正在{'聊天' if kind == 'chat' else '约会'}。"
                  f"{a.name} 对 {b.name} 的好感是 {aff:.0f}/100。"
                  f"请写他们之间 2~4 句简短自然的对话（每句不超过15字），"
                  f"格式：每行一句，行首用说话人名。只输出对话本身。")
        try:
            from .llm import chat as llm_chat
            text = await brain._run_in_thread(
                llm_chat, self.profile, model,
                "你是小镇生活的对话生成器，语言自然口语化。",
                prompt, 1.0, 200, 20.0)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:4]
            return lines
        except Exception:                     # noqa: BLE001
            return []
