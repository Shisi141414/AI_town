import tempfile
import unittest
from pathlib import Path

from engine import world, brain
from engine.models import Decision, Stats
from engine.simulation import Simulation
from engine.saves import SaveStore, capture
from engine.town_services import DEFS, JOB_GRADES, defaults, prepare, prompt_context, work_experience_rate


class CareerAndLeisureTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps(); self.sim=Simulation(); self.sim.spawn_players(3)
        self.p,self.other=self.sim.players[:2]

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()

    def start(self,key,**kwargs):
        self.sim._apply_decision(self.p,Decision(action=key,source='llm',**kwargs))
        for _ in range(200):
            if self.p.state=='doing':return
            self.p.tick(self.sim)
        self.fail('Could not start '+key)

    def finish(self):
        for _ in range(200):
            if self.p.state=='idle':return
            self.p.tick(self.sim)
        self.fail('Could not finish')

    def test_all_careers_have_higher_paid_shorter_senior_tiers_and_skill_gates(self):
        for key,meta in JOB_GRADES.items():
            with self.subTest(key=key):
                code,_,skill,_=DEFS[key];terms=self.sim.town_config[code][key]
                if terms['min_level']:
                    self.p.skills={skill:(terms['min_level']-1)*100}
                    with self.assertRaises(ValueError):prepare(self.sim,self.p,Decision(action=key))
                self.p.skills={skill:terms['min_level']*100}
                prepare(self.sim,self.p,Decision(action=key))
                if meta['grade']==2:
                    base=self.sim.town_config[code][meta['family']]
                    self.assertGreater(terms['hourly_wage'],base['hourly_wage'])
                    self.assertLess(terms['minutes'],base['minutes'])
                    self.assertGreater(terms['min_level'],base['min_level'])
        self.assertEqual(self.sim.town_config['O']['job_programmer']['min_level'],2)
        self.assertEqual(self.sim.town_config['O']['job_programmer_2']['min_level'],4)

    def test_every_occupation_earns_its_own_experience_and_teacher_is_double(self):
        for key,meta in JOB_GRADES.items():
            with self.subTest(key=key):
                code,_,skill,_=DEFS[key];terms=self.sim.town_config[code][key]
                start=terms['min_level']*100
                self.p.stats=Stats(energy=100,money=100);self.p.skills={skill:start}
                self.p.scene='town'
                self.p.x,self.p.y=next(q for q in world.find_tiles('town',code) if world.walkable('town',*q))
                prepare(self.sim,self.p,Decision(action=key));self.p.requested_duration=6;self.p._start_local(key,self.sim)
                for _ in range(6):self.p.tick(self.sim)
                self.assertEqual(set(self.p.skills),{skill})
                self.assertAlmostEqual(self.p.skills[skill],min(500,start+(6 if meta['teacher'] else 3)))
                if self.p.activity:self.p._finish_activity(self.sim,self.p.activity)

    def test_school_experience_setting_keeps_teacher_ratio_and_freezes_contract(self):
        self.sim.town_config['U']['study_computer']['work_xp_per_hour']=4
        self.assertEqual(work_experience_rate(self.sim,'job_programmer'),4)
        self.assertEqual(work_experience_rate(self.sim,'job_teacher_computer'),8)
        self.p.skills={'computer':300};self.start('job_teacher_computer',duration_minutes=10)
        self.assertEqual(self.p.activity_total,12)
        self.assertEqual(world.tile(self.p.scene,self.p.x,self.p.y),'U')
        self.sim.town_config['U']['study_computer']['work_xp_per_hour']=9
        for _ in range(3):self.p.tick(self.sim)
        self.assertAlmostEqual(self.p.skills['computer'],304)
        self.p._finish_activity(self.sim,self.p.activity)
        self.assertAlmostEqual(self.p.skills['computer'],304)

    def test_partial_work_checkpoint_does_not_repeat_experience_or_wages(self):
        self.p.skills={'computer':300};self.start('job_teacher_computer')
        for _ in range(3):self.p.tick(self.sim)
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));self.sim.save_id=store.allocate()
            branch=store.restore(store.checkpoint(self.sim),store.allocate())
            try:
                restored=branch.players[0]
                self.assertEqual(restored.skills['computer'],303)
                restored.tick(branch)
                self.assertEqual(restored.skills['computer'],304)
                self.assertEqual(restored.work_paid,round(70*4/6,2))
            finally:
                for p in branch.players:p.memory.close()

    def test_legacy_world_adds_new_fields_and_jobs_preserving_custom_values(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));self.sim.save_id=store.allocate();data=store.checkpoint(self.sim)
            config=data['state']['town_config']
            config['O'].pop('job_programmer_2');config['O']['job_programmer']['hourly_wage']=123
            config['U'].pop('job_teacher_computer');config['U']['study_computer'].pop('work_xp_per_hour')
            for y in range(2,11):
                data['maps']['town'][y]=data['maps']['town'][y].replace('J','.')
            branch=store.restore(data,store.allocate())
            try:
                self.assertEqual(branch.town_config['O']['job_programmer']['hourly_wage'],123)
                self.assertEqual(branch.town_config['U']['study_computer']['work_xp_per_hour'],3)
                self.assertIn('job_teacher_computer',branch.town_config['U'])
                self.assertEqual(world.tile('town',13,10),'J')
            finally:
                for p in branch.players:p.memory.close()

    def test_skill_levels_scale_medical_and_job_efficiency(self):
        gains=[]
        for lv in (1,3):
            self.p.skills={'medicine':lv*100};self.other.stats.hp=10;self.other.affinity[self.p.pid]=20
            self.start('home_treat',target=self.other.name)
            self.p.tick(self.sim)
            gains.append((self.other.stats.hp-10,self.other.affinity[self.p.pid]-20))
            self.p._finish_activity(self.sim,self.p.activity)
        for i in (0,1):self.assertAlmostEqual(gains[1][i],gains[0][i]*3)
        for lv in (1,4):
            self.p.skills={'computer':lv*100}
            # 一级软件工程师需二级，使用基础事务岗位检查等级效率。
            self.p.skills={'economics':lv*100}
            a=prepare(self.sim,self.p,Decision(action='work'))
            self.assertAlmostEqual(-a['effects']['energy'],9*(1-.02*lv)/6)

    def test_teacher_requires_the_subject_being_taught(self):
        self.p.skills={'medicine':300,'computer':200}
        with self.assertRaises(ValueError):prepare(self.sim,self.p,Decision(action='job_teacher_computer'))
        self.start('job_teacher_medicine');self.p.tick(self.sim)
        self.assertAlmostEqual(self.p.skills['medicine'],301)
        self.assertEqual(self.p.skills['computer'],200)

    def test_pond_swimming_enters_water_returns_to_shore_and_restores(self):
        self.start('swim',duration_minutes=20)
        self.assertEqual(world.tile('town',self.p.x,self.p.y),'W')
        self.assertFalse(world.walkable('town',self.p.x,self.p.y))
        self.sim.repair_positions();self.assertEqual(world.tile('town',self.p.x,self.p.y),'W')
        self.assertFalse(self.sim.position_free(self.other,'town',self.p.x,self.p.y))
        with tempfile.TemporaryDirectory() as temp:
            store=SaveStore(Path(temp));self.sim.save_id=store.allocate()
            branch=store.restore(store.checkpoint(self.sim),store.allocate())
            try:
                restored=branch.players[0]
                self.assertEqual(world.tile('town',restored.x,restored.y),'W')
                for _ in range(3):restored.tick(branch)
                self.assertEqual(restored.state,'idle')
                self.assertTrue(world.walkable('town',restored.x,restored.y))
            finally:
                for p in branch.players:p.memory.close()
        self.finish();self.assertTrue(world.walkable('town',self.p.x,self.p.y))

    def test_multiple_swimmers_and_fishers_do_not_overlap(self):
        self.start('swim',duration_minutes=60)
        self.sim._apply_decision(self.other,Decision(action='swim',duration_minutes=60))
        for _ in range(200):
            self.other.tick(self.sim)
            if self.other.state=='doing':break
        self.assertEqual(self.other.state,'doing')
        self.assertNotEqual((self.p.x,self.p.y),(self.other.x,self.other.y))
        self.p._finish_activity(self.sim,self.p.activity)
        self.start('fish',duration_minutes=20)
        self.assertNotEqual(world.tile('town',self.p.x,self.p.y),'W')
        self.assertTrue(any(world.tile('town',self.p.x+dx,self.p.y+dy)=='W' for dx,dy in ((0,1),(0,-1),(1,0),(-1,0))))

    def test_garden_actions_have_reachable_destinations_and_time_based_gains(self):
        for key in ('garden_walk','garden_flowers','garden_tend','fish'):
            with self.subTest(key=key):
                self.p.stats=Stats(mood=10,energy=50,hp=50,money=100)
                self.start(key,duration_minutes=20)
                start=self.p.stats.mood;self.finish()
                expected=self.sim.town_config[DEFS[key][0]][key]['mood_per_hour']/3
                self.assertAlmostEqual(self.p.stats.mood-start,expected)
                if key.startswith('garden_'):self.assertEqual(world.tile('town',self.p.x,self.p.y),'J')

    def test_model_can_select_new_actions_and_receives_exact_rules(self):
        for key in (*JOB_GRADES,'fish','swim','garden_walk','garden_flowers','garden_tend'):
            self.assertEqual(brain.parse_decision('{"action":"'+key+'"}').action,key)
        context=prompt_context(self.sim,self.p)
        for text in ('两倍','work_xp_per_hour','job_teacher_computer','job_programmer_2','fish','swim','garden_tend'):
            self.assertIn(text,context)


if __name__=='__main__':unittest.main()
