"""饭菜保质期回归测试（20刻过期、记忆、提示词剩余时间）。"""
import asyncio
import unittest

from engine import world
from engine.simulation import Simulation


class MealExpiryTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.p = self.sim.players[0]
        self.p.items['meal'] = 0
        self.p.meal_qualities = []
        self.p.meal_expiry = []

    def cook(self, n=2, quality=1):
        """模拟做好饭（与 cook 完成结算同逻辑）。"""
        self.p.items['meal'] = self.p.items.get('meal', 0) + n
        self.p.meal_qualities.extend([quality] * n)
        self.p.meal_expiry.extend([self.sim.global_tick + self.sim.mechanics['meal_shelf_life_ticks']] * n)

    def test_meal_expires_after_20_ticks_with_memory(self):
        self.cook(2)
        shelf = int(self.sim.mechanics['meal_shelf_life_ticks'])
        for _ in range(shelf-1):
            self.sim.global_tick += 1
            self.p.world_tick = self.sim.global_tick
            self.p.passive_drain(self.sim)
        self.assertEqual(self.p.items['meal'], 2)      # 保质期前一刻仍在
        self.sim.global_tick += 1
        self.p.world_tick = self.sim.global_tick
        self.p.passive_drain(self.sim)
        self.assertEqual(self.p.items['meal'], 0)      # 到期整刻过期
        self.assertEqual(self.p.meal_expiry, [])
        self.assertIn('过期变质', self.p.memory.brief(0, k=10, query='饭菜'))

    def test_partial_expiry_keeps_newer_meal(self):
        self.cook(2)
        shelf = int(self.sim.mechanics['meal_shelf_life_ticks'])
        self.sim.global_tick += shelf-5                # 第一批还剩5刻
        self.p.world_tick = self.sim.global_tick
        self.cook(1)                                   # 新饭（整段保质期）
        self.sim.global_tick += 6                      # 第一批到期，新饭还剩shelf-6刻
        self.p.world_tick = self.sim.global_tick
        self.p.passive_drain(self.sim)
        self.assertEqual(self.p.items['meal'], 1)
        self.assertEqual(len(self.p.meal_expiry), 1)
        self.assertGreater(self.p.meal_expiry[0], self.sim.global_tick)

    def test_prompt_shows_remaining_ticks(self):
        from engine import brain
        self.cook(1)
        self.p.scene, self.p.x, self.p.y = 'house', 8, 12
        prompt = brain.build_user_prompt(self.p, [], '08:00')
        self.assertIn('家常饭菜×1（', prompt)
        self.assertIn('刻后过期）', prompt)
        self.assertIn(str(int(self.sim.mechanics['meal_shelf_life_ticks'])), prompt)

    def test_give_meal_pops_expiry(self):
        self.cook(1)
        other = self.sim.players[1]
        other.stats.satiety = 10.0
        self.p._resolve_give_meal(self.sim, other)
        self.assertEqual(self.p.items['meal'], 0)
        self.assertEqual(self.p.meal_expiry, [])

    def test_eat_from_inventory_pops_expiry(self):
        self.cook(1)
        self.p.items['meal'] -= 1
        self.p.meal_expiry.pop(0)
        # 直接验证对齐修复逻辑：数量与到期列表不一致时被动补齐
        self.p.items['meal'] = 1
        self.p.passive_drain(self.sim)
        self.assertEqual(len(self.p.meal_expiry), 1)
        self.assertEqual(self.p.meal_expiry[0], self.sim.global_tick + self.sim.mechanics['meal_shelf_life_ticks'])


if __name__ == "__main__":
    unittest.main()
