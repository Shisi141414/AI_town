"""Behavioral checks for staged information, opening hours, memory and driving."""
import unittest
import asyncio
from unittest.mock import patch,AsyncMock

from engine import brain, world
from engine.models import Decision,ApiProfile
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
        self.assertIn('约10元/10轮',prompt)

    def test_prompt_names_all_roommates_with_gender_without_memorizing_observations(self):
        a,b,c=self.sim.players
        b.name='小舟';b.gender='女';c.name='小林';c.gender='男'
        a.known_roommates=[(o.name,o.gender) for o in (b,c)]
        b.stats.energy=20
        before=a.memory.counts().copy(),b.memory.counts().copy()
        prompt=brain.build_user_prompt(a,[b],self.sim.clock)
        self.assertIn('同住室友：小舟（女）、小林（男）',prompt)
        self.assertIn('极度劳累',prompt)
        self.assertEqual((a.memory.counts(),b.memory.counts()),before)
        a.user_prompt_template=brain.USER_TEMPLATE.replace('- 同住室友：{roommates}\n','').replace('话费按通话轮数由拨打者支付（约{phone_fee_per_tick:g}元/10轮）','话费每小时50元')
        old_template_prompt=brain.build_user_prompt(a,[b],self.sim.clock)
        self.assertIn('同住室友：小舟（女）、小林（男）',old_template_prompt)
        self.assertIn('约10元/10轮',old_template_prompt)   # 旧自定义模板被升级为新话费文案

    def test_phone_charges_caller_by_rounds_across_scenes(self):
        a,b=self.sim.players[:2]
        a.scene='house';b.scene='town'
        a.stats.money=100;b.stats.money=100
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        self.assertEqual((a.activity_key,b.activity_key),('phone','phone'))
        conv=a._conversation
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            for _ in range(6):asyncio.run(conv.advance())   # 2轮完成
        self.assertAlmostEqual(a.stats.money,98)            # 2轮×1元/轮（10元/刻÷10轮）
        self.assertEqual(b.stats.money,100)                 # 只有拨打者付钱
        self.assertEqual(conv.turn_count,2)
        self.assertEqual((a.activity_key,b.activity_key),('phone','phone'))   # 未结束

    def test_phone_stops_when_caller_cannot_pay_next_round(self):
        a,b=self.sim.players[:2]
        # 路径1：不足一轮话费（1元/轮）直接拨不出
        a.stats.money=0.5
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        self.assertIsNone(a._conversation)
        self.assertEqual(a.info_feedback,'话费不足，暂时无法拨打')
        # 路径2：够一轮拨通，聊完一轮后耗尽挂断
        a.stats.money=1.5
        a.info_feedback=''
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        conv=a._conversation
        self.assertIsNotNone(conv)
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            for _ in range(3):asyncio.run(conv.advance())   # 第1轮完成 → 收1元 → 余0.5
        self.assertAlmostEqual(a.stats.money,0.5)
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            asyncio.run(conv.advance())                     # 下一轮预检：0.5<1 → 挂断
        self.assertEqual(conv.phase,'ended')
        self.assertEqual(conv.ended_reason,'insufficient_money')
        self.assertAlmostEqual(a.stats.money,0.5)
        self.assertAlmostEqual(a.phone_paid,1.0)
        self.assertEqual((a.activity_key,b.activity_key),('',''))

    def test_phone_fee_mechanism_controls_billing_and_prompt(self):
        a,b=self.sim.players[:2]
        self.sim.mechanics['phone_fee_per_tick']=18
        a.phone_fee_per_tick=18      # 决策循环同步到玩家属性，直接构建提示词时需手动同步
        a.stats.money=200
        self.assertIn('约18元/10轮',brain.build_user_prompt(a,[],self.sim.clock))
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        conv=a._conversation
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            for _ in range(6):asyncio.run(conv.advance())   # 2轮×1.8元/轮
        self.assertAlmostEqual(a.stats.money,196.4)

    def test_phone_fee_change_does_not_reprice_earlier_rounds(self):
        a,b=self.sim.players[:2]
        a.stats.money=30
        self.sim._apply_decision(a,Decision(action='phone',target=b.name,duration_minutes=60))
        conv=a._conversation
        with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            asyncio.run(conv.advance())                    # 开场，未成轮
            asyncio.run(conv.advance())                    # a说完，未成轮
            self.sim.mechanics['phone_fee_per_tick']=2
            asyncio.run(conv.advance())                    # 第1轮完成 → 按 0.2元/轮 收
        self.assertAlmostEqual(a.stats.money,29.8)
        self.assertAlmostEqual(a.phone_paid,0.2)

    def test_gate_arrival_and_departure_notify_only_roommates_at_home(self):
        a,b,c=self.sim.players
        a.scene=b.scene='house';c.scene='town'
        a._cross_gate(self.sim)
        self.assertIn(a.name+' 出门',b.memory.brief(0))
        self.assertNotIn(a.name+' 出门',c.memory.brief(0))
        b.scene='town';c.scene='house'
        a._cross_gate(self.sim)
        self.assertIn(a.name+' 回到家',a.memory.brief(0))
        self.assertIn(a.name+' 回到家',c.memory.brief(0))
        self.assertNotIn(a.name+' 回到家',b.memory.brief(0))

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

    def test_nearby_roommates_show_actions_qualitative_needs_and_mutual_relation(self):
        a,b,c=self.sim.players
        a.x,a.y=10,10
        b.x,b.y=18,10
        c.x,c.y=10,18
        b.state='doing';b.activity={'name':'睡觉'}
        b.stats.hp=72;b.stats.satiety=28;b.stats.energy=55;b.stats.mood=24
        c.stats.hp=c.stats.satiety=c.stats.energy=c.stats.mood=90
        b.affinity[c.pid]=90;c.affinity[b.pid]=88
        nearby=[o for o in (b,c) if o.scene==a.scene and abs(o.x-a.x)+abs(o.y-a.y)<=8]
        prompt=brain.build_user_prompt(a,nearby,'08:00')
        self.assertIn(f'{b.name}（正在睡觉；极度饥饿、劳累、极度沮丧）',prompt)
        self.assertIn(f'{c.name}（暂无活动；状态良好）',prompt)
        self.assertIn(f'{b.name}与{c.name}：亲密',prompt)
        self.assertNotIn('satiety=28',prompt)
        c.x,c.y=19,18
        nearby=[o for o in (b,c) if o.scene==a.scene and abs(o.x-a.x)+abs(o.y-a.y)<=8]
        self.assertEqual(nearby,[b])

    def test_roommate_mutual_relation_uses_both_directions(self):
        a,b=self.sim.players[1:]
        for value,expected in ((10,'互相仇恨'),(30,'互相矛盾'),(50,'关系一般'),(70,'友好'),(90,'亲密')):
            a.affinity[b.pid]=value;b.affinity[a.pid]=value
            self.assertIn(expected,brain.nearby_description([a,b]))
        a.affinity[b.pid]=90;b.affinity[a.pid]=10
        self.assertIn('关系一般',brain.nearby_description([a,b]))

    def test_observation_writes_only_to_observer_with_time(self):
        a,b,c=self.sim.players
        a.scene=b.scene='house';c.scene='town'
        a.x,a.y=10,10;b.x,b.y=11,10
        b.stats.energy=25
        before={p.pid:p.memory.counts().copy() for p in (a,b,c)}
        observed=self.sim._observe_nearby(a)
        self.assertEqual(observed,[b])
        latest=a.memory.items[0].content
        self.assertIn(f'第{self.sim.day}天 {self.sim.clock}',latest)
        self.assertIn(b.name,latest)
        self.assertIn('极度劳累',latest)
        self.assertNotIn(c.name,latest)
        self.assertEqual(a.memory.counts()['1'],before[a.pid]['1']+1)
        self.assertEqual(b.memory.counts(),before[b.pid])
        self.assertEqual(c.memory.counts(),before[c.pid])
        a.scene='town';a.x,a.y=1,1;c.x,c.y=20,20
        no_one=a.memory.counts().copy()
        self.assertEqual(self.sim._observe_nearby(a),[])
        self.assertEqual(a.memory.counts(),no_one)

    def test_model_decision_records_only_visible_roommates(self):
        a,b,c=self.sim.players
        a.x,a.y=10,10;b.x,b.y=18,10;c.x,c.y=19,10
        self.sim.profile=ApiProfile(id='stub',name='stub',api_key='fake',default_model='stub')
        before={p.pid:p.memory.counts()['1'] for p in (a,b,c)}
        with patch('engine.brain.decide_llm',new_callable=AsyncMock,return_value=Decision(action='think')) as decide,patch.object(self.sim,'_apply_decision'):
            asyncio.run(self.sim._decide_one(a))
        self.assertEqual([o.pid for o in decide.call_args.args[3]],[b.pid])
        self.assertEqual(a.memory.counts()['1'],before[a.pid]+1)
        self.assertEqual(b.memory.counts()['1'],before[b.pid])
        self.assertEqual(c.memory.counts()['1'],before[c.pid])

    def test_editable_action_description_preserves_venue_discovery(self):
        from engine.mechanics import validate_activity_descriptions
        custom=dict(self.sim.activity_descriptions)
        custom['cook']='在厨房；做一顿热饭'
        custom['job_programmer']='在事务所；编写程序'
        self.sim.activity_descriptions=validate_activity_descriptions(custom)
        self.p.activity_descriptions=self.sim.activity_descriptions
        at_home=brain.build_user_prompt(self.p,[],self.sim.clock)
        self.assertIn('在厨房；做一顿热饭',at_home)
        self.assertNotIn('编写程序',at_home)
        self.place('O')
        at_office=brain.build_user_prompt(self.p,[],self.sim.clock)
        self.assertIn('在事务所；编写程序',at_office)
        with self.assertRaises(ValueError):
            validate_activity_descriptions({**custom,'cook':'多行\n描述'})

    def test_town_directory_shows_distance_from_resident(self):
        from engine.town_services import NAMES
        home=prompt_context(self.sim,self.p)
        for code,name in NAMES.items():
            self.assertRegex(home,rf'{name} {code}（距我约\d+格）')
        self.place('O')
        self.assertIn('事务所 O（距我约0格）',prompt_context(self.sim,self.p))

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
