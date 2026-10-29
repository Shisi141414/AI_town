import json
import tempfile
import unittest
from pathlib import Path
from engine import brain,world
from engine.models import Decision
from engine.simulation import Simulation
from engine.saves import SaveStore
from engine.resident_tools import instant_action,computer_seat,use_computer

class ResidentToolTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps();self.sim=Simulation();self.sim.spawn_players(5)
        self.a,self.b=self.sim.players[:2]
    def tearDown(self):
        for p in self.sim.players:p.memory.close()
    def complete(self,d):
        self.sim._apply_decision(self.a,d)
        for _ in range(150):
            self.a.tick(self.sim)
            if self.a.state=='idle':break
        self.assertEqual(self.a.state,'idle')
    def test_computer_crud_requires_arrival_and_keeps_own_files(self):
        self.a.scene='town';self.a.x,self.a.y=2,12
        d=Decision(action='computer',operation='create',title='怕忘记的事',content='下次和小鹿一起种花',source='llm')
        self.sim._apply_decision(self.a,d)
        self.assertFalse(self.a.computer_files)
        for _ in range(150):
            self.a.tick(self.sim)
            if self.a.state=='idle':break
        self.assertEqual(len(self.a.computer_files),1)
        self.assertEqual((self.a.x,self.a.y),computer_seat(self.a))
        f=self.a.computer_files[0];fid=f['id']
        self.complete(Decision(action='computer',operation='edit',file_id=fid,content='新约定'))
        self.complete(Decision(action='computer',operation='read',file_id=fid))
        self.assertIn('新约定',self.a.computer_feedback)
        self.b.decision=Decision(action='computer',operation='delete',file_id=fid)
        use_computer(self.sim,self.b,self.b.decision)
        self.assertEqual(len(self.a.computer_files),1)
        self.complete(Decision(action='computer',operation='delete',file_id=fid))
        self.assertFalse(self.a.computer_files)

    def test_computer_records_revealed_only_after_explicit_view_at_desk(self):
        self.a.computer_files=[{'id':'private-1','title':'私人计划','content':'明晚在花园见面'}]
        self.a.computer_feedback='旧的查看结果：明晚在花园见面'
        away=brain.build_user_prompt(self.a,[],self.sim.clock)
        self.assertNotIn('私人计划',away)
        self.assertNotIn('明晚在花园见面',away)
        self.a.x,self.a.y=computer_seat(self.a)
        self.a.computer_feedback=''
        at_desk=brain.build_user_prompt(self.a,[],self.sim.clock)
        self.assertNotIn('私人计划',at_desk)
        self.assertNotIn('明晚在花园见面',at_desk)
        self.a.decision=Decision(action='computer',operation='list')
        use_computer(self.sim,self.a,self.a.decision)
        listed=brain.build_user_prompt(self.a,[],self.sim.clock)
        self.assertIn('私人计划',listed)
        self.assertNotIn('明晚在花园见面',listed)
        self.a.computer_operation_done=False
        self.a.decision=Decision(action='computer',operation='read',file_id='private-1')
        use_computer(self.sim,self.a,self.a.decision)
        self.assertIn('明晚在花园见面',brain.build_user_prompt(self.a,[],self.sim.clock))
        self.a.scene='town'
        self.assertNotIn('明晚在花园见面',brain.build_user_prompt(self.a,[],self.sim.clock))
        self.assertNotIn('私人计划',brain.build_user_prompt(self.b,[],self.sim.clock))

    def test_direct_computer_operation_away_from_desk_does_not_read(self):
        self.a.computer_files=[{'id':'private-1','title':'私人计划','content':'明晚在花园见面'}]
        self.a.decision=Decision(action='computer',operation='read',file_id='private-1')
        use_computer(self.sim,self.a,self.a.decision)
        self.assertFalse(self.a.computer_feedback)
        self.assertFalse(self.a.computer_operation_done)
    def test_computer_game_duration_and_desk_no_collision(self):
        for p in self.sim.players:
            self.assertTrue(world.walkable('house',*computer_seat(p)))
        self.sim._apply_decision(self.a,Decision(action='computer_game',duration_minutes=120))
        for _ in range(100):
            self.a.tick(self.sim)
            if self.a.state=='doing':break
        self.assertEqual(self.a.activity_key,'computer_game')
        self.assertEqual(self.a.activity_total,12)
        self.assertEqual((self.a.x,self.a.y),computer_seat(self.a))
    def test_computer_completion_is_not_duplicated_in_mid_event_checkpoint(self):
        self.a.x,self.a.y=computer_seat(self.a)
        self.a.decision=Decision(action='computer',operation='create',content='只存一份')
        use_computer(self.sim,self.a,self.a.decision);use_computer(self.sim,self.a,self.a.decision)
        self.assertEqual(len(self.a.computer_files),1)
    def test_emoji_sender_recipient_memory_and_snapshot(self):
        instant_action(self.sim,self.a,Decision(action='emoji',target=self.b.name,emoji='❤️'))
        self.assertIn('❤️',self.a.snapshot().speech)
        self.assertTrue(any('❤️' in m.content for m in self.b.memory.items))
        self.assertEqual(self.sim.events[-1].scope,'game')
    def test_transfer_is_generic_conserves_money_and_supports_either_direction(self):
        initial=sum(p.stats.money for p in self.sim.players)
        instant_action(self.sim,self.a,Decision(action='transfer',target=self.b.name,amount=12.34,reason='明天见'))
        instant_action(self.sim,self.a,Decision(action='transfer',target=self.a.name,money_from=self.b.name,amount=2.34,reason='自己决定'))
        self.assertAlmostEqual(self.a.stats.money,90);self.assertAlmostEqual(self.b.stats.money,110)
        self.assertAlmostEqual(sum(p.stats.money for p in self.sim.players),initial)
        self.assertEqual(len(self.a.transactions),2)
        self.assertEqual(self.a.transactions,self.b.transactions)
        for d in (Decision(action='transfer',target=self.b.name,amount=999),Decision(action='transfer',target=self.b.name,amount=.001),Decision(action='transfer',target=self.b.name,money_from=self.sim.players[2].name,amount=1)):
            with self.assertRaises(ValueError):instant_action(self.sim,self.a,d)
        self.assertAlmostEqual(sum(p.stats.money for p in self.sim.players),initial)
    def test_parse_new_tools_and_private_prompt(self):
        for payload in ({'action':'emoji','target':self.b.name,'emoji':'😢'}, {'action':'transfer','target':self.b.name,'amount':5.25}, {'action':'computer','operation':'create','title':'计划','content':'不要忘掉'}):
            d=brain.parse_decision(json.dumps(payload,ensure_ascii=False));self.assertIsNotNone(d)
            for k,v in payload.items():self.assertEqual(getattr(d,k),v)
        self.a.computer_files=[{'id':'mine','title':'私人的秘密','content':'私有正文'}]
        own=brain.build_user_prompt(self.a,[],self.sim.clock)
        other=brain.build_user_prompt(self.b,[],self.sim.clock)
        self.assertNotIn('私人的秘密',own);self.assertNotIn('私人的秘密',other)
        self.assertNotIn('用户可以',own);self.assertNotIn('借钱',own);self.assertNotIn('抢钱',own)
        self.assertIsNone(brain.parse_decision('{"action":"transfer","amount":-3}'))

    def test_money_reason_only_returns_through_memory_or_deliberate_notes(self):
        reason='只存在记忆里的罕见转移理由'
        instant_action(self.sim,self.a,Decision(action='transfer',target=self.b.name,amount=1,reason=reason))
        self.assertTrue(any(reason in m.content for m in self.a.memory.items))
        self.assertIn(reason,brain.build_user_prompt(self.a,[],self.sim.clock))
        self.a.memory.db.execute('DELETE FROM memories');self.a.memory.db.commit()
        prompt=brain.build_user_prompt(self.a,[],self.sim.clock)
        self.assertNotIn(reason,prompt)
        self.assertEqual(self.a.transactions[0]['reason'],reason)
    def test_full_snapshot_and_recording_include_computers_and_transfers(self):
        self.a.x,self.a.y=computer_seat(self.a)
        self.a.decision=Decision(action='computer',operation='create',content='永久保存的手记')
        use_computer(self.sim,self.a,self.a.decision)
        instant_action(self.sim,self.a,Decision(action='transfer',target=self.b.name,amount=3))
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp)/'saves');self.sim.save_id=store.allocate()
            self.sim.replay_dir=Path(temp)/'replays';self.sim.ensure_recording();self.sim.record_frame()
            data=store.checkpoint(self.sim)
            restored=store.restore(data,store.allocate())
            try:
                self.assertEqual(restored.players[0].computer_files,self.a.computer_files)
                self.assertEqual(restored.players[0].transactions,self.a.transactions)
                frame=store.replay_frame(data,self.sim.replay_dir,0)['frame']
                self.assertEqual(frame['players'][0]['computer_files'],self.a.computer_files)
            finally:
                for p in restored.players:p.memory.close()
                self.sim.finish_recording()
