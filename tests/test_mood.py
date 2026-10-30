"""心情机制回归测试（2026-10 改版）：

规则锚点（基础消耗从机制面板读取，默认 0.6/tick，2026-10-30 平衡性下调）：
- 基础：每 tick 心情 -base_mood_drain
- 同区域室友好感 > 60：每 tick +2 起，随好感线性上升，80 → +6（斜率 0.2/点）
- 患病：每 tick -4（固定）
- 同区域室友好感 < 30：每 tick -2 起，随好感下降加重，0 → -5（斜率 0.1/点）
- 精力 < 20：每 tick 额外 -(20-精力)/10
- 心情 < 20 连续 18 tick（3 小时）后，每 tick 扣 0.5 HP

离线运行：不读取或调用真实模型网关。
"""
import asyncio
import unittest

from engine import activities, world
from engine.simulation import Simulation


class RelationshipMoodFunctionTests(unittest.TestCase):
    """relationship_mood_per_tick 纯函数锚点"""

    def test_neutral_band(self):
        # 30 ~ 60（含端点）不产生任何影响
        for aff in (30.0, 40.0, 50.0, 60.0):
            self.assertEqual(activities.relationship_mood_per_tick(aff), 0.0)

    def test_positive_anchors(self):
        f = activities.relationship_mood_per_tick
        self.assertAlmostEqual(f(61.0), 2.2)    # 刚过 60 即 +2 起步
        self.assertAlmostEqual(f(70.0), 4.0)
        self.assertAlmostEqual(f(80.0), 6.0)    # 用户指定锚点
        self.assertAlmostEqual(f(100.0), 10.0)

    def test_negative_anchors(self):
        f = activities.relationship_mood_per_tick
        self.assertAlmostEqual(f(29.0), -2.1)   # 刚低于 30 即 -2 起步
        self.assertAlmostEqual(f(20.0), -3.0)
        self.assertAlmostEqual(f(10.0), -4.0)
        self.assertAlmostEqual(f(0.0), -5.0)    # 用户指定锚点

    def test_clamped_out_of_range(self):
        f = activities.relationship_mood_per_tick
        self.assertAlmostEqual(f(150.0), f(100.0))
        self.assertAlmostEqual(f(-20.0), f(0.0))


class PassiveDrainMoodTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(5)
        asyncio.run(self.sim.run_identity_phase())
        self.a = self.sim.players[0]
        self.b = self.sim.players[1]
        # 稳定环境：数值远离其它阈值，好感默认 50（中性）
        for p in self.sim.players:
            p.stats.mood = 50.0
            p.stats.energy = 80.0
            p.stats.satiety = 80.0
            p.stats.hp = 100.0
            p.illness_severity = 0
        # 放到同一客厅设施区（L）触发同区域判定
        tiles = world.find_tiles("house", "L")
        self.assertTrue(tiles)
        x, y = tiles[0]
        for p in (self.a, self.b):
            p.scene, p.x, p.y = "house", x, y

    def drain_mood_delta(self, p):
        """跑一次被动结算，返回心情变化量（其他消耗远离阈值，不影响心情）"""
        before = p.stats.mood
        p.passive_drain(self.sim)
        return p.stats.mood - before

    def test_base_drain_one_per_tick(self):
        # 好感中性（50）且无其他因素：每 tick -base_mood_drain（默认0.6）
        base = self.sim.mechanics['base_mood_drain']
        self.assertAlmostEqual(self.drain_mood_delta(self.a), -base)

    def test_affectionate_roommate_boosts_mood(self):
        # 好感 80 的室友同区域：-base + 6
        base = self.sim.mechanics['base_mood_drain']
        self.a.affinity[self.b.pid] = 80.0
        self.assertAlmostEqual(self.drain_mood_delta(self.a), 6.0 - base)
        # 单向好感：b 对 a 仍是 50，b 只扣基础
        self.assertAlmostEqual(self.drain_mood_delta(self.b), -base)

    def test_disliked_roommate_drains_mood(self):
        # 好感 0 的室友同区域：-base - 5
        base = self.sim.mechanics['base_mood_drain']
        self.a.affinity[self.b.pid] = 0.0
        self.assertAlmostEqual(self.drain_mood_delta(self.a), -5.0 - base)

    def test_relationship_effect_requires_same_zone(self):
        # b 去室外（不同场景）：好感 80 也不再提供增益，只剩基础消耗
        base = self.sim.mechanics['base_mood_drain']
        self.a.affinity[self.b.pid] = 80.0
        self.b.scene, self.b.x, self.b.y = "town", 2, 12
        self.assertAlmostEqual(self.drain_mood_delta(self.a), -base)

    def test_illness_flat_minus_four(self):
        self.a.illness_severity = 2
        # -base（基础）-4（患病）
        base = self.sim.mechanics['base_mood_drain']
        self.assertAlmostEqual(self.drain_mood_delta(self.a), -4.0 - base)

    def test_low_energy_extra_mood_drain(self):
        # 精力结算顺序：先被动 +1 恢复，再判定阈值；10+1=11 → 罚 (20-11)/10
        self.a.stats.energy = 10.0
        base = self.sim.mechanics['base_mood_drain']
        expected = -base - (20.0 - (10.0 + 1.0)) / 10.0
        self.assertAlmostEqual(self.drain_mood_delta(self.a), expected)
        self.assertAlmostEqual(self.a.stats.energy, 11.0)

    def test_sustained_low_mood_damages_hp_after_delay(self):
        self.a.stats.mood = 10.0
        hp0 = self.a.stats.hp
        # 前 17 tick：只掉心情不掉血
        for _ in range(17):
            self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp, hp0)
        self.assertEqual(self.a.low_mood_ticks, 17)
        # 第 18 tick 起开始扣血，每 tick 0.5
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp, hp0 - 0.5)
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp, hp0 - 1.0)

    def test_mood_recovery_resets_counter(self):
        self.a.stats.mood = 10.0
        for _ in range(25):
            self.a.passive_drain(self.sim)
        self.assertGreater(self.a.low_mood_ticks, 0)
        # 心情恢复到阈值以上：计数清零，不再扣血
        hp_now = self.a.stats.hp
        self.a.stats.mood = 50.0
        self.a.passive_drain(self.sim)
        self.assertEqual(self.a.low_mood_ticks, 0)
        self.assertAlmostEqual(self.a.stats.hp, hp_now)


if __name__ == "__main__":
    unittest.main()
