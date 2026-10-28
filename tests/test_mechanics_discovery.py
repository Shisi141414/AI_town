"""Behavioral checks for staged information, opening hours, memory and driving."""
import unittest
import asyncio
from unittest.mock import patch

from engine import brain, world
from engine.models import Decision
from engine.simulation import Simulation
from engine.town_services import inspect, prompt_context


class MechanicsDiscoveryTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim=Simulation()
        self.sim.spawn_players(3)
        self.p=self.sim.players[0]

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()

    def place(self,code):
        self.p.scene='town'
        self.p.x,self.p.y=next(q for q in world.find_tiles('town',code) if world.walkable('town',*q))

    def test_discovery_needs_visit_and_inspect(self):
        home=prompt_context(self.sim,self.p)
        self.assertIn('事务所',home)
        self.assertNotIn('job_programmer',home)
        self.place('O')
        onsite=prompt_context(self.sim,self.p)
        self.assertIn('job_programmer',onsite)
        self.assertNotIn('hourly_wage',onsite)
        details=inspect(self.sim,self.p,'job_programmer')
        self.assertIn('时薪90',details)
        self.assertNotIn('energy_cost_per_hour',details)
        with self.assertRaises(ValueError):inspect(self.sim,self.p,'eat_out')
        self.place('S')
        self.assertIn('私家汽车 1000元',prompt_context(self.sim,self.p))

    def test_prompt_emphasizes_computer_emoji_and_optional_memory(self):
        self.p.town_context=prompt_context(self.sim,self.p)
        prompt=brain.build_user_prompt(self.p,[],'08:00')
        for word in ('action=visit','action=inspect','action=computer','action=emoji','memory_note'):
            self.assertIn(word,prompt)
        self.assertNotIn('hourly_wage',prompt)
        self.assertNotIn('job_programmer',prompt)
        self.assertIn('姓名'+self.p.name,prompt)
        self.assertIn('未患疾病',prompt)
        self.assertIn('汽车在道路上更快，不消耗饱食，但会消耗油费',prompt)
        self.assertNotIn('出行偏好',prompt)
        self.assertNotIn('营业时间',prompt)
        self.assertIn('特别建议用自己的电脑记录',prompt)

    def test_phone_charges_caller_by_elapsed_time_across_scenes(self):
        a,b=self.sim.players[:2]
        a.scene='house';b.scene='town'
        a.stats.money=100;b.stats.money=100
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        self.assertEqual((a.activity_key,b.activity_key),('phone','phone'))
        conv=a._conversation
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            for _ in range(6):asyncio.run(conv.advance())
        self.assertEqual(a.stats.money,50)
        self.assertEqual(b.stats.money,100)
        self.assertEqual(conv.phase,'ended')
        self.assertEqual((a.activity_key,b.activity_key),('',''))

    def test_phone_stops_when_caller_cannot_pay_next_tick(self):
        a,b=self.sim.players[:2]
        a.stats.money=10
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        conv=a._conversation
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            asyncio.run(conv.advance())
            asyncio.run(conv.advance())
        self.assertEqual(conv.elapsed,1)
        self.assertEqual(conv.ended_reason,'insufficient_money')
        self.assertEqual(a.stats.money,1.67)
        self.assertEqual(b.stats.money,100)

    def test_venue_hours_and_skill_levels_are_contextual(self):
        self.p.skills['computer']=205
        home=prompt_context(self.sim,self.p)
        self.assertIn('计算机2级',home)
        self.assertNotIn('文学0级',home)
        self.assertNotIn('营业时间',home)
        self.place('O')
        onsite=prompt_context(self.sim,self.p)
        self.assertIn('事务所营业时间',onsite)
        self.assertNotIn('饭店营业时间',onsite)

    def test_prompt_sections_are_ordered_and_rules_not_duplicated(self):
        self.p.town_context=prompt_context(self.sim,self.p)
        text=brain.build_user_prompt(self.p,[],'08:00')
        headings=['# 本次决定','## 当前情况','## 已知信息','## 可选行动','## 行动方法','## 输出格式']
        self.assertEqual([text.index(h) for h in headings],sorted(text.index(h) for h in headings))
        self.assertEqual(text.count('action=inspect'),1)
        self.assertEqual(text.count('action=visit'),1)
        self.assertIn('室内生活：',text)
        self.assertIn('社交互动：',text)
        self.assertNotIn('hourly_wage',text)

    def test_driving_course_is_required_and_passenger_is_not_licensed(self):
        with self.assertRaisesRegex(ValueError,'驾照'):self.sim.set_travel_mode(self.p,'car')
        self.place('U')
        self.p.stats.money=100
        self.sim._apply_decision(self.p,Decision(action='license_driving',source='llm'))
        for _ in range(30):
            if self.p.state=='doing':break
            self.p.tick(self.sim)
        self.assertEqual(self.p.state,'doing')
        self.assertEqual(self.p.stats.money,20)
        for _ in range(12):self.p.tick(self.sim)
        self.assertTrue(self.p.driver_license)
        self.sim.set_travel_mode(self.p,'car')
        self.assertEqual(self.p.travel_mode,'car')
        other=self.sim.players[1]
        self.p.x,self.p.y=2,12
        self.sim.vehicles[0].x,self.sim.vehicles[0].y=2,12
        other.scene,other.x,other.y='town',3,12
        self.sim.board_car(other,self.p)
        self.assertFalse(other.driver_license)
        self.assertEqual(other.vehicle_driver,self.p.pid)

    def test_closed_venue_does_not_charge(self):
        self.place('R')
        before=self.p.stats.money
        self.sim._apply_decision(self.p,Decision(action='eat_out',source='llm'))
        self.assertNotEqual(self.p.state,'doing')
        self.assertEqual(self.p.stats.money,before)

    def test_activity_note_is_stored_after_completion(self):
        self.p._start_local('think',self.sim)
        while self.p.state=='doing':self.p.tick(self.sim)
        self.assertIn('思考',self.p.pending_memory_activity)
        self.assertEqual(brain.parse_decision('{"action":"sleep","memory_note":"记住这件事"}').memory_note,'记住这件事')

    def test_sex_requires_two_choices_and_game_is_only_on_computer(self):
        from engine.activities import ACTIVITIES,activity_menu
        self.assertIn('sex',ACTIVITIES)
        self.assertNotIn('game',ACTIVITIES)
        self.assertIn('- computer_game:',activity_menu())
        self.assertNotIn('- game:',activity_menu())
        self.assertEqual(brain.parse_decision('{"action":"打游戏"}').action,'computer_game')
        a,b=self.sim.players[:2]
        a.scene=b.scene='house';a.x,a.y=11,10;b.x,b.y=12,10
        self.sim._apply_decision(a,Decision(action='sex',target=b.name,location='here'))
        self.assertEqual(a.state,'waiting_consent')
        self.assertNotEqual(b.state,'doing')
        self.sim._apply_decision(b,Decision(action='sex',target=a.name,location='here'))
        self.assertEqual((a.activity_key,b.activity_key),('sex','sex'))
        self.assertNotEqual((a.x,a.y),(b.x,b.y))

    def test_sex_decline_and_bed_pair(self):
        a,b=self.sim.players[:2]
        self.sim._apply_decision(a,Decision(action='sex',target=b.name,location='bed'))
        self.assertEqual(a.state,'waiting_consent')
        self.sim._apply_decision(b,Decision(action='think'))
        self.assertNotEqual(a.state,'waiting_consent')
        self.assertFalse(self.sim.sex_proposals)
        if b.activity:b._finish_activity(self.sim,b.activity)
        self.sim._apply_decision(a,Decision(action='sex',target=b.name,location='bed'))
        self.sim._apply_decision(b,Decision(action='sex',target=a.name,location='bed'))
        for _ in range(80):
            for p in (a,b):p.tick(self.sim)
            if a.activity_key==b.activity_key=='sex':break
        self.assertEqual((a.activity_key,b.activity_key),('sex','sex'))
        self.assertNotEqual((a.x,a.y),(b.x,b.y))


if __name__=='__main__':unittest.main()
