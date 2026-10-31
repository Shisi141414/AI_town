"""机制面板保存被拒时：改动完整落盘留痕（data/rejected-mechanics-*.json）。"""
import glob
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
import server


class RejectedMechanicsDumpTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        # 取当前机制作为基底（只动会被拒的字段）
        base = self.client.get('/api/mechanics').json()
        self.rules = base['rules']
        self.hours = base['hours']
        self.activities = base['activities']
        self.template = base.get('prompt_template') or ''

    def _put(self, template=None, rules=None):
        return self.client.put('/api/mechanics', json={
            'rules': rules or self.rules,
            'hours': self.hours,
            'activities': self.activities,
            'prompt_template': template if template is not None else self.template,
        })

    def test_rejected_placeholder_dumps_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd(); os.chdir(tmp)
            try:
                bad = self.template + ' {not_allowed_field}'
                r = self._put(template=bad)
                self.assertEqual(r.status_code, 400)
                files = glob.glob('data/rejected-mechanics-*.json')
                self.assertEqual(len(files), 1)
                snap = json.load(open(files[0], encoding='utf-8'))
                self.assertIn('占位符', snap['reason'])
                self.assertEqual(snap['prompt_template'], bad)   # 完整改动快照可找回
                self.assertIn('rules', snap)
            finally:
                os.chdir(old)

    def test_dump_keeps_only_recent_20(self):
        # 直接验证留痕函数：超过 20 份时删最老
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd(); os.chdir(tmp)
            try:
                from engine.models import Settings
                for i in range(23):
                    server._dump_rejected_mechanics(
                        server.MechanicsUpdate(rules={}, hours={}, activities={},
                                               prompt_template=f'v{i}'), f'原因{i}')
                files = sorted(glob.glob('data/rejected-mechanics-*.json'))
                self.assertEqual(len(files), 20)
                first = json.load(open(files[0], encoding='utf-8'))
                self.assertEqual(first['prompt_template'], 'v3')   # v0~v2 已被清理
            finally:
                os.chdir(old)


if __name__ == '__main__':
    unittest.main()
