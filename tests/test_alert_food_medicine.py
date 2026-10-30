"""受伤决策与可转交的食物、药品。"""
import asyncio
import unittest
from unittest.mock import patch

from engine import brain, world
from engine.models import Decision
from engine.simulation import Simulation


class AlertFoodMedicineTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim=Simulation()
        self.sim.spawn_players(2)
        self.a,self.b=self.sim.players
        for p in self.sim.players:p.identity_done=True

    def tearDown(self):
        self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()

    def arrive(self,p,limit=180):
        for _ in range(limit):
            if p.state=='doing':return
            p.tick(self.sim)
        self.fail(f'{p.name}未到达行动位置')

    def finish(self,p,limit=150):
        for _ in range(limit):
            if p.state=='idle':return
            p.tick(self.sim)
        self.fail(f'{p.name}活动未结束')

    def test_starvation_interrupts_timer_and_next_tick_requests_decision(self):
        self.a.requested_duration=12
        self.a._start_local('think',self.sim)
        self.a.stats.satiety=0
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.a.state,'idle')
        self.assertIsNone(self.a.activity)
        self.assertIn('生命',self.a.injury_alert)
        self.assertIn('紧急情况',brain.build_user_prompt(self.a,[],self.sim.clock))
        chosen=[]
        async def choose(idlers):chosen.extend(idlers)
        with patch.object(self.sim,'_decide_batch',side_effect=choose):
            asyncio.run(self.sim.step_tick())
        self.assertIn(self.a,chosen)

    def test_same_injury_only_interrupts_once_until_cleared(self):
        # 同种持续伤害只打断一次；伤害结束清除标记后可再次打断
        self.a.stats.satiety=0
        self.a.requested_duration=60
        self.a._start_local('think',self.sim)
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.a.state,'idle')          # 首次饥饿 → 打断
        self.assertIn('starvation',self.a._injury_break_done)
        # 再开始活动：仍饥饿但不打断，角色可安心去吃饭
        self.a.requested_duration=60
        self.a._start_local('think',self.sim)
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.a.state,'doing')
        # 吃饱 → 标记清除
        self.a.stats.satiety=50
        asyncio.run(self.sim.step_tick())
        self.assertNotIn('starvation',self.a._injury_break_done)
        # 再次饥饿 → 能重新打断（防"永久免打扰"）
        self.a.stats.satiety=0
        self.a.requested_duration=60
        self.a._start_local('think',self.sim)
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.a.state,'idle')

    def test_illness_recovery_clears_interrupt_flag(self):
        self.a.illness_severity=1
        self.a.illness_until=1000
        self.a.requested_duration=60
        self.a._start_local('think',self.sim)
        self.sim._alert_health_loss(self.a,2)
        self.assertIn('illness',self.a._injury_break_done)
        self.sim.random_events.recover(self.sim,self.a)
        self.assertNotIn('illness',self.a._injury_break_done)

    def test_attack_is_not_deduplicated(self):
        # 攻击是单次事件：不写入打断标记，每次都能打断
        self.b.requested_duration=60
        self.b._start_local('think',self.sim)
        self.a.target_player=self.b
        self.a._resolve_attack(self.sim)
        self.assertEqual(self.b.state,'idle')
        self.assertNotIn('attack',self.b._injury_break_done)

    def test_sleep_healing_more_than_illness_damage_keeps_timer(self):
        self.a.stats.hp=50
        self.a.illness_severity=1
        self.a.illness_until=1000
        self.a.requested_duration=60
        self.a._start_local('sleep',self.sim)
        self.arrive(self.a)
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.a.activity_key,'sleep')
        self.assertEqual(self.a.injury_alert,'')

    def test_starving_resident_can_reach_indoor_food_in_one_tick(self):
        for i,roommate in enumerate(self.sim.players[1:]):
            roommate.scene,roommate.x,roommate.y='town',2+i,12
        self.a.stats.satiety=0
        self.a._plan_route(self.sim,'house',13,3)
        self.assertEqual(self.a.state,'moving')
        asyncio.run(self.sim.step_tick())
        self.assertEqual((self.a.x,self.a.y),(13,3))
        self.assertFalse(self.a.path)

    def test_attack_interrupts_bed_activity_and_releases_slot(self):
        self.b.requested_duration=12
        self.b._start_local('sleep',self.sim)
        self.arrive(self.b)
        self.assertIsNotNone(self.b.interaction_slot)
        self.a.target_player=self.b
        before=self.b.stats.hp
        self.a._resolve_attack(self.sim)
        self.assertLess(self.b.stats.hp,before)
        self.assertEqual(self.b.state,'idle')
        self.assertIsNone(self.b.interaction_slot)
        self.assertTrue(world.walkable('house',self.b.x,self.b.y))

    def test_cooked_meal_is_eaten_upon_giving(self):
        # 2026-10-29 起：送饭菜当场吃掉，不进对方背包（防饥饿互送死循环）
        self.a.skills['cooking']=200
        self.a.items['ingredient']=4
        self.a._cook_meals=2
        self.a._start_local('cook',self.sim)
        self.arrive(self.a)
        self.finish(self.a)
        self.assertEqual(self.a.items['meal'],2)
        self.assertEqual(self.a.meal_qualities,[2,2])
        self.a.x,self.a.y=10,11
        self.b.x,self.b.y=11,11
        self.b.stats.satiety=0
        self.sim._apply_decision(self.a,Decision(action='give_meal',target=self.b.name))
        self.arrive(self.a)
        self.finish(self.a)
        # 对方当场吃：不获得物品，饱食直接恢复
        self.assertEqual(self.b.items.get('meal',0),0)
        self.assertEqual(self.b.meal_qualities,[])
        self.assertGreater(self.b.stats.satiety,0)
        self.assertIn('送的饭菜',self.b.memory.brief(0,k=10,query='饭菜'))

    def test_clinic_medicine_can_treat_roommate(self):
        self.a.scene='town'
        self.a.x,self.a.y=next(q for q in world.find_tiles('town','B') if world.walkable('town',*q))
        self.a.stats.money=100
        self.sim._apply_decision(self.a,Decision(action='buy_medicine'))
        self.arrive(self.a)
        self.finish(self.a)
        self.assertEqual(self.a.items['medicine'],1)
        self.assertEqual(self.a.stats.money,75)
        self.a.scene=self.b.scene='house'
        self.a.x,self.a.y=10,11
        self.b.x,self.b.y=11,11
        self.b.stats.hp=40
        self.b.illness_severity=1
        self.sim._apply_decision(self.a,Decision(action='use_medicine',target=self.b.name))
        self.arrive(self.a)
        self.finish(self.a)
        self.assertEqual(self.a.items['medicine'],0)
        self.assertGreaterEqual(self.b.stats.hp,60)
        self.assertEqual(self.b.illness_severity,0)

    def test_daily_hunger_drain_is_reduced(self):
        before=self.a.stats.satiety
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(before-self.a.stats.satiety,0.4)
        self.assertEqual(self.sim.mechanics['walk_satiety_per_tile'],0.6)
        self.assertEqual(self.sim.mechanics['bicycle_satiety_per_tile'],0.4)

    def test_fullness_restores_health_only_above_threshold(self):
        self.a.stats.hp=60
        self.a.stats.satiety=81
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp,60.3)
        self.a.stats.satiety=80.4
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp,60.3)
        self.sim.mechanics['high_satiety_hp_threshold']=70
        self.sim.mechanics['high_satiety_hp_gain']=.8
        self.a.passive_drain(self.sim)
        self.assertAlmostEqual(self.a.stats.hp,61.1)
        self.a.stats.hp=99.9
        self.a.passive_drain(self.sim)
        self.assertEqual(self.a.stats.hp,100)

    def test_exposed_passive_rates_change_actual_health_and_mood(self):
        self.sim.mechanics['starvation_hp_loss']=3
        self.a.stats.satiety=0
        self.a.stats.hp=80
        self.a.passive_drain(self.sim)
        self.assertEqual(self.a.stats.hp,77)
        self.a.stats.satiety=50
        self.a.stats.mood=40
        self.a.decor_room=200
        self.sim.mechanics['decor_mood_per_200']=2
        self.a._start_local('think',self.sim)
        self.a.tick(self.sim)
        self.assertGreaterEqual(self.a.stats.mood,42)


if __name__=='__main__':unittest.main()
