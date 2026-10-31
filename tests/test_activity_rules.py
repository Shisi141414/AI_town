"""统一活动目录与执行合同。"""
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock

from engine import world
from engine.activities import activity_menu
from engine.activity_rules import STATS, apply, catalog, registry, validate_catalog
from engine.models import Decision
from engine.saves import capture
from engine.simulation import Simulation
from engine.town_services import action_spec, prepare
from engine.town_services import step_effects
from engine.shop_products import catalog as shop_catalog,apply as apply_shop
from engine import brain


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

    def test_multiple_places_and_target_effects(self):
        rows=catalog(self.sim)
        rows['cook']['places']={'house':['house:K'],'town':['town:C','town:J']}
        rows['cook']['target_mode']='optional'
        rows['cook']['self_affinity_per_tick']=2
        rows['cook']['target_affinity_per_tick']=-1
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        self.player.scene='town';self.player.x,self.player.y=next(iter(world.find_tiles('town','C')))
        spec=action_spec(self.sim,self.player,'cook')
        self.assertEqual(spec['scene'],'town')
        self.assertIn(spec['_chosen_location'],('town:C','town:J'))
        self.assertFalse(spec['_use_interaction_points'])
        self.assertEqual(spec['_self_affinity_per_tick'],2)
        self.assertEqual(spec['_target_affinity_per_tick'],-1)

    def test_place_and_here_requires_actor_at_selected_location(self):
        rows=catalog(self.sim)
        rows['think']['places']={'house':['house:L']}
        rows['think']['place_join']='and'
        rows['think']['position_options']=['here']
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        self.assertFalse(self.player.begin_activity('think',self.sim))
        self.assertIn('所选地点',self.player._rejection)
        self.player.x,self.player.y=next(iter(world.find_tiles('house','L')))
        self.assertTrue(self.player.begin_activity('think',self.sim))
        self.assertEqual(self.player.state,'doing')

    def test_place_and_target_requires_target_at_selected_location(self):
        rows=catalog(self.sim)
        rows['think']['places']={'house':['house:L']}
        rows['think']['place_join']='and'
        rows['think']['position_options']=['near_player']
        rows['think']['target_mode']='required'
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        target=self.sim.players[1]
        self.assertFalse(self.player.begin_activity('think',self.sim,target))
        self.player.x,self.player.y=next(iter(world.find_tiles('house','L')))
        target.x,target.y=self.player.x,self.player.y
        self.assertTrue(self.player.begin_activity('think',self.sim,target))
        self.assertEqual(self.player._pending_target_pid,target.pid)

    def test_place_or_target_falls_back_to_concrete_place_without_target(self):
        rows=catalog(self.sim)
        rows['think']['places']={'house':['house:L','near_player']}
        rows['think']['place_join']='or'
        rows['think']['position_options']=['near_player']
        rows['think']['target_mode']='optional'
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        self.assertTrue(self.player.begin_activity('think',self.sim))
        self.assertEqual(self.player._pending_key,'think')
        self.assertEqual(self.player._final_scene,None)

    def test_shop_product_creation_purchase_and_off_shelf(self):
        rows=shop_catalog(self.sim)
        rows['buy_seeds']={'name':'种子','emoji':'🌱','price':7,'enabled':True,'builtin':False}
        values=apply_shop(self.sim,rows)
        self.sim.activity_overrides,self.sim.disabled_activities,self.sim.custom_activities,self.sim.town_config,self.sim.shop_car=values
        self.assertIn('buy_seeds',registry(self.sim))
        _,_,round_trip=validate_catalog(self.sim,catalog(self.sim))
        self.assertEqual(round_trip['buy_seeds']['product_item'],'item_seeds')
        self.player.scene='town';self.player.x,self.player.y=next(iter(world.find_tiles('town','S')))
        self.player._start_local('buy_seeds',self.sim)
        self.assertEqual(self.player.items.get('item_seeds'),1)
        rows=shop_catalog(self.sim);rows['buy_seeds']['enabled']=False;rows['car']['enabled']=False
        values=apply_shop(self.sim,rows)
        self.sim.activity_overrides,self.sim.disabled_activities,self.sim.custom_activities,self.sim.town_config,self.sim.shop_car=values
        self.assertNotIn('buy_seeds',registry(self.sim))
        with self.assertRaises(ValueError):self.sim.buy_car(self.player)

    def test_decision_prompt_and_raw_reply_are_kept(self):
        self.player.activity_registry=registry(self.sim)
        with patch.object(brain,'build_user_prompt',return_value='我的完整提示词'),patch.object(brain,'_run_in_thread',new_callable=AsyncMock,return_value='{"action":"think"}'):
            asyncio.run(brain.decide_llm(None,'model',self.player,[],'08:00','系统提示词',0.2,2))
        self.assertEqual(self.player.last_system_prompt,'系统提示词')
        self.assertEqual(self.player.last_user_prompt,'我的完整提示词')
        self.assertEqual(self.player.last_raw_reply,'{"action":"think"}')
        self.assertIn('我的完整提示词',self.player.snapshot().last_user_prompt)

    def test_public_decoration_enters_home_roommate_memory(self):
        actor,home,away=self.sim.players
        away.scene='town'
        actor.activity={'name':'布置公共区'};actor.activity_total=2;actor.activity_left=2
        actor.decor_gain_per_tick=1
        step_effects(self.sim,actor,'decorate_common',{'mood':1})
        self.assertIn(actor.name+'在家里布置了公共区域',home.memory.brief(1000))
        self.assertNotIn(actor.name+'在家里布置了公共区域',away.memory.brief(1000))

    def test_periodic_affinity_budget_and_change(self):
        self.sim.mechanics['affinity_decisions_per_tick']=1
        with patch.object(self.sim,'profile_for',return_value=SimpleNamespace(default_model='mock')),patch.object(brain,'affinity_tick_llm',new_callable=AsyncMock,return_value=1):
            self.sim._enqueue_periodic_affinity()
            self.assertEqual(len(self.sim.affinity_queue),1)
            request=self.sim.affinity_queue[0]
            actor,target=self.sim.by_pid(request[1]),self.sim.by_pid(request[2])
            before=actor.affinity.get(target.pid,50)
            asyncio.run(self.sim._drain_affinity())
        self.assertEqual(actor.affinity[target.pid],before+1)

    def test_dialogue_edits_settle_once_per_ten_rounds(self):
        rows=catalog(self.sim)
        rows['chat']['effects']['mood']=2
        rows['chat']['joint_affinity_per_tick']=1
        rows['chat']['self_affinity_per_tick']=.5
        rows['chat']['target_affinity_per_tick']=-.5
        self.sim.activity_overrides,_,_=validate_catalog(self.sim,rows)
        actor,other=self.sim.players[:2]
        actor.stats.mood=other.stats.mood=50
        actor.affinity[other.pid]=other.affinity[actor.pid]=50
        actor.activity=action_spec(self.sim,actor,'chat');actor.activity_key='chat';actor.state='doing'
        with patch.object(self.sim,'profile_for',return_value=None):
            self.sim.start_conversation(actor,other,'chat')
            conv=actor._conversation
            with patch.object(conv,'_rule_turn',return_value={'say':'你好','act':'stay'}):
                for _ in range(24):
                    asyncio.run(conv.advance())
                    if conv.phase=='ended':break
        self.assertEqual(conv.turn_count,10)
        self.assertEqual(actor.stats.mood,52)
        self.assertEqual(other.stats.mood,52)
        self.assertAlmostEqual(actor.affinity[other.pid],51.5)
        self.assertAlmostEqual(other.affinity[actor.pid],50.5)


if __name__=='__main__':unittest.main()
