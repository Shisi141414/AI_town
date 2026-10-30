import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from engine import world
from engine.simulation import Simulation
from engine.saves import SaveStore
from engine.models import Decision

class SaveTests(unittest.TestCase):
    def test_roundtrip_preserves_route_activity_references_and_memory_order(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));sid=store.allocate()
            sim=Simulation(memory_dir=store.folder(sid)/'memories');sim.save_id=sid
            sim.spawn_players(2)
            sim.activity_descriptions['cook']='在厨房；准备晚餐给室友'
            asyncio.run(sim.run_identity_phase())
            p,q=sim.players
            p.gender='女';p.state='moving';p.path=[(9,9),(10,9)];p.target_player=q
            p.decision=Decision(action='sleep',duration_minutes=480)
            p.activity_left=48;p.activity_total=48;p.illness_severity=2;p.illness_until=88
            sim.day=3;sim.tick=71;sim.global_tick=311
            p.remember('第一件经历',8);p.remember('第二件经历',9)
            original=[m.id for m in p.memory.items]
            data=store.checkpoint(sim);store.activate(sid)
            branch=store.allocate();loaded=store.restore(store.current(),branch)
            a,b=loaded.players
            self.assertEqual((loaded.day,loaded.tick,loaded.global_tick),(3,71,311))
            self.assertEqual(loaded.activity_descriptions['cook'],'在厨房；准备晚餐给室友')
            self.assertEqual(a.path,[(9,9),(10,9)])
            self.assertIs(a.target_player,b)
            self.assertEqual(a.decision.duration_minutes,480)
            self.assertEqual((a.activity_left,a.illness_severity,a.illness_until),(48,2,88))
            self.assertEqual([m.id for m in a.memory.items],original)
            a.remember('副本独有',8)
            self.assertEqual(len(p.memory.items),len(original))
            for obj in (sim,loaded):
                obj.finish_recording()
                for resident in obj.players:resident.memory.close()

    def test_save_id_rejects_path_escape(self):
        with self.assertRaises(ValueError):SaveStore(Path('.')).folder('../elsewhere')

    def test_batch_delete_validates_all_before_removing_any(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp))
            ids=[store.allocate() for _ in range(3)]
            for sid in ids:
                (store.folder(sid)/'state.json').write_text(json.dumps({'version':1,'id':sid}),'utf-8')
            store.activate(ids[0])
            with self.assertRaises(ValueError):store.delete_many([ids[1],ids[0]])
            self.assertTrue((store.folder(ids[1])/'state.json').exists())
            self.assertEqual(store.delete_many([ids[1],ids[2],ids[1]]),2)
            self.assertFalse(store.folder(ids[1]).exists())
            self.assertFalse(store.folder(ids[2]).exists())
            self.assertTrue(store.folder(ids[0]).exists())
