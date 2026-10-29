"""电脑搜索引擎（computer/search）回归测试。

目录结构：一级目录 → 分类内容 → 具体条目（场所/岗位/课程/商品）。
离线运行：只用默认 Simulation，不调用任何模型。
"""
import asyncio
import json
import unittest

from engine import world
from engine.models import Decision
from engine.resident_tools import computer_seat, search_knowledge, use_computer
from engine.simulation import Simulation


class SearchTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.p = self.sim.players[0]

    def test_directory_lists_categories(self):
        r = search_knowledge(self.sim, '')
        cats = r['搜索目录']
        for cat in ('场所与营业时间', '工作岗位', '技能课程', '商品', '生活与急救'):
            self.assertIn(cat, cats)
        # '目录'/'帮助' 同义
        self.assertIn('搜索目录', search_knowledge(self.sim, '帮助'))

    def test_venues_index_has_hours_and_items(self):
        r = search_knowledge(self.sim, '场所')
        venues = r['场所与营业时间']
        self.assertEqual(len(venues), 12)
        cafe = next(v for v in venues if v['名称'] == '咖啡馆')
        self.assertEqual(cafe['代码'], 'C')
        self.assertEqual(cafe['营业时间'], '07:00-22:00')
        self.assertIn('喝咖啡', cafe['场内项目'])

    def test_venue_detail_groups_by_kind(self):
        r = search_knowledge(self.sim, '咖啡馆')
        d = r['场所详情']
        self.assertEqual(d['营业时间'], '07:00-22:00')
        coffee = next(x for x in d['活动服务'] if x['名称'] == '喝咖啡')
        self.assertEqual(coffee['费用元'], 8)
        self.assertEqual(coffee['时长分'], 30)
        # 代码也能查
        self.assertIn('场所详情', search_knowledge(self.sim, 'B'))

    def test_jobs_index_lists_wage_and_skill(self):
        r = search_knowledge(self.sim, '岗位')
        jobs = r['工作岗位']
        chef = next(j for j in jobs if j['岗位'] == '一级厨师')
        self.assertEqual(chef['场所'], '饭店')
        self.assertGreater(chef['时薪元'], 0)
        self.assertIn('烹饪', chef['技能'])
        self.assertIn('Lv2', chef['技能'])
        # 无技能岗也收录
        self.assertTrue(any(j['岗位'] == '一级事务助理' for j in jobs))

    def test_courses_index(self):
        r = search_knowledge(self.sim, '课程')
        courses = r['技能课程']
        self.assertTrue(any('计算机' in c['课程'] for c in courses))
        self.assertTrue(any('驾照' in c['课程'] for c in courses))
        self.assertTrue(all(c['场所'] == '学校' for c in courses))

    def test_goods_index(self):
        r = search_knowledge(self.sim, '商品')
        goods = r['商品']
        flower = next(g for g in goods if g['商品'] == '鲜花')
        self.assertEqual(flower['场所'], '商店')
        self.assertEqual(flower['费用元'], 12)

    def test_life_rules_cover_hospital(self):
        r = search_knowledge(self.sim, '急救')
        text = json.dumps(r, ensure_ascii=False)
        self.assertIn('1200', text)      # 急救费
        self.assertIn('100', text)       # 房费
        text2 = json.dumps(search_knowledge(self.sim, '住院'), ensure_ascii=False)
        self.assertIn('死亡', text2)

    def test_unknown_query_returns_catalog(self):
        r = search_knowledge(self.sim, '不存在的词xyz')
        self.assertIn('提示', r)
        self.assertIn('搜索目录', r)

    def test_specific_name_search_hits_item(self):
        r = search_knowledge(self.sim, '医师')
        hits = r['搜索结果']
        self.assertTrue(any('门诊医师' in str(h.get('名称', '')) for h in hits))

    def test_use_computer_search_operation(self):
        # 玩家在电脑前执行 search：结果进 computer_feedback
        self.p.scene = 'house'
        self.p.x, self.p.y = computer_seat(self.p)
        self.p.computer_operation_done = False
        self.p.computer_feedback = ''
        d = Decision(action='computer', operation='search', query='厨师')
        use_computer(self.sim, self.p, d)
        self.assertTrue(self.p.computer_feedback)
        result = json.loads(self.p.computer_feedback)
        # 具体名称直接命中条目（一级/二级厨师带时薪与技能要求）
        self.assertTrue(any('厨师' in str(h.get('名称', '')) for h in result['搜索结果']))


if __name__ == "__main__":
    unittest.main()
