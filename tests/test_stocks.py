"""小镇股市（多股票）与饱食95吃饭限制回归测试。

股市设计锚点：
- 7 只股票（小镇指数 + R/M/C/G/S/B 六场所股），总市值 5000
- 每小时行情：随机波动 ±5%，场所股叠加营业额漂移（>8元/小时看涨），
  并向基准均值回归（总市值长期稳定）
- 交易带 symbol；旧存档的 index 持仓迁移到组合
- 提示词：技能后附特殊活动提示；行情与持仓进提示词

饱食规则：饱食 ≥95（meal_block_satiety）不能吃饭、不能接受赠饭。
"""
import asyncio
import random
import unittest

from engine import world
from engine.models import Decision
from engine.simulation import Simulation
from engine.town_services import STOCKS, prompt_context, update_market


def simulate_hours(sim, hours):
    for _ in range(hours):
        sim.global_tick += 6
        update_market(sim)


class StockMarketTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.p = self.sim.players[0]

    def test_total_market_cap_5000(self):
        cap = sum(r['price'] * r['shares'] for r in self.sim.stocks.values())
        self.assertAlmostEqual(cap, 5000.0)
        self.assertEqual(len(self.sim.stocks), 7)
        for code in ('R', 'M', 'C', 'G', 'S', 'B'):
            self.assertIn(code, self.sim.stocks)

    def test_bigger_swing_than_old(self):
        # 旧版每步最多 ±1.5%；新版 ±5% 噪声 → 至少出现一次 >2% 的变动
        random.seed(7)
        moves = []
        price0 = self.sim.stocks['index']['price']
        for _ in range(40):
            simulate_hours(self.sim, 1)
            price1 = self.sim.stocks['index']['price']
            moves.append(abs(price1 - price0) / price0)
            price0 = price1
        self.assertGreater(max(moves), 0.02)

    def test_mean_reversion_keeps_cap_stable(self):
        random.seed(11)
        simulate_hours(self.sim, 120)          # 五天
        cap = sum(r['price'] * r['shares'] for r in self.sim.stocks.values())
        self.assertGreater(cap, 3500)
        self.assertLess(cap, 6500)

    def test_venue_revenue_lifts_price(self):
        # 饭店持续高营业额 → R 股价显著高于无生意的商店股
        random.seed(3)
        for _ in range(24):
            self.sim.venue_revenue['R'] = self.sim.venue_revenue.get('R', 0) + 40
            simulate_hours(self.sim, 1)
        self.assertGreater(self.sim.stocks['R']['price'], self.sim.stocks['S']['price'])

    def test_buy_sell_with_symbol(self):
        self.p.skills['economics'] = 100
        self.p.stats.money = 200.0
        self.sim.stocks['R']['price'] = 4.0
        self.p.decision = Decision(action='stock', operation='buy', shares=10, symbol='R')
        self.p.service_completed = False
        from engine.town_services import finish
        finish(self.sim, self.p, 'stock')
        self.assertEqual(self.p.stock_portfolio['R']['shares'], 10)
        self.assertAlmostEqual(self.p.stock_portfolio['R']['cost'], 40.0)
        self.assertAlmostEqual(self.p.stats.money, 160.0)
        # 卖出：持仓不足被拒
        self.p.decision = Decision(action='stock', operation='sell', shares=99, symbol='R')
        self.p.service_completed = False
        finish(self.sim, self.p, 'stock')
        self.assertEqual(self.p.stock_portfolio['R']['shares'], 10)
        # 卖一半
        self.sim.stocks['R']['price'] = 6.0
        self.p.decision = Decision(action='stock', operation='sell', shares=5, symbol='R')
        self.p.service_completed = False
        finish(self.sim, self.p, 'stock')
        self.assertEqual(self.p.stock_portfolio['R']['shares'], 5)
        self.assertAlmostEqual(self.p.stats.money, 190.0)

    def test_unknown_symbol_falls_back_to_index(self):
        self.p.skills['economics'] = 100
        self.p.stats.money = 100.0
        self.p.decision = Decision(action='stock', operation='buy', shares=2, symbol='XYZ')
        self.p.service_completed = False
        from engine.town_services import finish
        finish(self.sim, self.p, 'stock')
        self.assertIn('index', self.p.stock_portfolio)
        self.assertEqual(self.p.stock_shares, self.p.stock_portfolio['index']['shares'])

    def test_prompt_shows_market_and_skill_perks(self):
        self.p.skills['economics'] = 100
        self.p.skills['medicine'] = 150
        ctx = prompt_context(self.sim, self.p)
        self.assertIn('小镇股市（代码/名称/现价', ctx)
        self.assertIn('股票', ctx)
        self.assertIn('在家行医', ctx)          # 技能后的特殊活动提示
        # 无技能者不出现提示
        ctx2 = prompt_context(self.sim, self.sim.players[1])
        self.assertIn('可特别进行', ctx2 or 'x') if False else None
        self.assertNotIn('可特别进行', ctx2)

    def test_portfolio_shown_in_prompt(self):
        # 新契约：股市与持仓信息只对具备炒股能力（经济学≥1级）的人显示
        self.p.stock_portfolio['R'] = {'shares': 8, 'cost': 40.0}
        ctx = prompt_context(self.sim, self.p)
        self.assertNotIn('我的股票持仓', ctx)
        self.assertNotIn('小镇股市', ctx)
        self.p.skills['economics'] = 100
        ctx = prompt_context(self.sim, self.p)
        self.assertIn('我的股票持仓', ctx)
        self.assertIn('饭店8份', ctx)
        self.assertIn('小镇股市', ctx)

    def test_revenue_hook_in_begin_activity(self):
        # 咖啡馆消费 8 元 → venue_revenue['C'] 记 8（走完整决策→到场→开始路径）
        self.p.stats.money = 50.0
        self.p.scene = 'town'
        self.p.x, self.p.y = world.nearest_walkable('town', *world.find_tiles('town', 'C')[0])
        self.sim._apply_decision(self.p, Decision(action='coffee', source='llm'))
        for _ in range(200):
            if self.p.state == 'doing':
                break
            self.p.tick(self.sim)
        self.assertEqual(self.p.state, 'doing')
        self.assertAlmostEqual(self.sim.venue_revenue.get('C', 0), 8.0)


