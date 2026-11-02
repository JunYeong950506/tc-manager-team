"""Versioned TC authoring candidates, kept separate from published TC inventory."""
import hashlib
import json
import re

STATUSES = {None: '초안', 'registered': '등록 완료', 'needs_evidence': '보완 대기', 'awaiting_implementation': '구현 대기'}
OPERATIONS = {
    'candidates.list': ({'namespace'}, {'story_key', 'status', 'limit', 'offset'}),
    'candidates.get': ({'namespace', 'id'}, {'limit','offset'}),
    'candidates.save': ({'namespace', 'id', 'expected_revision', 'operation_id', 'draft', 'story_keys', 'status', 'reason', 'resume_when', 'evidence'}, {'target', 'registration'}),
}
WRITE_OPS = {'candidates.save'}
DDL = (
    'CREATE TABLE IF NOT EXISTS tc_authoring_candidates (namespace TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(namespace,id))',
    'CREATE TABLE IF NOT EXISTS tc_authoring_versions (namespace TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,operation_id TEXT NOT NULL,payload_hash TEXT NOT NULL,payload_json TEXT NOT NULL,actor_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(namespace,id,revision),UNIQUE(namespace,operation_id))',
    "CREATE TRIGGER IF NOT EXISTS tc_authoring_versions_no_update BEFORE UPDATE ON tc_authoring_versions BEGIN SELECT RAISE(ABORT,'Candidate history is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS tc_authoring_versions_no_delete BEFORE DELETE ON tc_authoring_versions BEGIN SELECT RAISE(ABORT,'Candidate history is append-only'); END",
)

class Conflict(ValueError):
    pass


