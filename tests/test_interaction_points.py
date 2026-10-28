import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import world,brain
from engine.models import Decision
from engine.simulation import Simulation
from engine.saves import SaveStore,capture
from engine.interaction_points import options,bed_points,access_cells


class InteractionPointTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps();self.sim=Simulation();self.sim.spawn_players(5)
        self.p=self.sim.players[0];self.sim.meal_until=10000

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()

    def arrive(self,p,sim=None):
        for _ in range(300):
            if p.state=='doing':return
            p.tick(sim or self.sim)
        self.fail(f'Could not arrive: {p.name}, {p.state}, {p.route_goal}, {p.x,p.y}')

    def test_every_bedroom_uses_own_bed_and_computer_position(self):
        for p in self.sim.players:
            for key in ('sleep','computer_game','computer'):
                with self.subTest(player=p.pid,key=key):
                    self.sim._apply_decision(p,Decision(action=key))
                    self.arrive(p)
                    self.assertEqual((p.x,p.y),options(p,key)[0][0])
                    self.assertEqual(p.scene,'house')
                    if key=='sleep':
                        self.assertFalse(world.walkable('house',p.x,p.y))
                        self.assertTrue(self.sim.position_free(p,'house',p.x,p.y))
                        other=next(q for q in self.sim.players if q is not p)
                        self.assertFalse(self.sim.position_free(other,'house',p.x,p.y))
                    p._finish_activity(self.sim,p.activity)
                    self.assertTrue(world.walkable('house',p.x,p.y))

    def test_cook_meal_shower_couch_and_sun_use_exact_points(self):
        for key in ('cook','eat','shower','watch_tv','game','balcony'):
            with self.subTest(key=key):
                self.sim._apply_decision(self.p,Decision(action=key))
                self.arrive(self.p)
                self.assertIn((self.p.x,self.p.y),[point for point,_ in options(self.p,key)])
                self.sim.repair_positions()
                self.assertEqual((self.p.x,self.p.y),tuple(self.p.interaction_slot['position']))
                self.p._finish_activity(self.sim,self.p.activity)
                self.assertIsNone(self.p.interaction_slot)
                self.assertTrue(world.walkable('house',self.p.x,self.p.y))

    def test_all_dining_seats_are_reserved_and_fifth_resident_waits_without_gains(self):
        for p in self.sim.players[:4]:
            self.sim._apply_decision(p,Decision(action='eat',duration_minutes=120))
            self.arrive(p)
        self.assertEqual(len({(p.x,p.y) for p in self.sim.players[:4]}),4)
        fifth=self.sim.players[4];self.sim._apply_decision(fifth,Decision(action='eat'))
        satiety=fifth.stats.satiety
        for _ in range(50):fifth.tick(self.sim)
        self.assertEqual(fifth.state,'moving')
        self.assertLessEqual(fifth.stats.satiety,satiety)
        before=[(p.x,p.y) for p in self.sim.players[:4]]
        self.sim.repair_positions()
        self.assertEqual([(p.x,p.y) for p in self.sim.players[:4]],before)
        desired=tuple(fifth.interaction_slot['position'])
        occupant=next(p for p in self.sim.players[:4] if (p.x,p.y)==desired)
        occupant._finish_activity(self.sim,occupant.activity)
        self.arrive(fifth)
        self.assertEqual((fifth.x,fifth.y),desired)

    def test_two_bathrooms_and_couch_seats_are_distinct(self):
        for key,count in (('shower',2),('watch_tv',4)):
            seated=[]
            for p in self.sim.players[:count]:
                self.sim._apply_decision(p,Decision(action=key,duration_minutes=120))
                self.arrive(p);seated.append((p.x,p.y))
            self.assertEqual(len(set(seated)),count)
            for p in self.sim.players[:count]:p._finish_activity(self.sim,p.activity)

    def test_bed_intimacy_routes_both_people_and_releases_positions(self):
        a,b=self.sim.players[:2];a.affinity[b.pid]=80;b.affinity[a.pid]=80
        self.sim._apply_decision(a,Decision(action='love',target=b.name,location='bed'))
        for _ in range(300):
            for p in (a,b):p.tick(self.sim)
            if a._conversation:break
        self.assertIsNotNone(a._conversation)
        self.assertEqual({(a.x,a.y),(b.x,b.y)},set(bed_points(a)[0]))
        self.assertNotEqual((a.x,a.y),(b.x,b.y))
        a._conversation._end('test')
        for p in (a,b):
            self.assertEqual(p.state,'idle')
            self.assertIsNone(p.interaction_slot)
            self.assertTrue(world.walkable('house',p.x,p.y))

    def test_low_affinity_bed_invitation_does_not_move_the_other_resident(self):
        a,b=self.sim.players[:2];pos=(b.x,b.y)
        a.decision=Decision(action='love',target=b.name,location='bed')
        self.assertFalse(a.begin_activity('love',self.sim,target=b))
        self.assertEqual((b.x,b.y),pos)
        self.assertIsNone(b.interaction_slot)

    def test_save_restores_furniture_use_and_legacy_activity_reroutes_without_repaying(self):
        self.sim._apply_decision(self.p,Decision(action='cook'))
        self.arrive(self.p);self.p.tick(self.sim)
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));self.sim.save_id=store.allocate()
            data=store.checkpoint(self.sim)
            data['players'][0].pop('interaction_slot')
            branch=store.restore(data,store.allocate())
            try:
                p=branch.players[0];money=p.stats.money;left=p.activity_left
                self.assertTrue(p.interaction_resume)
                self.arrive(p,branch)
                self.assertEqual(p.activity_left,left)
                self.assertEqual(p.stats.money,money)
                self.assertEqual((p.x,p.y),(13,3))
            finally:
                for p in branch.players:p.memory.close()

    def test_work_levelup_checkpoint_keeps_wages_and_experience_atomic(self):
        self.p.skills={'computer':399};self.sim._apply_decision(self.p,Decision(action='job_teacher_computer'))
        self.arrive(self.p)
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));self.sim.save_id=store.allocate();states=[];original=self.sim.log
            def log(kind,text,*args,**kwargs):
                if '达到' in text:states.append(capture(self.sim))
                return original(kind,text,*args,**kwargs)
            with patch.object(self.sim,'log',side_effect=log):self.p.tick(self.sim)
            self.assertTrue(states)
            branch=store.restore(states[0],store.allocate())
            try:
                p=branch.players[0];p.tick(branch)
                self.assertEqual(p.stats.model_dump(),self.p.stats.model_dump())
                self.assertEqual(p.skills,self.p.skills)
                self.assertEqual(p.activity_left,self.p.activity_left)
            finally:
                for p in branch.players:p.memory.close()

    def test_model_can_choose_bed_and_sun_alias(self):
        self.assertEqual(brain.parse_decision('{"action":"love","location":"bed","target":"室友"}').location,'bed')
        self.assertEqual(brain.parse_decision('{"action":"晒太阳"}').action,'balcony')


if __name__=='__main__':unittest.main()