class MealSatietyBlockTests(unittest.TestCase):
    def setUp(self):
        world.reset_maps()
        self.sim = Simulation()
        self.sim.spawn_players(3)
        asyncio.run(self.sim.run_identity_phase())
        self.a, self.b = self.sim.players[0], self.sim.players[1]

    def test_receiver_above_95_refuses_meal(self):
        self.a.items['meal'] = 1
        self.b.stats.satiety = 96.0
        self.b.stats.mood = 50.0
        self.a._resolve_give_meal(self.sim, self.b)
        # 饭菜保留、对方无变化
        self.assertEqual(self.a.items['meal'], 1)
        self.assertAlmostEqual(self.b.stats.satiety, 96.0)

    def test_eat_blocked_above_95(self):
        # 走完整路径：到场开始时被守卫拒绝，饭不消耗
        self.a.items['meal'] = 1
        # 99：走到餐桌使用位会因移动掉几点饱食，需保证到场时仍 ≥95
        self.a.stats.satiety = 99.0
        self.a.scene = 'house'
        from engine import world as w
        x, y = w.find_tiles('house', 'D')[0]
        self.a.x, self.a.y = w.nearest_walkable('house', x, y)
        self.sim._apply_decision(self.a, Decision(action='eat', source='llm'))
        for _ in range(200):
            self.a.tick(self.sim)
            if self.a.state in ('idle', 'waiting_model'):
                break
        self.assertNotEqual(self.a.state, 'doing')   # 始终没能开始吃
        self.assertEqual(self.a.items['meal'], 1)    # 饭没消耗

    def test_below_95_still_allowed(self):
        self.a.items['meal'] = 1
        self.a.stats.satiety = 94.0
        self.b.stats.satiety = 30.0
        self.a._resolve_give_meal(self.sim, self.b)
        self.assertEqual(self.a.items['meal'], 0)
        self.assertGreater(self.b.stats.satiety, 30.0)


if __name__ == "__main__":
    unittest.main()
