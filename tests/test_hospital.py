"""急救-借款-住院-死亡机制回归测试（2026-10-28）：

规则锚点：
- 生命 ≤10 触发紧急送医，急救费1200；现金不足 → 向室友借款（模型/规则决定）
- 借款凑齐 → 收治；借不够 → 不收治，继续承受结算，归零 → 死亡（移除玩家）
- 住院：每半天(72刻)房费100 → 生命+20、心情+15、饱食/精力满；付不起自动出院
- 出院保底：生命60/饱食80/精力80/心情40（均不低于现有值）
- 住院期间免日常被动消耗、免疾病结算

离线运行：不读取或调用真实模型网关（借款走规则模式兜底）。
"""
import asyncio
import unittest

from engine import hospital, world
from engine.simulation import Simulation


def drain(sim):
    """在事件循环内同步消化借款队列（规则模式无 LLM 调用，立即完成）。"""
    asyncio.run(sim._drain_loans())


class EmergencyTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        for p in self.sim.players:
            p.stats.hp = 100.0
            p.stats.money = 0.0
            p.illness_severity = 0
        self.a = self.sim.players[0]

    def test_hp_threshold_triggers_admission_with_cash(self):
        # 现金足够：直接收治（1200急救费 + 首个半天房费100）
        self.a.stats.money = 2000.0
        self.a.stats.hp = 8.0
        self.a.stats.mood = 10.0
        self.a.stats.satiety = 5.0
        self.a.stats.energy = 5.0
        self.a.passive_drain(self.sim)
        self.assertTrue(self.a.hospitalized)
        self.assertAlmostEqual(self.a.stats.money, 700.0)   # 2000-1200-100
        # 收治即急救处置：生命+20 心情+15 饱食/精力满
        # （本刻先跑过日常消耗：心情10-1基础-低精力罚(20-4.65)/10≈7.47，+15→22.47）
        self.assertAlmostEqual(self.a.stats.hp, 30.0)      # max(8+20, 30保底)
        self.assertAlmostEqual(self.a.stats.mood, 22.465, places=2)
        self.assertAlmostEqual(self.a.stats.satiety, 100.0)
        self.assertAlmostEqual(self.a.stats.energy, 100.0)
        # 送进诊所（B）
        from engine import world as w
        self.assertEqual(w.tile('town', self.a.x, self.a.y), 'B')
        # 流水为标准格式（前端资金往来面板可读）
        self.assertTrue(all('from' in t and 'amount' in t for t in self.a.transactions))

    def test_borrow_enough_admits(self):
        # 现金0、室友有钱：借款凑齐后收治
        self.b, self.c = self.sim.players[1], self.sim.players[2]
        self.b.stats.money = 2000.0
        self.c.stats.money = 2000.0
        # 提高好感让规则模式多借（aff/200 比例，好感100借一半）
        self.b.affinity[self.a.pid] = 100.0
        self.c.affinity[self.a.pid] = 100.0
        self.a.stats.hp = 5.0
        self.a.passive_drain(self.sim)
        self.assertTrue(self.a.awaiting_admission)
        drain(self.sim)
        self.assertTrue(self.a.hospitalized)
        self.assertFalse(self.a.awaiting_admission)
        # 借款总额 ≥1200，室友钱减少、记账
        total = sum(self.a.loans.values())
        self.assertGreaterEqual(total, 1200.0)
        self.assertLess(self.b.stats.money, 2000.0)
        self.assertTrue(any(t['reason'].startswith('借钱给') for t in self.b.transactions))

    def test_cannot_borrow_enough_refuses_and_death(self):
        # 室友也没钱：不收治，归零死亡
        self.b = self.sim.players[1]
        self.b.stats.money = 30.0
        self.a.stats.hp = 5.0
        self.a.passive_drain(self.sim)
        drain(self.sim)
        self.assertFalse(self.a.hospitalized)
        self.assertFalse(self.a.awaiting_admission)
        # 归零 → 死亡：移除玩家
        self.a.stats.hp = 0.0
        self.a.passive_drain(self.sim)
        self.assertNotIn(self.a, self.sim.players)
        # 其余玩家获得死亡记忆
        self.assertTrue(any('永远离开' in m for m in [self.b.memory.brief(0, k=50, query='离开')]))

    def test_hospitalized_no_passive_drain(self):
        # 住院期间：无饱食/精力/心情日常消耗
        self.a.hospitalized = True
        self.a.stats.satiety = 100.0
        self.a.stats.energy = 100.0
        self.a.stats.mood = 80.0
        for _ in range(5):
            self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.satiety, 100.0)
        self.assertAlmostEqual(self.a.stats.energy, 100.0)
        self.assertAlmostEqual(self.a.stats.mood, 80.0)

    def test_halfday_fee_and_heal(self):
        self.a.hospitalized = True
        self.a.stats.money = 500.0
        self.a.stats.hp = 50.0
        self.a.stats.mood = 20.0
        self.a.stats.satiety = 30.0
        self.a.stats.energy = 40.0
        for _ in range(hospital.HALF_DAY_TICKS):
            self.a.passive_drain(self.sim)
        # 一半天：-100元 +20生命 +15心情 饱食/精力满
        self.assertAlmostEqual(self.a.stats.money, 400.0)
        self.assertAlmostEqual(self.a.stats.hp, 70.0)
        self.assertAlmostEqual(self.a.stats.mood, 35.0)
        self.assertAlmostEqual(self.a.stats.satiety, 100.0)
        self.assertAlmostEqual(self.a.stats.energy, 100.0)

    def test_cannot_afford_fee_auto_discharge(self):
        self.a.hospitalized = True
        self.a.stats.money = 50.0
        self.a.stats.hp = 50.0
        self.a.stats.energy = 40.0
        self.a.stats.satiety = 30.0
        for _ in range(hospital.HALF_DAY_TICKS):
            self.a.passive_drain(self.sim)
        self.assertFalse(self.a.hospitalized)
        # 出院保底：生命60（高于现值50 → 60）；心情默认70高于保底40 → 不降低
        self.assertAlmostEqual(self.a.stats.hp, 60.0)
        self.assertAlmostEqual(self.a.stats.satiety, 80.0)
        self.assertAlmostEqual(self.a.stats.energy, 80.0)
        self.assertAlmostEqual(self.a.stats.mood, 70.0)

    def test_discharge_floor_not_lowering(self):
        # 出院时若数值高于保底，不降低
        self.a.hospitalized = True
        self.a.stats.hp = 75.0
        self.a.stats.mood = 66.0
        hospital.discharge(self.sim, self.a)
        self.assertAlmostEqual(self.a.stats.hp, 75.0)
        self.assertAlmostEqual(self.a.stats.mood, 66.0)
        self.assertAlmostEqual(self.a.stats.satiety, 80.0)   # 低于保底 → 提到80

    def test_hospital_decision_actions(self):
        # hospital_leave / hospital_stay 决策接线
        from engine.models import Decision
        self.a.hospitalized = True
        d = Decision(action='hospital_leave', reason='想回家')
        self.sim._apply_decision(self.a, d)
        self.assertFalse(self.a.hospitalized)
        # 未住院时这两个动作无效果
        d2 = Decision(action='hospital_leave', reason='')
        self.sim._apply_decision(self.a, d2)
        self.assertFalse(self.a.hospitalized)

    def test_illness_skipped_while_hospitalized(self):
        # 住院者不进入疾病结算
        self.a.hospitalized = True
        self.a.illness_severity = 1
        self.sim.random_events.tick(self.sim)
        # admit 会清疾病；这里直接验证 tick 不推进疾病伤害
        self.a.illness_severity = 2
        hp = self.a.stats.hp
        self.sim.random_events.tick(self.sim)
        self.assertAlmostEqual(self.a.stats.hp, hp)

    def test_loan_pay_records_transactions(self):
        self.b = self.sim.players[1]
        self.b.stats.money = 500.0
        self.a.loans = {}
        hospital.pay(self.sim, self.a, self.b, 200.0)
        self.assertAlmostEqual(self.b.stats.money, 300.0)
        self.assertAlmostEqual(self.a.loans[self.b.pid], 200.0)
        self.assertEqual(len(self.b.transactions), 1)
        self.assertEqual(len(self.a.transactions), 1)


if __name__ == "__main__":
    unittest.main()
