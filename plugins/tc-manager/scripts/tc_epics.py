"""Observed Jira Epic hierarchy, separate from reviewed Story and TC records."""
import json
import re

import tc_registry as registry

OPERATIONS = {
    'epics.search': ({'namespace'}, {'q', 'limit', 'offset'}),
    'epics.get': ({'namespace', 'key'}, {'limit', 'offset'}),
    'epics.for-case': ({'namespace', 'tc_id'}, set()),
}
DDL = (
    'CREATE TABLE IF NOT EXISTS epic_source_snapshots (namespace TEXT NOT NULL, epic_key TEXT NOT NULL, source_hash TEXT NOT NULL, raw_json BLOB NOT NULL, PRIMARY KEY(namespace,epic_key,source_hash))',
    'CREATE TABLE IF NOT EXISTS epic_source_heads (namespace TEXT NOT NULL, epic_key TEXT NOT NULL, source_hash TEXT NOT NULL, observed_at TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)), PRIMARY KEY(namespace,epic_key), FOREIGN KEY(namespace,epic_key,source_hash) REFERENCES epic_source_snapshots(namespace,epic_key,source_hash))',
    'CREATE TABLE IF NOT EXISTS story_epic_snapshots (namespace TEXT NOT NULL, story_key TEXT NOT NULL, source_hash TEXT NOT NULL, parent_json BLOB NOT NULL, PRIMARY KEY(namespace,story_key,source_hash))',
    'CREATE TABLE IF NOT EXISTS story_epic_heads (namespace TEXT NOT NULL, story_key TEXT NOT NULL, source_hash TEXT NOT NULL, observed_at TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)), PRIMARY KEY(namespace,story_key), FOREIGN KEY(namespace,story_key,source_hash) REFERENCES story_epic_snapshots(namespace,story_key,source_hash))',
    'CREATE TABLE IF NOT EXISTS epic_collection_state (namespace TEXT PRIMARY KEY, observed_at TEXT NOT NULL, complete INTEGER NOT NULL CHECK(complete IN (0,1)), last_complete_at TEXT)',
)


def initialize(db):
    for sql in DDL:
        db.execute(sql)


