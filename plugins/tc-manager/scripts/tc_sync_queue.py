"""Durable source refresh requests and ordering; no source writes."""
import json
import re
from datetime import datetime, timezone

import tc_registry as registry

OPERATIONS = {
    'sync.notify': ({'namespace', 'event_id', 'tc_key', 'story_keys'}, set()),
    'sync.status': ({'namespace'}, {'event_id'}),
    'catalog.search': ({'namespace', 'q'}, {'limit', 'team', 'inventory', 'scope'}),
    'catalog.teams': ({'namespace'}, set()),
}
DDL = (
    "CREATE TABLE IF NOT EXISTS source_refresh_queue (namespace TEXT NOT NULL,event_id TEXT NOT NULL,payload_hash TEXT NOT NULL,tc_key TEXT NOT NULL,story_keys TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt TEXT NOT NULL,last_error TEXT,created_at TEXT NOT NULL,completed_at TEXT,PRIMARY KEY(namespace,event_id))",
    "CREATE TABLE IF NOT EXISTS source_refresh_sequence (id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL)",
    "INSERT OR IGNORE INTO source_refresh_sequence VALUES (1,0)",
    "CREATE TABLE IF NOT EXISTS source_refresh_items (namespace TEXT NOT NULL,kind TEXT NOT NULL,item_key TEXT NOT NULL,sequence INTEGER NOT NULL,PRIMARY KEY(namespace,kind,item_key))",
    "CREATE TABLE IF NOT EXISTS source_refresh_state (namespace TEXT PRIMARY KEY,last_attempt TEXT,last_success TEXT,last_error TEXT)",
)


def initialize(db):
    for statement in DDL:
        db.execute(statement)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def notify(db, namespace, event_id, tc_key, story_keys):
    project = namespace.rsplit('/', 1)[-1]
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', event_id):
        raise ValueError('변경 알림 작업 ID 형식을 확인하세요')
    if not isinstance(tc_key, str) or not re.fullmatch(re.escape(project) + r'-T[1-9][0-9]*', tc_key):
        raise ValueError('변경 알림 TC 프로젝트가 다릅니다')
    if not isinstance(story_keys, list) or len(story_keys) > 100 or any(not isinstance(k, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*', k) for k in story_keys):
        raise ValueError('변경 알림 Story 키 형식을 확인하세요')
    story_keys = sorted(set(story_keys))
    fingerprint = registry.digest(dict(tc_key=tc_key, story_keys=story_keys))
    old = db.execute('SELECT payload_hash FROM source_refresh_queue WHERE namespace=? AND event_id=?', (namespace, event_id)).fetchone()
    if old and old[0] != fingerprint:
        raise ValueError('변경 알림 작업 ID가 다른 내용으로 재사용되었습니다')
    now = stamp()
    db.execute('INSERT OR IGNORE INTO source_refresh_queue(namespace,event_id,payload_hash,tc_key,story_keys,status,next_attempt,created_at) VALUES (?,?,?,?,?,?,?,?)',
               (namespace, event_id, fingerprint, tc_key, registry.canonical(story_keys), 'pending', now, now))
    return status(db, namespace, event_id)


def status(db, namespace, event_id=None):
    if event_id is not None:
        row = db.execute('SELECT event_id,tc_key,status,attempts,last_error,completed_at FROM source_refresh_queue WHERE namespace=? AND event_id=?', (namespace, event_id)).fetchone()
        return dict(row) if row else dict(event_id=event_id, status='not_found')
    state = db.execute('SELECT last_attempt,last_success,last_error FROM source_refresh_state WHERE namespace=?', (namespace,)).fetchone()
    return dict(counts=dict(db.execute('SELECT status,COUNT(*) FROM source_refresh_queue WHERE namespace=? GROUP BY status', (namespace,))),
                daily=dict(state) if state else None)


def sequence(db):
    initialize(db)
    return db.execute('SELECT value FROM source_refresh_sequence WHERE id=1').fetchone()[0]


def changed(db, namespace, kind, since):
    return {row[0] for row in db.execute('SELECT item_key FROM source_refresh_items WHERE namespace=? AND kind=? AND sequence>?', (namespace, kind, since))}


def mark(db, namespace, kind, keys):
    db.execute('UPDATE source_refresh_sequence SET value=value+1 WHERE id=1')
    seq = db.execute('SELECT value FROM source_refresh_sequence WHERE id=1').fetchone()[0]
    db.executemany('INSERT INTO source_refresh_items VALUES (?,?,?,?) ON CONFLICT(namespace,kind,item_key) DO UPDATE SET sequence=excluded.sequence',
                   [(namespace, kind, key, seq) for key in keys])
