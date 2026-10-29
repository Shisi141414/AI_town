"""统一活动目录与执行合同。"""
import unittest
import asyncio
from unittest.mock import patch

from engine import world
from engine.activities import activity_menu
from engine.activity_rules import STATS, apply, catalog, registry, validate_catalog
from engine.models import Decision
from engine.saves import capture
from engine.simulation import Simulation
from engine.town_services import action_spec, prepare


class ActivityRuleTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim=Simulation()
        self.sim.spawn_players(3)
        self.player=self.sim.players[0]

    def tearDown(self):
        self.sim.finish_recording()
        for player in self.sim.players:player.memory.close()

    def test_all_activity_kinds_have_complete_editable_fields(self):
        rows=catalog(self.sim)
        for key in ('cook','coffee','job_programmer','think'):
            row=rows[key]
            self.assertEqual(set(row['effects']),set(STATS))
            self.assertIn('money',row['effects'])
            self.assertIn('joint_affinity_per_tick',row)
            self.assertIn('location',row)
            self.assertIn('duration',row)
        self.assertEqual(rows['think']['effects']['money'],0)
        self.assertEqual(rows['cook']['effects']['hp'],0)

    def test_builtin_and_town_edits_reach_execution(self):
        rows=catalog(self.sim)
        rows['cook']['effects']['mood']=9
        rows['cook']['duration']=5
        rows['job_programmer']['effects']['money']=30
        rows['job_programmer']['cost']=2
        overrides,disabled,custom=validate_catalog(self.sim,rows)
        self.sim.activity_overrides=overrides
        self.sim.disabled_activities=list(disabled)
        self.sim.custom_activities=custom
        cook=action_spec(self.sim,self.player,'cook')
        self.assertEqual(cook['effects']['mood'],9)
        self.assertEqual(cook['duration'],5)
        self.player.skills['computer']=200
        job=prepare(self.sim,self.player,Decision(action='job_programmer'))
        self.assertEqual(job['effects']['money'],30)
        self.assertEqual(job['cost'],2)
        self.assertEqual(job['_terms']['hourly_wage'],180)

    def test_create_disable_delete_and_save(self):
        rows=catalog(self.sim)
        rows['write_diary']={'name':'写日记','emoji':'📝','description':'在室内记录心情',
            'scene':'house','location':'here','duration':2,'cost':3,
            'effects':{'hp':0,'satiety':0,'energy':-1,'mood':4,'money':0},
            'joint_affinity_per_tick':0,'enabled':True,'builtin':False,'special':{}}
        overrides,disabled,custom=validate_catalog(self.sim,rows)
        self.sim.activity_overrides=overrides
        self.sim.disabled_activities=list(disabled)
        self.sim.custom_activities=custom
        self.assertIn('write_diary',registry(self.sim))
        self.assertTrue(self.player.begin_activity('write_diary',self.sim))
        self.assertEqual(self.player.activity['cost'],3)
        self.assertEqual(self.player.activity_left,2)
        saved=capture(self.sim)
        self.assertIn('write_diary',saved['state']['custom_activities'])
        rows['write_diary']['enabled']=False
        rows['cook']['enabled']=False
        _,disabled,custom=validate_catalog(self.sim,rows)
        self.sim.disabled_activities=list(disabled)
        self.sim.custom_activities=custom
        self.assertNotIn('write_diary',registry(self.sim))
        self.assertNotIn('cook',registry(self.sim))
        self.assertFalse(self.player.begin_activity('cook',self.sim))
        del rows['write_diary']
        _,_,custom=validate_catalog(self.sim,rows)
        self.assertNotIn('write_diary',custom)

    def test_custom_town_activity_is_discovered_only_onsite(self):
        rows=catalog(self.sim)
        rows['garden_sketch']={'name':'花园写生','emoji':'🎨','description':'在小镇花园写生',
            'scene':'town','location':'town:J','duration':3,'cost':0,
            'effects':{'hp':0,'satiety':0,'energy':-1,'mood':2,'money':0},
            'joint_affinity_per_tick':1,'enabled':True,'builtin':False,'special':{}}
        _,_,self.sim.custom_activities=validate_catalog(self.sim,rows)
        actions=registry(self.sim)
        self.assertNotIn('garden_sketch',activity_menu('town',None,activities=actions))
        self.assertIn('garden_sketch',activity_menu('town','J',activities=actions))
        self.assertTrue(self.player.begin_activity('garden_sketch',self.sim))
        self.assertEqual(self.player._final_scene,'town')

    def test_phone_money_field_changes_actual_charge(self):
        rows=catalog(self.sim)
        self.assertEqual(rows['phone']['effects']['money'],-self.sim.mechanics['phone_fee_per_tick'])
        rows['phone']['effects']['money']=-3
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        caller,recipient=self.sim.players[:2]
        caller.stats.money=20
        self.sim._apply_decision(caller,Decision(action='phone',target=recipient.name,duration_minutes=10))
        conversation=caller._conversation
        with patch.object(conversation,'_rule_turn',return_value={'say':'你好','act':'stay'}):
            asyncio.run(conversation.advance())   # opening→a说话
            asyncio.run(conversation.advance())   # b说话
            asyncio.run(conversation.advance())   # 第1轮完成 → 收 3/10=0.3 元
        self.assertAlmostEqual(caller.stats.money,19.7)
        self.assertEqual(recipient.stats.money,100)


if __name__=='__main__':unittest.main()
