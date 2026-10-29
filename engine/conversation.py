"""回合制对话引擎 —— 状态机驱动的多轮 LLM 对话

核心规则（用户需求）：
1. 一个人说话时对方保持静默（回合制，杜绝抢话）
2. 每句话基于听到的内容回应（模型拿到完整对话历史）
3. 每轮可以说完这句就离开（leave）
4. 一方离开后，另一方可以 follow 追着继续说
   —— 被追的一方好感 -2（打扰），且对话降级为 follow 模式

状态机：
    ACTIVE(轮流说话) → (一方leave)
    LEAVING(对方决定是否follow) → follow → ACTIVE(降级)
                                → 不follow → ENDED
    ENDED → 摘要写入双方记忆 + 好感结算 + 对白事件

实现：Conversation 对象由 Simulation 持有，每个"说话回合"跨多个 tick：
发起者说一句（1 tick 等待 LLM）→ 对方听 → 对方决定回应/离开（1 tick）→ 循环
"""
from __future__ import annotations

import random
import asyncio
from typing import TYPE_CHECKING

from . import brain
from .llm import LLMError

if TYPE_CHECKING:
    from .player import Player
    from .simulation import Simulation

MAX_TURNS = 10                 # 单次对话最大轮数（防爆 chats）
FOLLOW_AFFINITY_PENALTY = 2.0  # 被追者的好感损失


