"""送饭菜/送药品当场使用（防互送死循环）回归测试。

- give_meal：饭菜不进对方背包，当场吃掉，数值 = N刻×(基础效果+品质加成)，
  对方获得"收到了...送的饭菜"记忆，好感+2
- use_medicine：用药后对方获得记忆
- 饥饿两人互送循环不再可能：收方背包不增加 meal
"""
import asyncio
import unittest

from engine import world
from engine.simulation import Simulation


class GiveConsumablesTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b = self.sim.players[0], self.sim.players[1]
        for p in self.sim.players:
            p.items.clear()
            p.meal_qualities.clear()

    def test_give_meal_eaten_immediately_not_in_inventory(self):
        self.a.items['meal'] = 1
        self.a.meal_qualities = [2]          # 烹饪品质2
        sat0 = self.b.stats.satiety = 10.0
        mood0 = self.b.stats.mood = 30.0
        self.a._resolve_give_meal(self.sim, self.b)
        # 饭菜消耗且不进对方背包（循环根源消除）
        self.assertEqual(self.a.items.get('meal', 0), 0)
        self.assertEqual(self.b.items.get('meal', 0), 0)
        self.assertEqual(self.b.meal_qualities, [])
        # 当场吃：3刻×(13+2×0.8)=43.8 饱食
        self.assertAlmostEqual(self.b.stats.satiety, min(100, 10 + 43.8))
        self.assertAlmostEqual(self.b.stats.mood, min(100, 30 + 3 * (2 + 2 * 0.3)))
        # 记忆与好感
        mem = self.b.memory.brief(0, k=10, query='饭菜')
        self.assertIn(self.a.name, mem)
        self.assertIn('送的饭菜', mem)
        self.assertAlmostEqual(self.b.affinity[self.a.pid], 52.0)

    def test_give_meal_without_meal_is_noop(self):
        sat0 = self.b.stats.satiety
        self.a._resolve_give_meal(self.sim, self.b)
        self.assertAlmostEqual(self.b.stats.satiety, sat0)

    def test_use_medicine_writes_memory(self):
        from engine import activities as act
        self.a.items['medicine'] = 1
        self.b.illness_severity = 1
        self.b.stats.hp = 40.0
        self.b.affinity[self.a.pid] = 50.0
        self.a.target_player = self.b
        self.a.scene = self.b.scene = 'house'
        self.a.x, self.a.y = self.b.x, self.b.y = 8, 8
        self.a.activity_key = 'use_medicine'
        spec = dict(act.ACTIVITIES['use_medicine'])
        self.a._finish_activity(self.sim, spec)
        # 药品消耗，生命+20，疾病治愈，记忆写入
        self.assertEqual(self.a.items.get('medicine', 0), 0)
        self.assertAlmostEqual(self.b.stats.hp, 60.0)
        self.assertEqual(self.b.illness_severity, 0)
        self.assertIn(self.a.name, self.b.memory.brief(0, k=10, query='药品'))

    def test_mutual_meal_loop_impossible(self):
        # 饥饿两人：A 送完 B，B 手里没有 meal，无法送回
        self.a.items['meal'] = 1
        self.b.stats.satiety = 5.0
        self.a._resolve_give_meal(self.sim, self.b)
        self.assertEqual(self.b.items.get('meal', 0), 0)
        # B 尝试送饭是空操作
        sat_before = self.a.stats.satiety
        self.b._resolve_give_meal(self.sim, self.a)
        self.assertAlmostEqual(self.a.stats.satiety, sat_before)


if __name__ == "__main__":
    unittest.main()
