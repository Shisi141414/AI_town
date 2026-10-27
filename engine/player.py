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
                 bedroom: tuple[int, int] | None = None):
        self.pid = pid                       # 稳定 ID：配置绑定/好感键
        self.name = name
        self.color = color
        self.emoji = emoji
        self.stats = Stats()
        self.memory = MemoryStream()
        self.affinity: dict[str, float] = {}  # 键改为 pid
        self.scene = "house"
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
        self.target_player: "Player | None" = None
        self.path: list[tuple[int, int]] = []
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
            pid=self.pid, name=self.name, color=self.color, emoji=self.emoji,
            scene=self.scene, x=self.x, y=self.y, stats=self.stats,
            state=self.state,
            activity=(self.activity["name"] if self.activity else ""),
            activity_emoji=(self.activity["emoji"] if self.activity else ""),
            target_name=(self.target_player.name if self.target_player else ""),
            plan=self.plan, model=self.model,
            home=world.zone_of(self.scene, self.x, self.y),
            speech=(self.speech if self.world_tick - self.speech_tick <= 2 else ""),
            affinity=aff_named, items=dict(self.items),
            decor_room=self.decor_room, identity_done=self.identity_done)

    # ------------------------------------------------------------- 记忆助手
    def remember(self, content: str, importance: float = 3.0, sim=None):
        self.memory.add(content, self.day, self.world_tick, importance)

    # ------------------------------------------------------------- 出行
    def _plan_route(self, sim: "Simulation", dest_scene: str, tx: int, ty: int,
                    adjacent: bool = False):
        """规划到 (dest_scene, tx, ty) 的路线；跨场景则先到大门。"""
        if adjacent:
            candidates = [(tx + dx, ty + dy)
                          for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                          if world.walkable(dest_scene, tx + dx, ty + dy)]
            if candidates:
                tx, ty = min(candidates,
                             key=lambda c: abs(c[0] - self.x) + abs(c[1] - self.y))
        tx, ty = world.nearest_walkable(dest_scene, tx, ty)
        if dest_scene == self.scene:
            self.path = world.bfs_path(self.scene, self.x, self.y, tx, ty)
            self._final_goal = None
            self._final_scene = None
        else:
            # 先到本场景大门
            if self.scene == "house":
                gate = (25, 12)
            else:
                gate = (1, 12)
            gx, gy = world.nearest_walkable(self.scene, *gate)
            self.path = world.bfs_path(self.scene, self.x, self.y, gx, gy)
            self._final_goal = (tx, ty)
            self._final_scene = dest_scene
        self.state = "moving"

    def _cross_gate(self, sim: "Simulation"):
        """到达大门：切换场景，继续向最终目的地走"""
        if self.scene == "house":
            self.scene = "town"
            self.x, self.y = 2, 12
            self.last_out_tick = self.world_tick
            sim.log("system", f"🚪 {self.name} 出门去了小镇", [self.name])
        else:
            self.scene = "house"
            self.x, self.y = 24, 12
            self.last_home_tick = self.world_tick
            sim.log("system", f"🏠 {self.name} 回到家", [self.name])
        # 继续寻路
        if self._final_goal and self._final_scene == self.scene:
            tx, ty = self._final_goal
            self.path = world.bfs_path(self.scene, self.x, self.y, tx, ty)
            self._final_goal = None
            self._final_scene = None
        else:
            self.path = []

    # ------------------------------------------------------------- 活动开始
    def begin_activity(self, key: str, sim: "Simulation", target=None) -> bool:
        a = act.ACTIVITIES.get(key)
        if not a:
            return False
        need = a.get("need", "")
        self.target_player = target

        # 需要物品
        if a.get("needs_item") and not any(v > 0 for v in self.items.values()):
            self.remember(f"我想{a['name']}但没有鲜花/盆栽", 4)
            return False

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

        # 设施类
        chars = act.facility_chars(key)
        if chars:
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
        a = act.ACTIVITIES[key]
        self.activity = a
        self.activity_key = key
        self.activity_left = a["duration"]
        self.state = "doing"
        # 物品类活动：购买获得/布置消耗
        if a.get("gives_item"):
            self.items[a["gives_item"]] = self.items.get(a["gives_item"], 0) + 1
            it = act.ITEMS[a["gives_item"]]
            self.remember(f"我买了{it['name']}", 3)
        if a.get("needs_item"):
            self._consume_decor_item(key, sim)
        sim.log("activity", f"{self.name} 开始{a['name']} {a['emoji']}", [self.name])

    def _consume_decor_item(self, key: str, sim: "Simulation"):
        """布置房间/公共区：消耗物品，提升装饰度"""
        # 优先用装饰度低的物品？用盆栽先（decor 高）——玩家自己决定不了，按顺序
        item = "plant" if self.items.get("plant", 0) > 0 else "flower"
        if self.items.get(item, 0) <= 0:
            return
        self.items[item] -= 1
        decor = act.ITEMS[item]["decor"]
        if key == "decorate_room":
            self.decor_room += decor
            self.remember(f"我把{act.ITEMS[item]['name']}摆在了卧室", 4)
            sim.log("activity", f"✨ {self.name} 布置了卧室（装饰+{decor}）", [self.name])
        else:
            sim.decor_common += decor
            self.remember(f"我把{act.ITEMS[item]['name']}放到了公共区域", 5)
            sim.log("activity", f"🌸 {self.name} 装点了公共区域（装饰+{decor}）",
                    [self.name])
            # 公共区装饰让在场室友小幅开心
            for o in sim.players:
                if o is not self and o.scene == "house":
                    o.stats.mood = min(100, o.stats.mood + 1)

    # ------------------------------------------------------------- tick
    def tick(self, sim: "Simulation") -> None:
        if self.state == "moving":
            # 每 tick 走 3 格（人物移动更利落）
            for _ in range(3):
                if self.path:
                    self.x, self.y = self.path.pop(0)
                    self.stats.energy += act.MOVE_EXTRA_ENERGY / 3
            if not self.path:
                # 到达大门？→ 跨场景
                if self._final_scene is not None and self._at_gate():
                    self._cross_gate(sim)
                    if self.path:
                        return
                if self.pending_key:
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
            self._step_activity(sim)

    def _at_gate(self) -> bool:
        if self.scene == "house":
            return abs(self.x - 25) + abs(self.y - 12) <= 1
        return abs(self.x - 1) + abs(self.y - 12) <= 1

    @property
    def pending_key(self) -> str:
        return self._pending_key

    def _start_social(self, key: str, sim: "Simulation", t: "Player"):
        if t.scene != self.scene or max(abs(t.x - self.x), abs(t.y - self.y)) > 2 \
                and not world.same_place(self, t):
            self.state = "idle"
            self._pending_key = ""
            return
        self._pending_key = ""
        self.target_player = t
        self._start_local(key, sim)
        if key == "attack":
            self._resolve_attack(sim)
        if key in ("chat", "love"):
            sim.request_dialogue(self, t, key)
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
        self.affinity[t.pid] = self.affinity.get(t.pid, 30) + 2
        t.affinity[self.pid] = t.affinity.get(self.pid, 30) + base
        t.remember(f"{self.name} 送了我{act.ITEMS[item]['name']}！", 8)
        self.remember(f"我送了{t.name}{act.ITEMS[item]['name']}", 6)
        sim.log("social", f"🎁 {self.name} 送 {t.name} {act.ITEMS[item]['emoji']}"
                          f"{act.ITEMS[item]['name']}（好感+{base}）", [self.name, t.name])
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
        eff: dict = dict(a.get("effects", {}))
        if act.is_night(self.hour) and a.get("night_bonus"):
            eff = {**eff, **a["night_bonus"]}

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
            if self.activity_left == a["duration"]:
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
        if a.get("cost"):
            self.stats.money -= a["cost"] / max(1, a["duration"])
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
        # 社交/协同互动的好感结算：LLM 决定增减
        if key in ("chat", "love", "game", "watch_tv", "work", "movie",
                   "park", "balcony", "cook", "eat", "exercise", "build"):
            partners = [o for o in sim.players
                        if o is not self and o.scene == self.scene
                        and world.same_place(self, o)
                        and o.activity_key == key]
            for o in partners:
                sim.request_affinity(self, o, key)
        if key in ("chat", "love") and self.target_player:
            t = self.target_player
            # 基础好感保证（LLM 增减之外的最小值）
            base = 2.0 if key == "chat" else 4.0
            self.affinity[t.pid] = self.affinity.get(t.pid, 30) + base
            t.affinity[self.pid] = t.affinity.get(self.pid, 30) + base
        self.remember(f"我完成了{a['name']}", 2)
        self.activity = None
        self.activity_key = ""
        self.target_player = None
        self._pending_key = ""
        self._pending_target_pid = None
        self.state = "idle"

    # ------------------------------------------------------------- 被动结算
    def passive_drain(self, sim: "Simulation") -> None:
        for k, v in act.PASSIVE_DRAIN.items():
            if v:
                setattr(self.stats, k, getattr(self.stats, k) + v)
        if self.stats.satiety <= 0:
            self.stats.hp += act.STARVE_HP_DAMAGE
        if self.stats.hp <= 0:
            self.stats.hp = 30
            self.stats.money = max(0.0, self.stats.money - 30)
            # 送到医院（室外）
            self.scene = "town"
            beds = world.find_tiles("town", "B")
            if beds:
                bx, by = random.choice(beds)
                self.x, self.y = world.nearest_walkable("town", bx, by)
            self.activity = None
            self.state = "idle"
            self.path = []
            self._pending_key = ""
            self.remember("我晕倒了，被送去医院急救（-30元）", 9)
            sim.log("system", f"🚑 {self.name} 生命归零，被送医急救（-30元）",
                    [self.name])
        self.stats.clamp()
        # 好感高的室友在场 → 心情微升
        for o in self.same_zone_players(sim):
            if self.affinity.get(o.pid, 0) > 60:
                self.stats.mood = min(100.0, self.stats.mood + 0.3)
        self.stats.clamp()
