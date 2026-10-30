import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from engine import world,brain
from engine.models import AppConfig,Decision
from engine.simulation import Simulation
from engine.saves import SaveStore
from engine.town_services import prepare,defaults,level,update_market

class TownServiceTests(unittest.TestCase):
 def setUp(self):
  world.reset_maps();self.sim=Simulation();self.sim.spawn_players(3);self.sim.tick=72;self.a,self.b=self.sim.players[:2]
 def tearDown(self):
  self.sim.finish_recording()
  for p in self.sim.players:p.memory.close()
 def start(self,key,**kwargs):
  self.sim._apply_decision(self.a,Decision(action=key,source='llm',**kwargs))
  for _ in range(200):
   if self.a.state=='doing':return
   self.a.tick(self.sim)
  self.fail('activity never started '+key)
 def finish(self):
  for _ in range(200):
   if self.a.state=='idle':return
   self.a.tick(self.sim)
  self.fail('activity never finished')
 def test_cafe_and_restaurant_have_distinct_effects(self):
  self.a.stats.energy=30;self.a.stats.mood=20;self.a.stats.satiety=20
  self.start('coffee');energy=self.a.stats.energy;food=self.a.stats.satiety;self.finish()
  self.assertAlmostEqual(self.a.stats.energy,energy+18);self.assertEqual(self.a.stats.satiety,food)
  self.start('cafe_read');energy=self.a.stats.energy;self.finish();self.assertAlmostEqual(self.a.stats.energy,energy+9)
  self.assertEqual(world.tile(self.a.scene,self.a.x,self.a.y),'C')
  self.start('eat_out');food=self.a.stats.satiety;self.finish();self.assertAlmostEqual(self.a.stats.satiety,food+45)
  self.assertEqual(world.tile(self.a.scene,self.a.x,self.a.y),'R')
 def test_shared_restaurant_meal_increases_both_affinities(self):
  for i,p in enumerate((self.a,self.b)):
   p.scene='town';p.x=20+i;p.y=10;p.affinity={};prepare(self.sim,p,Decision(action='eat_out'));p._start_local('eat_out',self.sim)
  for i in range(3):
   self.sim.global_tick+=1
   for p in (self.a,self.b):p.tick(self.sim)
  self.assertAlmostEqual(self.a.affinity[self.b.pid],52);self.assertAlmostEqual(self.b.affinity[self.a.pid],52)
 def test_school_cost_duration_experience_and_interruption(self):
  self.a.stats.money=300
  self.start('study_medicine')
  self.assertEqual(self.a.activity_total,18);self.assertEqual(self.a.stats.money,220)
  self.assertFalse(self.a.skills);self.finish();self.assertAlmostEqual(self.a.stats.money,220)
  self.assertEqual(level(self.a,'medicine'),1)
  self.assertEqual(world.tile(self.a.scene,self.a.x,self.a.y),'U')
  self.start('study_economics');self.a.tick(self.sim)
  self.a.state='idle';self.a.activity=None;self.a.activity_key=''
  self.assertEqual(level(self.a,'economics'),0);self.assertGreater(self.a.skills['economics'],0)
 def test_job_gates_hourly_wages_and_no_extra_pay_after_finish(self):
  with self.assertRaises(ValueError):prepare(self.sim,self.a,Decision(action='job_doctor'))
  self.a.skills['medicine']=300;money=self.a.stats.money
  self.start('job_doctor',duration_minutes=10)
  self.assertEqual(self.a.activity_total,12)
  self.finish();self.assertAlmostEqual(self.a.stats.money-money,220,places=6)
  value=self.a.stats.money;self.a.tick(self.sim);self.assertEqual(value,self.a.stats.money)
 def test_wage_partial_time_and_decimal_rounding(self):
  self.start('job_reception');start=self.a.stats.money
  for _ in range(6):self.a.tick(self.sim)
  self.assertAlmostEqual(self.a.stats.money-start,35,places=6)
 def test_medical_self_and_roommate_treatment_and_limits(self):
  self.a.skills['medicine']=100;self.a.stats.hp=70
  self.sim.random_events.onset(self.sim,self.a,1)
  self.start('home_treat');self.finish()
  self.assertEqual(self.a.illness_severity,0);self.assertGreater(self.a.stats.hp,70)
  self.b.stats.hp=70;self.b.affinity[self.a.pid]=50
  self.start('home_treat',target=self.b.name);self.finish()
  self.assertGreater(self.b.stats.hp,70);self.assertAlmostEqual(self.b.affinity[self.a.pid],52)
  self.b.illness_severity=3
  with self.assertRaises(ValueError):prepare(self.sim,self.a,Decision(action='home_treat',target=self.b.name))
 def test_cooking_skill_improves_meal_and_saved_quality(self):
  self.a.skills['cooking']=200;self.a.stats.satiety=0;self.a.stats.energy=60;self.a.stats.mood=10
  self.a.items['ingredient']=4
  self.start('cook');energy_at_stove=self.a.stats.energy;self.finish()
  self.assertEqual(self.sim.meal_quality,2)
  # 新契约（食材制）：做饭本身不再直接回饱食，产出2份饭菜、消耗2份食材、耗精力
  self.assertEqual(self.a.items.get('meal'),2)
  self.assertEqual(self.a.items.get('ingredient'),2)
  self.assertLess(self.a.stats.energy,energy_at_stove)
  self.sim._apply_decision(self.a,Decision(action='eat'))
  for _ in range(200):
   if self.a.state=='doing':break
   self.a.tick(self.sim)
  for _ in range(200):
   self.a.tick(self.sim)
   if self.a.state=='idle':break
  self.assertGreater(self.a.stats.satiety,39)
  self.b.stats.satiety=0;self.b.stats.energy=10;self.b.stats.mood=10
  self.sim._apply_decision(self.b,Decision(action='eat'))
  for _ in range(200):
   if self.b.state=='doing':break
   self.b.tick(self.sim)
  energy_at_table=self.b.stats.energy
  for _ in range(200):
   self.b.tick(self.sim)
   if self.b.state=='idle':break
  self.assertGreater(self.b.stats.satiety,39);self.assertGreater(self.b.stats.energy,energy_at_table)
 def test_stock_skill_gate_buy_sell_and_failed_transactions(self):
  with self.assertRaises(ValueError):prepare(self.sim,self.a,Decision(action='stock',operation='buy',shares=1))
  self.a.skills['economics']=100
  self.start('stock',operation='buy',shares=4);self.finish()
  self.assertEqual(self.a.stock_shares,4);self.assertEqual(self.a.stats.money,60)
  self.sim.stocks['index']['price']=12;self.sim.market_price=12
  self.start('stock',operation='sell',shares=2);self.finish()
  self.assertEqual(self.a.stock_shares,2);self.assertEqual(self.a.stats.money,84)
  self.start('stock',operation='sell',shares=99);self.finish()
  self.assertEqual(self.a.stock_shares,2);self.assertEqual(self.a.stats.money,84)
 def test_snapshots_restore_parameters_active_contract_skills_and_market(self):
  self.a.stats.money=300;self.start('study_computer');self.a.tick(self.sim)
  self.a.skills['economics']=100;self.a.stock_shares=2;self.a.stock_cost=20
  self.sim.market_price=11;self.sim.town_config['U']['study_computer']['price']=99
  with tempfile.TemporaryDirectory() as temp:
   store=SaveStore(Path(temp));self.sim.save_id=store.allocate();data=store.checkpoint(self.sim);branch=store.restore(data,store.allocate())
   try:
    p=branch.players[0];self.assertEqual(p.activity['_terms']['price'],60)
    self.assertEqual(branch.town_config['U']['study_computer']['price'],99)
    self.assertEqual((branch.market_price,p.stock_shares,p.stock_cost),(11,2,20))
    for _ in range(30):
     if p.state=='idle':break
     p.tick(branch)
    self.assertEqual(level(p,'computer'),1)
   finally:
    for p in branch.players:p.memory.close()
 def test_prompt_discloses_terms_only_at_the_shop(self):
  from engine.town_services import prompt_context
  self.sim.town_config['S']['car']['price']=1234
  self.a.town_context=prompt_context(self.sim,self.a)
  text=brain.build_user_prompt(self.a,[],self.sim.clock)
  for term in ('home_treat','shares','action=visit','action=inspect'):self.assertIn(term,text)
  self.assertNotIn('烹饪0级',text)
  for term in ('1234','study_science','job_doctor'):self.assertNotIn(term,text)
  self.a.scene='town';self.a.x,self.a.y=next(q for q in world.find_tiles('town','S') if world.walkable('town',*q))
  self.assertIn('1234',prompt_context(self.sim,self.a))
  for key in ('study_computer','job_chef','coffee','stock','home_treat'):
   self.assertEqual(brain.parse_decision(json.dumps({'action':key})).action,key)
  self.assertEqual(brain.parse_decision('{"action":"stock","shares":3,"operation":"buy"}').shares,3)
 def test_continue_from_completion_event_does_not_repeat_reward_or_energy_cost(self):
  from engine.recording import read_frame
  with tempfile.TemporaryDirectory() as temp:
   store=SaveStore(Path(temp)/'saves');self.sim.save_id=store.allocate();self.sim.replay_dir=Path(temp)/'replays'
   self.sim.ensure_recording();self.a.stats.money=300
   self.start('study_computer');self.finish()
   recorder=self.sim.recorder
   n=recorder.db.execute("SELECT COUNT(*) FROM records WHERE kind='frame'").fetchone()[0]
   checkpoint=read_frame(self.sim.replay_dir,recorder.id,n-1)['frame']['checkpoint']
   self.assertEqual(checkpoint['players'][0]['activity_left'],0)
   branch=store.restore(checkpoint,store.allocate())
   try:
    p=branch.players[0];before=p.stats.model_dump();xp=dict(p.skills)
    p.tick(branch)
    self.assertEqual(p.stats.model_dump(),before);self.assertEqual(p.skills,xp);self.assertEqual(p.state,'idle')
   finally:
    for p in branch.players:p.memory.close()
    self.sim.finish_recording()
 def test_redirect_to_restaurant_uses_edited_price(self):
  self.sim.global_tick=2;self.sim.town_config['R']['eat_out']['price']=25
  self.start('eat');self.assertEqual(self.a.stats.money,75)
  self.assertEqual(self.a.activity['_terms']['price'],25)
 def test_coffee_rates_scale_with_time_and_exceed_dining(self):
  from engine.models import Stats
  gains=[]
  for ticks in (1,6):
   for i,p in enumerate((self.a,self.b)):
    p.scene='town';p.x=4+i;p.y=10;p.stats=Stats(energy=0,mood=0)
    p.affinity={};prepare(self.sim,p,Decision(action='coffee'))
    p.requested_duration=ticks;p._start_local('coffee',self.sim)
   for _ in range(ticks):
    self.sim.global_tick+=1
    for p in (self.a,self.b):p.tick(self.sim)
   gains.append((self.a.stats.energy,self.a.stats.mood,self.a.affinity[self.b.pid]-50))
  for short,long in zip(*gains):self.assertAlmostEqual(long,short*6)
  self.assertEqual(self.sim.town_config['C']['coffee']['affinity_per_hour'],8)
  self.assertGreater(self.sim.town_config['C']['coffee']['affinity_per_hour'],self.sim.town_config['R']['eat_out']['affinity_per_hour'])
 def test_learning_experience_is_a_rate_and_tuition_paid_once(self):
  from engine.models import Stats
  gains=[]
  for ticks in (1,6):
   p=self.a;p.scene='town';p.x,p.y=11,23;p.skills={};p.stats=Stats(money=300,energy=80)
   self.sim._apply_decision(p,Decision(action='study_computer',duration_minutes=ticks*10))
   for _ in range(100):
    if p.state=='idle':break
    p.tick(self.sim)
   gains.append((p.skills['computer'],300-p.stats.money,80-p.stats.energy))
  for index in (0,2):self.assertAlmostEqual(gains[1][index],gains[0][index]*6,delta=1e-5)
  self.assertEqual(gains[0][1],60);self.assertEqual(gains[1][1],60)
 def test_prepaid_tuition_does_not_stop_study_when_wallet_is_empty(self):
  p=self.a;p.scene='town';p.x,p.y=11,23;p.stats.money=60
  self.sim._apply_decision(p,Decision(action='study_computer',source='llm',duration_minutes=60))
  for _ in range(30):
   p.tick(self.sim)
   if p.state=='idle':break
  self.assertEqual(p.stats.money,0);self.assertAlmostEqual(p.skills['computer'],50,places=5)

class BuildingApiTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'CONFIG_PATH',Path(self.temp.name)/'config.json');self.patch.start()
  server.config=AppConfig();server.sim=Simulation();server._identity_started=False;server._identity_task=None;server._control_lock=asyncio.Lock();world.reset_maps()
  self.client=TestClient(server.app);self.client.__enter__()
 def tearDown(self):
  server.sim.stop();self.client.__exit__(None,None,None);self.patch.stop();self.temp.cleanup()
 def test_edit_paused_lock_running_and_validation(self):
  catalog=self.client.get('/api/town/buildings').json();self.assertEqual(len(catalog['U']['parameters']),19)
  values=catalog['C']['parameters'];values['coffee']['price']=9
  self.assertEqual(self.client.put('/api/town/buildings/C',json={'parameters':values}).status_code,200)
  server.sim.running=True
  self.assertEqual(self.client.put('/api/town/buildings/C',json={'parameters':values}).status_code,409)
  server.sim.running=False;values['coffee']['minutes']=13
  self.assertEqual(self.client.put('/api/town/buildings/C',json={'parameters':values}).status_code,400)
  self.assertEqual(server.sim.town_config['C']['coffee']['minutes'],30)
 def test_car_price_and_config_saved_and_reset_isolated(self):
  values=self.client.get('/api/town/buildings').json()['S']['parameters'];values['car']['price']=50
  self.assertEqual(self.client.put('/api/town/buildings/S',json={'parameters':values}).status_code,200)
  self.assertEqual(server.save_store().current()['state']['town_config']['S']['car']['price'],50)
  server.sim.buy_car(server.sim.players[0]);self.assertEqual(server.sim.players[0].stats.money,50)
  self.client.post('/api/control',json={'cmd':'reset'})
  self.assertEqual(server.sim.town_config['S']['car']['price'],1000)