class Conversation:
    def __init__(self, a: "Player", b: "Player", kind: str, sim: "Simulation"):
        self.a = a                    # 发起者
        self.b = b                    # 应答者
        self.kind = kind              # chat / love / follow
        self.sim = sim
        self.history: list[tuple[str, str]] = []   # (说话人名, 台词)
        # 对话与电话不占活动时长：10轮以内不消耗时间，见 _step_tick 冻结逻辑
        self.duration_limit = (a.activity_total if a.activity_custom_duration else None) \
            if kind == 'love' else None
        self.elapsed = 0
        self.turn_count = 0
        self.phase = "opening"        # opening / a_turn / b_turn / leaving / ended
        self.waiting_on: "Player | None" = self.a  # 正在说话的人
        self.followed = 0             # 被追赶次数
        self._billed_rounds = 0       # 电话已计费轮数
        self.ended_reason = ""        # natural / leave / follow_rejected / max

    # ------------------------------------------------------------ 驱动
    def _phone_fee_per_round(self) -> float:
        """每轮话费：电话 10 轮 ≈ 1 刻，话费按刻价折算到轮。
        优先用机制面板对 phone 活动effects.money 的自定义费率。"""
        override = self.sim.activity_overrides.get('phone', {}).get('effects', {}).get('money')
        per_tick = -override if override is not None else max(0.0, self.sim.mechanics['phone_fee_per_tick'])
        return round(max(0.0, per_tick) / 10, 2)

    async def advance(self) -> None:
        saved=self.__dict__.copy()
        activity_left=self.a.activity_left
        try:
            if self.kind=='phone':
                fee=self._phone_fee_per_round()
                if self.a.stats.money+1e-8 < fee:
                    self._end('insufficient_money')
                    return
            await self._advance()
            # 电话：每完成一轮收一轮话费；无数值增减、无固定好感变化
            if self.kind=='phone' and self.turn_count>getattr(self,'_billed_rounds',0):
                self._billed_rounds=self.turn_count
                fee=self._phone_fee_per_round()
                self.a.stats.money=round(max(0.0,self.a.stats.money-fee),2)
                self.a.stats.clamp()
                self.a.phone_paid=round(getattr(self.a,'phone_paid',0)+fee,2)
            if self.elapsed>saved['elapsed'] and self.kind=='love':
                key='love'
                from .activities import ACTIVITIES
                base_effects=(self.a.activity or {}).get('effects',ACTIVITIES[key].get('effects',{}))
                for speaker,other in ((self.a,self.b),(self.b,self.a)):
                    for stat,rate in base_effects.items():
                        setattr(speaker.stats,stat,getattr(speaker.stats,stat)+rate)
                    speaker.stats.clamp()
                    affinity_rate=(speaker.activity or {}).get('_joint_affinity_per_tick',8/6)
                    speaker.affinity[other.pid]=max(0,min(100,speaker.affinity.get(other.pid,50)+affinity_rate))
        except asyncio.CancelledError:
            self.__dict__.update(saved);self.a.activity_left=activity_left
            raise

    async def _advance(self) -> None:
        """由 Simulation 每 tick 调一次：推进当前 phase"""
        if self.phase == "ended":
            return
        self.elapsed += 1
        if self.duration_limit is not None:
            self.a.activity_left = max(0,self.duration_limit-self.elapsed)
            if self.elapsed >= self.duration_limit:
                self._end("planned_duration")
                return
        if self.phase == "opening":
            self.phase = "a_turn"
            await self._do_turn(self.a, self.b, opener=True)
        elif self.phase == "a_turn":
            # a 上一轮说完了，轮到 b
            self.phase = "b_turn"
            await self._do_turn(self.b, self.a)
        elif self.phase == "b_turn":
            self.turn_count += 1
            if self.duration_limit is None and self.turn_count >= MAX_TURNS:
                self._end("max")
                return
            self.phase = "a_turn"
            await self._do_turn(self.a, self.b)
        elif self.phase == "leaving":
            await self._resolve_leaving()

    # ------------------------------------------------------------ 单回合
    async def _do_turn(self, speaker: "Player", listener: "Player",
                       opener: bool = False) -> None:
        """speaker 说一句话（LLM 或规则库），处理 leave/follow"""
        prof = self.sim.profile_for(speaker.pid)
        model = self.sim.model_of.get(speaker.pid) or (prof.default_model if prof else "")
        turn = None
        if prof and model and self.sim.settings.dialogue_generation:
            try:
                turn = await brain.conv_turn_llm(
                    prof, model, speaker.name, listener.name, self.kind,
                    speaker.affinity.get(listener.pid, 50),
                    self.history, timeout=self.sim.settings.decision_timeout,
                    memories=speaker.memory.brief(speaker.world_tick, k=4, query=listener.name, budget=1200))
            except Exception:                  # noqa: BLE001
                turn = None
        if turn is None:
            # 规则库兜底
            turn = self._rule_turn(speaker, listener, opener)

        act = turn["act"]
        say = turn["say"]

        # 防复读：与历史重复（自己或对方最近的话）→ 换规则库台词
        recent = [l for _, l in self.history[-4:]]
        if say and (say in recent or say in [l for _, l in self.history[-2:]]):
            alt = [x for x in brain.RULE_REPLIES + brain.RULE_CLOSERS
                   if x not in recent]
            say = random.choice(alt) if alt else say
            if act == "leave":
                say = random.choice(brain.RULE_CLOSERS)

        if act == "leave":
            # 说完一句告别的话再走
            if say:
                self._record(speaker, say)
            self._log_line(speaker, say, closing=True)
            self.leaver = speaker
            self.phase = "leaving"
            return

        self._record(speaker, say)
        self._log_line(speaker, say)

        # 说 stay 但达到上限由外层 phase 控制
        speaker.speech = say
        speaker.speech_tick = speaker.world_tick

    def _rule_turn(self, speaker: "Player", listener: "Player",
                   opener: bool) -> dict:
        """规则模式：随机说话/离开，好感低更容易走开"""
        aff = speaker.affinity.get(listener.pid, 50)
        # 开场必定说；后续按好感和轮数决定是否继续
        leave_prob = 0.0
        if not opener:
            base = 0.18 if aff >= 50 else 0.32
            leave_prob = base + 0.05 * max(0, self.turn_count - 2)
        if random.random() < leave_prob:
            return {"say": random.choice(brain.RULE_CLOSERS), "act": "leave"}
        if opener or not self.history:
            return {"say": random.choice(brain.RULE_OPENERS), "act": "stay"}
        return {"say": random.choice(brain.RULE_REPLIES), "act": "stay"}

    # ------------------------------------------------------------ 离开处理
    async def _resolve_leaving(self) -> None:
        """leaver 走了；另一人决定 follow 还是放行"""
        leaver = self.leaver
        if self.kind=='phone':
            self._end('natural')
            return
        stayer = self.b if leaver is self.a else self.a
        # stayer 决定
        prof = self.sim.profile_for(stayer.pid)
        model = self.sim.model_of.get(stayer.pid) or (prof.default_model if prof else "")
        follow = False
        if prof and model and self.sim.settings.dialogue_generation:
            try:
                # 用对话模板问 stayer：TA 说要走，你跟吗？
                hist = self.history + [(leaver.name, "（起身准备离开）")]
                turn = await brain.conv_turn_llm(
                    prof, model, stayer.name, leaver.name, self.kind,
                    stayer.affinity.get(leaver.pid, 50),
                    hist, timeout=self.sim.settings.decision_timeout,
                    memories=stayer.memory.brief(stayer.world_tick, k=4, query=leaver.name, budget=1200))
                follow = turn["act"] == "follow"
                if follow and turn["say"]:
                    self._record(stayer, turn["say"])
                    self._log_line(stayer, turn["say"])
                    stayer.speech = turn["say"]
                    stayer.speech_tick = stayer.world_tick
            except Exception:                  # noqa: BLE001
                follow = False
        else:
            follow = (random.random() < 0.25
                      and stayer.affinity.get(leaver.pid, 50) > 60)

        if follow and self.followed < 2:       # 最多追 2 次
            self.followed += 1
            self.kind = "follow"
            # 被追者好感惩罚（被打扰）
            leaver.affinity[stayer.pid] = max(
                0.0, leaver.affinity.get(stayer.pid, 50) - FOLLOW_AFFINITY_PENALTY)
            self.sim.log("social",
                         f"🏃 {stayer.name} 追着 {leaver.name} 继续聊"
                         f"（{leaver.name} 有点无奈，好感-{FOLLOW_AFFINITY_PENALTY:.0f}）",
                         [stayer.pid, leaver.pid])
            leaver.remember(f"{stayer.name} 追着我说话，有点烦", 5)
            # 对话继续，但轮到被追者（leaver）先说
            self.phase = "a_turn" if leaver is self.a else "b_turn"
            return
        self._end("natural")

    # ------------------------------------------------------------ 收尾
    def _record(self, speaker: "Player", say: str):
        self.history.append((speaker.name, say))

    def _log_line(self, speaker: "Player", say: str, closing: bool = False):
        if say:
            kind_cn = {"chat": "对话", "love": "约会", "follow": "边走边聊", "phone": "通话"}[self.kind]
            self.sim.log("dialogue", f"{speaker.name}：{say}（与{kind_cn}）",
                         [self.a.pid, self.b.pid])

    def _end(self, reason: str):
        self.phase = "ended"
        self.ended_reason = reason
        # 立即从活跃列表移除：不等下一轮（可能被等待模型的时钟暂停挡住）
        try:
            self.sim.conversations.remove(self)
        except ValueError:
            pass
        # 双方解除对话状态
        for p in (self.a, self.b):
            if p._conversation is self:
                p._conversation = None
                # 聊完就地恢复空闲（或继续之前的活动）
                if p.state == "doing" and p.activity_key in ("chat", "love", "phone"):
                    p.activity = None
                    p.activity_key = ""
                    p.activity_left = 0
                    p.state = "idle"
                if getattr(p,'_bed_partner_pid',None):
                    from .interaction_points import release
                    release(self.sim,p)
        # 摘要写入记忆：优先让双方各自的模型从对话中择取关键信息
        n_lines = len(self.history)
        if n_lines:
            kind_cn = {"chat": "对话", "love": "约会", "follow": "交谈", "phone": "通话"}[self.kind]
            self._remember_dialogue(self.a, self.b, kind_cn, n_lines)
            self._remember_dialogue(self.b, self.a, kind_cn, n_lines)
        self.sim.on_conversation_end(self)

    def _remember_dialogue(self, p: "Player", other: "Player", kind_cn: str, n_lines: int):
        """对话后记忆：LLM 择取关键信息；失败/无模型退回机械摘要。"""
        prof = self.sim.profile_for(p.pid)
        model = self.sim.model_of.get(p.pid) or (prof.default_model if prof else "")
        if prof and model and self.sim.settings.dialogue_generation:
            async def _summarize():
                try:
                    memo = await brain.summarize_dialogue_llm(
                        prof, model, p.name, other.name, kind_cn, self.history,
                        timeout=self.sim.settings.decision_timeout)
                    p.remember(memo or self._fallback_memo(p, other, kind_cn, n_lines), 6)
                except Exception:                  # noqa: BLE001
                    p.remember(self._fallback_memo(p, other, kind_cn, n_lines), 6)
            try:
                asyncio.create_task(_summarize())
                return
            except RuntimeError:
                pass
        p.remember(self._fallback_memo(p, other, kind_cn, n_lines), 6)

    def _fallback_memo(self, p: "Player", other: "Player", kind_cn: str, n_lines: int) -> str:
        excerpt = "；".join(f"{w}：“{t}”" for w, t in self.history[:4])
        return f"我和{other.name}{kind_cn}了{n_lines}句：{excerpt}"
