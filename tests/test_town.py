"""离线回归检查：使用临时配置，不读取或调用真实模型网关。"""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import server
from engine import brain, world, activities
from engine.models import ApiProfile, AppConfig, Decision
from engine.simulation import Simulation
from engine.conversation import Conversation


class EngineTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(5)
        asyncio.run(self.sim.run_identity_phase())
        self.p = self.sim.players[0]
        for resident in self.sim.players:resident.driver_license=True

    def on_road(self, p=None):
        p = p or self.p
        p.scene, p.x, p.y = "town", 2, 12
        return p

    def test_walk_and_bicycle_speed_and_hunger(self):
        for mode, speed in (("walk", 1), ("bicycle", 4)):
            with self.subTest(mode=mode):
                p = self.on_road()
                self.sim.set_travel_mode(p, mode)
                start=p.x
                p._plan_route(self.sim, "town", 24, 12)
                before = p.stats.satiety
                p.tick(self.sim)
                self.assertEqual(p.x, start + speed)
                self.assertLess(p.stats.satiety, before)

    def test_car_twelve_tiles_cost_and_no_hunger(self):
        p = self.on_road()
        self.sim.set_travel_mode(p, "car")
        p._plan_route(self.sim, "town", 24, 12)
        hunger, money = p.stats.satiety, p.stats.money
        p.tick(self.sim)
        p.passive_drain(self.sim)
        self.assertEqual((p.x, p.y), (14, 12))
        self.assertEqual(p.stats.satiety, hunger)
        self.assertEqual(money - p.stats.money, 2)
        self.assertEqual(self.sim.vehicles[0].x, 14)

    def test_car_one_tile_still_pays_once_for_the_tick(self):
        p=self.on_road()
        self.sim.set_travel_mode(p,'car')
        p._plan_route(self.sim,'town',3,12)
        before=p.stats.money
        p.tick(self.sim)
        self.assertEqual((p.x,p.y),(3,12))
        self.assertEqual(before-p.stats.money,2)

    def test_indoor_always_walks(self):
        self.sim.set_travel_mode(self.p, "car")
        self.p.x, self.p.y = 8, 12
        self.p._plan_route(self.sim, "house", 15, 12)
        self.p.tick(self.sim)
        self.assertEqual((self.p.x,self.p.y),(15,12))
        self.assertTrue(world.walkable("house",self.p.x,self.p.y))
        self.assertEqual(self.p.transport, "walk")

    def test_any_reachable_indoor_destination_arrives_in_one_tick(self):
        p=self.p
        for i,other in enumerate(self.sim.players[1:]):
            other.scene,other.x,other.y='town',2+i,12
        starts=[world.BEDROOM_ANCHORS[0],world.BEDROOM_ANCHORS[-1],(22,5),(23,22)]
        goals=[world.BEDROOM_ANCHORS[-1],(22,5),(23,22),world.BEDROOM_ANCHORS[0]]
        for start,goal in zip(starts,goals):
            with self.subTest(start=start,goal=goal):
                p.scene='house';p.x,p.y=self.sim.free_position('house',*start,p)
                destination=self.sim.free_position('house',*goal,p)
                p._plan_route(self.sim,'house',*destination)
                self.assertTrue(p.path)
                p.tick(self.sim)
                self.assertEqual((p.x,p.y),destination)
                self.assertTrue(self.sim.position_free(p,'house',p.x,p.y))

    def test_shared_car_is_exclusive_and_parking_has_location(self):
        a, b = self.sim.players[:2]
        self.on_road(a)
        self.on_road(b)
        self.sim.set_travel_mode(a, "car")
        with self.assertRaises(ValueError):
            self.sim.set_travel_mode(b, "car")
        a._plan_route(self.sim, "town", 24, 12)
        a.tick(self.sim)
        self.sim.set_travel_mode(a, "walk")
        # 空车可远程预约，实际驾驶仍须先走到停车点。
        self.sim.set_travel_mode(b, "car")
        self.assertNotEqual((b.x,b.y),(self.sim.vehicles[0].x,self.sim.vehicles[0].y))
        b.x, b.y = a.x, a.y
        self.assertEqual(b.car_id, "shared-car")

    def test_purchase_1000_and_private_ownership(self):
        a, b = self.sim.players[:2]
        with self.assertRaises(ValueError):
            self.sim.buy_car(a)
        a.stats.money = 1000
        car = self.sim.buy_car(a)
        self.assertEqual(a.stats.money, 0)
        self.assertEqual(car.owner_id, a.pid)
        self.assertEqual(sum(v.owner_id is None for v in self.sim.vehicles), 1)
        with self.assertRaises(ValueError):
            self.sim.buy_car(a)
        self.sim.set_travel_mode(a, "car")
        self.assertEqual(a.car_id, car.id)
        self.sim.set_travel_mode(b, "car")
        self.assertEqual(b.car_id, "shared-car")

    def test_riders_follow_and_leave_at_road_edge(self):
        a, b = self.sim.players[:2]
        self.on_road(a)
        self.on_road(b)
        self.sim.set_travel_mode(a, "car")
        self.sim.board_car(b, a)
        a._plan_route(self.sim, "town", 24, 12)
        hunger = b.stats.satiety
        a.tick(self.sim)
        b.tick(self.sim)
        b.passive_drain(self.sim)
        self.assertEqual((a.x, a.y), (b.x, b.y))
        self.assertEqual(b.stats.satiety, hunger)
        a._plan_route(self.sim, "town", a.x, 11)
        a.tick(self.sim)
        self.assertIsNone(b.vehicle_driver)
        self.assertNotEqual((b.x,b.y),(a.x,a.y))
        self.assertNotEqual((b.x,b.y),(14,12))
        self.assertTrue(world.walkable(b.scene,b.x,b.y))
        self.assertEqual(a.passengers, [])

    def test_capacity_self_and_distance(self):
        a = self.on_road()
        self.sim.set_travel_mode(a, "car")
        with self.assertRaises(ValueError):
            self.sim.board_car(a, a)
        for b in self.sim.players[1:4]:
            self.on_road(b)
            self.sim.board_car(b, a)
        last = self.on_road(self.sim.players[4])
        with self.assertRaises(ValueError):
            self.sim.board_car(last, a)
        self.assertEqual(len(a.passengers), 3)

    def test_empty_fuel_walks_without_negative_money(self):
        p = self.on_road()
        self.sim.set_travel_mode(p, "car")
        p.stats.money = 0
        p._plan_route(self.sim, "town", 24, 12)
        p.tick(self.sim)
        self.assertEqual(p.x, 3)
        self.assertEqual(p.stats.money, 0)
        self.assertEqual(self.sim.vehicles[0].x, 2)

    def test_gate_drops_passengers_and_releases_shared_car(self):
        a, b = self.sim.players[:2]
        self.on_road(a)
        self.on_road(b)
        self.sim.set_travel_mode(a, "car")
        self.sim.board_car(b, a)
        a._plan_route(self.sim, "house", 20, 12)
        a.tick(self.sim)
        self.assertEqual(a.scene, "house")
        self.assertEqual(b.scene, "town")
        self.assertIsNone(b.vehicle_driver)
        self.assertIsNone(self.sim.vehicles[0].driver_id)
        self.assertIsNone(a.car_id)

    def test_own_bedroom_and_separated_rooms(self):
        a, b = self.sim.players[:2]
        self.assertFalse(world.same_place(a, b))
        a.x, a.y = b.x, b.y
        a.begin_activity("sleep", self.sim)
        from engine.interaction_points import bed_points
        self.assertEqual(a.path[-1], bed_points(a)[0][0])

    def test_every_walkable_tile_is_reachable(self):
        for scene, start in (("house", world.BEDROOM_ANCHORS[0]), ("town", (2, 12))):
            for y in range(world.SIZE):
                for x in range(world.SIZE):
                    if world.walkable(scene, x, y) and (x, y) != start:
                        self.assertTrue(world.bfs_path(scene, *start, x, y), (scene, x, y))

    def test_five_bedrooms_have_identical_rectangular_floor_space(self):
        self.assertEqual(len(world.BEDROOM_RECTS), 5)
        self.assertEqual(len(world.find_tiles("house", "h")), 120)
        for rect, anchor in zip(world.BEDROOM_RECTS, world.BEDROOM_ANCHORS):
            x0, y0, x1, y1 = rect
            self.assertEqual((x1-x0+1, y1-y0+1), (6, 4))
            self.assertTrue(x0 <= anchor[0] <= x1 and y0 <= anchor[1] <= y1)
            for y in range(y0, y1+1):
                for x in range(x0, x1+1):
                    self.assertEqual(world.tile("house", x, y), "h")
            self.assertTrue(world.bfs_path("house", *anchor, 25, 12))

    def test_clinic_and_fields_are_separated_by_road(self):
        self.assertEqual(len(world.find_tiles("town", "B")), 18)
        self.assertEqual(len(world.find_tiles("town", "F")), 18)
        for x in range(19, 25):
            self.assertEqual(world.tile("town", x, 21), "B")
            self.assertEqual(world.tile("town", x, 22), "#")
            self.assertEqual(world.tile("town", x, 23), "F")
        self.assertEqual(world.tile("town", 18, 24), "#")

    def test_model_gender_survives_identity_snapshot_and_prompt(self):
        profile = ApiProfile(id="stub", name="stub", api_key="fake", default_model="stub")
        for supplied, expected in (("女", "女"), ("male", "男")):
            with self.subTest(gender=supplied):
                import json
                answer = json.dumps({"name": "小禾", "color": "#abcdef", "gender": supplied})
                with patch("engine.brain.chat", return_value=answer):
                    ident = asyncio.run(brain.choose_identity_llm(profile, "stub", []))
                self.assertEqual(ident["gender"], expected)
                self.sim.apply_identity(self.p, ident["name"], ident["color"], gender=ident["gender"])
                self.assertEqual(self.p.snapshot().gender, expected)
                self.assertIn("性别"+expected, brain.build_user_prompt(self.p, [], "08:00"))

    def test_identity_phase_keeps_gender_when_model_names_collide(self):
        self.sim.profile = ApiProfile(id="stub", name="stub", api_key="fake", default_model="stub")
        for p in self.sim.players:p.identity_done=False
        identity = {"name": "小禾", "color": "#abcdef", "gender": "女"}
        with patch("engine.brain.choose_identity_llm", new_callable=AsyncMock, return_value=identity):
            asyncio.run(self.sim.run_identity_phase())
        self.assertTrue(all(p.gender == "女" and p.identity_done for p in self.sim.players))
        self.assertEqual(len({p.name for p in self.sim.players}), 5)

    def test_manual_player_joins_identity_phase_by_default(self):
        self.sim.profile = ApiProfile(id="stub", name="stub", api_key="fake", default_model="stub")
        new = self.sim.add_player_manual("居民6", "#7f8c8d", "🙂")
        self.assertFalse(new.identity_done)          # 默认参与身份自选
        fixed = self.sim.add_player_manual("小固", "#123456", "🙂", join_identity=False)
        self.assertTrue(fixed.identity_done)         # 显式关闭则固定身份
        self.assertTrue(all(p.identity_done for p in self.sim.players if p is not new))
        identity = {"name": "小新", "color": "#abcdef", "gender": "女"}
        with patch("engine.brain.choose_identity_llm", new_callable=AsyncMock, return_value=identity) as choose:
            asyncio.run(self.sim.run_identity_phase())
        # 手动居民（默认勾选）被模型询问并改名；固定身份居民保持原名未被询问
        self.assertEqual(new.name, "小新")
        self.assertEqual(new.gender, "女")
        self.assertTrue(new.identity_done)
        self.assertEqual(fixed.name, "小固")
        self.assertEqual(choose.call_count, 1)

    def test_gender_closed_choices_in_initial_prompt_without_correction(self):
        profile=ApiProfile(id='stub',name='stub',api_key='fake',default_model='stub')
        invalid='{"name":"小禾","gender":"非二元","color":"#abcdef"}'
        with patch('engine.brain.chat',return_value=invalid) as chat:
            self.assertIsNone(asyncio.run(brain.choose_identity_llm(profile,'stub',[])))
        self.assertEqual(chat.call_count,1)
        self.assertIn('男/女',chat.call_args.args[2])
        self.assertIn('「男」「女」',chat.call_args.args[3])
        self.sim.apply_identity(self.p,'小禾','#abcdef',gender='非二元')
        self.assertEqual(self.p.snapshot().gender,'男')

    def test_model_can_choose_purchase_ride_and_mode(self):
        d = brain.parse_decision('{"action":"work","travel_mode":"bicycle"}')
        self.assertEqual(d.travel_mode, "bicycle")
        self.sim._apply_decision(self.p, d)
        self.assertEqual(self.p.travel_mode, "bicycle")
        self.p.stats.money = 1000
        self.p.scene='town';self.p.x,self.p.y=next(q for q in world.find_tiles('town','S') if world.walkable('town',*q))
        self.sim._apply_decision(self.p, Decision(action="buy_car"))
        self.assertEqual(len(self.sim.vehicles), 2)
        prompt = brain.build_user_prompt(self.p, [], "08:00")
        self.assertIn("buy_car", prompt)
        self.assertIn("ride", prompt)

    def test_mock_model_purchase_runs_through_async_decision(self):
        self.p.stats.money = 1000
        self.p.scene='town';self.p.x,self.p.y=next(q for q in world.find_tiles('town','S') if world.walkable('town',*q))
        self.sim.profile = ApiProfile(id="stub",name="stub",api_key="fake",default_model="stub-model")
        with patch("engine.brain.chat",return_value='{"action":"buy_car","reason":"想买自己的车"}') as chat:
            asyncio.run(self.sim._decide_one(self.p))
        chat.assert_called_once()
        self.assertEqual(self.p.stats.money,0)
        self.assertTrue(any(v.owner_id==self.p.pid for v in self.sim.vehicles))

    def test_disable_dialogue_uses_rule_lines_without_model_call(self):
        self.sim.profile=ApiProfile(id="stub",name="stub",api_key="fake",default_model="stub")
        self.sim.settings.dialogue_generation=False
        conv=Conversation(self.sim.players[0],self.sim.players[1],"chat",self.sim)
        with patch("engine.brain.conv_turn_llm",new_callable=AsyncMock) as generate:
            asyncio.run(conv.advance())
        generate.assert_not_called()
        self.assertTrue(conv.history)

    def test_identity_does_not_advance_clock(self):
        self.sim.identity_phase = True
        asyncio.run(self.sim.step_tick())
        self.assertEqual(self.sim.global_tick, 0)

    def test_eating_out_uses_restaurant_and_building_does_not_become_walkthrough(self):
        self.sim.tick=72
        p=self.on_road();p.x=18
        self.sim._apply_decision(p,Decision(action='eat_out'))
        for _ in range(100):
            p.tick(self.sim)
            if p.state=='idle':break
        self.assertEqual(world.tile(p.scene,p.x,p.y),'R')
        self.assertEqual(p.stats.money,90)
        self.assertEqual(world.FACILITY_CHARS['R']['name'],'饭店')
        self.assertFalse(world.walkable('town',20,8))
        self.assertTrue(world.walkable('town',20,10))

    def test_save_cancellation_keeps_conversation_turn_and_affinity_request(self):
        self.sim.profile=ApiProfile(id='stub',name='stub',api_key='fake',default_model='stub')
        a,b=self.sim.players[:2]
        conv=Conversation(a,b,'chat',self.sim)
        with patch('engine.brain.conv_turn_llm',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):asyncio.run(conv.advance())
        self.assertEqual((conv.phase,conv.elapsed,conv.turn_count,conv.history),('opening',0,0,[]))
        request=(a.pid,b.pid,'chat');self.sim.affinity_queue=[request]
        with patch('engine.brain.affinity_change_llm',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):asyncio.run(self.sim._drain_affinity())
        self.assertEqual(self.sim.affinity_queue,[request])
        self.assertFalse(self.sim._affinity_busy)

    def test_cooking_makes_food_and_night_bonus_adds(self):
        p = self.p
        p._start_local("cook", self.sim)
        for _ in range(100):
            if p.state=='doing':break
            p.tick(self.sim)
        for _ in range(4):
            p.tick(self.sim)
        self.assertEqual(self.sim.meal_until,60)
        p.hour = 23
        p.stats.energy = 20
        p._start_local("sleep", self.sim)
        for _ in range(100):
            if p.state=='doing':break
            p.tick(self.sim)
        p.stats.energy=20
        p.tick(self.sim)
        self.assertEqual(p.stats.energy,30)

    def test_public_decoration_and_item_payment(self):
        p = self.p
        p.items["plant"] = 1
        self.assertTrue(p.begin_activity("decorate_common",self.sim))
        for _ in range(30):
            p.tick(self.sim)
        self.assertEqual(p.items["plant"],0)
        self.assertGreater(self.sim.decor_common,0)
        before = p.stats.money
        p._start_local("buy_flower",self.sim)
        self.assertEqual(p.stats.money,before-12)
        self.assertEqual(p.items["flower"],1)

    def test_both_scenes_in_model_menu(self):
        prompt = brain.build_user_prompt(self.p,[],"08:00")
        self.assertIn("action=visit",prompt)
        self.assertIn("- sleep:",prompt)

    def test_two_day_rule_simulation(self):
        import random
        random.seed(20261030)                      # 固定种子：不依赖其他测试消耗的随机数
        self.sim.random_events.rng.seed(20261030)
        from engine.interaction_points import active_slot
        async def run():
            for _ in range(288):
                await self.sim.step_tick()
            for p in self.sim.players:
                slot = active_slot(p)              # 正在使用家具使用位（沙发/床/浴室等）时
                on_slot = slot and (p.x, p.y) == tuple(slot['position'])  # 站在家具格上是合法状态
                if not on_slot:
                    self.assertTrue(world.walkable(p.scene,p.x,p.y))
                for value in (p.stats.hp,p.stats.energy,p.stats.satiety,p.stats.mood):
                    self.assertTrue(0<=value<=100)
                self.assertGreaterEqual(p.stats.money,0)
        asyncio.run(run())

    def test_pause_cancelled_decision_returns_to_idle(self):
        async def run():
            async def slow_decision(_):
                await asyncio.sleep(60)
            self.sim._decide_batch = slow_decision
            self.sim.players[0].state = "deciding"
            self.sim.start()
            await asyncio.sleep(0)
            self.sim.pause()
            await asyncio.sleep(0)
            self.assertEqual(self.sim.players[0].state, "idle")
        asyncio.run(run())


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(server, "CONFIG_PATH", Path(self.temp.name)/"config.json")
        self.patch.start()
        server.config = AppConfig()
        server.sim = Simulation()
        server._identity_started = False
        server._identity_task = None
        server._control_lock = asyncio.Lock()
        world.reset_maps()
        self.client = TestClient(server.app)
        self.client.__enter__()

    def tearDown(self):
        server.sim.stop()
        self.client.__exit__(None, None, None)
        self.patch.stop()
        self.temp.cleanup()

    def test_no_keys_returned(self):
        data = dict(name="demo", base_url="https://example.invalid/v1", api_key="secret-test-key")
        response = self.client.post("/api/profiles", json=data)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("secret-test-key", self.client.get("/api/profiles").text)

    def test_event_categories_are_separate_and_resident_records_have_no_write_api(self):
        p=server.sim.players[0]
        server.sim.log('social','真实游戏经历',[p.pid])
        server.sim.log('memory','整合了记忆',[p.pid],scope='user')
        games=self.client.get('/api/events?scope=game').json()
        notices=self.client.get('/api/events?scope=user').json()
        self.assertTrue(games);self.assertTrue(notices)
        self.assertTrue(all(e['scope']=='game' for e in games))
        self.assertTrue(all(e['scope']=='user' for e in notices))
        self.assertFalse(any('整合了记忆' in m.content for m in p.memory.items))
        paths=self.client.get('/openapi.json').json()['paths']
        self.assertFalse(any('computer' in route or 'transfer' in route for route in paths))

    def test_save_reset_load_branch_and_memory_isolation(self):
        p=server.sim.players[0]
        p.gender='女';p.stats.money=456;p.remember('旧档独有经历',8)
        old=server.sim.save_id
        response=self.client.post('/api/control',json={'cmd':'reset'})
        self.assertEqual(response.status_code,200)
        new=server.sim.save_id
        self.assertNotEqual(old,new)
        self.assertTrue(all(not any(p.memory.counts().values()) for p in server.sim.players))
        self.assertEqual(server.sim.clock,'08:00')
        r=self.client.post('/api/saves/'+old+'/load')
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn(server.sim.save_id,(old,new))
        p=server.sim.players[0]
        self.assertEqual((p.gender,p.stats.money),('女',456))
        self.assertTrue(any(m.content=='旧档独有经历' for m in p.memory.items))
        self.assertEqual(len(self.client.get('/api/saves').json()['items']),3)
        self.assertEqual(self.client.post('/api/saves/current').status_code,200)

    def test_save_metadata_favorite_rename_delete_and_current_protection(self):
        sid=self.client.post('/api/saves/current').json()['id']
        self.assertNotEqual(sid,server.sim.save_id)
        self.assertEqual(self.client.put(f'/api/saves/{sid}/metadata',json={'name':'我的第一天','favorite':True}).status_code,200)
        first=self.client.get('/api/saves').json()['items'][0]
        self.assertEqual((first['id'],first['name'],first['favorite']),(sid,'我的第一天',True))
        self.client.post('/api/saves/current')
        self.assertEqual(server.save_store().metadata(sid)['name'],'我的第一天')
        self.assertEqual(self.client.delete('/api/saves/'+server.sim.save_id).status_code,409)
        self.assertEqual(self.client.delete('/api/saves/'+sid).status_code,200)
        self.assertFalse(server.save_store().folder(sid).exists())

    def test_full_context_restore_uses_saved_prompt_settings_bindings_and_memory(self):
        server.sim.system_prompt='存档中的角色设定'
        server.sim.settings.temperature=.42
        server.sim.model_of={'resident-1':'saved-model'}
        server.sim.binding_of={'resident-1':''}
        server.sim.players[0].remember('保存之前的约定',9)
        sid=self.client.post('/api/saves/current').json()['id']
        server.sim.system_prompt='之后改动的设定';server.sim.settings.temperature=.9
        server.sim.players[0].remember('保存之后的经历',8)
        self.assertEqual(self.client.post('/api/saves/'+sid+'/load').status_code,200)
        self.assertEqual(server.sim.system_prompt,'存档中的角色设定')
        self.assertEqual(server.sim.settings.temperature,.42)
        self.assertEqual(server.sim.players[0].model,'saved-model')
        texts=[m.content for m in server.sim.players[0].memory.items]
        self.assertIn('保存之前的约定',texts);self.assertNotIn('保存之后的经历',texts)
        self.assertEqual(server.config.system_prompt,'存档中的角色设定')

    def test_replay_frame_continuation_restores_that_frame_without_future_memory(self):
        self.client.post('/api/control',json={'cmd':'step'})
        rid=server.sim.recorder.id
        frames=self.client.get('/api/replays').json()[0]['frames']
        index=frames-1
        frame=self.client.get(f'/api/replays/{rid}/frames/{index}').json()
        self.assertTrue(frame['can_continue'])
        self.assertEqual(frame['frame']['checkpoint']['state']['global_tick'],1)
        sid=self.client.post('/api/saves/current').json()['id']
        bounded=self.client.get(f'/api/saves/{sid}/frames/0').json()['total']
        server.sim.players[0].remember('未来才会发生的事',9)
        self.client.post('/api/control',json={'cmd':'step'})
        self.assertEqual(self.client.get(f'/api/saves/{sid}/frames/0').json()['total'],bounded)
        result=self.client.post(f'/api/replays/{rid}/frames/{index}/continue')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(server.sim.global_tick,1)
        self.assertNotIn('未来才会发生的事',[m.content for m in server.sim.players[0].memory.items])
        self.assertFalse(server.sim.running)

    def test_snapshot_without_recording_can_replay_and_resume(self):
        sid=self.client.post('/api/saves/current').json()['id']
        result=self.client.get(f'/api/saves/{sid}/frames/0')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['total'],1)
        self.assertTrue(result.json()['can_continue'])
        self.assertEqual(self.client.post(f'/api/saves/{sid}/frames/0/continue').status_code,200)

    def test_snapshot_restores_live_conversation_and_excludes_api_credentials(self):
        import json
        a,b=server.sim.players[:2]
        conv=Conversation(a,b,'chat',server.sim)
        conv.phase='b_turn';conv.history=[(a.name,'昨天一起做饭很开心')]
        conv.elapsed=4;conv.turn_count=2
        server.sim.conversations=[conv];a._conversation=b._conversation=conv
        profile=ApiProfile(id='saved',name='saved',api_key='private-key-never-in-saves',default_model='saved-model')
        server.config.profiles=[profile];server.config.active_profile_id=profile.id
        server._sync_from_config()
        sid=self.client.post('/api/saves/current').json()['id']
        data=server.save_store().read(sid)
        self.assertNotIn(profile.api_key,json.dumps(data))
        self.assertEqual(self.client.post(f'/api/saves/{sid}/load').status_code,200)
        restored=server.sim.conversations[0]
        self.assertEqual((restored.phase,restored.elapsed,restored.turn_count),('b_turn',4,2))
        self.assertEqual(restored.history,conv.history)
        self.assertIs(server.sim.players[0]._conversation,restored)
        self.assertEqual(server.sim.profile.api_key,profile.api_key)

    def test_failed_reset_keeps_existing_world(self):
        old=server.sim
        old.players[0].remember('保存失败也不能丢失',8)
        with patch('engine.saves.SaveStore.checkpoint',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.client.post('/api/control',json={'cmd':'reset'})
        self.assertIs(server.sim,old)
        self.assertTrue(any(m.content=='保存失败也不能丢失' for m in old.players[0].memory.items))

    def test_cross_origin_and_host_rejected(self):
        self.assertEqual(self.client.post("/api/control", json={"cmd":"reset"}, headers={"Origin":"https://bad.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/state", headers={"Host":"bad.example"}).status_code, 400)
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/ws", headers={"Origin":"https://bad.example"}):
                pass

    def test_bad_bindings_and_unsafe_player_fields(self):
        self.assertEqual(self.client.put("/api/config",json={"player_bindings":{"unknown":{}}}).status_code,400)
        self.assertEqual(self.client.post("/api/players",json={"name":"x", "color":"red;position:fixed"}).status_code,422)
        self.assertEqual(self.client.post("/api/players",json={"name":"<script>"}).status_code,422)
        self.assertEqual(self.client.get("/api/events?limit=-1").status_code,422)

    def test_transport_purchase_and_invalid_modes(self):
        p = server.sim.players[0]
        self.assertEqual(self.client.put(f"/api/players/{p.pid}/travel",json={"mode":"bicycle"}).status_code,200)
        self.assertEqual(self.client.put(f"/api/players/{p.pid}/travel",json={"mode":"plane"}).status_code,422)
        self.assertEqual(self.client.post(f"/api/players/{p.pid}/car").status_code,409)
        p.stats.money = 1000
        self.assertEqual(self.client.post(f"/api/players/{p.pid}/car").status_code,200)
        self.assertEqual(p.stats.money,0)

    def test_reset_restores_maps_and_vehicles(self):
        x, y = world.BEDROOM_ANCHORS[0]
        world.HOUSE[y][x] = "X"
        self.client.post("/api/control",json={"cmd":"reset"})
        self.assertEqual(world.HOUSE[y][x],"h")
        self.assertEqual(len(server.sim.vehicles),1)

    def test_reset_preserves_default_resident_model_bindings(self):
        profile=ApiProfile(id="test",name="test",api_key="fake",default_model="default")
        server.config.profiles=[profile]
        pid=server.sim.players[0].pid
        server.config.player_bindings={pid:{"profile_id":"test","model":"chosen"}}
        self.client.post("/api/control",json={"cmd":"reset"})
        self.assertEqual(server.sim.players[0].pid,pid)
        self.assertEqual(server.sim.model_of[pid],"chosen")
        self.assertEqual(server.sim.players[0].model,"chosen")

    def test_single_step_initializes_residents(self):
        r=self.client.post("/api/control",json={"cmd":"step"})
        self.assertEqual(r.status_code,200)
        self.assertTrue(all(p.identity_done for p in server.sim.players))
        self.assertEqual(server.sim.global_tick,1)

    def test_static_files_and_openapi(self):
        for path in ("/", "/static/app.js", "/static/base.css", "/static/pixel-scene.js", "/static/pixel-theme.css", "/static/favicon.svg", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code,200,path)

    def test_paused_websocket_publishes_travel_changes_and_maps(self):
        p = server.sim.players[0]
        with self.client.websocket_connect("/ws") as ws:
            first=ws.receive_json()
            self.assertIn("maps",first)
            self.client.put(f"/api/players/{p.pid}/travel",json={"mode":"bicycle"})
            updated=ws.receive_json()
            self.assertEqual(updated["players"][0]["travel_mode"],"bicycle")
            self.assertEqual(updated["tick"],0)

    def test_ride_endpoint_and_duplicate_car_purchase(self):
        a,b=server.sim.players[:2]
        a.driver_license=True
        a.scene,b.scene="town","town"
        a.x,b.x,a.y,b.y=2,2,12,12
        self.client.put(f"/api/players/{a.pid}/travel",json={"mode":"car"})
        self.assertEqual(self.client.put(f"/api/players/{b.pid}/ride",json={"driver_id":a.pid}).status_code,200)
        self.assertEqual(b.vehicle_driver,a.pid)
        self.assertEqual(self.client.put(f"/api/players/{b.pid}/ride",json={"driver_id":None}).status_code,200)
        self.assertIsNone(b.vehicle_driver)
        a.stats.money=2000
        self.client.post(f"/api/players/{a.pid}/car")
        self.assertEqual(self.client.post(f"/api/players/{a.pid}/car").status_code,409)
        self.assertEqual(a.stats.money,1000)

    def test_memory_pagination_and_deep_archive_survives_reset(self):
        p=server.sim.players[0]
        for i in range(800):   # 10:1压缩比下需更多条目才能推进到三级
            p.remember(f"原始经历{i}", 4)
        asyncio.run(server.sim.maintain_memories())
        deep_ids={m.id for m in p.memory.page(3,limit=None)}
        self.assertTrue(deep_ids)
        result=self.client.get(f"/api/players/{p.pid}/memories?tier=3&limit=2").json()
        self.assertEqual(len(result["items"]),2)
        self.assertTrue(result["has_more"])
        self.assertEqual(self.client.get(f"/api/players/{p.pid}/memories?tier=4").status_code,422)
        response=self.client.post("/api/control",json={"cmd":"reset"}).json()
        restored=server.sim.players[0]
        self.assertEqual(restored.memory.counts(),{'1':0,'2':0,'3':0})
        archived=server.save_store().read(response['archived_save_id'])
        self.assertEqual({m['id'] for m in archived['memories'][p.pid] if m['tier']==3},deep_ids)

    def test_automatic_recording_api_and_replay_survives_reset(self):
        self.assertEqual(self.client.get('/api/replays').json(),[])
        self.client.post('/api/control',json={'cmd':'step'})
        archive=self.client.get('/api/replays').json()[0]
        frame=self.client.get(f"/api/replays/{archive['id']}/frames/0")
        self.assertEqual(frame.status_code,200)
        self.assertEqual(frame.json()['frame']['clock'],'08:00')
        self.assertIn('maps',frame.json()['frame'])
        self.assertEqual(server.sim.global_tick,1)
        self.client.post('/api/control',json={'cmd':'reset'})
        self.assertEqual(self.client.get(f"/api/replays/{archive['id']}/frames/0").status_code,200)
        self.assertEqual(server.sim.global_tick,0)
        self.assertEqual(self.client.put('/api/config',json={'decision_timeout':240}).status_code,200)
        self.assertEqual(server.sim.settings.decision_timeout,240)

    def test_running_simulation_disallows_manual_step(self):
        self.client.post("/api/control",json={"cmd":"start"})
        self.assertEqual(self.client.post("/api/control",json={"cmd":"step"}).status_code,409)
        self.client.post("/api/control",json={"cmd":"pause"})


if __name__ == "__main__":
    unittest.main()
