"""Socrate: dependency-free local multiplayer quizzes. Run python app.py."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import sqlite3
import socket
import threading
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = Path(os.environ.get('SOCRATE_DB', str(ROOT / 'data' / 'quizzes.db')))
GAMES = {}
LOCK = threading.RLock()
ADMIN_SESSIONS = {}
ADMIN_KEY = None


PASSWORD_WORDS = ('amber apple atlas autumn bamboo beach birch blue breeze brook cedar cherry '
                  'cloud coral cosmos dawn delta dune earth echo elm fern field flame forest '
                  'frost garden globe gold grape green harbor hazel hill honey iris island jade '
                  'lake leaf lemon lilac lunar maple meadow mint moon moss ocean olive orbit '
                  'peach pearl pine planet plum pond rain river robin rose ruby sage sand sky '
                  'snow solar star stone summer sun tide tulip valley violet wave willow wind').split()


def readable_password():
    return '-'.join(secrets.choice(PASSWORD_WORDS) for _ in range(4)) + f'-{secrets.randbelow(9000) + 1000}'


def set_admin_password(password):
    global ADMIN_KEY
    if os.environ.get('SOCRATE_ADMIN_KEY'):
        raise ValueError('SOCRATE_ADMIN_KEY is set. Change or unset it before setting a saved password.')
    if not 8 <= len(password) <= 128 or password != password.strip():
        raise ValueError('Use 8–128 characters without leading or trailing spaces.')
    key_file = DB.parent / 'admin-key.txt'
    key_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=key_file.parent,
                                         prefix='.admin-', delete=False) as file:
            temporary = Path(file.name)
            file.write(password)
        os.replace(temporary, key_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    ADMIN_KEY = password
    ADMIN_SESSIONS.clear()


def admin_key():
    global ADMIN_KEY
    if ADMIN_KEY is None:
        ADMIN_KEY = os.environ.get('SOCRATE_ADMIN_KEY')
        if not ADMIN_KEY:
            key_file = DB.parent / 'admin-key.txt'
            key_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(key_file, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
                    file.write(readable_password())
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


def quiz_game_status(quiz_id):
    matches = [game for game in GAMES.values() if game.get('quiz_id') == quiz_id]
    game = next((game for game in matches if game['phase'] != 'finished'), matches[-1] if matches else None)
    if game is None:
        return None
    expire(game)
    return dict(code=game['code'], phase=game['phase'], players=len(game['players']),
                active=game['phase'] != 'finished')


class QuizServer(ThreadingHTTPServer):
    # A classroom can open dozens of browser connections at the same instant.
    request_queue_size = 128
    daemon_threads = True


def lan_addresses():
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        # Determine the local interface used by the default route; sends no data.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in addresses if not ip.startswith('127.') and ip != '0.0.0.0')


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
        path = urlparse(self.path).path.rstrip('/') or '/'
        try:
            if path == '/api/server-info':
                host, port = self.server.server_address[:2]
                addresses = lan_addresses() if host == '0.0.0.0' else [host]
                urls = [f'http://{address}:{port}/play' for address in addresses if not address.startswith('127.')]
                return self.reply({'player_urls': urls, 'local_only': host.startswith('127.')})
            if path == '/api/admin/session':
                if self.require_admin():
                    self.reply({'admin': True})
                return
            if path == '/api/quizzes':
                if not self.require_admin():
                    return
                with connect() as db:
                    items = [dict(id=row[0], **json.loads(row[1])) for row in db.execute('SELECT id, body FROM quizzes ORDER BY rowid DESC')]
                with LOCK:
                    for item in items:
                        item['game'] = quiz_game_status(item['id'])
                return self.reply(items)
            if path.startswith('/api/games/'):
                with LOCK:
                    snapshot = state(GAMES[path.split('/')[3]], self.headers.get('Authorization', ''))
                return self.reply(snapshot)
            files = {'/': ('index.html', 'text/html'), '/play': ('index.html', 'text/html'), '/admin': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if path not in files:
                return self.reply({'error': 'Not found'}, 404)
            name, mime = files[path]
            raw = (ROOT / 'static' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (KeyError, ValueError) as exc:
            self.reply({'error': str(exc) if isinstance(exc, ValueError) else 'Game not found. It may have ended or the server restarted.'}, 400)

    def do_DELETE(self):
        if not self.require_admin():
            return
        parts = urlparse(self.path).path.split('/')
        if len(parts) != 4 or parts[1:3] != ['api', 'quizzes'] or not parts[3]:
            return self.reply({'error': 'Not found'}, 404)
        with LOCK, connect() as db:
            deleted = db.execute('DELETE FROM quizzes WHERE id=?', (parts[3],)).rowcount
        if not deleted:
            return self.reply({'error': 'Quiz not found.'}, 404)
        self.reply({'deleted': True})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 262144:
                raise ValueError('Request is empty or too large.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            path = urlparse(self.path).path
            if path in ('/api/quizzes', '/api/games') and not self.require_admin():
                return
            with LOCK:
                result, status = self.post_action(path, data)
            self.reply(result, status)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self.reply({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid request or game not found.'}, 400)

    def post_action(self, path, data):
        def response(data, status=200):
            return data, status

        if path == '/api/admin/login':
            if not secrets.compare_digest(str(data.get('key', '')).encode(), admin_key().encode()):
                return response({'error': 'Incorrect admin key.'}, 403)
            now = time.time()
            for old in list(ADMIN_SESSIONS):
                if ADMIN_SESSIONS[old] <= now:
                    del ADMIN_SESSIONS[old]
            token = secrets.token_urlsafe(32)
            ADMIN_SESSIONS[token] = now + 43200
            return response({'token': token})
        if path == '/api/admin/logout':
            ADMIN_SESSIONS.pop(self.headers.get('X-Admin-Token', ''), None)
            return response({'ok': True})
        if path == '/api/quizzes':
            quiz = validate_quiz(data)
            ident = secrets.token_hex(8)
            with connect() as db:
                db.execute('INSERT INTO quizzes VALUES (?, ?)', (ident, json.dumps(quiz)))
            return response(dict(id=ident, **quiz), 201)
        if path == '/api/games':
            with connect() as db:
                row = db.execute('SELECT body FROM quizzes WHERE id=?', (data['quiz_id'],)).fetchone()
            if row is None:
                raise ValueError('Quiz not found.')
            for existing in GAMES.values():
                if existing.get('quiz_id') == data['quiz_id'] and existing['phase'] != 'finished':
                    return response(dict(code=existing['code'], token=existing['host'], resumed=True))
            code = str(secrets.randbelow(900000) + 100000)
            while code in GAMES:
                code = str(secrets.randbelow(900000) + 100000)
            token = secrets.token_urlsafe(24)
            GAMES[code] = dict(code=code, host=token, quiz_id=data['quiz_id'], quiz=json.loads(row[0]), players={}, phase='lobby', index=-1, submissions={})
            return response(dict(code=code, token=token), 201)
        parts = path.split('/')
        if len(parts) != 5 or parts[1:3] != ['api', 'games']:
            return response({'error': 'Not found'}, 404)
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
            return response(dict(code=game['code'], token=token))
        if action == 'end':
            if not secrets.compare_digest(token, game['host']):
                return response({'error': 'Host access required.'}, 403)
            game['phase'] = 'finished'
            return response(state(game, token))
        if action == 'next':
            if not secrets.compare_digest(token, game['host']):
                return response({'error': 'Host access required.'}, 403)
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
            return response(state(game, token))
        if action == 'answer':
            if token not in game['players']:
                return response({'error': 'Player access required.'}, 403)
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
            points = round(1000 * max(0, 1 - elapsed / q['seconds'])) if correct else 0
            game['submissions'][token] = dict(correct=correct, points=points, response_seconds=round(elapsed, 2))
            return response({'accepted': True})
        return response({'error': 'Not found'}, 404)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=8075, type=int)
    parser.add_argument('--set-admin-password', action='store_true', help='Choose a new admin password, then exit')
    args = parser.parse_args()
    if args.set_admin_password:
        try:
            password = getpass.getpass('New admin password (at least 8 characters): ')
            confirmation = getpass.getpass('Confirm new password: ')
            if password != confirmation:
                raise ValueError('Passwords do not match. Nothing was changed.')
            set_admin_password(password)
        except (ValueError, OSError) as exc:
            parser.exit(1, f'{exc}\n')
        except (EOFError, KeyboardInterrupt):
            parser.exit(1, 'Password change cancelled.\n')
        parser.exit(0, 'Admin password saved. Start the app again to use it.\n')
    with connect():
        pass
    print(f'Admin password: {admin_key()}', flush=True)
    browser_host = 'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host
    print(f'Admin screen: http://{browser_host}:{args.port}/admin', flush=True)
    print(f'Player screen: http://{browser_host}:{args.port}/play', flush=True)
    if args.host == '0.0.0.0':
        for address in lan_addresses():
            print(f'Players on Wi-Fi: http://{address}:{args.port}/play', flush=True)
        print('Share a Wi-Fi address with players, not localhost. Allow Python in your firewall.', flush=True)
    print(f'Socrate is running on {args.host}:{args.port}', flush=True)
    QuizServer((args.host, args.port), Handler).serve_forever()
