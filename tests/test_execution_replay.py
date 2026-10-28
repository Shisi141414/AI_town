"""时间、可选时长、模型等待和过程录制的离线回归。"""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from engine import brain, world
from engine.llm import LLMError, _extract_text
from engine.models import ApiProfile, Decision
from engine.recording import recordings, read_frame, open_recording
from engine.simulation import Simulation


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim=Simulation()
        self.sim.spawn_players(1)
        asyncio.run(self.sim.run_identity_phase())
        self.p=self.sim.players[0]

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()

    def test_start_at_eight_and_roll_over_after_sixteen_hours(self):
        self.assertEqual(self.sim.clock,'08:00')
        async def run():
            for _ in range(96):await self.sim.step_tick()
        asyncio.run(run())
        self.assertEqual((self.sim.day,self.sim.clock,self.sim.global_tick),(2,'00:00',96))

    def test_chosen_sleep_duration_is_counted_after_arrival(self):
        self.sim._apply_decision(self.p,Decision(action='sleep',source='llm',duration_minutes=480))
        self.assertEqual(self.p.requested_duration,48)
        for _ in range(10):
            if self.p.state=='doing':break
            self.p.tick(self.sim)
        self.assertEqual(self.p.activity_left,48)
        for _ in range(47):self.p.tick(self.sim)
        self.assertEqual(self.p.state,'doing')
        self.assertEqual(self.p.snapshot().activity_minutes_left,10)
        self.p.tick(self.sim)
        self.assertEqual(self.p.state,'idle')

    def test_no_decision_call_during_two_hour_game(self):
        self.p.requested_duration=12
        self.p._start_local('computer_game',self.sim)
        for _ in range(100):
            if self.p.state=='doing':break
            self.p.tick(self.sim)
        with patch.object(self.sim,'_decide_batch',new_callable=AsyncMock) as decide:
            async def run():
                for _ in range(12):await self.sim.step_tick()
            asyncio.run(run())
        decide.assert_not_called()
        self.assertEqual(self.p.state,'idle')

    def test_tolerant_parser_handles_fences_multiple_objects_aliases_and_hours(self):
        cases=[
            ('说明```json\n{"action":"sleep","duration_hours":8}\n```',480,'sleep'),
            ('{"meta":1} {"action":"玩游戏","duration":"2小时"} {"extra":2}',120,'computer_game'),
            ("{'action':'睡眠8小时','reason':'休息',}",480,'sleep'),
            ('{"decision":{"activity":"work","duration_minutes":25,}}',30,'work'),
            ('{"action":{"name":"sleep","duration_hours":8}}',480,'sleep'),
        ]
        for text,duration,action in cases:
            with self.subTest(text=text):
                d=brain.parse_decision(text)
                self.assertIsNotNone(d)
                self.assertEqual((d.action,d.duration_minutes),(action,duration))
        self.assertIsNone(brain.parse_decision('没有行动'))

    def profile(self):
        self.sim.profile=ApiProfile(id='stub',name='stub',api_key='NEVER-RECORD-THIS',default_model='model')
        return self.sim.profile

    def test_transient_error_and_format_error_are_retried_before_success(self):
        profile=self.profile()
        with patch('engine.brain.chat',side_effect=[LLMError('服务暂忙'), '格式不对', '{"action":"sleep","duration_hours":8}']) as call, patch('engine.brain.asyncio.sleep',new_callable=AsyncMock):
            result=asyncio.run(brain.decide_llm(profile,'model',self.p,[],'08:00','system',0.8,90))
        self.assertEqual(call.call_count,3)
        self.assertEqual(result.duration_minutes,480)
        self.assertIn('上次回答',call.call_args.args[3])
        self.assertGreaterEqual(call.call_args.args[5],2048)

    def test_auth_error_waits_without_rule_and_clock_does_not_advance(self):
        self.profile()
        before=self.p.stats.model_dump()
        with patch('engine.brain.chat',side_effect=LLMError('HTTP401',status=401,retryable=False)) as call, patch.object(self.sim,'_apply_rule') as rule:
            asyncio.run(self.sim.step_tick())
            asyncio.run(self.sim.step_tick())
        rule.assert_not_called()
        call.assert_called_once()
        self.assertEqual(self.p.state,'waiting_model')
        self.assertEqual(self.sim.clock,'08:00')
        self.assertEqual(self.sim.global_tick,0)
        self.assertEqual(self.p.stats.model_dump(),before)
        self.p.next_decision_at=0
        with patch('engine.brain.chat',return_value='{"action":"sleep"}'):
            asyncio.run(self.sim.step_tick())
        self.assertEqual(self.sim.clock,'08:10')
        self.assertNotEqual(self.p.state,'waiting_model')

    def test_pending_reply_is_awaited_without_advancing_or_falling_back(self):
        self.profile()
        async def run():
            ready=asyncio.Event()
            started=asyncio.Event()
            async def delayed(*args,**kwargs):
                started.set()
                await ready.wait()
                return Decision(action='sleep',source='llm',duration_minutes=480)
            with patch('engine.brain.decide_llm',side_effect=delayed),patch.object(self.sim,'_apply_rule') as rule:
                task=asyncio.create_task(self.sim.step_tick())
                await asyncio.wait_for(started.wait(),1)
                self.assertEqual(self.p.state,'deciding')
                self.assertEqual(self.sim.clock,'08:00')
                self.assertFalse(task.done())
                ready.set()
                await task
                rule.assert_not_called()
                self.assertEqual(self.sim.clock,'08:10')
        asyncio.run(run())

    def test_invalid_model_action_does_not_silently_become_rule_action(self):
        self.profile()
        with patch.object(self.sim,'_apply_rule') as rule:
            self.sim._apply_decision(self.p,Decision(action='love',target='不存在',source='llm'))
        rule.assert_not_called()
        self.assertEqual(self.p.state,'waiting_model')

    def test_content_blocks_and_reasoning_only_response(self):
        profile=self.profile()
        self.assertEqual(_extract_text(profile,{'choices':[{'message':{'content':[{'type':'text','text':'{"action":"sleep"}'}]}}]}),'{"action":"sleep"}')
        with self.assertRaises(LLMError):
            _extract_text(profile,{'choices':[{'message':{'content':None,'reasoning_content':'仍在思考'}}]})

    def test_recording_contains_frames_memory_models_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            self.sim.replay_dir=Path(temp)
            self.profile()
            with patch('engine.brain.chat',return_value='{"action":"sleep","duration_hours":8}'):
                asyncio.run(self.sim.step_tick())
            rid=self.sim.recorder.id
            self.p.remember('需要保留的逐句聊天',5)
            self.sim.pause()
            self.sim.record_frame()
            entries=recordings(Path(temp))
            self.assertEqual(len(entries),1)
            self.assertGreater(entries[0]['frames'],1)
            self.sim.finish_recording()
            with open_recording(Path(temp),rid) as db:
                records=db.execute('SELECT kind,data FROM records').fetchall()
            text=json.dumps(records,ensure_ascii=False)
            self.assertNotIn('NEVER-RECORD-THIS',text)
            self.assertIn('需要保留的逐句聊天',text)
            self.assertTrue({'header','event','frame','memory','model','decision','end'}<={r[0] for r in records})
            frame=read_frame(Path(temp),rid,entries[0]['frames']-1)
            self.assertIn('maps',frame['frame'])
            self.assertFalse(frame['frame']['running'])
            self.assertEqual(self.sim.global_tick,1) # 读取回放不会推进当前世界
            with self.assertRaises(ValueError):read_frame(Path(temp),'../secret',0)
            with self.assertRaises(IndexError):read_frame(Path(temp),rid,999999)


if __name__=='__main__':unittest.main()