def initialize(db):
    for sql in DDL:
        db.execute(sql)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def string(value, name, maximum=4000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (not empty and not value.strip()):
        raise ValueError(name + ' 형식을 확인하세요')
    return value


def all_items(db, namespace='all', allowed=('*',)):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='tc_authoring_candidates'").fetchone():
        return []
    query = 'SELECT namespace,payload_json FROM tc_authoring_candidates'
    rows = db.execute(query if namespace == 'all' else query + ' WHERE namespace=?', () if namespace == 'all' else (namespace,))
    return [json.loads(row['payload_json']) for row in rows if '*' in allowed or row['namespace'] in allowed]


def summary(item):
    return dict(item, draft={'title':item['draft']['title']}, evidence=[], registration=({'remote_key':item['registration']['remote_key']} if item.get('registration') else None))


def execute(db, op, args, principal, now):
    namespace = args['namespace']
    allowed = principal['namespaces']
    if namespace != 'all' and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*/[A-Z][A-Z0-9_]*', namespace):
        raise ValueError('프로젝트 namespace를 확인하세요')
    if namespace != 'all' and '*' not in allowed and namespace not in allowed:
        raise ValueError('접근할 수 없는 프로젝트입니다')
    if op == 'candidates.list':
        if 'story_key' in args and (not isinstance(args['story_key'],str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*',args['story_key'])):
            raise ValueError('Story 키를 확인하세요')
        items = all_items(db, namespace, allowed)
        if 'story_key' in args:
            items = [item for item in items if args['story_key'] in item['story_keys']]
        if 'status' in args:
            if args['status'] not in STATUSES:
                raise ValueError('작성 상태를 확인하세요')
            items = [item for item in items if item['status'] == args['status']]
        limit, offset = args.get('limit', 25), args.get('offset', 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('페이지 범위를 확인하세요')
        items.sort(key=lambda item: (item['updated_at'], item['id']), reverse=True)
        return dict(items=[summary(item) for item in items[offset:offset+limit]], total=len(items))
    if namespace == 'all':
        raise ValueError('저장할 프로젝트를 지정하세요')
    candidate_id = string(args['id'], '후보 ID', 200)
    row = db.execute('SELECT payload_json FROM tc_authoring_candidates WHERE namespace=? AND id=?', (namespace, candidate_id)).fetchone()
    current = json.loads(row[0]) if row else None
    if op == 'candidates.get':
        if not current:
            raise ValueError('저장된 초안이 없습니다')
        limit, offset = args.get('limit',2), args.get('offset',0)
        if type(limit) is not int or not 1 <= limit <= 2 or type(offset) is not int or offset < 0:
            raise ValueError('이력 페이지 범위를 확인하세요')
        history = [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM tc_authoring_versions WHERE namespace=? AND id=? ORDER BY revision DESC LIMIT ? OFFSET ?', (namespace, candidate_id,limit,offset))]
        total = db.execute('SELECT COUNT(*) FROM tc_authoring_versions WHERE namespace=? AND id=?',(namespace,candidate_id)).fetchone()[0]
        return dict(current=current, history=history,history_total=total,limit=limit,offset=offset)
    if principal['role'] == 'viewer':
        raise ValueError('조회 계정은 초안을 저장할 수 없습니다')
    status = args['status']
    if not (status is None or isinstance(status, str)) or status not in STATUSES:
        raise ValueError('작성 상태는 등록 완료·보완 대기·구현 대기만 지정하세요')
    expected = args['expected_revision']
    if type(expected) is not int or expected < 0:
        raise ValueError('expected_revision을 확인하세요')
    operation_id = string(args['operation_id'], '작업 ID', 200)
    fingerprint = digest(args)
    previous = db.execute('SELECT payload_hash,payload_json FROM tc_authoring_versions WHERE namespace=? AND operation_id=?', (namespace, operation_id)).fetchone()
    if previous:
        if previous[0] != fingerprint:
            raise Conflict('같은 작업 ID의 내용이 다릅니다')
        return dict(item=json.loads(previous[1]), duplicate=True)
    if expected != (current['revision'] if current else 0):
        raise Conflict('초안이 변경됐습니다. 최신 버전을 조회한 뒤 보완하세요')
    draft = args['draft']
    if not isinstance(draft, dict) or not isinstance(draft.get('title'), str) or not draft['title'].strip():
        raise ValueError('제목이 포함된 전체 초안 객체가 필요합니다')
    for field in ('title','objective','precondition'):
        if field in draft: string(draft[field], '초안 '+field, 100000, empty=field != 'title')
    if 'steps' in draft and (not isinstance(draft['steps'],list) or len(draft['steps']) > 1000 or any(not isinstance(step,dict) or any(field in step and not isinstance(step[field],str) for field in ('step','test_data','expected_result')) for step in draft['steps'])):
        raise ValueError('단계는 문자열 열을 가진 객체 배열이어야 합니다')
    if 'blockers' in draft and not isinstance(draft['blockers'],list):
        raise ValueError('미해결 항목은 배열이어야 합니다')
    if len(json.dumps(args, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ValueError('초안·근거는 1MB 이하로 나누어 저장하세요')
    keys = args['story_keys']
    if not isinstance(keys, list) or len(keys) > 100 or any(not isinstance(key,str) or not re.fullmatch(namespace.rsplit('/',1)[1] + r'-[1-9][0-9]*', key) for key in keys):
        raise ValueError('같은 프로젝트의 Story 키 목록이 필요합니다')
    if not isinstance(args['evidence'], list) or any(not isinstance(item,dict) for item in args['evidence']):
        raise ValueError('조회한 근거 객체 목록이 필요합니다')
    reason = string(args['reason'], '대기 사유', empty=status not in {'needs_evidence','awaiting_implementation'})
    resume = string(args['resume_when'], '재개 조건', empty=status not in {'needs_evidence','awaiting_implementation'})
    target = args.get('target')
    if target is not None and not isinstance(target,dict):
        raise ValueError('저장 위치는 객체 또는 null입니다')
    registration = args.get('registration')
    if status == 'registered':
        if not isinstance(registration,dict) or registration.get('status') != 'verified' or registration.get('draft_hash') != digest(draft):
            raise ValueError('현재 초안과 일치하는 verified 등록 재조회 기록이 필요합니다')
        if not re.fullmatch(namespace.rsplit('/',1)[1] + r'-T[1-9][0-9]*', str(registration.get('remote_key',''))):
            raise ValueError('실제 Zephyr TC 키가 필요합니다')
        if not re.fullmatch(r'[a-f0-9]{64}', str(registration.get('readback_hash',''))) or registration.get('human_reviewed_draft_hash') != digest(draft):
            raise ValueError('재조회 해시와 최종 사람 검토의 초안 해시가 필요합니다')
        if draft.get('blockers'):
            raise ValueError('미해결 항목이 있는 초안은 등록 완료로 처리할 수 없습니다')
        if not target or target.get('project_key') != namespace.rsplit('/',1)[1] or not target.get('folder_name') or type(target.get('folder_id')) is not int:
            raise ValueError('확인한 프로젝트·폴더 저장 위치가 필요합니다')
    elif registration is not None:
        raise ValueError('등록 기록은 등록 완료 상태에서만 지정하세요')
    registered_key = registration['remote_key'] if registration else current.get('registered_key') if current else None
    if registration and current and current.get('registered_key') not in (None, registered_key):
        raise Conflict('이미 연결된 원격 TC가 있습니다. 재생성하지 말고 해당 TC를 확인하세요')
    item = dict(registered_key=registered_key,namespace=namespace,id=candidate_id,revision=expected+1,draft=draft,story_keys=sorted(set(keys)),status=status,
                reason=reason,resume_when=resume,evidence=args['evidence'],target=target,registration=registration,
                created_at=current['created_at'] if current else now,updated_at=now)
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
    db.execute('INSERT INTO tc_authoring_versions VALUES (?,?,?,?,?,?,?,?)', (namespace,candidate_id,expected+1,operation_id,fingerprint,encoded,principal['id'],now))
    db.execute('INSERT INTO tc_authoring_candidates VALUES (?,?,?,?) ON CONFLICT(namespace,id) DO UPDATE SET revision=excluded.revision,payload_json=excluded.payload_json', (namespace,candidate_id,expected+1,encoded))
    return dict(item=item,duplicate=False)
