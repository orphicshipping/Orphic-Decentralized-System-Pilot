"""Local test-only SQLite atomic storage; not Cloudflare runtime qualification."""
import json
import sqlite3
import sys

path, action = sys.argv[1:]
request = json.load(sys.stdin)
with sqlite3.connect(path, timeout=3) as db:
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, revision INTEGER, body TEXT)')
    db.execute("INSERT OR IGNORE INTO state VALUES (1, 0, '{}')")
    db.commit()
    if action == 'read':
        revision, body = db.execute('SELECT revision, body FROM state WHERE id=1').fetchone()
        print(json.dumps({'revision': revision, 'body': json.loads(body)}))
    elif action == 'cas':
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute('UPDATE state SET revision=revision+1, body=? WHERE id=1 AND revision=?',
                             (json.dumps(request['body']), request['revision'])).rowcount
        db.commit()
        print(json.dumps({'changed': bool(changed)}))
    else:
        raise ValueError('unknown fixture command')
