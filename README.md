# Socrate

A small, local Kahoot-style quiz app. Python backend, SQLite storage, and plain HTML/CSS/JavaScript frontend. No accounts, cloud services, GitHub connection, external assets, or package installation required.

## Run

Requires Python 3.10 or newer. From this folder:

```sh
python app.py
```

Open **http://localhost:8075/admin** to manage quizzes and host games. Sign in using the **Admin access key** printed in the server terminal. The key is generated on first startup and kept in `data/admin-key.txt`, so it stays the same after restarting. You can set `SOCRATE_ADMIN_KEY` before starting the server to use your own key instead. Keep it private.

Players open **http://localhost:8075/play** (or the root address), enter the six-digit game code and a nickname, and play. They cannot view the quiz collection, create quizzes, or host games. Admin access is enforced by the backend, not only by hiding controls. Use separate tabs or devices for hosting and playing; host and player sessions are stored separately.

Admin sign-in lasts up to 12 hours, or until sign-out or server restart. Signing out revokes quiz-management access. A game already hosted retains its separate host token so it can still be controlled.

For other devices on your Wi-Fi:

```sh
python app.py --host 0.0.0.0 --port 8075
```

Players open `http://YOUR-COMPUTER-LAN-IP:8075/play`. Admins use `/admin`. Allow inbound TCP port 8075 in your computer's firewall if necessary. Internet access is unnecessary. Stop the server with Ctrl+C.

## Features

- Single choice, multiple correct answers, true/false, and short text answers.
- Per-question timers from 5–120 seconds; accepted text alternatives ignore case and surrounding spaces.
- Local quiz collection, with duplicate-and-edit for creating variations.
- Game lobby, unique nicknames, timed answers, answer reveal, and final leaderboard.
- Correct answers earn 500–1,000 points depending on speed. Multiple-answer questions require the exact set.
- Host controls question progression; all answers or the timer ending reveal results automatically.

Quizzes persist in `data/quizzes.db`. Back up that file to keep your collection. Set `SOCRATE_DB` to use another file. Live games and scores are kept in memory and reset when the server restarts. Refreshing a tab reconnects using its stored session.

This is a trusted local-network app. Quiz management and answer keys require admin sign-in; game codes and separate host/player tokens control live play. HTTP on the local network is unencrypted. Keep the app on a private network; do not expose it directly to the Internet.

## Check

```sh
python -m unittest discover -s tests -v
```

Integration tests use a temporary database and exercise a complete four-question game, scoring, access control, duplicate answers, invalid quizzes, timeouts, admin sign-in/sign-out, and blocked player access to quiz management.

## Try the Universe sample quiz

From this folder, run:

```sh
python import_quiz.py examples/universe-quiz.json
python app.py
```

Sign in at `/admin`, refresh the collection, and host **Explore the Universe**. It includes one single-choice, one multiple-answer, one true/false, and one short-answer question. Importing the same unchanged quiz again does not create a duplicate. The importer also accepts your own quiz JSON files in the same format.
