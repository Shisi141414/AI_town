"""重置只换世界不换机制：机制面板配置（含提示词模板/活动编辑器/场所参数）继承旧世界。"""
import unittest

import server
from engine.simulation import Simulation


class ResetCarryOverTests(unittest.TestCase):
    def test_carry_over_mechanics(self):
        old = Simulation()
        # 模拟用户在机制面板的大改
        old.mechanics['hospital_cost'] = 777
        old.town_config['C']['coffee']['price'] = 8
        old.activity_overrides['coffee'] = {'cost': 30, 'effects': {'energy': 8}}
        old.disabled_activities = ['attack']
        old.custom_activities = {'diary': {'name': '写日记'}}
        old.user_prompt_template = '我的自定义模板 {menu} {town} {memories}'
        old.activity_config['sleep']['duration'] = 9
        old.opening_hours['R'] = (7, 23)
        old.shop_car = {'name': '越野车', 'emoji': '🚙', 'enabled': False}
        old.activity_descriptions['sleep'] = '睡觉睡觉'

        fresh = Simulation()
        server._carry_over_mechanics(old, fresh)

        self.assertEqual(fresh.mechanics['hospital_cost'], 777)
        self.assertEqual(fresh.town_config['C']['coffee']['price'], 8)
        self.assertEqual(fresh.activity_overrides['coffee'], {'cost': 30, 'effects': {'energy': 8}})
        self.assertEqual(fresh.disabled_activities, ['attack'])
        self.assertIn('diary', fresh.custom_activities)
        self.assertIn('自定义模板', fresh.user_prompt_template)
        self.assertEqual(fresh.activity_config['sleep']['duration'], 9)
        self.assertEqual(fresh.opening_hours['R'], (7, 23))
        self.assertFalse(fresh.shop_car['enabled'])
        self.assertEqual(fresh.activity_descriptions['sleep'], '睡觉睡觉')
        # 世界状态是新开局：不该继承的没继承
        self.assertEqual(len(fresh.players), 0)
        self.assertEqual(fresh.relations, {})
        # 深拷贝防共享：改 fresh 不影响 old
        fresh.mechanics['hospital_cost'] = 1
        self.assertEqual(old.mechanics['hospital_cost'], 777)


if __name__ == '__main__':
    unittest.main()
