"""Socrate: dependency-free local multiplayer quizzes. Run python app.py."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = Path(os.environ.get('SOCRATE_DB', str(ROOT / 'data' / 'quizzes.db')))
GAMES = {}
LOCK = threading.RLock()
ADMIN_SESSIONS = {}
ADMIN_KEY = None


def admin_key():
    global ADMIN_KEY
    if ADMIN_KEY is None:
        ADMIN_KEY = os.environ.get('SOCRATE_ADMIN_KEY')
        if not ADMIN_KEY:
            key_file = DB.parent / 'admin-key.txt'
            key_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with key_file.open('x', opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
                    file.write(secrets.token_urlsafe(24))
            except FileExistsError:
                pass
            ADMIN_KEY = key_file.read_text().strip()
        if not ADMIN_KEY:
            raise ValueError('Admin key must not be empty.')
    return ADMIN_KEY


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS quizzes (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    return db


def validate_quiz(data):
    title = str(data.get('title', '')).strip()[:120]
    questions = data.get('questions', [])
    if not title or not isinstance(questions, list) or not 1 <= len(questions) <= 100:
        raise ValueError('Add a title and between 1 and 100 questions.')
    clean = []
    for q in questions:
        kind = q.get('type')
        text = str(q.get('text', '')).strip()[:1000]
        duration = int(q.get('seconds', 20))
        if kind not in ('single', 'multiple', 'boolean', 'text') or not text or not 5 <= duration <= 120:
            raise ValueError('Each question needs text, a valid type, and 5–120 seconds.')
        options = [str(x).strip()[:300] for x in q.get('options', [])]
        answers = q.get('answers', [])
        if kind == 'text':
            answers = [str(x).strip()[:300] for x in answers if str(x).strip()]
            options = []
            if not answers:
                raise ValueError('Enter at least one accepted answer.')
        else:
            if kind == 'boolean':
                options = ['True', 'False']
            if not 2 <= len(options) <= 6 or not all(options):
                raise ValueError('Enter 2–6 nonempty options.')
            if not answers or any(type(x) is not int or x < 0 or x >= len(options) for x in answers):
                raise ValueError('Select the correct answer(s).')
            answers = sorted(set(answers))
            if kind != 'multiple' and len(answers) != 1:
                raise ValueError('Select exactly one correct answer.')
        clean.append(dict(type=kind, text=text, seconds=duration, options=options, answers=answers))
    return dict(title=title, questions=clean)


def question(game):
    return game['quiz']['questions'][game['index']]


def expire(game):
    if game['phase'] == 'question' and time.time() >= game['deadline']:
        game['phase'] = 'results'
        for token, submission in game['submissions'].items():
            game['players'][token]['score'] += submission['points']


def state(game, token):
    expire(game)
    host = secrets.compare_digest(token, game['host'])
    player = game['players'].get(token)
    if not host and player is None:
        raise ValueError('Join this game first.')
    result = dict(code=game['code'], title=game['quiz']['title'], phase=game['phase'],
                  index=game['index'], total=len(game['quiz']['questions']), host=host,
                  players=sorted([dict(name=p['name'], score=p['score']) for p in game['players'].values()], key=lambda p: -p['score']))
    if game['phase'] in ('question', 'results'):
        q = question(game)
        result.update(question={k: q[k] for k in ('type', 'text', 'options')},
                      remaining=max(0, game['deadline'] - time.time()),
                      duration=q['seconds'],
                      answered=len(game['submissions']), submitted=token in game['submissions'])
        if game['phase'] == 'results':
            result['question']['answers'] = q['answers']
            submission = game['submissions'].get(token, {})
            result.update(correct=submission.get('correct', False),
                          points=submission.get('points', 0),
                          response_seconds=submission.get('response_seconds'))
    return result


class Handler(BaseHTTPRequestHandler):
    def reply(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def require_admin(self):
        token = self.headers.get('X-Admin-Token', '')
        with LOCK:
            valid = ADMIN_SESSIONS.get(token, 0) > time.time()
        if not valid:
            self.reply({'error': 'Admin sign-in required.'}, 403)
        return valid

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/admin/session':
                if self.require_admin():
                    self.reply({'admin': True})
                return
            if path == '/api/quizzes':
                if not self.require_admin():
                    return
                with connect() as db:
                    items = [dict(id=row[0], **json.loads(row[1])) for row in db.execute('SELECT id, body FROM quizzes ORDER BY rowid DESC')]
                return self.reply(items)
            if path.startswith('/api/games/'):
                with LOCK:
                    return self.reply(state(GAMES[path.split('/')[3]], self.headers.get('Authorization', '')))
            files = {'/': ('index.html', 'text/html'), '/play': ('index.html', 'text/html'), '/admin': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if path not in files:
                return self.reply({'error': 'Not found'}, 404)
            name, mime = files[path]
            raw = (ROOT / 'static' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (KeyError, ValueError) as exc:
            self.reply({'error': str(exc) if isinstance(exc, ValueError) else 'Game not found. It may have ended or the server restarted.'}, 400)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 262144:
                raise ValueError('Request is empty or too large.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            path = urlparse(self.path).path
            with LOCK:
                if path == '/api/admin/login':
                    if not secrets.compare_digest(str(data.get('key', '')).encode(), admin_key().encode()):
                        return self.reply({'error': 'Incorrect admin key.'}, 403)
                    now = time.time()
                    for old in list(ADMIN_SESSIONS):
                        if ADMIN_SESSIONS[old] <= now:
                            del ADMIN_SESSIONS[old]
                    token = secrets.token_urlsafe(32)
                    ADMIN_SESSIONS[token] = now + 43200
                    return self.reply({'token': token})
                if path == '/api/admin/logout':
                    ADMIN_SESSIONS.pop(self.headers.get('X-Admin-Token', ''), None)
                    return self.reply({'ok': True})
                if path in ('/api/quizzes', '/api/games') and not self.require_admin():
                    return
                if path == '/api/quizzes':
                    quiz = validate_quiz(data)
                    ident = secrets.token_hex(8)
                    with connect() as db:
                        db.execute('INSERT INTO quizzes VALUES (?, ?)', (ident, json.dumps(quiz)))
                    return self.reply(dict(id=ident, **quiz), 201)
                if path == '/api/games':
                    with connect() as db:
                        row = db.execute('SELECT body FROM quizzes WHERE id=?', (data['quiz_id'],)).fetchone()
                    if row is None:
                        raise ValueError('Quiz not found.')
                    code = str(secrets.randbelow(900000) + 100000)
                    while code in GAMES:
                        code = str(secrets.randbelow(900000) + 100000)
                    token = secrets.token_urlsafe(24)
                    GAMES[code] = dict(code=code, host=token, quiz=json.loads(row[0]), players={}, phase='lobby', index=-1, submissions={})
                    return self.reply(dict(code=code, token=token), 201)
                parts = path.split('/')
                if len(parts) != 5 or parts[1:3] != ['api', 'games']:
                    return self.reply({'error': 'Not found'}, 404)
                game = GAMES[parts[3]]
                expire(game)
                action = parts[4]
                token = self.headers.get('Authorization', '')
                if action == 'join':
                    name = str(data.get('name', '')).strip()[:30]
                    if game['phase'] != 'lobby' or not name:
                        raise ValueError('Enter a nickname and join before the game starts.')
                    if any(p['name'].casefold() == name.casefold() for p in game['players'].values()):
                        raise ValueError('That nickname is already taken.')
                    token = secrets.token_urlsafe(24)
                    game['players'][token] = dict(name=name, score=0)
                    return self.reply(dict(code=game['code'], token=token))
                if action == 'next':
                    if not secrets.compare_digest(token, game['host']):
                        return self.reply({'error': 'Host access required.'}, 403)
                    if game['phase'] == 'question':
                        raise ValueError('Wait for the shared question timer to finish.')
                    elif game['phase'] in ('lobby', 'results'):
                        game['index'] += 1
                        game['submissions'] = {}
                        if game['index'] == len(game['quiz']['questions']):
                            game['phase'] = 'finished'
                        else:
                            game['phase'] = 'question'
                            game['started'] = time.time()
                            game['deadline'] = game['started'] + question(game)['seconds']
                    return self.reply(state(game, token))
                if action == 'answer':
                    if token not in game['players']:
                        return self.reply({'error': 'Player access required.'}, 403)
                    if game['phase'] != 'question' or token in game['submissions'] or data.get('index') != game['index']:
                        raise ValueError('This question is closed or already answered.')
                    q = question(game)
                    answer = data.get('answer')
                    if q['type'] == 'text':
                        correct = isinstance(answer, str) and answer.strip().casefold() in [x.casefold() for x in q['answers']]
                    else:
                        if not isinstance(answer, list) or any(type(x) is not int for x in answer):
                            raise ValueError('Select an answer.')
                        correct = sorted(set(answer)) == q['answers']
                    elapsed = max(0, time.time() - game['started'])
                    points = round(500 + 500 * max(0, 1 - elapsed / q['seconds'])) if correct else 0
                    game['submissions'][token] = dict(correct=correct, points=points, response_seconds=round(elapsed, 2))
                    return self.reply({'accepted': True})
                return self.reply({'error': 'Not found'}, 404)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self.reply({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid request or game not found.'}, 400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8075, type=int)
    args = parser.parse_args()
    with connect():
        pass
    print(f'Admin access key: {admin_key()}', flush=True)
    print(f'Admin screen: http://{args.host}:{args.port}/admin', flush=True)
    print(f'Socrate is running at http://{args.host}:{args.port}', flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
