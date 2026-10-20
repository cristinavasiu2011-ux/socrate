"""Import a quiz JSON file into the local Socrate database."""
import argparse
import json
from pathlib import Path
import secrets

from app import connect, validate_quiz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='Path to a quiz JSON file')
    args = parser.parse_args()
    try:
        quiz = validate_quiz(json.loads(args.file.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    with connect() as db:
        existing = db.execute('SELECT id FROM quizzes WHERE body = ?', (json.dumps(quiz),)).fetchone()
        if existing:
            print(f'Already imported: {quiz["title"]}')
            return
        db.execute('INSERT INTO quizzes (id, body) VALUES (?, ?)',
                   (secrets.token_hex(8), json.dumps(quiz)))
    print(f'Imported: {quiz["title"]} ({len(quiz["questions"])} questions)')


if __name__ == '__main__':
    main()
