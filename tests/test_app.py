import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class GameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.temp.name) / 'test.db'
        app.ADMIN_KEY = 'test-admin-key'
        cls.admin_token = 'test-admin-session'
        app.ADMIN_SESSIONS[cls.admin_token] = app.time.time() + 3600
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, data=None, token='', status=200, admin=True):
        conn = http.client.HTTPConnection(*self.server.server_address)
        conn.request('GET' if data is None else 'POST', path,
                     None if data is None else json.dumps(data),
                     {'Authorization': token, 'Content-Type': 'application/json',
                      'X-Admin-Token': self.admin_token if admin else ''})
        response = conn.getresponse()
        raw = response.read()
        self.assertEqual(response.status, status, raw)
        conn.close()
        return json.loads(raw)

    def test_admin_boundaries(self):
        self.request('/api/quizzes', admin=False, status=403)
        self.request('/api/quizzes', {'title': 'Unauthorized'}, admin=False, status=403)
        self.request('/api/games', {'quiz_id': 'anything'}, admin=False, status=403)
        self.request('/api/admin/login', {'key': 'wrong'}, admin=False, status=403)
        login = self.request('/api/admin/login', {'key': 'test-admin-key'}, admin=False)
        conn = http.client.HTTPConnection(*self.server.server_address)
        headers = {'X-Admin-Token': login['token'], 'Content-Type': 'application/json'}
        conn.request('GET', '/api/quizzes', headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        conn.request('POST', '/api/admin/logout', '{}', headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        conn.request('GET', '/api/quizzes', headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.close()

    def test_complete_game_all_question_types(self):
        questions = [
            dict(type='single', text='2 + 2?', options=['4', '5'], answers=[0], seconds=20),
            dict(type='multiple', text='Even numbers?', options=['2', '3', '4'], answers=[0, 2], seconds=20),
            dict(type='boolean', text='Earth is round?', answers=[0], seconds=20),
            dict(type='text', text='Capital of France?', answers=['Paris'], seconds=20),
        ]
        quiz = self.request('/api/quizzes', dict(title='Integration quiz', questions=questions), status=201)
        self.assertTrue(any(q['id'] == quiz['id'] for q in self.request('/api/quizzes')))
        host = self.request('/api/games', {'quiz_id': quiz['id']}, status=201)
        base = '/api/games/' + host['code']
        player = self.request(base + '/join', {'name': 'Ada'}, admin=False)
        self.request('/api/quizzes', token=player['token'], admin=False, status=403)
        self.request('/api/quizzes', {'title': 'Player edit'}, token=player['token'], admin=False, status=403)
        self.request('/api/games', {'quiz_id': quiz['id']}, token=player['token'], admin=False, status=403)
        self.request(base + '/join', {'name': 'ada'}, status=400)
        self.request(base + '/next', {}, player['token'], status=403)
        for index, answer in enumerate([[0], [0, 2], [0], '  PARIS  ']):
            self.request(base + '/next', {}, host['token'])
            state = self.request(base, token=player['token'], admin=False)
            self.assertNotIn('answers', state['question'])
            self.request(base + '/answer', {'index': index - 1, 'answer': answer}, player['token'], status=400)
            self.request(base + '/answer', {'index': index, 'answer': answer}, player['token'], admin=False)
            self.request(base + '/answer', {'index': index, 'answer': answer}, player['token'], status=400)
            state = self.request(base, token=player['token'], admin=False)
            self.assertEqual(state['phase'], 'question')
            self.assertNotIn('correct', state)
            self.request(base + '/next', {}, host['token'], status=400)
            with app.LOCK:
                app.GAMES[host['code']]['deadline'] = 0
            state = self.request(base, token=player['token'], admin=False)
            self.assertEqual(state['phase'], 'results')
            self.assertTrue(state['correct'])
        result = self.request(base + '/next', {}, host['token'])
        self.assertEqual(result['phase'], 'finished')
        self.assertGreater(result['players'][0]['score'], 3500)

    def test_shared_timer_and_speed_scoring(self):
        q = self.request('/api/quizzes', dict(title='Shared clock', questions=[dict(type='boolean', text='True?', seconds=20, answers=[0])]), status=201)
        h = self.request('/api/games', {'quiz_id': q['id']}, status=201)
        base = '/api/games/' + h['code']
        players = [self.request(base + '/join', {'name': name}, admin=False) for name in ('Fast', 'Slow', 'Wrong', 'Missed')]
        self.request(base + '/next', {}, h['token'])
        start = app.GAMES[h['code']]['started']
        with patch('app.time.time', return_value=start + 2):
            first = self.request(base, token=players[0]['token'], admin=False)
            second = self.request(base, token=players[1]['token'], admin=False)
            self.assertEqual(first['index'], second['index'])
            self.assertEqual(first['remaining'], second['remaining'])
            self.assertEqual(first['question'], second['question'])
            self.request(base + '/answer', {'index': 0, 'answer': [0]}, players[0]['token'], admin=False)
        with patch('app.time.time', return_value=start + 10):
            self.request(base + '/answer', {'index': 0, 'answer': [0]}, players[1]['token'], admin=False)
            self.request(base + '/answer', {'index': 0, 'answer': [1]}, players[2]['token'], admin=False)
            hidden = self.request(base, token=players[0]['token'], admin=False)
            self.assertEqual(hidden['phase'], 'question')
            self.assertTrue(all(p['score'] == 0 for p in hidden['players']))
            self.assertNotIn('answers', hidden['question'])
        with patch('app.time.time', return_value=start + 20):
            self.request(base + '/answer', {'index': 0, 'answer': [0]}, players[3]['token'], admin=False, status=400)
            fast = self.request(base, token=players[0]['token'], admin=False)
            slow = self.request(base, token=players[1]['token'], admin=False)
            wrong = self.request(base, token=players[2]['token'], admin=False)
            missed = self.request(base, token=players[3]['token'], admin=False)
            self.assertEqual(fast['phase'], 'results')
            self.assertEqual(fast['points'], 950)
            self.assertEqual(slow['points'], 750)
            self.assertEqual(fast['response_seconds'], 2)
            self.assertEqual(slow['response_seconds'], 10)
            self.assertEqual(wrong['points'], 0)
            self.assertEqual(missed['points'], 0)
            self.assertIsNone(missed['response_seconds'])
            self.assertEqual(fast['players'][0], {'name': 'Fast', 'score': 950})
            self.assertEqual(self.request(base, token=h['token'])['players'], fast['players'])

    def test_timeout_and_invalid_quiz(self):
        self.request('/api/quizzes', {'title': '', 'questions': []}, status=400)
        q = self.request('/api/quizzes', dict(title='Timeout', questions=[dict(type='boolean', text='True?', seconds=5, answers=[0])]), status=201)
        h = self.request('/api/games', {'quiz_id': q['id']}, status=201)
        base = '/api/games/' + h['code']
        p = self.request(base + '/join', {'name': 'Grace'})
        self.request(base + '/next', {}, h['token'])
        with app.LOCK:
            app.GAMES[h['code']]['deadline'] = 0
        self.request(base + '/answer', {'index': 0, 'answer': [0]}, p['token'], status=400)
        self.assertEqual(self.request(base, token=h['token'])['phase'], 'results')
        self.request(base, token='wrong', status=400)


if __name__ == '__main__':
    unittest.main()
