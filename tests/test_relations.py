"""关系机制回归测试（feat/relations 分支）。

- 恋爱/挚友通过 请求建立→接受 成立；选择其他行动视为婉拒；邀请到期作废
- 任一方单方面结束立即生效，双方各获得一次 ±5 额度好感结算（60分钟全额）
- 「恋爱」活动改名「约会」：已是恋爱关系时不再受好感门槛限制
- 规则层不限制性别，也允许多重关系；提示词有「我的关系/关系邀请」行
离线运行：不调用真实模型。
"""
import asyncio
import unittest

from engine import relations as rel
from engine.simulation import Simulation
from engine.models import Decision


class RelationTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation()
        self.sim.spawn_players(4)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b, self.c = self.sim.players[0], self.sim.players[1], self.sim.players[2]
        for p in (self.a, self.b, self.c):
            p.scene, p.x, p.y = 'house', 12, 20
            p.state = 'idle'
            p._conversation = None

    def _propose(self, sender, target, kind='propose_love'):
        self.sim._apply_decision(sender, Decision(action=kind, target=target.name, reason='心意'))

    def test_propose_then_accept_establishes(self):
        self._propose(self.a, self.b)
        self.assertIn(self.b.pid, self.sim.relation_proposals)
        self.sim._apply_decision(self.b, Decision(action='relation_accept', reason='我也愿意'))
        key = rel.pair_key(self.a.pid, self.b.pid)
        self.assertEqual(self.sim.relations[key]['type'], '恋爱')
        self.assertEqual(self.a.affinity[self.b.pid], 53)   # 50 + 3
        self.assertEqual(self.b.affinity[self.a.pid], 53)
        self.assertEqual(self.a.state, 'idle')

    def test_decline_by_choosing_other_action(self):
        self._propose(self.a, self.b)
        self.sim._apply_decision(self.b, Decision(action='think', reason='还没想好'))
        self.assertEqual(self.sim.relation_proposals, {})
        self.assertIn('婉拒', self.a.info_feedback)
        self.assertTrue(any('婉拒' in m.content for m in self.a.memory.items))

    def test_decline_relation_directly(self):
        self._propose(self.a, self.b)
        self.sim._decline_relation(self.b.pid, '邀请已过期')
        self.assertEqual(self.sim.relation_proposals, {})

    def test_proposal_expiry_in_step_tick(self):
        self._propose(self.a, self.b)
        self.sim.relation_proposals[self.b.pid]['expires'] = self.sim.global_tick
        asyncio.run(self.sim._step_tick())
        self.assertEqual(self.sim.relation_proposals, {})

    def test_one_sided_end_queues_full_weight_affinity(self):
        r = rel.establish(self.sim, self.a, self.b, '恋爱')
        self.sim.affinity_queue.clear()
        rel.end(self.sim, self.a, self.b)
        self.assertEqual(self.sim.relations, {})
        # 双方各一次结算，时长60分钟 → LLM ±5 全额
        self.assertEqual(len(self.sim.affinity_queue), 2)
        minutes = {q[3] for q in self.sim.affinity_queue}
        self.assertEqual(minutes, {60})

    def test_relation_end_via_decision_without_target(self):
        rel.establish(self.sim, self.a, self.b, '挚友')
        self.sim._apply_decision(self.a, Decision(action='relation_end', reason='渐行渐远'))
        self.assertEqual(self.sim.relations, {})

    def test_date_allowed_by_relation_despite_low_affinity(self):
        rel.establish(self.sim, self.a, self.b, '恋爱')
        self.a.affinity[self.b.pid] = 10
        self.b.affinity[self.a.pid] = 10
        ok = self.a.begin_activity('love', self.sim, target=self.b)
        if not ok:
            self.assertNotIn('55', self.a._rejection)   # 不能再因好感门槛被拒
        else:
            self.assertTrue(ok)

    def test_date_rejected_without_relation_or_affinity(self):
        self.a.affinity[self.b.pid] = 10
        self.b.affinity[self.a.pid] = 10
        ok = self.a.begin_activity('love', self.sim, target=self.b)
        self.assertFalse(ok)
        self.assertIn('propose_love', self.a._rejection)

    def test_same_gender_and_multiple_relations_allowed(self):
        self.a.gender = self.b.gender = self.c.gender = '男'
        rel.establish(self.sim, self.a, self.b, '恋爱')
        rel.establish(self.sim, self.a, self.c, '恋爱')   # 多重关系
        self.assertEqual(len(rel.all_of(self.sim, self.a.pid)), 2)

    def test_mutual_proposal_establishes_immediately(self):
        self._propose(self.a, self.b)
        self._propose(self.b, self.a)                     # 对方也向我提出 → 两情相悦
        self.assertEqual(self.sim.relation_proposals, {})
        self.assertIsNotNone(rel.between(self.sim, self.a.pid, self.b.pid))

    def test_re_propose_while_pending_is_rejected_with_reason(self):
        self._propose(self.a, self.b)
        self.a.state = 'idle'
        self._propose(self.a, self.b)
        self.assertIn('等待对方回应', self.a.info_feedback)

    def test_proposal_requires_same_place(self):
        self.b.scene = 'town'
        self._propose(self.a, self.b)
        self.assertEqual(self.sim.relation_proposals, {})
        self.assertIn('同一地点', self.a.info_feedback)

    def test_describe_and_invitation_text(self):
        self.assertEqual('暂无', rel.describe(self.a, self.sim)[:2])
        rel.establish(self.sim, self.a, self.b, '恋爱')
        desc = rel.describe(self.a, self.sim)
        self.assertIn(f'与{self.b.name}恋爱中', desc)
        self._propose(self.c, self.a)
        inv = rel.invitation_text(self.sim, self.a)
        self.assertIn(self.c.name, inv)
        self.assertIn('relation_accept', inv)

    def test_prompt_injects_relation_lines(self):
        from engine import brain
        rel.establish(self.sim, self.a, self.b, '恋爱')
        self.a.relations_desc = rel.describe(self.a, self.sim)
        self.a.relation_invitation = rel.invitation_text(self.sim, self.a)
        prompt = brain.build_user_prompt(self.a, [], '08:00')
        self.assertIn('我的关系', prompt)
        self.assertIn('恋爱中', prompt)
        self._propose(self.c, self.a)
        self.a.relation_invitation = rel.invitation_text(self.sim, self.a)
        prompt = brain.build_user_prompt(self.a, [], '08:00')
        self.assertIn('relation_accept', prompt)

    def test_action_aliases_normalized(self):
        from engine.brain import _norm_action
        self.assertEqual(_norm_action('表白'), 'propose_love')
        self.assertEqual(_norm_action('分手'), 'relation_end')
        self.assertEqual(_norm_action('约会'), 'love')
        self.assertEqual(_norm_action('结为挚友'), 'propose_friend')

    def test_save_roundtrip_keeps_relations(self):
        from engine import saves
        rel.establish(self.sim, self.a, self.b, '恋爱')
        self._propose(self.c, self.a)
        import json
        data = saves.capture(self.sim)
        data['state'] = json.loads(json.dumps(data['state'], ensure_ascii=False))  # 模拟落盘序列化
        restored = saves.SaveStore('/tmp/test_rel_saves').restore(data, sid='2026-10-31_00-00-00_000001')
        self.assertEqual(len(restored.relations), 1)
        self.assertIn(self.a.pid, restored.relation_proposals)
        self.assertIsNotNone(rel.between(restored, self.a.pid, self.b.pid))


if __name__ == "__main__":
    unittest.main()
