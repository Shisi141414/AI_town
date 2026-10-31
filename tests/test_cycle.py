"""生理周期机制回归测试（feat/cycle-mechanism 分支）。

锚点：
- 男性：紧张度随5天周期线性上升，精力/饱食/心情消耗×(1.0→1.4)；
  卫生间「自行解决」（30分钟、共-60精力）或「互动」释放后归基线并开新周期
- 女性：第1天生理期随机少量掉血；第4~5天黄体期未满足时心情消耗×1.5，
  「恋爱」或双方同意的「互动」后恢复
- 女性不能「自行解决」；菜单/提示词同步说明
离线运行：不调用真实模型。
"""
import asyncio
import unittest
from unittest.mock import patch

from engine import cycle as physio
from engine.simulation import Simulation
from engine.models import Decision


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation()
        self.sim.spawn_players(4)
        asyncio.run(self.sim.run_identity_phase())
        # 规则模式身份默认男性：手动分配一男一女便于测试
        self.sim.players[0].gender = '男'
        self.sim.players[1].gender = '女'
        self.male = self.sim.players[0]
        self.female = self.sim.players[1]
        # 锚点初始随机偏移（避免全员周期同步），测试周期逻辑需要显式归零
        for p in self.sim.players:
            p.cycle_anchor = 0

    def _tick(self, n=1):
        self.sim.global_tick += n
        for p in self.sim.players:
            p.day, p.world_tick, p.hour = self.sim.day, self.sim.global_tick, self.sim.hour

    def test_male_tension_and_multipliers_grow_then_release(self):
        m = self.male
        self._tick(0)
        self.assertAlmostEqual(physio.male_tension(m, self.sim), 0.0)
        self.assertAlmostEqual(physio.multipliers(m, self.sim)['energy'], 1.0)
        self._tick(physio.CYCLE_TICKS - 1)                  # 第5天最后一刻（满张力）
        self.assertAlmostEqual(physio.male_tension(m, self.sim), 1.0, delta=0.01)
        self.assertAlmostEqual(physio.multipliers(m, self.sim)['energy'], 1.4, delta=0.01)
        self._tick(1)                                      # 跨周期自动翻页
        self.assertAlmostEqual(physio.male_tension(m, self.sim), 0.0)
        physio.release_male(m, self.sim)                    # 释放开新周期
        self.assertAlmostEqual(physio.multipliers(m, self.sim)['satiety'], 1.0)

    def test_self_relief_activity_flow(self):
        m = self.male
        self._tick(4 * 144)                                 # 第5天，紧张高
        m.scene, m.x, m.y = 'house', 12, 22
        m.activity_key = ''
        m.items['ingredient'] = 0
        self.sim._apply_decision(m, Decision(action='self_relief', source='llm', reason='释放'))
        for _ in range(30):
            m.tick(self.sim)
            if m.state == 'doing':
                break
        self.assertEqual(m.state, 'doing')
        energy0 = m.stats.energy
        for _ in range(10):
            m.tick(self.sim)
            if m.state == 'idle':
                break
        self.assertEqual(m.state, 'idle')
        # 共损耗约60精力（20/刻×3刻），倍率归1，周期重开
        self.assertAlmostEqual(m.stats.energy, max(0.0, energy0 - 60), delta=2.0)
        self.assertAlmostEqual(physio.multipliers(m, self.sim)['mood'], 1.0)

    def test_female_cannot_self_relief(self):
        f = self.female
        f.scene, f.x, f.y = 'house', 12, 22
        ok = f.begin_activity('self_relief', self.sim)
        self.assertFalse(ok)
        self.assertIn('只有男性', f._rejection)

    def test_female_period_hp_loss_and_memory(self):
        f = self.female
        self._tick(0)
        hp0 = f.stats.hp
        with patch.object(physio.random, 'random', return_value=0.0):  # 必定触发
            for _ in range(10):
                f.passive_drain(self.sim)
        self.assertLess(f.stats.hp, hp0)
        self.assertTrue(any('生理期' in m for m in f.memory.items and
                            [mm.content for mm in f.memory.items]))

    def test_female_luteal_mood_multiplier_and_satisfaction(self):
        f = self.female
        f.cycle_anchor = self.sim.global_tick - physio.LUTEAL_START  # 进入黄体期
        self.assertAlmostEqual(physio.multipliers(f, self.sim)['mood'], 1.5)
        physio.satisfy_female(f, self.sim, how='恋爱')
        self.assertAlmostEqual(physio.multipliers(f, self.sim)['mood'], 1.0)
        # 翻页进入下一周期的黄体期且未再满足 → 惩罚恢复
        self._tick(physio.CYCLE_TICKS)
        self.assertAlmostEqual(physio.multipliers(f, self.sim)['mood'], 1.5)

    def test_sex_releases_male_and_satisfies_female(self):
        m, f = self.male, self.female
        self._tick(4 * 144)
        m.activity_key = 'sex'
        m._finish_activity(self.sim, {'name': '亲密互动'})
        self.assertAlmostEqual(physio.multipliers(m, self.sim)['energy'], 1.0)
        f.cycle_anchor = self.sim.global_tick - physio.LUTEAL_START
        f.activity_key = 'sex'
        f._finish_activity(self.sim, {'name': '亲密互动'})
        self.assertAlmostEqual(physio.multipliers(f, self.sim)['mood'], 1.0)

    def test_drain_multiplier_applies_to_passive_mood(self):
        f = self.female
        f.stats.mood = 50.0
        f.stats.energy = 80.0
        f.stats.satiety = 80.0
        f.cycle_anchor = self.sim.global_tick - physio.LUTEAL_START
        before = f.stats.mood
        f.passive_drain(self.sim)
        base = self.sim.mechanics['base_mood_drain']  # 0.6
        self.assertAlmostEqual(before - f.stats.mood, base * 1.5, places=5)

    def test_prompt_describes_cycle(self):
        from engine.town_services import prompt_context
        m, f = self.male, self.female
        self._tick(physio.CYCLE_TICKS - 1)
        ctx_m = prompt_context(self.sim, m)
        self.assertIn('生理周期（5天一轮）', ctx_m)
        self.assertIn('自行解决', ctx_m)
        self.assertIn('×1.40', ctx_m)
        f.cycle_anchor = self.sim.global_tick - physio.LUTEAL_START
        ctx_f = prompt_context(self.sim, f)
        self.assertIn('黄体期', ctx_f)
        self.assertIn('×1.5', ctx_f)
        self.assertNotIn('{d}', ctx_f)

    def test_rule_engine_suggests_relief_for_tense_male(self):
        from engine import brain
        m = self.male
        m.scene, m.x, m.y = 'house', 12, 22
        m.stats.energy = 90.0
        m.stats.satiety = 60.0
        m.stats.mood = 60.0
        m.stats.money = 100.0
        m.stats.hp = 100.0
        self._tick(4 * 144)
        d = brain.decide_rule(m, [], 12)
        self.assertEqual(d.action, 'self_relief')


