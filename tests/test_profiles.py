import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import requests
from fastapi.testclient import TestClient
import server
from engine import llm, world
from engine.models import ApiProfile, AppConfig
from engine.simulation import Simulation


class ProfileProbeTests(unittest.TestCase):
    def setUp(self):
        self.profile = ApiProfile(id='probe', name='probe', api_key='private-key',
                                  base_url='https://example.invalid/v1', default_model='reasoning-model')

    def test_probe_uses_realistic_budget_and_configured_wait(self):
        with patch.object(llm, 'chat', return_value='OK') as chat:
            ok, message = llm.test_profile(self.profile, timeout=180, temperature=.7)
        self.assertTrue(ok)
        self.assertIn('reasoning-model', message)
        self.assertEqual(chat.call_args.kwargs['timeout'], 180)
        self.assertEqual(chat.call_args.kwargs['temperature'], .7)
        self.assertEqual(chat.call_args.kwargs['max_tokens'], 2048)

    def test_missing_model_does_not_guess_or_send_request(self):
        self.profile.default_model = ''
        with patch.object(llm, 'chat') as chat:
            ok, message = llm.test_profile(self.profile)
        self.assertFalse(ok)
        self.assertIn('未选择', message)
        chat.assert_not_called()

    def test_timeout_and_protocol_errors_are_distinct_and_secrets_hidden(self):
        for error, expected in [(requests.ConnectTimeout(), '连接模型服务超时'),
                                (requests.ReadTimeout(), '等待模型回答超时'),
                                (requests.ConnectionError(), '无法连接模型服务')]:
            with self.subTest(expected=expected), patch.object(llm.requests, 'post', side_effect=error):
                ok, message = llm.test_profile(self.profile)
                self.assertFalse(ok)
                self.assertIn(expected, message)
        with patch.object(llm, 'chat', side_effect=llm.LLMError('bad private-key response')):
            self.assertNotIn('private-key', llm.test_profile(self.profile)[1])
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': None, 'reasoning_content': 'thinking'}}]}
        with patch.object(llm.requests, 'post', return_value=response):
            ok, message = llm.test_profile(self.profile)
            self.assertFalse(ok)
            self.assertIn('最终回答', message)
            self.assertNotIn('网络', message)


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(server, 'CONFIG_PATH', Path(self.temp.name)/'config.json')
        self.path_patch.start()
        server.config = AppConfig(); server.sim = Simulation()
        server._identity_started = False; server._identity_task = None
        server._control_lock = asyncio.Lock(); world.reset_maps()
        self.client = TestClient(server.app); self.client.__enter__()
        self.body = dict(name='gateway', base_url='https://example.invalid/v1', api_key='private-key', default_model='old-model')
        self.id = self.client.post('/api/profiles', json=self.body).json()['id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.path_patch.stop(); self.temp.cleanup()

    def test_edit_preserves_id_bindings_key_and_checkpoint(self):
        p = server.sim.players[0]
        server.config.player_bindings[p.pid] = dict(profile_id=self.id, model='bound-model')
        body = {**self.body, 'name': 'edited', 'default_model': 'new-model', 'api_key': ''}
        result = self.client.put('/api/profiles/'+self.id, json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['id'], self.id)
        self.assertNotIn('api_key', result.json())
        self.assertEqual(server.config.profiles[0].api_key, 'private-key')
        self.assertEqual(server.config.active_profile_id, self.id)
        self.assertEqual(server.config.player_bindings[p.pid]['model'], 'bound-model')
        self.assertEqual(p.model, 'bound-model')
        context = server.save_store().current()['runtime_context']
        self.assertEqual(context['profiles'][0]['name'], 'edited')
        self.assertNotIn('api_key', context['profiles'][0])
        # Restart applies checkpoint metadata without losing edited values or the local key.
        server.apply_saved_context(server.save_store().current())
        self.assertEqual(server.config.profiles[0].name, 'edited')
        self.assertEqual(server.config.profiles[0].api_key, 'private-key')
        body['api_key'] = 'replacement'
        self.assertEqual(self.client.put('/api/profiles/'+self.id, json=body).status_code, 200)
        self.assertEqual(server.config.profiles[0].api_key, 'replacement')
        self.assertEqual(self.client.put('/api/profiles/missing', json=body).status_code, 404)
        self.assertEqual(self.client.put('/api/profiles/'+self.id, json={**body, 'format': 'bad'}).status_code, 422)

    def test_probe_uses_bound_model_when_default_empty(self):
        server.config.profiles[0].default_model = ''
        server.config.settings.decision_timeout = 180
        server.config.settings.temperature = .6
        p = server.sim.players[0]
        server.config.player_bindings[p.pid] = dict(profile_id='', model='actual-model')
        with patch.object(server, 'test_profile', return_value=(True, 'OK')) as probe:
            response = self.client.post('/api/profiles/test', json={'id': self.id})
        self.assertTrue(response.json()['ok'])
        self.assertEqual(probe.call_args.args[1:], ('actual-model', 180, .6))
        with patch.object(server, 'test_profile', return_value=(True, 'OK')) as probe:
            self.client.post('/api/profiles/test', json={'id': self.id, 'model': 'explicit'})
        self.assertEqual(probe.call_args.args[1], 'explicit')

    def test_draft_models_can_reuse_key_without_revealing_it(self):
        response = Mock(status_code=200)
        response.json.return_value = {'data': [{'id': 'available-model'}]}
        with patch.object(server.req_lib, 'get', return_value=response) as request:
            result = self.client.post('/api/models', json={**self.body, 'api_key': '', 'profile_id': self.id})
        self.assertEqual(result.json()['models'], ['available-model'])
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer private-key')
        self.assertNotIn('private-key', result.text)


if __name__ == '__main__':
    unittest.main()
