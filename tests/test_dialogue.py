"""对话（chat）改版回归测试：无数值增减、10轮=1刻、LLM择取记忆、提示词精简。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from engine import activities, brain, world
from engine.models import ApiProfile
from engine.simulation import Simulation

FAKE_PROFILE = ApiProfile(id='fake', name='fake', base_url='http://x', api_key='k', default_model='m')


class DialogueTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b = self.sim.players[0], self.sim.players[1]
        self.sim.profile_for = lambda pid: FAKE_PROFILE   # 强制走 LLM 路径
        self.sim.model_of = {}
        self.sim.binding_of = {}

    def place_together(self):
        self.a.scene = self.b.scene = 'house'
        self.a.x, self.a.y = 8, 12
        self.b.x, self.b.y = 9, 12
        self.a.state = self.b.state = 'doing'
        self.a.activity_key = self.b.activity_key = 'chat'
        # 三人都保持长时间活动：放行刻后不会被置 idle，
        # 避免 fake profile 的决策失败阻塞时钟、卡死对话
        for q in (self.a, self.b):
            q.activity_left = 999
        c = self.sim.players[2]
        c.state, c.activity_key = 'doing', 'sleep'
        c.activity_left = 999
        c.scene, c.x, c.y = 'house', 15, 5

    def test_renamed_no_effects(self):
        self.assertEqual(activities.ACTIVITIES['chat']['name'], '对话')
        self.assertEqual(activities.ACTIVITIES['chat']['effects'], {})
        self.assertNotIn('增进感情', activities.ACTIVITIES['chat']['desc'])

    def test_dialogue_changes_no_stats_or_affinity(self):
        """对话全程：心情/精力/好感都不动（对话本身无数值增减）。"""
        self.place_together()
        for p, o in ((self.a, self.b), (self.b, self.a)):
            p.affinity[o.pid] = 50.0
        snapshot = {p.pid: (p.stats.mood, p.stats.energy, p.stats.hp) for p in (self.a, self.b)}
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "嗯", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="聊了些日常")):
            self.sim.start_conversation(self.a, self.b, 'chat')
            for _ in range(60):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        # 对话机制本身零增减；10轮对话放行约4刻，允许正常基础消耗（心情约-4）
        for p in (self.a, self.b):
            m, e, hp = snapshot[p.pid]
            self.assertGreaterEqual(p.stats.mood, m - 5.01)
            self.assertGreaterEqual(p.stats.hp, hp - 0.01)
        self.assertAlmostEqual(self.a.affinity[self.b.pid], 50.0)   # 好感完全不变
        self.assertAlmostEqual(self.b.affinity[self.a.pid], 50.0)

    def test_ten_rounds_cost_one_tick(self):
        """10轮对话（MAX_TURNS=10）只推进1刻世界时间。"""
        self.place_together()
        tick0 = self.sim.global_tick
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "好呀", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="闲聊几句")):
            self.sim.start_conversation(self.a, self.b, 'chat')
            for _ in range(80):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        # 3轮对话≈1刻：10轮对话约放行3刻 + 结束余量
        self.assertLessEqual(self.sim.global_tick - tick0, 4)
        self.assertGreaterEqual(self.sim.global_tick - tick0, 3)

    def test_llm_summary_into_memory(self):
        """对话结束：双方记忆为 LLM 择取的关键信息。"""
        self.place_together()
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "明天一起做饭吧", "act": "leave"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="和小B约好明天一起做饭")):
            self.sim.start_conversation(self.a, self.b, 'chat')
            async def run():
                for _ in range(60):
                    await self.sim._step_tick()
                    if not self.sim.conversations:
                        break
                await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
            asyncio.run(run())
        self.assertIn('约好明天一起做饭', self.a.memory.brief(0, k=10, query='对话'))

    def test_rule_mode_fallback_summary(self):
        """无模型：机械摘要兜底仍写入记忆。"""
        self.sim.profile_for = lambda pid: None      # 规则模式
        self.place_together()
        self.sim.start_conversation(self.a, self.b, 'chat')
        for _ in range(80):
            asyncio.run(self.sim._step_tick())
            if not self.sim.conversations:
                break
        self.assertIn('对话', self.a.memory.brief(0, k=10, query='对话'))

    def test_conv_template_has_no_status_noise(self):
        self.assertNotIn('心情', brain.CONV_TURN_TEMPLATE)
        self.assertNotIn('精力', brain.CONV_TURN_TEMPLATE)
        self.assertIn('记忆', brain.CONV_TURN_TEMPLATE)
        self.assertIn('随时可以结束', brain.CONV_TURN_TEMPLATE)


if __name__ == "__main__":
    unittest.main()


class PhoneDialogueTests(DialogueTests):
    """电话与对话同机制，仅按轮收话费。"""

    def setUp(self):
        super().setUp()
        # 对话双方 doing 状态避免决策系统介入；电话可跨地点
        self.a.state = self.b.state = 'doing'
        self.a.activity_key = self.b.activity_key = 'phone'
        self.a.activity_left = self.b.activity_left if False else 999
        self.b.activity_left = 999
        self.a.scene, self.a.x, self.a.y = 'house', 8, 12
        self.b.scene, self.b.x, self.b.y = 'town', 12, 12
        c = self.sim.players[2]
        c.state, c.activity_key = 'doing', 'sleep'
        c.activity_left = 999

    def test_phone_no_effects_per_round_fee(self):
        self.a.stats.money = 50.0
        snapshot = {p.pid: (p.stats.mood, p.stats.energy, p.stats.hp) for p in (self.a, self.b)}
        self.a.affinity[self.b.pid] = self.b.affinity[self.a.pid] = 50.0
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "嗯", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="通话几句")):
            self.sim.start_conversation(self.a, self.b, 'phone')
            for _ in range(80):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        # 话费：10轮 × 1元/轮 = 10元（phone_fee_per_tick=10 → 每轮1/10）
        self.assertAlmostEqual(self.a.stats.money, 40.0)
        self.assertAlmostEqual(self.a.phone_paid, 10.0)
        # 无数值增减、好感不变（被放行约4刻的基础消耗除外）
        for p in (self.a, self.b):
            m, e, hp = snapshot[p.pid]
            self.assertGreaterEqual(p.stats.mood, m - 5.01)
        self.assertAlmostEqual(self.a.affinity[self.b.pid], 50.0)
        self.assertAlmostEqual(self.b.affinity[self.a.pid], 50.0)

    def test_phone_insufficient_money_hangs_up(self):
        self.a.stats.money = 0.5            # 不足一轮话费
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "喂", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="通话")):
            self.sim.start_conversation(self.a, self.b, 'phone')
            for _ in range(40):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        self.assertFalse(self.sim.conversations)
        conv_ended = not self.sim.conversations
        self.assertTrue(conv_ended)

    def test_phone_effects_empty_and_ten_rounds_one_tick(self):
        self.assertEqual(activities.ACTIVITIES['phone']['effects'], {})
        tick0 = self.sim.global_tick
        self.a.stats.money = 100.0
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "好", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="通话")):
            self.sim.start_conversation(self.a, self.b, 'phone')
            for _ in range(80):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        self.assertLessEqual(self.sim.global_tick - tick0, 4)


class DialoguePacingTests(DialogueTests):
    """对话节奏与防堵门：每轮精力消耗、连聊冷却、门口挪位。"""

    def test_each_round_costs_energy(self):
        self.place_together()
        self.sim.mechanics['base_energy_regen'] = 0    # 隔离基础回复，只看对话消耗
        e0a, e0b = self.a.stats.energy, self.b.stats.energy
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "嗯", "act": "stay"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="简短对话")):
            self.sim.start_conversation(self.a, self.b, 'chat')
            for _ in range(40):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        # 至少一轮：双方精力都有下降（每轮0.15）
        self.assertLess(self.a.stats.energy, e0a)
        self.assertLess(self.b.stats.energy, e0b)

    def test_cooldown_blocks_immediate_rechat(self):
        self.place_together()
        with patch.object(brain, 'conv_turn_llm',
                          AsyncMock(return_value={"say": "好", "act": "leave"})), \
             patch.object(brain, 'summarize_dialogue_llm',
                          AsyncMock(return_value="聊了几句")):
            self.sim.start_conversation(self.a, self.b, 'chat')
            for _ in range(40):
                asyncio.run(self.sim._step_tick())
                if not self.sim.conversations:
                    break
        self.assertFalse(self.sim.conversations)
        # 立刻再聊：被冷却拒绝，双方回到 idle
        self.a.state = self.b.state = 'doing'
        self.a.activity_key = self.b.activity_key = 'chat'
        self.sim.start_conversation(self.a, self.b, 'chat')
        self.assertFalse(self.sim.conversations)
        self.assertEqual(self.a.state, 'idle')
        self.assertIn('刚聊过', self.a.info_feedback)
        self.assertIn('刚聊过', self.a.memory.brief(0, k=10, query='刚聊过'))

    def test_conversation_steps_off_gate_tile(self):
        # 发起者站在大门格 'E' 上开始对话 → 挪到旁边非门格，不堵门
        from engine import world as w
        gates = w.find_tiles('town', 'E')
        self.assertTrue(gates)
        gx, gy = gates[0]
        self.a.scene = self.b.scene = 'town'
        self.a.x, self.a.y = gx, gy
        # b 站在离门口 2 格的路格上（门旁空位留给 a 挪用）
        self.b.x, self.b.y = gx + 2, gy
        while not (w.walkable('town', self.b.x, self.b.y)
                   and w.tile('town', self.b.x, self.b.y) != 'E'):
            self.b.x += 1
        for q in (self.a, self.b):
            q.state, q.activity_left = 'doing', 999
        self.a.activity_key = self.b.activity_key = 'chat'
        self.sim.start_conversation(self.a, self.b, 'chat')
        self.assertTrue(self.sim.conversations)
        self.assertNotEqual((self.a.x, self.a.y), (gx, gy))   # 发起者已挪离大门
        self.assertNotEqual(w.tile('town', self.a.x, self.a.y), 'E')
