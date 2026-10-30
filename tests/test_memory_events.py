"""分层记忆、持久化、故障降级及随机事件的离线检查。"""
import asyncio
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from engine import brain
from engine.memory import MemoryStream, PROMPT_CHARS, THRESHOLDS
from engine.models import ApiProfile, MemoryItem
from engine.random_events import RandomEvents, DISEASE_CHANCE, AFFINITY_CHANCE, DURATION
from engine.simulation import Simulation


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.mem = MemoryStream(rng=random.Random(8))

    def tearDown(self):
        self.mem.close()

    def fill(self, n, start=0):
        for i in range(start, start+n):
            self.mem.add(f'与小禾一起做饭，经历编号{i}，保留原始事实', 1, i, 5)

    def test_all_records_enter_first_tier_and_long_records_are_chunked(self):
        text='完整聊天内容'*400
        self.mem.add(text, 1, 1)
        self.assertEqual(''.join(m.content for m in reversed(self.mem.page(1, limit=None))), text)
        self.assertTrue(all(len(m.content)<=240 and m.tier==1 for m in self.mem.items))

    def test_model_and_random_quota_and_compression(self):
        self.fill(48)
        chosen=[]
        async def selector(entries, count):
            self.assertEqual(count, 4)
            chosen.extend(m.id for m in entries[:count])
            return [{'id':m.id,'summary':'精简：'+m.content} for m in entries[:count]]
        asyncio.run(self.mem.maintain(selector))
        promoted=self.mem.page(2)
        self.assertEqual(self.mem.counts(), {'1':8,'2':4,'3':0})
        self.assertEqual(sum(m.selection_weight==0.8 for m in promoted),3)
        self.assertEqual(sum(m.selection_weight==0.2 for m in promoted),1)
        self.assertEqual(sum(m.selected_by=='模型主动选择' for m in promoted),3)
        self.assertTrue(all(m.content.startswith('精简：') for m in promoted if m.selection_weight==0.8))
        self.assertTrue(all(m.content.startswith('与小禾') for m in promoted if m.selection_weight==0.2))

    def test_random_draw_uses_full_model_nomination_then_whole_source_pool(self):
        class Draw:
            calls=[]
            def sample(self,population,k):
                self.calls.append((len(population),k))
                return population[-k:] if len(self.calls)==1 else population[:k]
        draw=Draw()
        self.mem.rng=draw
        self.fill(48)
        async def nominate(entries,count):
            self.assertEqual(count,4)
            return [{"id":m.id,"summary":"候选"+str(i)} for i,m in enumerate(entries[:4])]
        asyncio.run(self.mem.maintain(nominate))
        self.assertEqual(draw.calls,[(4,3),(37,1)])
        active={m.content for m in self.mem.page(2) if m.selection_weight==0.8}
        self.assertLessEqual(active,{"候选"+str(i) for i in range(4)})
        self.assertEqual(len(active),3)
        # 模型候选0、1虽未进入第一次抽样，仍通过全池随机抽取被保留。
        lucky=[m.content for m in self.mem.page(2) if m.selection_weight==0.2]
        self.assertTrue(any(('经历编号0，' in s) or ('经历编号1，' in s) for s in lucky))

    def test_bad_model_output_cannot_remove_all_memories(self):
        for response in (None, [{'id':'invented','summary':'不应入库'}], [{'id':None}], []):
            with self.subTest(response=response):
                self.fill(48)
                asyncio.run(self.mem.maintain(AsyncMock(return_value=response)))
                self.assertGreater(len(self.mem.items),0)
                self.assertFalse(any(m.content=='不应入库' for m in self.mem.items))
        self.assertTrue(any(m.selected_by=='重要度兜底' for m in self.mem.items))

    def test_timeout_falls_back_but_cancellation_keeps_originals(self):
        self.fill(48)
        original={m.id for m in self.mem.items}
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.mem.maintain(AsyncMock(side_effect=asyncio.CancelledError)))
        self.assertEqual({m.id for m in self.mem.items},original)
        asyncio.run(self.mem.maintain(AsyncMock(side_effect=TimeoutError)))
        self.assertEqual(self.mem.counts()['2'],4)

    def test_storage_failure_rolls_back_promotion(self):
        self.fill(48)
        original={m.id for m in self.mem.items}
        with patch.object(self.mem, '_insert', side_effect=OSError('模拟磁盘写入失败')):
            with self.assertRaises(OSError):
                asyncio.run(self.mem.maintain())
        self.assertEqual({m.id for m in self.mem.items},original)
        self.assertEqual(self.mem.counts()['2'],0)

    def test_long_running_tiers_bounded_deep_memories_never_evicted(self):
        deep_ids=set()
        for day in range(12):
            self.fill(240, day*240)
            asyncio.run(self.mem.maintain())
            current={m.id for m in self.mem.page(3,limit=None)}
            self.assertTrue(deep_ids<=current)
            deep_ids=current
            self.assertLess(self.mem.counts()['1'],THRESHOLDS[1][0])
            self.assertLess(self.mem.counts()['2'],THRESHOLDS[2][0])
            self.assertLessEqual(len(self.mem.brief(100000)), PROMPT_CHARS)
        self.assertGreater(len(deep_ids),20)
        text=self.mem.brief(100000)
        self.assertTrue(all('[L'+str(tier) in text for tier in (1,2,3)))

    def test_deep_archive_survives_reopen_and_old_memory_is_searchable(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'resident.sqlite3'
            mem=MemoryStream(path)
            with mem.db:
                mem._insert(MemoryItem(id='old',tier=3,content='小禾承诺一起看海',tick=1))
                for i in range(100):
                    mem._insert(MemoryItem(id=str(i),tier=3,content='普通日常',tick=100+i))
            mem.close()
            restored=MemoryStream(path)
            self.assertEqual(restored.counts()['3'],101)
            self.assertIn('看海',restored.brief(200,query='看海'))
            restored.close()

    def test_model_selection_prompt_is_bounded_and_parsed(self):
        profile=ApiProfile(id='stub',name='stub',api_key='fake')
        candidates=[MemoryItem(id=str(i),content='长'*240) for i in range(40)]
        with patch('engine.brain.chat', return_value='{"keep":[{"id":"0","summary":"保留"}]}') as chat:
            result=asyncio.run(brain.select_memories_llm(profile,'stub','小满',candidates,8))
        self.assertEqual(result[0]['id'],'0')
        self.assertLess(len(chat.call_args.args[3]),10000)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.sim=Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.p=self.sim.players[0]

    def tearDown(self):
        for p in self.sim.players:
            p.memory.close()

    def test_probability_calibration_is_low(self):
        self.assertAlmostEqual(1-(1-DISEASE_CHANCE)**24,0.02)
        self.assertAlmostEqual(1-(1-AFFINITY_CHANCE)**24,0.04)
        rng=random.Random(77)
        events=[sum(rng.random()<p for _ in range(240000)) for p in (DISEASE_CHANCE,AFFINITY_CHANCE)]
        self.assertTrue(150<events[0]<260,events)
        self.assertTrue(320<events[1]<490,events)

    def test_illness_effects_recovery_and_treatment(self):
        events=self.sim.random_events
        events.rng.random=lambda:1
        events.onset(self.sim,self.p,3)
        before=self.p.stats.hp
        self.sim.global_tick=1
        events.tick(self.sim)
        self.assertAlmostEqual(before-self.p.stats.hp,0.12)
        self.assertIn('重症',self.p.snapshot().illness)
        self.assertEqual(brain.decide_rule(self.p,[],8).action,'treat')
        self.p._start_local('treat',self.sim)
        for _ in range(3):self.p.tick(self.sim)
        self.assertEqual(self.p.illness_severity,0)
        events.onset(self.sim,self.p,1)
        self.sim.global_tick+=DURATION[1]
        events.tick(self.sim)
        self.assertEqual(self.p.illness_severity,0)

    def test_cooldowns_prevent_repeat_and_affinity_is_clamped(self):
        events=self.sim.random_events
        events.rng.random=lambda:0
        self.sim.global_tick=6
        self.p.affinity[self.sim.players[1].pid]=100
        events.tick(self.sim)
        n=len([e for e in self.sim.events if e.type=='random'])
        self.sim.global_tick=12
        events.tick(self.sim)
        self.assertEqual(n,len([e for e in self.sim.events if e.type=='random' and '加重' not in e.text]))
        self.assertTrue(all(0<=v<=100 for p in self.sim.players for v in p.affinity.values()))

    def test_disease_does_not_hide_existing_zero_hp(self):
        self.sim.random_events.onset(self.sim,self.p,3)
        self.p.stats.hp=0
        self.sim.global_tick=1
        self.sim.random_events.tick(self.sim)
        self.assertEqual(self.p.stats.hp,0)

    def test_activity_and_every_chat_line_enter_memory_for_participants(self):
        a,b=self.sim.players[:2]
        self.sim.log('activity','开始一项活动',[a.name])
        self.sim.log('dialogue','小满：整句聊天原文',[a.pid,b.pid])
        for p in (a,b):
            self.assertTrue(any(m.content=='小满：整句聊天原文' and m.tier==1 for m in p.memory.items))
        self.assertTrue(any(m.content=='开始一项活动' for m in a.memory.items))

    def test_tick_runs_compaction_with_residents_bound_model(self):
        self.sim.profile=ApiProfile(id='stub',name='stub',api_key='fake',default_model='stub-model')
        for i in range(48):self.p.remember(f'记忆{i}',3)
        async def select(profile,model,name,entries,count,timeout):
            self.assertEqual(model,'stub-model')
            return [{'id':m.id,'summary':m.content} for m in entries[:count]]
        with patch('engine.brain.select_memories_llm',side_effect=select) as call:
            asyncio.run(self.sim.maintain_memories())
        call.assert_called_once()
        self.assertEqual(self.p.memory.counts()['2'],4)
        self.assertTrue(any(e.type=='memory' for e in self.sim.events))


if __name__=='__main__':
    unittest.main()
