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
    server=app.QuizServer(('127.0.0.1',0),app.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            admin=browser.new_page()
            errors=[]
            admin.on('pageerror',lambda error: errors.append(str(error)))
            admin.goto(origin+'/admin/')
            admin.get_by_label('Admin password').fill('browser-test-key')
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
            shared_context = browser.new_context()
            extra_players = []
            for index in range(3):
                tab = shared_context.new_page()
                tab.on('pageerror',lambda error: errors.append(str(error)))
                tab.goto(origin + '/play')
                tab.get_by_label('Game code').fill(code)
                tab.get_by_label('Your nickname').fill(f'Extra {index}')
                tab.get_by_role('button',name='Join game').click()
                tab.get_by_text('Get comfortable.',exact=False).wait_for()
                extra_players.append(tab)
            tokens = [tab.evaluate("JSON.parse(sessionStorage.getItem('socrate-player-session')).token") for tab in extra_players]
            assert len(set(tokens)) == 3
            admin.get_by_text('Extra 2',exact=True).wait_for()
            observer = browser.new_page()
            observer.on('pageerror',lambda error: errors.append(str(error)))
            observer.goto(origin + '/admin')
            observer.get_by_label('Admin password').fill('browser-test-key')
            observer.get_by_role('button',name='Sign in').click()
            observer.locator('[data-status]').filter(has_text='Waiting for players').wait_for()
            admin.get_by_role('button',name='Start game').click()
            observer.locator('[data-status]').filter(has_text='Game started').wait_for()
            assert observer.get_by_role('button',name='Resume game').is_visible()
            player.get_by_role('button',name='Jupiter',exact=True).click()
            player.get_by_role('button',name='Lock in answer').click()
            player.get_by_text('Answer locked in',exact=False).wait_for()
            for tab in extra_players:
                tab.get_by_role('button',name='Jupiter',exact=True).click()
                tab.get_by_role('button',name='Lock in answer').click()
                tab.get_by_text('Answer locked in',exact=False).wait_for()
            admin.get_by_text('4 / 4 answered',exact=True).wait_for()
            # A temporary network loss must not remove the player or end polling.
            player.context.set_offline(True)
            player.locator('#connection').wait_for(state='visible')
            player.context.set_offline(False)
            player.locator('#connection').wait_for(state='hidden')
            player.get_by_text('Answer locked in',exact=False).wait_for()
            admin.once('dialog', lambda dialog: dialog.accept())
            admin.get_by_role('button',name='Admin collection',exact=True).click()
            admin.get_by_role('button',name='Resume game').click()
            player.get_by_text('Answer locked in',exact=False).wait_for()
            assert admin.evaluate("JSON.parse(sessionStorage.getItem('socrate-host-session')).code") == code
            admin.get_by_role('button',name='End game',exact=True).wait_for()
            admin.once('dialog', lambda dialog: dialog.accept())
            admin.get_by_role('button',name='End game',exact=True).click()
            admin.locator('[data-status]').filter(has_text='Finished').wait_for()
            observer.locator('[data-status]').filter(has_text='Finished').wait_for()
            assert admin.get_by_role('button',name='Host game').is_visible()
            observer.close()
            print('Live admin status, resuming existing game, and ending game: PASSED')
            shared_context.close()
            print('Four browser players, separate tab identities, and reconnection: PASSED')
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
