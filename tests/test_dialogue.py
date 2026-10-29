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
        # 第三人处于活动中，避免 fake profile 的决策失败阻塞整个 tick
        c = self.sim.players[2]
        c.state, c.activity_key = 'doing', 'sleep'
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
        # 对话机制本身零增减；被放行的1刻允许正常基础消耗（心情-1/饱食-0.4）
        for p in (self.a, self.b):
            m, e, hp = snapshot[p.pid]
            self.assertGreaterEqual(p.stats.mood, m - 1.01)
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
        self.assertLessEqual(self.sim.global_tick - tick0, 1)

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
