"""Shared human checks, independent of collected source content and review approval."""
import re

OPERATIONS = {
    'checks.get': ({'namespace', 'id'}, set()),
    'checks.set': ({'namespace', 'id', 'checked', 'expected_revision'}, {'actor_name'}),
}
WRITE_OPS = {'checks.set'}
DDL = (
    'CREATE TABLE IF NOT EXISTS tc_human_checks (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, checked INTEGER NOT NULL CHECK(checked IN (0,1)), checked_by TEXT, checked_at TEXT, revision INTEGER NOT NULL CHECK(revision>0), PRIMARY KEY(namespace,tc_id))',
    'CREATE TABLE IF NOT EXISTS tc_human_check_events (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, revision INTEGER NOT NULL, checked INTEGER NOT NULL CHECK(checked IN (0,1)), actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, occurred_at TEXT NOT NULL, PRIMARY KEY(namespace,tc_id,revision))',
    "CREATE TRIGGER IF NOT EXISTS tc_human_check_events_no_update BEFORE UPDATE ON tc_human_check_events BEGIN SELECT RAISE(ABORT,'Check history is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS tc_human_check_events_no_delete BEFORE DELETE ON tc_human_check_events BEGIN SELECT RAISE(ABORT,'Check history is append-only'); END",
)
GENERIC_NAMES = {'shared-user', 'local-user', 'anonymous', 'unknown', 'system', 'user', '공용 db 사용자'}


class RevisionConflict(ValueError):
    pass


def initialize(db):
    for statement in DDL:
        db.execute(statement)


def available(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tc_human_checks'").fetchone())


def identity(namespace, tc_id):
    if not isinstance(namespace, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*/[A-Z][A-Z0-9_]*', namespace):
        raise ValueError('확인할 TC의 프로젝트를 지정하세요')
    project = namespace.rsplit('/', 1)[1]
    if not isinstance(tc_id, str) or not re.fullmatch(re.escape(project) + r'-T[1-9][0-9]*', tc_id):
        raise ValueError('확인할 TC 키를 지정하세요')
    return namespace, tc_id


def default():
    return dict(checked=False, checked_by=None, checked_at=None, revision=0)


def view(row):
    return dict(checked=bool(row['checked']), checked_by=row['checked_by'],
                checked_at=row['checked_at'], revision=row['revision'])


def get(db, namespace, tc_id):
    key = identity(namespace, tc_id)
    if not available(db):
        return default()
    row = db.execute('SELECT checked,checked_by,checked_at,revision FROM tc_human_checks WHERE namespace=? AND tc_id=?', key).fetchone()
    return view(row) if row else default()


def get_many(db, identities):
    keys = list(dict.fromkeys(identity(*key) for key in identities))
    result = {key: default() for key in keys}
    if not keys or not available(db):
        return result
    for start in range(0, len(keys), 400):
        batch = keys[start:start + 400]
        rows = db.execute('SELECT namespace,tc_id,checked,checked_by,checked_at,revision FROM tc_human_checks WHERE (namespace,tc_id) IN (' + ','.join('(?,?)' for _ in batch) + ')', [value for key in batch for value in key])
        for row in rows:
            result[(row['namespace'], row['tc_id'])] = view(row)
    return result


def human_name(principal, supplied=None):
    if supplied is not None and (not isinstance(supplied, str) or len(supplied) > 100 or any(ord(c) < 32 for c in supplied)):
        raise ValueError('확인자 이름은 100자 이하로 입력하세요')
    principal_name = principal.get('display_name', '').strip()
    actor_id = principal.get('id', '').strip()
    if actor_id and actor_id.casefold() not in GENERIC_NAMES and principal_name.casefold() not in GENERIC_NAMES and principal_name:
        return actor_id, principal_name
    name = (supplied or '').strip()
    if not name or name.casefold() in GENERIC_NAMES:
        raise ValueError('공용 접속에서는 확인자 이름을 입력하세요')
    return actor_id, name


def execute(db, namespace, op, args, principal, now):
    key = identity(namespace, args['id'])
    if op == 'checks.get':
        return get(db, *key)
    if op != 'checks.set':
        raise ValueError('지원하지 않는 확인 작업입니다')
    checked, expected = args['checked'], args['expected_revision']
    if type(checked) is not bool or type(expected) is not int or not 0 <= expected <= 2**31 - 1:
        raise ValueError('checked와 expected_revision 형식을 확인하세요')
    actor_id, actor_name = human_name(principal, args.get('actor_name'))
    row = db.execute('SELECT ai_agent,inventory FROM dashboard_cases WHERE namespace=? AND tc_id=?', key).fetchone()
    if not row or not row['ai_agent'] or row['inventory'] == 'test':
        raise ValueError('현재 수집된 ai_agent TC만 확인할 수 있습니다')
    current = get(db, *key)
    if expected > current['revision'] or (checked != current['checked'] and expected != current['revision']):
        raise RevisionConflict('다른 사용자가 확인 상태를 변경했습니다. 최신 상태를 조회한 뒤 다시 시도하세요')
    if checked == current['checked']:
        return current
    revision = current['revision'] + 1
    checker, checked_at = (actor_name, now) if checked else (None, None)
    db.execute('INSERT INTO tc_human_checks VALUES (?,?,?,?,?,?) ON CONFLICT(namespace,tc_id) DO UPDATE SET checked=excluded.checked,checked_by=excluded.checked_by,checked_at=excluded.checked_at,revision=excluded.revision', (*key, int(checked), checker, checked_at, revision))
    db.execute('INSERT INTO tc_human_check_events VALUES (?,?,?,?,?,?,?)', (*key, revision, int(checked), actor_id, actor_name, now))
    return dict(checked=checked, checked_by=checker, checked_at=checked_at, revision=revision)
