import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class GameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.temp.name) / 'test.db'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, data=None, token='', status=200):
        conn = http.client.HTTPConnection(*self.server.server_address)
        conn.request('GET' if data is None else 'POST', path,
                     None if data is None else json.dumps(data),
                     {'Authorization': token, 'Content-Type': 'application/json'})
        response = conn.getresponse()
        raw = response.read()
        self.assertEqual(response.status, status, raw)
        conn.close()
        return json.loads(raw)

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
        player = self.request(base + '/join', {'name': 'Ada'})
        self.request(base + '/join', {'name': 'ada'}, status=400)
        self.request(base + '/next', {}, player['token'], status=403)
        for index, answer in enumerate([[0], [0, 2], [0], '  PARIS  ']):
            self.request(base + '/next', {}, host['token'])
            state = self.request(base, token=player['token'])
            self.assertNotIn('answers', state['question'])
            self.request(base + '/answer', {'index': index - 1, 'answer': answer}, player['token'], status=400)
            self.request(base + '/answer', {'index': index, 'answer': answer}, player['token'])
            self.request(base + '/answer', {'index': index, 'answer': answer}, player['token'], status=400)
            state = self.request(base, token=player['token'])
            self.assertEqual(state['phase'], 'results')
            self.assertTrue(state['correct'])
        result = self.request(base + '/next', {}, host['token'])
        self.assertEqual(result['phase'], 'finished')
        self.assertGreater(result['players'][0]['score'], 3500)

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
