"""机制记录的持久化、失败留痕与批量删除。"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from engine.mechanics_records import MechanicsRecords


class MechanicsRecordTests(unittest.TestCase):
    def test_store_roundtrip_rename_and_atomic_batch_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MechanicsRecords(Path(folder))
            first = store.create({'rules': {'walk_speed': 2}}, 'failed', '速度无效',
                                 {'rules.walk_speed': 'abc'})
            second = store.create({'rules': {'walk_speed': 1}})
            self.assertIn('未成功：速度无效', first['name'])
            self.assertEqual(store.read(first['id'])['raw_fields']['rules.walk_speed'], 'abc')
            self.assertEqual(len(store.listing()), 2)
            self.assertEqual(store.rename(first['id'], '旧机制')['name'], '旧机制')
            with self.assertRaises(FileNotFoundError):
                store.delete_many([first['id'], '20260101-000000-aaaaaaaaaaaa'])
            self.assertEqual(len(store.listing()), 2)
            self.assertEqual(store.delete_many([first['id'], second['id']]), 2)
            self.assertEqual(store.listing(), [])
            with self.assertRaises(ValueError):
                store.read('../../config')

    def test_rejected_application_appears_in_records(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MechanicsRecords(Path(folder))
            payload = {
                'rules': copy.deepcopy(server.sim.mechanics),
                'hours': copy.deepcopy(server.sim.opening_hours),
                'activities': copy.deepcopy(server.sim.activity_config),
                'prompt_template': '无效模板 {unknown}',
            }
            with patch.object(server, 'mechanics_records', return_value=store), \
                 patch.object(server, '_dump_rejected_mechanics'):
                client = TestClient(server.app)
                response = client.put('/api/mechanics', json=payload)
                self.assertEqual(response.status_code, 400)
                listing = client.get('/api/mechanics/records').json()['items']
                self.assertEqual(len(listing), 1)
                self.assertEqual(listing[0]['status'], 'failed')
                self.assertIn('占位符', listing[0]['name'])
                detail = client.get('/api/mechanics/records/' + listing[0]['id']).json()
                self.assertEqual(detail['payload']['prompt_template'], payload['prompt_template'])
                renamed = client.put('/api/mechanics/records/' + listing[0]['id'],
                                     json={'name': '待修复'}).json()
                self.assertEqual(renamed['name'], '待修复')
                deleted = client.post('/api/mechanics/records/delete-selected',
                                      json={'ids': [listing[0]['id']]}).json()
                self.assertEqual(deleted['deleted'], 1)


if __name__ == '__main__':
    unittest.main()
