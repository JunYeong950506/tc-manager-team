"""Immutable source responses and separately versioned searchable Story views."""
import json
import re
from urllib.parse import urlsplit

import tc_registry as registry

DDL = (
    "CREATE TABLE IF NOT EXISTS story_source_views (namespace TEXT NOT NULL, issue_key TEXT NOT NULL, source_hash TEXT NOT NULL, view_hash TEXT NOT NULL, updated_at TEXT NOT NULL, observed_at TEXT NOT NULL, extractor TEXT NOT NULL, raw_json BLOB NOT NULL, view_json BLOB NOT NULL, warnings_json TEXT NOT NULL, actor TEXT NOT NULL, review_json TEXT NOT NULL, PRIMARY KEY(namespace,issue_key,source_hash,view_hash))",
    "CREATE TABLE IF NOT EXISTS story_source_heads (namespace TEXT NOT NULL, issue_key TEXT NOT NULL, source_hash TEXT NOT NULL, view_hash TEXT NOT NULL, PRIMARY KEY(namespace,issue_key), FOREIGN KEY(namespace,issue_key,source_hash,view_hash) REFERENCES story_source_views(namespace,issue_key,source_hash,view_hash))",
)


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='story_source_views'").fetchone())


def current(db, namespace):
    if not exists(db):
        return {}
    result = {}
    for row in db.execute('SELECT v.issue_key,v.view_json,v.observed_at,v.view_hash,v.source_hash,v.extractor,v.warnings_json FROM story_source_heads h JOIN story_source_views v USING(namespace,issue_key,source_hash,view_hash) WHERE h.namespace=?', (namespace,)):
        result[row['issue_key']] = dict(json.loads(row['view_json']), fetched_at=row['observed_at'],
            content_hash=row['view_hash'], source_hash=row['source_hash'], extractor=row['extractor'],
            parsing_warnings=json.loads(row['warnings_json']))
    return result


def store(db, namespace, rows, records, scope, actor, legacy):
    # Imported lazily: tc_stories delegates only source-backed imports here.
    import tc_stories as stories
    if not isinstance(records, list) or len(records) != len(rows):
        raise ValueError('STORY_SOURCE_RECORDS_MISMATCH')
    known_ids = {row['issue_id']: row['key'] for row in legacy.values()}
    inserted, pending = 0, 0
    for row, record in zip(rows, records):
        if not isinstance(record, dict) or not {'key', 'raw_issue', 'extractor', 'warnings'} <= set(record) or set(record) - {'key', 'raw_issue', 'extractor', 'warnings', 'review'} or record['key'] != row['key']:
            raise ValueError('INVALID_STORY_SOURCE_RECORD')
        raw = record['raw_issue']
        if urlsplit(row['source_url']).hostname + '/' + row['key'].rsplit('-', 1)[0] != namespace:
            raise ValueError('STORY_SOURCE_IDENTITY_MISMATCH')
        if (not isinstance(raw, dict) or raw.get('key') != row['key'] or str(raw.get('id')) != row['issue_id']
                or stories.timestamp(raw.get('fields', {}).get('updated')) != row['updated_at']
                or raw.get('fields', {}).get('project', {}).get('key') != row['key'].rsplit('-', 1)[0]):
            raise ValueError('STORY_SOURCE_IDENTITY_MISMATCH')
        extractor, warnings = record['extractor'], record['warnings']
        if not isinstance(extractor, str) or not re.fullmatch(r'[a-z0-9_.-]{1,80}', extractor):
            raise ValueError('INVALID_STORY_EXTRACTOR')
        if not isinstance(warnings, list) or any(not isinstance(w, str) or not re.fullmatch(r'[A-Z][A-Z0-9_:.-]{0,120}', w) for w in warnings):
            raise ValueError('INVALID_STORY_PARSE_WARNINGS')
        if row['issue_id'] in known_ids and known_ids[row['issue_id']] != row['key']:
            raise ValueError('STORY_SOURCE_IDENTITY_MISMATCH')
        previous = legacy.get(row['key'])
        if previous and (previous['issue_id'] != row['issue_id'] or previous['source_url'] != row['source_url']):
            raise ValueError('STORY_SOURCE_IDENTITY_MISMATCH')
        known_ids[row['issue_id']] = row['key']
        source_hash = registry.digest(raw)
        review = dict(status='search_index')
        view = {k: v for k, v in row.items() if k != 'fetched_at'}
        view_hash = registry.digest(dict(view=view, extractor=extractor, warnings=warnings))
        inserted += db.execute('INSERT OR IGNORE INTO story_source_views VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (namespace, row['key'], source_hash, view_hash, row['updated_at'], row['fetched_at'], extractor,
             registry.storage.pack(db, registry.canonical(raw)), registry.storage.pack(db, registry.canonical(view)),
             registry.canonical(warnings), actor, registry.canonical(review))).rowcount
        db.execute('UPDATE story_source_views SET observed_at=MAX(observed_at,?),review_json=? WHERE namespace=? AND issue_key=? AND source_hash=? AND view_hash=?',
            (row['fetched_at'], registry.canonical(review), namespace, row['key'], source_hash, view_hash))
        selected = db.execute('SELECT v.updated_at,v.observed_at FROM story_source_heads h JOIN story_source_views v USING(namespace,issue_key,source_hash,view_hash) WHERE h.namespace=? AND h.issue_key=?', (namespace, row['key'])).fetchone()
        if (not selected or (row['updated_at'], row['fetched_at']) >= (selected['updated_at'], selected['observed_at'])):
            db.execute('INSERT INTO story_source_heads VALUES (?,?,?,?) ON CONFLICT(namespace,issue_key) DO UPDATE SET source_hash=excluded.source_hash,view_hash=excluded.view_hash',
                (namespace, row['key'], source_hash, view_hash))
    keys = [row['key'] for row in rows]
    # Fetch time is metadata, not another copy of the source or search text.
    collection_hash = registry.digest(dict(query=scope['query'], keys=keys, sources=[registry.digest(r['raw_issue']) for r in records]))
    db.execute('INSERT OR IGNORE INTO story_collections VALUES (?,?,?,?,?)',
        (namespace, collection_hash, registry.canonical(scope), registry.canonical(keys), actor))
    return dict(inserted_snapshots=inserted, collected_keys=keys, scope=scope, source_preserved=True,
                partial_text_count=sum(bool(record['warnings']) for record in records), pending_review_count=pending)


