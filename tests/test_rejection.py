"""行动拒绝机制与移动阻挡记忆回归测试。

拒绝通道：系统规则拒绝（饱食95不收饭/用药健康/好感不足）由系统给出原因；
对象拒绝（亲密邀请）可说明理由。被拒时双方写记忆、发出者获 info_feedback。
移动阻挡：连续3刻受阻时识别阻挡物（室友名/家具墙壁）写入记忆。
"""
import asyncio
import unittest

from engine import world
from engine.models import Decision
from engine.simulation import Simulation


class RejectionTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b = self.sim.players[0], self.sim.players[1]
        # 相邻同场景，模拟走近后的直接决策
        self.a.scene = self.b.scene = 'house'
        self.a.x, self.a.y = 8, 12
        self.b.x, self.b.y = 9, 12
        for p in self.sim.players:
            p.state = 'idle'
            p.info_feedback = ''
        self.b.target_player = None
        self.a.target_player = None

    def test_give_meal_rejected_writes_both_memories(self):
        self.a.items['meal'] = 1
        self.b.stats.satiety = 99.0
        self.sim._apply_decision(self.a, Decision(action='give_meal', target=self.b.name, source='llm'))
        # 发出者：记忆 + 反馈
        mem_a = self.a.memory.brief(0, k=10, query='被拒绝')
        self.assertIn(self.b.name, mem_a)
        self.assertIn('吃得很饱', mem_a)
        self.assertIn('拒绝', self.a.info_feedback)
        self.assertIn('吃得很饱', self.a.info_feedback)
        # 对象：记忆
        mem_b = self.b.memory.brief(0, k=10, query='没有成行')
        self.assertIn(self.a.name, mem_b)
        self.assertIn('吃得很饱', mem_b)
        # 饭没消耗
        self.assertEqual(self.a.items['meal'], 1)

    def test_use_medicine_rejected_for_healthy_target(self):
        self.a.items['medicine'] = 1
        self.b.stats.hp = 100.0
        self.b.illness_severity = 0
        self.sim._apply_decision(self.a, Decision(action='use_medicine', target=self.b.name, source='llm'))
        self.assertIn('健康', self.a.info_feedback)
        self.assertIn('健康', self.b.memory.brief(0, k=10, query='没有成行'))

    def test_love_rejected_by_affinity(self):
        self.a.affinity[self.b.pid] = 30.0
        self.b.affinity[self.a.pid] = 40.0
        self.sim._apply_decision(self.a, Decision(action='love', target=self.b.name, source='llm'))
        self.assertIn('好感未互达', self.a.info_feedback)   # 新文案：未建立关系且好感不足
        self.assertIn('约会', self.a.memory.brief(0, k=10, query='被拒绝'))
        self.assertIn('好感', self.b.memory.brief(0, k=10, query='没有成行'))

    def test_eat_out_blocked_when_satiety_high(self):
        # 饱食达上限时饭店用餐与家中用餐同样被拒，且给出理由（防饭店无限聚餐）
        self.a.stats.satiety = 96.0
        ok = self.a.begin_activity('eat_out', self.sim)
        self.assertFalse(ok)
        self.assertIn('上限', self.a._rejection)
        # 家中用餐同门槛
        ok = self.a.begin_activity('eat', self.sim)
        self.assertFalse(ok)
        self.assertIn('吃得很饱', self.a._rejection)
        # 饿了才允许下馆子
        self.a.stats.satiety = 40.0
        self.assertTrue(self.a.begin_activity('eat_out', self.sim)
                        or 'eat_out' == self.a._pending_key)

    def test_sex_decline_carries_target_reason(self):
        # a 邀请 → b 选择其他行动 → 拒绝带 b 的理由
        self.sim.sex_proposals[self.b.pid] = {
            'from': self.a.pid, 'location': 'here', 'duration': 3,
            'expires': self.sim.global_tick + 12}
        self.a.state = 'waiting_consent'
        self.b.x, self.b.y = self.a.x + 1, self.a.y
        self.sim._apply_decision(self.b, Decision(
            action='think', target='', reason='想先休息一下', source='llm'))
        self.assertNotIn(self.b.pid, self.sim.sex_proposals)
        mem_a = self.a.memory.brief(0, k=10, query='亲密邀请被拒绝')
        self.assertIn(self.b.name, mem_a)
        self.assertIn('想先休息', mem_a)                 # 对象说明的理由
        self.assertIn('拒绝', self.a.info_feedback)
        mem_b = self.b.memory.brief(0, k=10, query='拒绝了')
        self.assertIn(self.a.name, mem_b)

    def test_phone_busy_rejection(self):
        self.b.state = 'doing'
        self.a.stats.money = 50.0
        self.sim._apply_decision(self.a, Decision(action='phone', target=self.b.name, source='llm'))
        self.assertIn('打电话被拒', self.a.info_feedback)
        self.assertIn('正忙', self.a.memory.brief(0, k=10, query='打电话'))
        self.assertIn('没能接', self.b.memory.brief(0, k=10, query='电话'))

    def test_target_far_away_reason(self):
        self.b.x, self.b.y = self.a.x + 20, self.a.y   # 超出6格
        self.sim._apply_decision(self.a, Decision(action='chat', target=self.b.name, source='llm'))
        self.assertIn('太远', self.a.info_feedback)


class BlockageMemoryTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b = self.sim.players[0], self.sim.players[1]
        self.a.scene = self.b.scene = 'house'

    def test_blocked_by_roommate_writes_memory(self):
        # a 在移动中被 b 挡住：连续受阻后写入记忆
        self.a.state = 'moving'
        self.a.travel_mode = 'walk'
        self.a.route_goal = (self.a.x, self.a.y - 4)
        self.a.path = []                       # 无可用路径
        self.a.blocked_ticks = 2               # 再受阻一次即达到3
        self.a._block_notified = False
        self.a._blocked_cell = (self.a.x, self.a.y - 1)
        self.b.x, self.b.y = self.a.x, self.a.y - 1
        self.b.plan = '在门口发呆'
        before = self.a.memory.counts()
        self.a.tick(self.sim)
        mem = self.a.memory.brief(0, k=10, query='挡住')
        self.assertIn(self.b.name, mem)        # 明确指出是谁挡住
        self.assertIn('挡住去路', mem)
        # 本轮已绕行移动（通知后标记重置），后续不再为同一阻挡重复写记忆
        counts = self.a.memory.counts()
        self.a.tick(self.sim)
        self.assertEqual(self.a.memory.counts(), counts)


if __name__ == "__main__":
    unittest.main()
