"""Lossless, content-addressed JSON storage for registry format 2."""
import hashlib
import json
import sqlite3
import zlib

PREFIX = b'TCJSON2\x00'
DDL = "CREATE TABLE json_objects (hash BLOB PRIMARY KEY, codec TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL) WITHOUT ROWID"
FIELDS = {
    'bundles': {'canonical_json': 'bundle'},
    'bundle_inputs': {'raw_json': 'bundle'},
    'sources': {'raw_json': 'text'},
    'cases': {'raw_json': 'text', 'sources_json': 'sources', 'script_json': 'text'},
    'facets': {'raw_json': 'text'},
    'story_snapshots': {'raw_json': 'text'},
    'story_source_views': {'raw_json': 'text', 'view_json': 'text'},
    'source_test_cases': {'raw_json': 'text'},
    'source_story_links': {'links_json': 'text'},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def raw_execute(db, sql, args=()):
    cursor = db.cursor()
    cursor.row_factory = None
    return cursor.execute(sql, args)


def pack(db, text, kind='text'):
    if not isinstance(text, str):
        raise ValueError('JSON 보존 데이터는 문자열이어야 합니다')
    original = text.encode('utf-8')
    digest = hashlib.sha256(original).digest()
    if raw_execute(db, 'SELECT 1 FROM json_objects WHERE hash=?', (digest,)).fetchone():
        return PREFIX + digest
    codec, payload = 'text', original
    if kind in {'sources', 'bundle'}:
        value = json.loads(text.lstrip("\ufeff"))
        if canonical(value) == text:
            if kind == 'sources' and isinstance(value, list):
                payload = canonical([pack(db, canonical(v))[len(PREFIX):].hex() for v in value]).encode('utf-8')
                codec = 'sources'
            elif kind == 'bundle' and isinstance(value, dict) and isinstance(value.get('sources'), list) and isinstance(value.get('test_cases'), list):
                rest = {k: v for k, v in value.items() if k not in {'sources', 'test_cases'}}
                payload = canonical(dict(rest=rest, sources=[pack(db, canonical(v))[len(PREFIX):].hex() for v in value['sources']],
                                         test_cases=[pack(db, canonical(v))[len(PREFIX):].hex() for v in value['test_cases']])).encode('utf-8')
                codec = 'bundle'
    raw_execute(db, 'INSERT OR IGNORE INTO json_objects VALUES (?,?,?,?)',
                (digest, codec, len(original), zlib.compress(payload, 6)))
    return PREFIX + digest


def unpack(db, value, depth=0):
    if not isinstance(value, bytes) or not value.startswith(PREFIX):
        return value
    if len(value) != len(PREFIX) + 32 or depth > 3:
        raise ValueError('잘못된 DB JSON 참조')
    digest = value[len(PREFIX):]
    row = raw_execute(db, 'SELECT codec,size,data FROM json_objects WHERE hash=?', (digest,)).fetchone()
    if row is None:
        raise ValueError('DB JSON 원본이 없습니다')
    codec, size, compressed = row
    payload = zlib.decompress(compressed).decode('utf-8')
    if codec in {'sources', 'bundle'}:
        parts = json.loads(payload)
        def resolve(ref):
            return json.loads(unpack(db, PREFIX + bytes.fromhex(ref), depth + 1))
        if codec == 'sources':
            payload = canonical([resolve(ref) for ref in parts])
        else:
            payload = canonical(dict(parts['rest'], sources=[resolve(ref) for ref in parts['sources']],
                                     test_cases=[resolve(ref) for ref in parts['test_cases']]))
    elif codec != 'text':
        raise ValueError('지원하지 않는 DB JSON codec')
    encoded = payload.encode('utf-8')
    if len(encoded) != size or hashlib.sha256(encoded).digest() != digest:
        raise ValueError('DB JSON 내용 해시가 일치하지 않습니다')
    return payload


class StoredRow:
    """sqlite.Row-compatible logical values; unpack large JSON only when read."""
    def __init__(self, cursor, values):
        self.db = cursor.connection
        self.row = sqlite3.Row(cursor, values)

    def keys(self):
        return self.row.keys()

    def __len__(self):
        return len(self.row)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return tuple(self)[key]
        return unpack(self.db, self.row[key])

    def __iter__(self):
        return (self[index] for index in range(len(self)))


def encode_row(db, table, columns, values):
    fields = FIELDS.get(table, {})
    return tuple(pack(db, v, fields[k]) if k in fields and v is not None else v for k, v in zip(columns, values))