if __name__ == "__main__":
    unittest.main()


class PeriodInjuryTests(unittest.TestCase):
    """生理期失血：归因明确、不打断活动；中断活动可恢复进度。"""

    def setUp(self):
        self.sim = Simulation()
        self.sim.spawn_players(2)
        asyncio.run(self.sim.run_identity_phase())
        self.f = self.sim.players[0]
        self.f.gender = '女'

    def _tick(self, n=1):
        self.sim.global_tick += n
        for p in self.sim.players:
            p.day, p.world_tick, p.hour = self.sim.day, self.sim.global_tick, self.sim.hour

    def test_period_loss_attributed_and_does_not_break_activity(self):
        from engine import cycle as physio
        f = self.f
        f.cycle_anchor = self.sim.global_tick          # 进入生理期
        f.scene, f.x, f.y = 'house', 13, 3             # 厨房灶位
        f.items['ingredient'] = 4
        f.state = 'idle'
        self.sim._apply_decision(f, Decision(action='cook', duration_minutes=60, source='llm'))
        for _ in range(20):
            f.tick(self.sim)
            if f.state == 'doing':
                break
        self.assertEqual(f.state, 'doing')
        left0 = f.activity_left
        hp0 = f.stats.hp
        with patch.object(physio.random, 'random', return_value=0.0):  # 必定失血
            f.passive_drain(self.sim)
            self._tick(0)
            self.sim._interrupt_health_losses({f.pid: hp0})
        self.assertLess(f.stats.hp, hp0)                       # 确实掉了血
        self.assertIn('生理期', f.injury_alert)                 # 原因明确
        self.assertIn('period', f._injury_break_done)
        self.assertEqual(f.state, 'doing')                     # 活动未被打断
        self.assertEqual(f.activity_left, left0)               # 进度未丢失
        # 同期后续失血不再打扰
        f.injury_alert = ''
        with patch.object(physio.random, 'random', return_value=0.0):
            f.passive_drain(self.sim)
            self._tick(0)
            self.sim._interrupt_health_losses({f.pid: f.stats.hp})
        self.assertEqual(f.injury_alert, '')

    def test_interrupted_activity_resumes_with_progress(self):
        f = self.f
        f.gender = '男'
        f.scene, f.x, f.y = 'house', 13, 3
        f.items['ingredient'] = 4
        f.state = 'idle'
        self.sim._apply_decision(f, Decision(action='cook', duration_minutes=60, source='llm'))
        for _ in range(20):
            f.tick(self.sim)
            if f.state == 'doing':
                break
        self.assertEqual(f.state, 'doing')
        for _ in range(2):
            f.tick(self.sim)                                   # 做了2刻
        left_before = f.activity_left
        self.sim._cancel_timed_action(f)                       # 模拟受伤中断
        self.assertEqual(f.state, 'idle')
        self.assertEqual(f._suspended_activity['key'], 'cook')
        self.assertEqual(f._suspended_activity['left'], left_before)
        # 再次选择同一活动：从中断处继续
        f.state = 'idle'
        self.sim._apply_decision(f, Decision(action='cook', source='llm'))
        for _ in range(20):
            f.tick(self.sim)
            if f.state == 'doing':
                break
        self.assertEqual(f.state, 'doing')
        self.assertEqual(f.activity_left, left_before)         # 进度恢复而非重来
        self.assertIn('继续', f.info_feedback)


