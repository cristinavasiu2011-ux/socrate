"""Optional real-browser smoke check. Requires Playwright and Chromium."""
import sys, tempfile, threading, json, shutil, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from playwright.sync_api import sync_playwright
from http.server import ThreadingHTTPServer
with tempfile.TemporaryDirectory() as tmp:
    app.DB=Path(tmp)/'test.db'
    app.ADMIN_KEY='browser-test-key'
    quiz=app.validate_quiz(json.loads((ROOT / 'examples/universe-quiz.json').read_text()))
    with app.connect() as db:
        db.execute('INSERT INTO quizzes VALUES (?,?)', ('test',json.dumps(quiz)))
    server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            admin=browser.new_page()
            errors=[]
            admin.on('pageerror',lambda error: errors.append(str(error)))
            admin.goto(origin+'/admin/')
            admin.get_by_label('Admin access key').fill('browser-test-key')
            admin.get_by_role('button',name='Sign in').click()
            admin.get_by_role('button',name='Host game').click()
            admin.locator('.code').wait_for()
            code=admin.locator('.code').inner_text()
            player=browser.new_page()
            player.on('pageerror',lambda error: errors.append(str(error)))
            player.goto(origin+'/play/')
            player.get_by_label('Game code').fill(code)
            player.get_by_label('Your nickname').fill('Tester')
            player.get_by_role('button',name='Join game').click()
            player.get_by_text('Get comfortable.',exact=False).wait_for()
            admin.get_by_role('button',name='Start game').click()
            player.get_by_role('button',name='Jupiter',exact=True).click()
            player.get_by_role('button',name='Lock in answer').click()
            player.get_by_text('Answer locked in',exact=False).wait_for()
            print('Normal /admin and /play sign-in, host, join, answer: PASSED')
            for route in ['/admin', '/admin/', '/play', '/play/']:
                page=browser.new_page()
                response=page.goto(origin+route)
                assert response.status == 200, route
                assert response.headers.get('cache-control') == 'no-store'
                assert page.locator('#key' if 'admin' in route else '#code').is_visible()
                page.close()
            for broken_storage in [False, True]:
                context = browser.new_context()
                if broken_storage:
                    context.add_init_script("Object.defineProperty(window, 'sessionStorage', {get(){throw new Error('Storage blocked');}});")
                else:
                    context.add_init_script("sessionStorage.setItem('socrate-player-session', '{bad json'); sessionStorage.setItem('socrate-host-session', '{bad json');")
                for route in ['/admin/', '/play/']:
                    page = context.new_page()
                    page.on('pageerror',lambda error: errors.append(str(error)))
                    page.goto(origin + route)
                    assert page.locator('#key' if 'admin' in route else '#code').is_visible()
                    page.close()
                context.close()
            assert not errors, errors
            print('Route variants, cache headers, corrupt sessions, blocked storage: PASSED')
            browser.close()
    finally:
        server.shutdown();server.server_close()
