"""Durable change notifications; never retries Zephyr creation."""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing


class Outbox:
    def __init__(self, path, server, namespace):
        self.path, self.server, self.namespace = Path(path), server, namespace

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS notifications (server TEXT, namespace TEXT, event_id TEXT, payload TEXT NOT NULL, PRIMARY KEY(server,namespace,event_id))')
        return db

    def put(self, payload):
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with closing(self.connect()) as db, db:
            old = db.execute('SELECT payload FROM notifications WHERE server=? AND namespace=? AND event_id=?', (self.server, self.namespace, payload['event_id'])).fetchone()
            if old and old[0] != text:
                raise ValueError('같은 작업 ID에 다른 변경 알림을 저장할 수 없습니다')
            db.execute('INSERT OR IGNORE INTO notifications VALUES (?,?,?,?)', (self.server, self.namespace, payload['event_id'], text))

    def namespaces(self):
        if not self.path.exists():
            return []
        with closing(self.connect()) as db:
            return [row[0] for row in db.execute('SELECT DISTINCT namespace FROM notifications WHERE server=?', (self.server,))]

    def flush(self, send):
        if not self.path.exists():
            return {}
        results = {}
        with closing(self.connect()) as db:
            rows = db.execute('SELECT event_id,payload FROM notifications WHERE server=? AND namespace=? ORDER BY rowid LIMIT 100', (self.server, self.namespace)).fetchall()
        for event_id, payload in rows:
            try:
                result = send('sync.notify', json.loads(payload))
            except (ValueError, OSError):
                break
            if not isinstance(result, dict) or result.get('status') not in {'pending', 'running', 'failed', 'completed'}:
                break
            with closing(self.connect()) as db, db:
                db.execute('DELETE FROM notifications WHERE server=? AND namespace=? AND event_id=?', (self.server, self.namespace, event_id))
            results[event_id] = result
        return results


def operation_id(tc_key, content_hash):
    return hashlib.sha256((tc_key + ':' + content_hash).encode('utf-8')).hexdigest()
