"""Online SQLite backup; retains the newest fourteen daily snapshots."""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

source = Path(os.environ.get('POLKA_DATABASE', '/opt/polka/data/polka.sqlite3'))
destination = Path(os.environ.get('POLKA_BACKUPS', '/opt/polka/backups'))
destination.mkdir(parents=True, exist_ok=True)
target = destination / ('polka-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.sqlite3')
with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(target)) as backup:
    db.backup(backup)
    assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
os.chmod(target, 0o600)
for old in sorted(destination.glob('polka-*.sqlite3'), reverse=True)[14:]:
    old.unlink()
