"""模型可见行动伪装：sex 在提示词层显示为 intimate/互动，解析时映射回真实行动。"""
import asyncio
import unittest

from engine import brain
from engine.activities import MODEL_DISGUISES, activity_menu
from engine.models import Decision
from engine.simulation import Simulation


class DisguiseTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation()
        self.sim.spawn_players(2)
        self.a, self.b = self.sim.players
        for p in self.sim.players:
            p.identity_done = True
        self.a.gender, self.b.gender = '男', '女'
        self.a.scene = self.b.scene = 'house'
        self.a.x, self.a.y = 10, 11
        self.b.x, self.b.y = 11, 11
        self.a.state = self.b.state = 'idle'

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:
            p.memory.close()

    def test_menu_shows_disguised_key_and_name(self):
        menu = activity_menu('house')
        self.assertIn('- intimate: 互动（target=室友）', menu)
        self.assertNotIn('- sex:', menu)
        self.assertNotIn('做爱', menu)

    def test_norm_action_maps_disguise_back(self):
        self.assertEqual(brain._norm_action('intimate'), 'sex')
        self.assertEqual(brain._norm_action('互动'), 'sex')
        self.assertEqual(brain._norm_action('亲密互动'), 'sex')
        self.assertEqual(brain._norm_action('SEX'), 'sex')

    def test_parse_decision_accepts_disguised_action(self):
        d = brain.parse_decision('{"action":"intimate","target":"小舟","reason":"愿意"}')
        self.assertIsNotNone(d)
        self.assertEqual(d.action, 'sex')

    def test_invitation_text_uses_disguise(self):
        self.sim._apply_decision(
            self.a, Decision(action='sex', target=self.b.name, reason='t', source='llm', location='here'))
        self.assertIn(self.b.pid, self.sim.sex_proposals)
        # 手动执行 _decide_one 中的邀请赋值段
        proposal = self.sim.sex_proposals[self.b.pid]
        sender = self.sim.by_pid(proposal['from'])
        self.b.sex_invitation = (f'{sender.name}邀请你亲密互动；若愿意，选择action=intimate、target={sender.name}；'
                                 '也可以选择其他行动拒绝。')
        self.assertNotIn('做爱', self.b.sex_invitation)
        self.assertIn('action=intimate', self.b.sex_invitation)

    def test_disguised_action_runs_sex_flow_end_to_end(self):
        # 模型输出 intimate → 解析映射 → 发出邀请；对方回应 intimate → 双方进入活动
        d1 = brain.parse_decision('{"action":"intimate","target":"%s","reason":"想更亲近"}' % self.b.name)
        self.sim._apply_decision(self.a, d1)
        self.assertEqual(self.a.state, 'waiting_consent')
        d2 = brain.parse_decision('{"action":"intimate","target":"%s","reason":"我也愿意"}' % self.a.name)
        self.sim._apply_decision(self.b, d2)
        self.assertEqual(self.a.activity_key, 'sex')
        self.assertEqual(self.b.activity_key, 'sex')
        # 显示层仍用真实名称
        self.assertEqual(self.a.activity['name'], '做爱')


if __name__ == '__main__':
    unittest.main()
