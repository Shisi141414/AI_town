"""实体占位、动态避让、出入口及长时间运行回归。

2026-10-30 碰撞语义更新（用户要求：通道狭窄，角色互为障碍导致卡死）：
- 走路与寻路只检测静态墙/家具（position_free / travel_path），角色间允许重叠穿行；
- 碰撞检测保留在决策层：活动使用位分配（route）、落点（free_position）只看活动占位
  （并尽量避开人体但不强制）；车辆驾驶仍避让行人。
"""
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
        """所有玩家站在静态合法格上（允许角色间重叠穿行）。"""
        for p in self.sim.players:
            if p.vehicle_driver:continue
            self.assertTrue(self.sim.position_free(p,p.scene,p.x,p.y),(p.name,p.scene,p.x,p.y))
    def assert_no_activity_slot_clash(self):
        """决策层：活动使用位互不冲突（同一格不会被两人同时预约/使用）。"""
        from engine.interaction_points import active_slot
        seen={}
        for p in self.sim.players:
            slot=active_slot(p)
            if not slot:continue
            key=(slot['position'])
            self.assertNotIn(key,seen,(key,seen,p.name))
            seen[key]=p.name
    def test_spawn_more_than_bedrooms_uses_distinct_free_spaces(self):
        before={(p.scene,p.x,p.y) for p in self.sim.players}
        for i in range(4):self.sim.add_player_manual('入住'+str(i),'#abcdef','🙂')
        after={(p.scene,p.x,p.y) for p in self.sim.players}
        self.assertEqual(len(after),len(before)+4)   # 落点尽量不重叠
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
        a.driver_license=True
        a.scene=b.scene='town';a.x,a.y=2,12;b.x,b.y=6,12
        self.sim.set_travel_mode(a,'car');a._plan_route(self.sim,'town',24,12)
        visited=[]
        for _ in range(5):
            a.tick(self.sim);visited.append((a.x,a.y));self.assert_clear()
        self.assertNotIn((b.x,b.y),visited)
        self.assertGreater(a.x,6)
    def test_stale_path_checks_every_step(self):
        # 路径上的格子按静态地图逐格校验（town 每刻一格预算）
        a,b=self.a,self.b;a.scene=b.scene='town';a.x,a.y=3,12;b.x,b.y=5,12
        a.path=[(4,12),(5,12),(6,12)];a.route_goal=(6,12);a.state='moving'
        a.tick(self.sim)
        self.assertEqual((a.x,a.y),(4,12));self.assert_clear()
    def test_walk_through_crowded_corridor_does_not_stall(self):
        # 狭窄通道对向行走：不再互相阻挡，若干刻内各自通过（允许瞬间重叠）
        a,b=self.a,self.b;a.scene=b.scene='house';a.x,a.y=6,8;b.x,b.y=8,8
        a.state=b.state='moving'
        a.route_goal=(9,8);a.path=[(7,8),(8,8),(9,8)]
        b.route_goal=(6,7);b.path=[(7,8),(6,8),(6,7)]
        for i in range(30):
            for p in ((a,b) if i%2 else (b,a)):p.tick(self.sim)
            self.assert_clear()
        self.assertEqual((a.state,b.state),('idle','idle'))
        self.assertGreaterEqual(a.x,8);self.assertLessEqual(b.x,6)
    def test_doorway_can_be_passed_when_idle_resident_stands_there(self):
        # 门口站着人也能直接穿门而过（过去靠让路，现在互不阻挡）
        a,b=self.a,self.b;a.x,a.y=6,8;b.x,b.y=7,8;b.state='idle'
        a.route_goal=(9,8);a.path=[(7,8),(8,8),(9,8)];a.state='moving'
        for _ in range(10):a.tick(self.sim);self.assert_clear()
        self.assertGreaterEqual(a.x,8)
        self.assertEqual(a.state,"idle")
    def test_gate_and_emergency_positions_do_not_overlap(self):
        self.b.scene='house';self.b.x,self.b.y=24,12
        self.a.scene='town';self.a.x,self.a.y=1,12
        self.a._final_goal=(8,8);self.a._final_scene='house'
        self.a._cross_gate(self.sim)
        self.assertEqual(self.a.scene,'house')
        self.assertNotEqual((self.a.x,self.a.y),(self.b.x,self.b.y))   # 落点尽量避开人体
        for p in (self.a,self.b):p.stats.hp=0;p.passive_drain(self.sim)
        self.assert_clear()
    def test_legacy_overlap_is_tolerated_without_changing_identity_or_memory(self):
        # 重叠不再是错误状态：不强制修复，身份与记忆不变
        self.a.x,self.a.y=self.b.x,self.b.y
        ids=[p.pid for p in self.sim.players];counts=[p.memory.counts() for p in self.sim.players]
        self.sim.repair_positions();self.assert_clear()
        self.assertEqual(ids,[p.pid for p in self.sim.players]);self.assertEqual(counts,[p.memory.counts() for p in self.sim.players])
    def test_activity_slots_do_not_clash_when_two_cook(self):
        # 决策层碰撞：两人同时做饭分到不同的灶位
        from engine.interaction_points import route
        self.a.x,self.a.y,self.a.scene=12,4,'house'
        self.b.x,self.b.y,self.b.scene=12,5,'house'
        self.assertTrue(route(self.sim,self.a,'cook'))
        self.assertTrue(route(self.sim,self.b,'cook'))
        self.assertNotEqual(self.a.interaction_slot['position'],self.b.interaction_slot['position'])
        self.assert_no_activity_slot_clash()

    def test_long_offline_run_remains_collision_free(self):
        random.seed(771)
        async def run():
            for _ in range(200):
                await self.sim.step_tick();self.assert_clear()
            self.assert_no_activity_slot_clash()
        asyncio.run(run())