def available(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='epic_source_heads'").fetchone())


def namespace_parts(namespace):
    if not isinstance(namespace, str) or namespace.count('/') != 1:
        raise ValueError('INVALID_EPIC_NAMESPACE')
    host, project = namespace.split('/')
    if (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', host)
            or not re.fullmatch(r'[A-Z][A-Z0-9_]*', project)):
        raise ValueError('INVALID_EPIC_NAMESPACE')
    return host, project


def issue_key(key, project=None):
    if (not isinstance(key, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*', key)
            or project and key.rsplit('-', 1)[0] != project):
        raise ValueError('INVALID_EPIC_ISSUE_KEY')
    return key


def is_epic(issue_type):
    if not isinstance(issue_type, dict):
        raise ValueError('INVALID_EPIC_ISSUE_TYPE')
    level = issue_type.get('hierarchyLevel')
    if level is not None:
        if type(level) is not int:
            raise ValueError('INVALID_EPIC_ISSUE_TYPE')
        return level == 1
    name = issue_type.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('INVALID_EPIC_ISSUE_TYPE')
    return name.strip().casefold() in {'epic', '에픽'}


def validate_issue(raw, full=False):
    if not isinstance(raw, dict):
        raise ValueError('INVALID_EPIC_SOURCE')
    key = issue_key(raw.get('key'))
    if not re.fullmatch(r'[1-9][0-9]*', str(raw.get('id', ''))):
        raise ValueError('INVALID_EPIC_SOURCE_ID')
    fields = raw.get('fields')
    if not isinstance(fields, dict) or not isinstance(fields.get('summary'), str):
        raise ValueError('INVALID_EPIC_SOURCE_FIELDS')
    epic = is_epic(fields.get('issuetype'))
    if full:
        import tc_stories as stories
        if (not epic or fields.get('project', {}).get('key') != key.rsplit('-', 1)[0]
                or 'description' not in fields):
            raise ValueError('INVALID_EPIC_SOURCE_FIELDS')
        stories.timestamp(fields.get('updated'))
    return key


def validate_payload(namespace, payload):
    import tc_stories as stories
    _, project = namespace_parts(namespace)
    required = {'observed_at', 'complete', 'story_keys', 'epics', 'parents', 'epic_keys'}
    if not isinstance(payload, dict) or set(payload) != required or type(payload['complete']) is not bool:
        raise ValueError('INVALID_EPIC_PAYLOAD')
    observed = stories.timestamp(payload['observed_at'])
    for name in ('story_keys', 'epic_keys', 'epics', 'parents'):
        if not isinstance(payload[name], list):
            raise ValueError('INVALID_EPIC_PAYLOAD')
    keys = {}
    for name in ('story_keys', 'epic_keys'):
        values = [issue_key(value, project) for value in payload[name]]
        if len(values) != len(set(values)):
            raise ValueError('DUPLICATE_EPIC_SOURCE_KEY')
        keys[name] = set(values)
    epics = {}
    for raw in payload['epics']:
        key = validate_issue(raw, full=True)
        if key in epics:
            raise ValueError('DUPLICATE_EPIC_SOURCE_KEY')
        epics[key] = raw
    parents = {}
    for row in payload['parents']:
        if not isinstance(row, dict) or set(row) != {'story_key', 'parent'}:
            raise ValueError('INVALID_STORY_EPIC_RELATION')
        key = issue_key(row['story_key'], project)
        if key in parents or key not in keys['story_keys']:
            raise ValueError('INVALID_STORY_EPIC_RELATION')
        if row['parent'] is not None:
            validate_issue(row['parent'])
            if row['parent']['key'] == key:
                raise ValueError('INVALID_STORY_EPIC_RELATION')
        parents[key] = row['parent']
    own_epics = {key for key in epics if key.rsplit('-', 1)[0] == project}
    if payload['complete'] and (set(parents) != keys['story_keys'] or own_epics != keys['epic_keys']):
        raise ValueError('INCOMPLETE_EPIC_INVENTORY')
    for parent in parents.values():
        if parent is None:
            continue
        target = epics.get(parent['key'])
        parent_is_epic = is_epic(parent['fields']['issuetype'])
        if target is not None and (str(target['id']) != str(parent['id']) or not parent_is_epic):
            raise ValueError('EPIC_PARENT_IDENTITY_MISMATCH')
        if payload['complete'] and parent_is_epic and parent['key'].rsplit('-', 1)[0] == project and target is None:
            raise ValueError('INCOMPLETE_EPIC_PARENT')
    return observed, keys, epics, parents


def apply(db, namespace, payload, protected_stories=(), protected_epics=()):
    observed, keys, epics, parents = validate_payload(namespace, payload)
    _, project = namespace_parts(namespace)
    protected = {'epic': set(protected_epics), 'story': set(protected_stories)}
    for values in protected.values():
        for key in values:
            issue_key(key, project)
    db.execute('SAVEPOINT tc_epics_apply')
    try:
        initialize(db)
        previous = db.execute('SELECT last_complete_at FROM epic_collection_state WHERE namespace=?', (namespace,)).fetchone()
        if previous and previous['last_complete_at'] and observed < previous['last_complete_at']:
            db.execute('RELEASE tc_epics_apply')
            return dict(epics_updated=0, relations_updated=0, observed_at=observed, complete=payload['complete'], skipped='older_than_complete_collection')
        written = {'epic': 0, 'story': 0}
        for kind, rows in (('epic', epics), ('story', parents)):
            snapshot_table = 'epic_source_snapshots' if kind == 'epic' else 'story_epic_snapshots'
            head_table = 'epic_source_heads' if kind == 'epic' else 'story_epic_heads'
            key_column = 'epic_key' if kind == 'epic' else 'story_key'
            for key, raw in rows.items():
                if key.rsplit('-', 1)[0] != project or key in protected[kind]:
                    continue
                old = db.execute(f'SELECT observed_at FROM {head_table} WHERE namespace=? AND {key_column}=?', (namespace, key)).fetchone()
                if old and old['observed_at'] > observed:
                    continue
                digest = registry.digest(raw)
                db.execute(f'INSERT OR IGNORE INTO {snapshot_table} VALUES (?,?,?,?)',
                           (namespace, key, digest, registry.storage.pack(db, registry.canonical(raw))))
                db.execute(f'INSERT INTO {head_table} VALUES (?,?,?,?,1) ON CONFLICT(namespace,{key_column}) DO UPDATE SET source_hash=excluded.source_hash,observed_at=excluded.observed_at,active=1',
                           (namespace, key, digest, observed))
                written[kind] += 1
            if payload['complete']:
                inventory = keys['epic_keys' if kind == 'epic' else 'story_keys']
                existing = db.execute(f'SELECT {key_column} FROM {head_table} WHERE namespace=? AND observed_at<=?', (namespace, observed)).fetchall()
                for row in existing:
                    if row[key_column] not in inventory and row[key_column] not in protected[kind]:
                        db.execute(f'UPDATE {head_table} SET active=0,observed_at=? WHERE namespace=? AND {key_column}=?', (observed, namespace, row[key_column]))
        db.execute('INSERT INTO epic_collection_state VALUES (?,?,?,?) ON CONFLICT(namespace) DO UPDATE SET observed_at=MAX(observed_at,excluded.observed_at),complete=CASE WHEN excluded.observed_at>=observed_at THEN excluded.complete ELSE complete END,last_complete_at=CASE WHEN excluded.last_complete_at IS NULL THEN last_complete_at WHEN last_complete_at IS NULL THEN excluded.last_complete_at ELSE MAX(last_complete_at,excluded.last_complete_at) END',
                   (namespace, observed, int(payload['complete']), observed if payload['complete'] else None))
        db.execute('RELEASE tc_epics_apply')
        return dict(epics_updated=written['epic'], relations_updated=written['story'], observed_at=observed, complete=payload['complete'])
    except Exception:
        db.execute('ROLLBACK TO tc_epics_apply')
        db.execute('RELEASE tc_epics_apply')
        raise


def source_status(db, namespace):
    if not available(db):
        return dict(status='not_collected')
    row = db.execute('SELECT observed_at,complete,last_complete_at FROM epic_collection_state WHERE namespace=?', (namespace,)).fetchone()
    if row is None:
        return dict(status='not_collected')
    return dict(status='collected', observed_at=row['observed_at'], complete=bool(row['complete']), last_complete_at=row['last_complete_at'])


def parent_view(namespace, raw):
    host, _ = namespace_parts(namespace)
    fields = raw['fields']
    return dict(key=raw['key'], title=fields['summary'], issue_type=fields['issuetype'].get('name', ''),
                source_url=f'https://{host}/browse/{raw["key"]}')


def epic_view(namespace, raw):
    from tc_story_intake import searchable
    result = parent_view(namespace, raw)
    warnings = set()
    result.update(description=searchable(raw['fields']['description'], warnings),
                  text_extraction=dict(status='partial' if warnings else 'complete', warnings=sorted(warnings)))
    return result


def current_epics(db, namespace):
    if not available(db):
        return {}
    return {row['epic_key']: epic_view(namespace, json.loads(row['raw_json'])) for row in db.execute(
        'SELECT h.epic_key,s.raw_json FROM epic_source_heads h JOIN epic_source_snapshots s USING(namespace,epic_key,source_hash) WHERE h.namespace=? AND h.active=1', (namespace,))}


def for_story(db, namespace, key):
    _, project = namespace_parts(namespace)
    issue_key(key, project)
    if not available(db):
        return dict(status='not_collected', epic=None)
    row = db.execute('SELECT h.active,h.observed_at,s.parent_json FROM story_epic_heads h JOIN story_epic_snapshots s USING(namespace,story_key,source_hash) WHERE h.namespace=? AND h.story_key=?', (namespace, key)).fetchone()
    if row is None:
        return dict(status='not_collected', epic=None)
    result = dict(observed_at=row['observed_at'], epic=None)
    if not row['active']:
        return dict(result, status='not_in_latest_collection')
    parent = json.loads(row['parent_json'])
    if parent is None:
        return dict(result, status='no_parent')
    result['parent'] = parent_view(namespace, parent)
    if not is_epic(parent['fields']['issuetype']):
        return dict(result, status='non_epic_parent')
    if parent['key'].rsplit('-', 1)[0] != project:
        return dict(result, status='cross_project_parent', limitation='다른 프로젝트의 상위 에픽입니다. 해당 프로젝트 조회 권한으로 별도 확인합니다.')
    row = db.execute('SELECT h.active,s.raw_json FROM epic_source_heads h JOIN epic_source_snapshots s USING(namespace,epic_key,source_hash) WHERE h.namespace=? AND h.epic_key=?', (namespace, parent['key'])).fetchone()
    if row is None or not row['active']:
        return dict(result, status='epic_not_collected')
    result['epic'] = epic_view(namespace, json.loads(row['raw_json']))
    return dict(result, status='linked')


def grouped(db, namespace, epic_key, story_rows, case_index, relations):
    import tc_stories as stories
    import tc_source_catalog as catalog
    found, cases = [], {}
    for key in relations.get(epic_key, []):
        story = story_rows.get(key)
        linked = stories.related_cases(case_index, story, db, namespace) if story else (catalog.story_cases(db, namespace, key) or [])
        visible = []
        for case in linked:
            tc_id = case.get('tc_id', case.get('key'))
            case_namespace = case.get('namespace', namespace)
            if isinstance(tc_id, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*-T[1-9][0-9]*', tc_id):
                case_namespace = namespace.rsplit('/', 1)[0] + '/' + tc_id.rsplit('-T', 1)[0]
            if case_namespace != namespace or not isinstance(tc_id, str):
                continue
            item = dict(case, namespace=namespace, tc_id=tc_id)
            cases[(namespace, tc_id)] = item
            visible.append(item)
        found.append(dict(story=stories.content_view(story) if story else dict(key=key), cases=visible,
                          status='collected' if story else 'story_not_collected',
                          finding='observed_links' if visible else 'no_link_in_collected_data'))
    found.sort(key=lambda row: row['story']['key'])
    return found, [cases[key] for key in sorted(cases)]


def execute(db, namespace, op, args):
    import tc_stories as stories
    _, project = namespace_parts(namespace)
    if op not in OPERATIONS:
        raise ValueError('지원하지 않는 Epic 명령입니다')
    required, optional = OPERATIONS[op]
    if not isinstance(args, dict) or set(args) - required - optional or (required - {'namespace'}) - set(args):
        raise ValueError('Epic 명령의 필수/지원 필드를 확인하세요')
    if args.get('namespace', namespace) != namespace:
        raise ValueError('INVALID_EPIC_NAMESPACE')
    status = source_status(db, namespace)
    if op == 'epics.for-case':
        tc_id = stories.text(args['tc_id'], 'tc_id')
        if status['status'] == 'not_collected':
            return dict(status='not_collected', tc_id=tc_id, stories=[], epics=[], source_status=status)
        import tc_source_catalog as catalog
        if (not db.execute("SELECT 1 FROM sqlite_master WHERE name='story_link_reads'").fetchone()
                and catalog.case_stories(db, namespace, tc_id) is None):
            return dict(status='not_observed', tc_id=tc_id, stories=[], epics=[], source_status=status)
        links = stories.case_links(db, namespace, tc_id)
        items, epics, unresolved, seen = [], {}, [], set()
        def add_story(story, content_status):
            key = story['key']
            if key in seen:
                return
            seen.add(key)
            relation = for_story(db, namespace, key)
            items.append(dict(story=story, hierarchy=relation, status=content_status))
            if relation['epic']:
                epics[relation['epic']['key']] = relation['epic']
        for item in links['stories']:
            story = item['story']
            if story['key'].rsplit('-', 1)[0] != project:
                unresolved.append(dict(key=story['key'], namespace=namespace.rsplit('/', 1)[0] + '/' + story['key'].rsplit('-', 1)[0],
                                       status='outside_namespace', scope='outside_requested_namespace'))
                continue
            add_story(story, 'collected')
        for link in links.get('unresolved_links', []):
            key, target_namespace = link.get('key'), link.get('namespace')
            if (target_namespace == namespace and isinstance(key, str)
                    and re.fullmatch(re.escape(project) + r'-[1-9][0-9]*', key)):
                add_story(dict(key=key), 'story_not_collected')
                unresolved.append(dict(key=key, namespace=namespace, status='story_not_collected', scope='requested_namespace'))
            elif isinstance(key, str) and isinstance(target_namespace, str):
                unresolved.append(dict(key=key, namespace=target_namespace, status='outside_namespace', scope='outside_requested_namespace'))
            else:
                unresolved.append(dict(issue_id=link.get('issue_id'), status='story_identity_unresolved', scope='unresolved'))
        return dict(tc_id=tc_id, stories=items, epics=[epics[key] for key in sorted(epics)], unresolved_links=unresolved, source_status=status,
                    limitation='확인된 Story 연결을 통해 에픽을 조회합니다. 연결 누락은 기능 미검증 확정이 아닙니다.')
    limit, offset = args.get('limit', 20), args.get('offset', 0)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError('limit/offset 형식을 확인하세요')
    epics = current_epics(db, namespace)
    if op == 'epics.get':
        key = issue_key(args['key'], project)
        if key not in epics:
            return dict(status='not_collected', key=key, source_status=status)
        selected = [(key, epics[key])]
    else:
        query = args.get('q', '')
        if not isinstance(query, str):
            raise ValueError('q는 문자열이어야 합니다')
        terms = registry.tokens(query)
        selected = [(key, epic) for key, epic in sorted(epics.items()) if not terms or terms & registry.tokens(' '.join((key, epic['title'], epic['description'])))]
    if not selected:
        return dict(status='searched' if status['status'] != 'not_collected' else 'not_collected', total=0, items=[],
                    offset=offset, limit=limit, source_status=status)
    story_rows = stories.current_stories(db, namespace)
    index = stories.case_index(db, namespace) if db.execute("SELECT 1 FROM sqlite_master WHERE name='story_link_reads'").fetchone() else {}
    relations = {}
    if available(db):
        for row in db.execute('SELECT h.story_key,s.parent_json FROM story_epic_heads h JOIN story_epic_snapshots s USING(namespace,story_key,source_hash) WHERE h.namespace=? AND h.active=1', (namespace,)):
            parent = json.loads(row['parent_json'])
            if parent and is_epic(parent['fields']['issuetype']):
                relations.setdefault(parent['key'], []).append(row['story_key'])
    items = []
    for key, epic in selected if op == 'epics.get' else selected[offset:offset + limit]:
        grouped_stories, cases = grouped(db, namespace, key, story_rows, index, relations)
        item = dict(epic=epic, story_count=len(grouped_stories), tc_count=len(cases),
                    unlinked_story_count=sum(not row['cases'] for row in grouped_stories),
                    finding='observed_links' if cases else 'no_link_in_collected_data')
        if op == 'epics.get':
            return dict(item, status='collected', stories=grouped_stories[offset:offset + limit], cases=cases,
                        offset=offset, limit=limit, source_status=status,
                        limitation='TC 수는 연결된 TC 키의 중복을 제거한 값입니다. 연결 미확인은 실제 TC 부재나 검증 공백 확정이 아닙니다.')
        items.append(item)
    return dict(status='searched' if status['status'] != 'not_collected' else 'not_collected', total=len(selected), items=items,
                offset=offset, limit=limit, source_status=status)
