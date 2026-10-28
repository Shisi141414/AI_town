"""实体占位、动态避让、出入口及长时间运行回归。"""
import asyncio
import random
import unittest
from engine import world
from engine.simulation import Simulation

class CollisionTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps();self.sim=Simulation();self.sim.spawn_players(5)
        asyncio.run(self.sim.run_identity_phase())
        self.a,self.b=self.sim.players[:2]
    def tearDown(self):
        self.sim.stop();self.sim.finish_recording()
        for p in self.sim.players:p.memory.close()
    def assert_clear(self):
        seen=set()
        for p in self.sim.players:
            if p.vehicle_driver:continue
            self.assertTrue(self.sim.position_free(p,p.scene,p.x,p.y),(p.name,p.scene,p.x,p.y))
            cell=(p.scene,p.x,p.y);self.assertNotIn(cell,seen);seen.add(cell)
    def test_spawn_more_than_bedrooms_uses_distinct_free_spaces(self):
        for i in range(4):self.sim.add_player_manual('入住'+str(i),'#abcdef','🙂')
        self.assert_clear()
    def test_static_furniture_and_building_roof_block_paths(self):
        self.assertFalse(world.walkable('house',4,2)) # 第一间卧室的床
        self.assertFalse(world.walkable('house',12,12)) # 餐桌
        self.assertFalse(world.walkable('town',3,2)) # 商店屋顶
        for scene in ('house','town'):
            start=world.nearest_walkable(scene,12,12)
            for goal in [(4,2),(12,12),(20,20)]:
                end=world.nearest_walkable(scene,*goal)
                for cell in world.bfs_path(scene,*start,*end):self.assertTrue(world.walkable(scene,*cell))
    def test_fast_car_cannot_tunnel_through_resident(self):
        a,b=self.a,self.b
        a.scene=b.scene='town';a.x,a.y=2,12;b.x,b.y=6,12
        self.sim.set_travel_mode(a,'car');a._plan_route(self.sim,'town',24,12)
        visited=[]
        for _ in range(5):
            a.tick(self.sim);visited.append((a.x,a.y));self.assert_clear()
        self.assertNotIn((b.x,b.y),visited)
        self.assertGreater(a.x,6)
    def test_stale_path_checks_every_step(self):
        a,b=self.a,self.b;a.scene=b.scene='town';a.x,a.y=3,12;b.x,b.y=5,12
        a.path=[(4,12),(5,12),(6,12)];a.route_goal=(6,12);a.state='moving'
        a.tick(self.sim)
        self.assertEqual((a.x,a.y),(4,12));self.assert_clear()
    def test_gate_and_emergency_positions_do_not_overlap(self):
        self.b.scene='house';self.b.x,self.b.y=24,12
        self.a.scene='town';self.a.x,self.a.y=1,12
        self.a._final_goal=(8,8);self.a._final_scene='house'
        self.a._cross_gate(self.sim);self.assertNotEqual((self.a.x,self.a.y),(self.b.x,self.b.y))
        for p in (self.a,self.b):p.stats.hp=0;p.passive_drain(self.sim)
        self.assert_clear()
    def test_legacy_overlap_repaired_without_changing_identity_or_memory(self):
        self.a.x,self.a.y=self.b.x,self.b.y
        ids=[p.pid for p in self.sim.players];counts=[p.memory.counts() for p in self.sim.players]
        self.sim.repair_positions();self.assert_clear()
        self.assertEqual(ids,[p.pid for p in self.sim.players]);self.assertEqual(counts,[p.memory.counts() for p in self.sim.players])
    def test_doorway_can_be_passed_when_idle_resident_yields(self):
        a,b=self.a,self.b;a.x,a.y=6,8;b.x,b.y=7,8;b.state='idle'
        a.route_goal=(9,8);a.path=[(7,8),(8,8),(9,8)];a.state='moving'
        for _ in range(10):a.tick(self.sim);self.assert_clear()
        self.assertGreaterEqual(a.x,8)
        self.assertEqual(a.state,"idle")
    def test_oncoming_residents_pass_single_door_without_overlap(self):
        a,b=self.a,self.b;a.x,a.y=6,8;b.x,b.y=8,8
        a.state=b.state='moving'
        a.route_goal=(9,8);a.path=[(7,8),(8,8),(9,8)]
        b.route_goal=(6,7);b.path=[(7,8),(6,8),(6,7)]
        for i in range(30):
            for p in ((a,b) if i%2 else (b,a)):p.tick(self.sim)
            self.assert_clear()
        self.assertEqual((a.state,b.state),('idle','idle'))
        self.assertGreaterEqual(a.x,8);self.assertLessEqual(b.x,6)

    def test_long_offline_run_remains_collision_free(self):
        random.seed(771)
        async def run():
            for _ in range(200):
                await self.sim.step_tick();self.assert_clear()
        asyncio.run(run())