class RandomPhaseTests(unittest.TestCase):
    """周期初始相位随机：不同角色锚点错开，状态自洽。"""

    def test_initial_anchors_are_staggered(self):
        import collections
        from engine import cycle as physio
        anchors = []
        for _ in range(3):
            sim = Simulation()
            sim.spawn_players(4)
            anchors.extend(p.cycle_anchor for p in sim.players)
        # 全员同锚点（旧bug）的概率应被随机器打散：12个锚点至少4种不同取值
        self.assertGreaterEqual(len(set(anchors)), 4)
        # 锚点语义自洽：elapsed ∈ [0, 720)，男性紧张度与阶段一致
        sim = Simulation(); sim.spawn_players(1)
        p = sim.players[0]; p.gender = '男'
        e = physio.elapsed_ticks(p, sim)
        self.assertGreaterEqual(e, 0)
        self.assertLess(e, physio.CYCLE_TICKS)
        self.assertAlmostEqual(physio.male_tension(p, sim), min(1.0, e / physio.CYCLE_TICKS))

    def test_female_may_start_in_period_or_luteal_consistently(self):
        from engine import cycle as physio
        sim = Simulation(); sim.spawn_players(30)
        seen = set()
        for p in sim.players:
            p.gender = '女'
            p.day, p.world_tick, p.hour = sim.day, sim.global_tick, sim.hour
            if physio.in_period(p, sim):
                seen.add('period')
                self.assertFalse(physio.in_luteal(p, sim))
            elif physio.in_luteal(p, sim):
                seen.add('luteal')
            else:
                seen.add('calm')
        self.assertTrue(seen & {'period', 'luteal', 'calm'})
