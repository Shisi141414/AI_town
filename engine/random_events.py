"""低频生活事件；独立随机源，不改变原有活动的随机序列。"""
import random

CHECK_INTERVAL = 6
DISEASE_DAILY = 0.02
AFFINITY_DAILY = 0.04
DISEASE_CHANCE = 1-(1-DISEASE_DAILY)**(CHECK_INTERVAL/144)
AFFINITY_CHANCE = 1-(1-AFFINITY_DAILY)**(CHECK_INTERVAL/144)
SEVERITY_NAMES = {1: '轻症', 2: '中症', 3: '重症'}
DURATION = {1: 18, 2: 36, 3: 60}


class RandomEvents:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.disease_ready = {}
        self.affinity_ready = {}

    def onset(self, sim, p, severity):
        p.care_minutes = 0
        p.illness_severity = severity
        p.illness_until = sim.global_tick + DURATION[severity]
        p.illness_name = self.rng.choice(['感冒', '肠胃不适'])
        self.disease_ready[p.pid] = sim.global_tick + 3*144
        sim.log('random', f'🤒 {p.name} 出现{p.illness_name}（{SEVERITY_NAMES[severity]}），可休息或去诊所治疗', [p.pid])

    def recover(self, sim, p, treated=False):
        if not p.illness_severity:
            return
        p.care_minutes = 0
        p.illness_severity = 0
        p.illness_until = 0
        p.illness_name = ''
        sim.log('random', f'🌿 {p.name} '+('完成治疗，身体恢复了' if treated else '经过一段时间，身体自然恢复了'), [p.pid])

    def tick(self, sim):
        now = sim.global_tick
        for p in sim.players:
            if getattr(p, 'hospitalized', False):
                continue
            if p.illness_severity:
                if now >= p.illness_until:
                    self.recover(sim, p)
                else:
                    severity = p.illness_severity
                    # 每次疾病累计伤害很小，不会单独导致生命归零；原有生存规则不变。
                    p.stats.hp = max(1, p.stats.hp-{1:0.02, 2:0.06, 3:0.12}[severity]) if p.stats.hp > 1 else p.stats.hp
                    p.stats.energy = max(0, p.stats.energy-0.02*severity)
                    if now % 12 == 0 and severity < 3 and self.rng.random() < sim.mechanics['disease_worsen_chance']:
                        p.illness_severity += 1
                        sim.log('random', f'🌡 {p.name} 的{p.illness_name}加重为{SEVERITY_NAMES[p.illness_severity]}', [p.pid])
            if now % CHECK_INTERVAL:
                continue
            disease_chance=1-(1-sim.mechanics['disease_daily_chance'])**(CHECK_INTERVAL/144)
            affinity_chance=1-(1-sim.mechanics['affinity_daily_chance'])**(CHECK_INTERVAL/144)
            if not p.illness_severity and now >= self.disease_ready.get(p.pid, 0) and self.rng.random() < disease_chance:
                severity = self.rng.choices([1,2,3], weights=[85,13,2], k=1)[0]
                self.onset(sim, p, severity)
            if now >= self.affinity_ready.get(p.pid, 0) and self.rng.random() < affinity_chance:
                others = [o for o in sim.players if o is not p]
                if others:
                    other = self.rng.choice(others)
                    delta = self.rng.choice([-2,-1,1,2])
                    before = p.affinity.get(other.pid, 50)
                    after = min(100, max(0, before+delta))
                    p.affinity[other.pid] = after
                    self.affinity_ready[p.pid] = now+144
                    sim.log('random', f'💭 {p.name} 忽然想起{other.name}的一件小事，好感{after-before:+.0f}', [p.pid])