def pending_reviews(db, namespace, key=None, include_raw=False):
    if not exists(db):
        return []
    result = []
    query = 'SELECT * FROM story_source_views WHERE namespace=?' + (' AND issue_key=?' if key else '')
    for row in db.execute(query, (namespace, key) if key else (namespace,)):
        review = json.loads(row['review_json'])
        if review['status'] != 'pending':
            continue
        head = db.execute('SELECT v.updated_at,v.observed_at FROM story_source_heads h JOIN story_source_views v USING(namespace,issue_key,source_hash,view_hash) WHERE h.namespace=? AND h.issue_key=?', (namespace, row['issue_key'])).fetchone()
        if head and (head['updated_at'], head['observed_at']) >= (row['updated_at'], row['observed_at']):
            continue
        item = dict(key=row['issue_key'], source_hash=row['source_hash'], review=review)
        if include_raw:
            item['raw_issue'] = json.loads(row['raw_json'])
            item['candidate'] = json.loads(row['view_json'])
        result.append(item)
    return result


def source(db, namespace, key, selected):
    if not exists(db) or not selected.get('source_hash'):
        return dict(status='unavailable', reason='기존 자료에는 원본 응답이 보존되지 않았습니다')
    row = db.execute('SELECT * FROM story_source_views WHERE namespace=? AND issue_key=? AND source_hash=? AND view_hash=?',
                     (namespace, key, selected['source_hash'], selected['content_hash'])).fetchone()
    return dict(status='preserved', source_hash=row['source_hash'], extractor=row['extractor'],
                warnings=json.loads(row['warnings_json']), raw_issue=json.loads(row['raw_json']))


def history(db, namespace, key):
    if not exists(db):
        return []
    return [dict(content_hash=row['view_hash'], updated_at=row['updated_at'], source_hash=row['source_hash'],
                 extractor=row['extractor'], parsing_warnings=json.loads(row['warnings_json']))
            for row in db.execute('SELECT view_hash,updated_at,source_hash,extractor,warnings_json FROM story_source_views WHERE namespace=? AND issue_key=? ORDER BY updated_at DESC,observed_at DESC', (namespace, key))]
