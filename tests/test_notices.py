"""角色经历与运行界面提示的边界。"""
import asyncio
import unittest
from unittest.mock import patch,AsyncMock
from engine import brain
from engine.llm import LLMError
from engine.models import ApiProfile,MemoryItem
from engine.simulation import Simulation

class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.sim=Simulation();self.sim.spawn_players(1)
        asyncio.run(self.sim.run_identity_phase());self.p=self.sim.players[0]
    def tearDown(self):
        self.sim.finish_recording();self.p.memory.close()
    def test_management_and_runtime_events_never_become_resident_memories(self):
        before=self.p.memory.counts()
        for typ,text in [('system','等待模型回复'),('error','网络请求超时'),('memory','小舟整合了记忆')]:
            event=self.sim.log(typ,text,[self.p.pid])
            self.assertEqual(event.scope,'user')
        self.sim._decision_progress(self.p,'等待模型回复（第2/3次）')
        self.assertEqual(self.p.memory.counts(),before)
        self.assertIn('等待模型回复',self.p.decision_status)
        event=self.sim.log('system','小舟回到家',[self.p.pid],scope='game')
        self.assertEqual(event.scope,'game')
        self.assertTrue(any(m.content=='小舟回到家' for m in self.p.memory.items))
    def test_network_failure_visible_only_to_user_not_next_model_prompt(self):
        self.sim.profile=ApiProfile(id='stub',name='stub',api_key='fake',default_model='stub')
        before=self.p.memory.counts()
        with patch('engine.brain.decide_llm',new_callable=AsyncMock,side_effect=LLMError('网络请求超时')):
            asyncio.run(self.sim._decide_one(self.p))
        self.assertEqual(self.p.memory.counts(),before)
        prompt=brain.build_user_prompt(self.p,[],'08:00')
        for fragment in ('等待模型','网络请求超时','等待重试'):self.assertNotIn(fragment,prompt)
        self.assertTrue(any('网络请求超时' in e.text for e in self.sim.events))
    def test_old_notice_memories_filtered_and_cleaned_at_all_three_tiers(self):
        with self.p.memory.db:
            for tier,text in [(1,'小舟正在等待模型决定下一项活动'),(2,'我和小满做饭。小舟整合了记忆。'),(3,'小舟将40条1级记忆整理为10条2级记忆')]:
                self.p.memory._insert(MemoryItem(id='old-'+str(tier),tier=tier,content=text))
        brief=self.p.memory.brief(0)
        self.assertIn('我和小满做饭',brief)
        for text in ('等待模型','整合了记忆','记忆整理'):self.assertNotIn(text,brief)
        self.assertEqual(self.p.memory.purge_runtime_notices(),3)
        self.assertFalse(any('等待模型' in m.content or '记忆整理' in m.content for m in self.p.memory.items))
    def test_notice_input_is_not_accepted_by_memory_storage(self):
        before=self.p.memory.counts()
        self.p.remember('小舟整合了记忆')
        self.p.remember('等待模型回复')
        self.assertEqual(self.p.memory.counts(),before)
