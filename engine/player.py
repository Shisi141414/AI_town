"""玩家框架 —— 可插拔智能体的生命周期封装

设计核心（用户需求：「封装玩家算法框架，可添加任意数量玩家，指定模型」）：

    Player = 数值身体 + 记忆流 + 决策器 + 执行器
              ↑状态        ↑经验     ↑可插拔      ↑确定性规则

- 决策器有两个实现：LLM（远端模型） / 规则引擎（本地兜底）
- 同一份提示词模板 → 差异完全来自记忆与状态的涌现
- 添加玩家 = spawn 一个 Player 挑一个空位，无需改任何代码
- 每个玩家独立绑定模型名（写进 config.player_models）
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from . import activities as act
from . import brain, world
from .memory import MemoryStream
from .models import Decision, PlayerSnapshot, Stats

if TYPE_CHECKING:  # 避免循环导入，仅类型提示
    from .simulation import Simulation


class Player:
    def __init__(self, name: str, color: str, emoji: str,
                 home_char: str = "H", spawn: tuple[int, int] | None = None):
        self.name = name
        self.color = color
        self.emoji = emoji
        self.stats = Stats()
        self.memory = MemoryStream()
        self.affinity: dict[str, float] = {}      # 我 → 他人好感
        self.x, self.y = spawn or self._rand_spawn()
        self.state = "idle"                       # idle/deciding/moving/doing
        self.activity: dict | None = None         # 进行中的活动定义
        self.activity_key = ""
        self.activity_left = 0                    # 活动剩余 tick
        self.target_player: "Player | None" = None
        self.target_tile: tuple[int, int] | None = None
        self.path: list[tuple[int, int]] = []     # BFS 路径
        self.decision: Decision | None = None
        self.plan = ""                            # 最近理由（快照展示）
        self.speech = ""                          # 对白气泡
        self.speech_tick = 0
        self.day = 1
        self.world_tick = 0
        self.hour = 8                             # 游戏内小时
        self._pending_key = ""                    # 挂起活动（走到目的地后开始）
        # 家的位置：属于某块小屋区
        tiles = world.find_facility_tiles(home_char)
        self.home_tiles = tiles
        self.model = ""                           # 由 Simulation 注入

    # ------------------------------------------------------------- 出生点
    def _rand_spawn(self) -> tuple[int, int]:
        """出生在住宅区旁边（离各设施都近，避免开局马拉松）"""
        homes = world.find_facility_tiles("H")
        if homes:
            hx, hy = random.choice(homes)
            for r in range(1, 4):
                cand = [(hx+dx, hy+dy)
                        for dx in range(-r, r+1) for dy in range(-r, r+1)
                        if max(abs(dx), abs(dy)) == r and world.walkable(hx+dx, hy+dy)]
                if cand:
                    return random.choice(cand)
        roads = world.find_facility_tiles("#")
        return random.choice(roads) if roads else (5, 5)

    # ------------------------------------------------------------- 快照
    def snapshot(self) -> PlayerSnapshot:
        fac = world.facility_at(self.x, self.y)
        return PlayerSnapshot(
            name=self.name, color=self.color, emoji=self.emoji,
            x=self.x, y=self.y, stats=self.stats,
            state=self.state,
            activity=(self.activity["name"] if self.activity else ""),
            activity_emoji=(self.activity["emoji"] if self.activity else ""),
            target_name=(self.target_player.name if self.target_player else ""),
            plan=self.plan, model=self.model,
            home=(fac["name"] if fac else ""),
            speech=(self.speech if self.world_tick - self.speech_tick <= 2 else ""),
            affinity=dict(self.affinity),
        )

    # ------------------------------------------------------------- 感知
    def nearby_players(self, others: list["Player"], radius: int = 6) -> list["Player"]:
        """视野半径内的其他玩家（切比雪夫距离）"""
        return [o for o in others
                if max(abs(o.x - self.x), abs(o.y - self.y)) <= radius]

    # ------------------------------------------------------------- 行动开始
    def begin_activity(self, key: str, sim: "Simulation", target=None) -> bool:
        """尝试开始活动。校验→寻路→就位后正式开始；不满足返回 False。"""
        a = act.ACTIVITIES.get(key)
        if not a:
            return False
        need = a.get("need", "")
        self.target_player = target

        # 目标玩家类活动：需要目标在视野内
        if need == "player":
            if target is None:
                return False
            if max(abs(target.x - self.x), abs(target.y - self.y)) > 8:
                return False
            # love 需要双方好感达标
            if key == "love":
                mutual = (self.affinity.get(target.name, 0) >= a["consent_threshold"]
                          and target.affinity.get(self.name, 0) >= a["consent_threshold"])
                if not mutual:
                    return False
            self.pending_key = key
            self._walk_to(sim, target.x, target.y, adjacent=True)
            return True

        # 设施类活动：找最近的允许设施
        chars = act.facility_chars(key)
        if chars:
            best: tuple[int, int] | None = None
            best_d = 10**9
            for ch in chars:
                for (tx, ty) in world.find_facility_tiles(ch):
                    d = abs(tx - self.x) + abs(ty - self.y)
                    if d < best_d:
                        best_d, best = d, (tx, ty)
            if best is None:
                return False
            self.pending_key = key
            self._walk_to(sim, *best)
            return True

        # 任意位置活动（think/wander）
        self._start_local(key, sim)
        return True

    def _walk_to(self, sim: "Simulation", tx: int, ty: int, adjacent: bool = False):
        """设置寻路。adjacent=True 时走到目标旁边即可（社交/攻击需要）"""
        if adjacent:
            # 找目标周围一格
            candidates = [(tx + dx, ty + dy)
                          for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                          if world.walkable(tx + dx, ty + dy)]
            if candidates:
                best = min(candidates,
                           key=lambda c: abs(c[0] - self.x) + abs(c[1] - self.y))
                tx, ty = best
        tx, ty = world.nearest_reachable(self.x, self.y, tx, ty)
        self.path = world.bfs_path(self.x, self.y, tx, ty)
        self.state = "moving"

    def _start_local(self, key: str, sim: "Simulation"):
        """就地开始活动（已到达或无需设施）"""
        a = act.ACTIVITIES[key]
        self.activity = a
        self.activity_key = key
        self.activity_left = a["duration"]
        self.state = "doing"
        sim.log("activity", f"{self.name} 开始{a['name']} {a['emoji']}", [self.name])

    # ------------------------------------------------------------- 每 tick 推进
    def tick(self, sim: "Simulation") -> None:
        """状态机：idle → (决策) → moving → doing → idle"""
        others = sim.players if sim.players else []
        others = [o for o in others if o is not self]

        if self.state == "moving":
            self._step_move(sim)
            # 到位后若挂着待执行活动（走到设施才开始的），正式开始
            if not self.path and self.pending_key:
                need = (act.ACTIVITIES.get(self.pending_key, {}).get("need", ""))
                if need == "player" and self.target_player:
                    self._start_social(self.pending_key, sim)
                else:
                    self._start_local(self.pending_key, sim)
            return

        if self.state == "doing":
            self._step_activity(sim)
            return

        if self.state == "idle":
            # 让 Simulation 决策（LLM 异步已在 sim 层完成时直接接收结果）
            pass
        # deciding 状态由 sim 驱动

    # 挂起的活动（走到目的地后开始）
    @property
    def pending_key(self) -> str:
        return getattr(self, "_pending_key", "")

    @pending_key.setter
    def pending_key(self, v: str) -> None:
        self._pending_key = v

    def _step_move(self, sim: "Simulation"):
        """每 tick 走 2 格"""
        for _ in range(2):
            if self.path:
                self.x, self.y = self.path.pop(0)
                self.stats.energy += act.MOVE_EXTRA_ENERGY / 2
        if not self.path:
            self.path = []
        self.stats.clamp()

    def _start_social(self, key: str, sim: "Simulation"):
        """玩家互动型活动开始（校验相邻）"""
        t = self.target_player
        if t is None or max(abs(t.x - self.x), abs(t.y - self.y)) > 1:
            self.state = "idle"
            return
        self._start_local(key, sim)
        if key == "attack":
            # 攻击立即结算一次，剩余 tick 视为缠斗
            self._resolve_attack(sim)
        if key in ("chat", "love"):
            sim.request_dialogue(self, t, key)

    def _resolve_attack(self, sim: "Simulation"):
        t = self.target_player
        if t is None:
            return
        dmg = act.ACTIVITIES["attack"]["damage"]
        t.stats.hp -= dmg
        self.affinity[t.name] = self.affinity.get(t.name, 30) - 12
        t.affinity[self.name] = t.affinity.get(self.name, 30) - 15
        t.memory.add(f"被{self.name}攻击了！损失{dmg:.0f}生命",
                     self.day, self.world_tick, importance=9)
        self.memory.add(f"我攻击了{t.name}", self.day, self.world_tick, importance=7)
        sim.log("combat", f"⚔️ {self.name} 攻击了 {t.name}（-{dmg:.0f} HP）",
                [self.name, t.name])
        t.speech = "好痛！"
        t.speech_tick = t.world_tick
        t.state = "idle"  # 被打断
        t.activity = None
        t.activity_left = 0

    def _step_activity(self, sim: "Simulation"):
        """活动中：结算数值效果，倒计时结束"""
        key = self.activity_key
        a = self.activity or act.ACTIVITIES.get(key, {})
        eff: dict = a.get("effects", {})
        night_mult = 1.0
        if act.is_night(self.hour) and key in act.NIGHT_BONUS:
            eff = {**eff, **act.NIGHT_BONUS[key]}

        for stat_name, delta in eff.items():
            setattr(self.stats, stat_name, getattr(self.stats, stat_name) + delta)
        if a.get("cost"):
            self.stats.money -= a["cost"] / max(1, a["duration"])  # 分摊扣费
        self.stats.clamp()

        # 社交活动结束：结算好感
        self.activity_left -= 1
        if self.activity_left <= 0:
            if key in ("chat", "love") and self.target_player:
                gain = a.get("affinity", 0)
                t = self.target_player
                self.affinity[t.name] = self.affinity.get(t.name, 30) + gain
                t.affinity[self.name] = t.affinity.get(self.name, 30) + gain
                t.memory.add(f"和{self.name}{a['name']}，很开心",
                             self.day, self.world_tick, importance=6)
            self.memory.add(f"我完成了{a['name']}",
                            self.day, self.world_tick,
                            importance=2)
            self.activity = None
            self.activity_key = ""
            self.target_player = None
            self.state = "idle"

    # ------------------------------------------------------------- 被动结算
    def passive_drain(self, sim: "Simulation") -> None:
        """每 tick 自然消耗 + 极端状态惩罚"""
        for k, v in act.PASSIVE_DRAIN.__dict__.items():
            if k.startswith("_") or k == "model_fields":
                continue
            if isinstance(v, (int, float)) and v != 0:
                setattr(self.stats, k, getattr(self.stats, k) + v)
        if self.stats.satiety <= 0:
            self.stats.hp += act.STARVE_HP_DAMAGE
        if self.stats.hp <= 0:
            # 昏迷：强制传送到医院，扣钱急救
            self.stats.hp = 30
            self.stats.money = max(0.0, self.stats.money - 30)
            beds = world.find_facility_tiles("B")
            if beds:
                self.x, self.y = random.choice(beds)
            self.activity = None
            self.state = "idle"
            self.path = []
            self.memory.add("我饿晕了，被送去医院急救（-30元）",
                            self.day, self.world_tick, importance=9)
            sim.log("system", f"🚑 {self.name} 生命归零，被急救（花费30元）",
                    [self.name])
        self.stats.clamp()
        # 记忆衰减式心情漂移：好感高的人在场时心情微升
        for o in self.nearby_players([p for p in sim.players if p is not self]):
            if self.affinity.get(o.name, 0) > 60:
                self.stats.mood += 0.3
        self.stats.clamp()
