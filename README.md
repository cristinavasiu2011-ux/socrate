# Socrate

A small, local Kahoot-style quiz app. Python backend, SQLite storage, and plain HTML/CSS/JavaScript frontend. No accounts, cloud services, GitHub connection, external assets, or package installation required.

## Run

Requires Python 3.10 or newer. From this folder:

```sh
python app.py
```

Open **http://localhost:8075/admin** to manage quizzes and host games. Sign in using the **Admin password** printed in the server terminal. New installations generate a readable password made of words and digits. It is kept in `data/admin-key.txt`, so it stays the same after restarting. Existing passwords remain valid until you change them. Keep your password private.

Players open **http://localhost:8075/play** (or the root address), enter the six-digit game code and a nickname, and play. They cannot view the quiz collection, create quizzes, or host games. Admin access is enforced by the backend, not only by hiding controls. Use separate tabs or devices for hosting and playing; host and player sessions are stored separately.

Admin sign-in lasts up to 12 hours, or until sign-out or server restart. Signing out revokes quiz-management access. A game already hosted retains its separate host token so it can still be controlled.

For other devices on your Wi-Fi:

```sh
python app.py --host 0.0.0.0 --port 8075
```

The default host is `0.0.0.0`, so `python app.py` also accepts Wi-Fi players. Use `--host 127.0.0.1` for access only on this computer. The terminal prints available Wi-Fi player URLs. Share one of those with every player; `localhost` points to each player’s own device. Players need different nicknames and must join before the host starts the game.

Players open `http://YOUR-COMPUTER-LAN-IP:8075/play`. Admins use `/admin`. Allow inbound TCP port 8075 in your computer's firewall if necessary. Internet access is unnecessary. Stop the server with Ctrl+C.

## Features

- Single choice, multiple correct answers, true/false, and short text answers.
- Per-question timers from 5–120 seconds; accepted text alternatives ignore case and surrounding spaces.
- Local quiz collection, with duplicate-and-edit for creating variations.
- The admin collection shows whether each quiz is waiting, started, showing results, or finished, along with its code and player count. Each quiz can have only one active game; hosting it again resumes the existing game. Use **End game** to close it before starting a fresh one.
- Admins can delete quizzes from the collection after confirming. Deletion is permanent; games already running keep their quiz and continue normally.
- Game lobby, unique nicknames, timed answers, answer reveal, and final leaderboard.
- Simultaneous player connections, independent tab sessions, and automatic recovery after brief network interruptions. The admin lobby displays a LAN player URL when available.
- Correct answers earn `round(1000 × time remaining / question duration)` points (0–1,000); incorrect and missed answers earn zero. Response time is measured when the server receives the answer. Multiple-answer questions require the exact set.
- The host starts each question for everyone together. A shared server timer closes answers and reveals results when time runs out, even if everyone answered early. The host then starts the next question.
- A smooth countdown follows the server timer; each device refreshes game state every 300 ms. Results show each player’s response time and points. Scores stay hidden until the timer ends.

Quizzes persist in `data/quizzes.db`. Back up that file to keep your collection. Set `SOCRATE_DB` to use another file. Live games and scores are kept in memory and reset when the server restarts. Refreshing a tab reconnects using its stored session.

This is a trusted local-network app. Quiz management and answer keys require admin sign-in; game codes and separate host/player tokens control live play. HTTP on the local network is unencrypted. Keep the app on a private network; do not expose it directly to the Internet.

## Check

```sh
python -m unittest discover -s tests -v
```

Integration tests use a temporary database and exercise a complete four-question game, scoring, access control, duplicate answers, invalid quizzes, timeouts, admin sign-in/sign-out, blocked player access to quiz management, synchronized timers, and speed-based scoring.

## Try the Universe sample quiz

From this folder, run:

```sh
python import_quiz.py examples/universe-quiz.json
python app.py
```

Sign in at `/admin`, refresh the collection, and host **Explore the Universe**. It includes one single-choice, one multiple-answer, one true/false, and one short-answer question. Importing the same unchanged quiz again does not create a duplicate. The importer also accepts your own quiz JSON files in the same format.

## If a screen does not load

Stop the old server with Ctrl+C, update the files, and restart:

```sh
git pull origin main
python app.py --host 0.0.0.0 --port 8075
```

On the computer running Python, open `http://localhost:8075/admin` or `http://localhost:8075/play`. On another device, replace `localhost` with the server computer's local IP. Both routes also accept a trailing slash. Use HTTP, not HTTPS, and reload existing browser tabs after updating. The Python server must stay running; GitHub Pages or opening `static/index.html` directly cannot run the app.

For optional browser checks, install Playwright and Chromium in your development environment, then run `python tests/browser_smoke.py`. Set `CHROMIUM_PATH` to use an existing Chromium executable. These are development tools; the app itself still has no package dependencies.


## Choose your own admin password

Stop the app with Ctrl+C, then run:

```sh
python app.py --set-admin-password
python app.py
```

Enter a password of at least 8 characters and confirm it. Typing is hidden in the terminal. The command saves your password locally without changing quizzes. Restart the app after changing it. If you use `SOCRATE_ADMIN_KEY`, change or unset that environment variable first because it overrides the saved password.

Players in different browsers on the same computer can all use `http://localhost:8075/play`. Each player should enter a different nickname and the same game code before the host starts. They do not need the admin password.
