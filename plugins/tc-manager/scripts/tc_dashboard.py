"""Rebuildable read model. Source content, versions and review history remain untouched."""
from datetime import datetime, timedelta, timezone
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import tc_registry as registry
import tc_checks as checks

FILTERS = {'projects', 'team', 'inventory', 'scope', 'ai_agent', 'month', 'date_missing', 'folder', 'label', 'q', 'limit', 'offset', 'product', 'created_through', 'created_from'}
OPERATIONS = {
    'dashboard.projects': ({'namespace'}, set()),
    'dashboard.summary': ({'namespace'}, FILTERS | {'months', 'year', 'start_month', 'end_month', 'all_time'}),
    'catalog.list': ({'namespace'}, FILTERS | {'check_status', 'sort', 'unlinked'}),
    'catalog.get': ({'namespace', 'id'}, set()),
}
KST = timezone(timedelta(hours=9))
DDL = (
    'CREATE TABLE IF NOT EXISTS dashboard_cases (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, project TEXT NOT NULL, title TEXT NOT NULL, objective TEXT NOT NULL, folder TEXT NOT NULL, team TEXT NOT NULL, inventory TEXT NOT NULL, kpi INTEGER, created_at TEXT, created_month TEXT, ai_agent INTEGER NOT NULL, labels_json TEXT NOT NULL, folder_missing INTEGER NOT NULL, observed_at TEXT, PRIMARY KEY(namespace,tc_id))',
    'CREATE INDEX IF NOT EXISTS dashboard_scope ON dashboard_cases(namespace,team,inventory,created_month)',
    'CREATE INDEX IF NOT EXISTS dashboard_month ON dashboard_cases(created_month,team,inventory,ai_agent)',
    'CREATE TABLE IF NOT EXISTS dashboard_links (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, story_namespace TEXT NOT NULL, story_key TEXT NOT NULL, relation TEXT NOT NULL, basis TEXT NOT NULL, PRIMARY KEY(namespace,tc_id,story_namespace,story_key,relation,basis))',
    'CREATE INDEX IF NOT EXISTS dashboard_story ON dashboard_links(story_namespace,story_key)',
    'CREATE TABLE IF NOT EXISTS dashboard_story_dates (namespace TEXT NOT NULL,issue_key TEXT NOT NULL,created_at TEXT,created_month TEXT,PRIMARY KEY(namespace,issue_key))',
    'CREATE TABLE IF NOT EXISTS dashboard_story_status (namespace TEXT NOT NULL,issue_key TEXT NOT NULL,status_category TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(namespace,issue_key))',
    'CREATE TABLE IF NOT EXISTS dashboard_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
)


def exists(db, table):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone())


def original_url(namespace, tc_id):
    parts = namespace.rsplit('/', 1) if isinstance(namespace, str) else []
    if len(parts) != 2 or parts[0] != 'hanwhavision.atlassian.net' or parts[1] not in {'PX', 'PXW', 'PXM'}:
        return None
    project = parts[1]
    if not isinstance(tc_id, str) or not re.fullmatch(re.escape(project) + r'-T[1-9][0-9]*', tc_id):
        return None
    url = ('https://' + parts[0] + '/jira/software/projects/' + project +
           '/apps/3feb7ced-1450-4676-aded-099c99bf534b/2baaeb69-15ac-4955-8eb6-e346aa1567aa#/v2/testCase/' + tc_id)
    project_id = {'PX': '10194', 'PXW': '12005', 'PXM': '11273'}.get(project)
    return url + ('?projectId=' + project_id if project_id else '')


def upsert_story_status(db, namespace, raw):
    category = (raw.get('fields', {}).get('status') or {}).get('statusCategory', {}).get('key')
    updated = raw.get('fields', {}).get('updated')
    if not updated or not raw.get('key'):
        return
    try:
        updated = datetime.fromisoformat(updated.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return
    category = category if category in {'new', 'indeterminate', 'done'} else None
    db.execute('INSERT INTO dashboard_story_status VALUES (?,?,?,?) ON CONFLICT(namespace,issue_key) DO UPDATE SET status_category=excluded.status_category,updated_at=excluded.updated_at WHERE excluded.updated_at>=dashboard_story_status.updated_at',
               (namespace, raw['key'], category, updated))


def story_completion(db, namespaces, start=None, end=None):
    tables = [t for t in ['story_heads', 'story_source_heads'] if exists(db, t)]
    if not tables:
        return dict(total=0, completed=0, unknown=0, percent=None)
    heads = ' UNION '.join('SELECT namespace,issue_key FROM ' + t for t in tables)
    marks = ','.join('?' for _ in namespaces)
    date_join = ' LEFT JOIN dashboard_story_dates d USING(namespace,issue_key)' if start else ''
    date_filter = ' AND d.created_month>=? AND d.created_month<=?' if start else ''
    date_params = [start, end] if start else []
    row = db.execute("SELECT COUNT(*) AS total,SUM(s.status_category='done') AS completed,SUM(s.status_category IS NULL) AS unknown FROM (" + heads + ') h LEFT JOIN dashboard_story_status s USING(namespace,issue_key)' + date_join + ' WHERE h.namespace IN (' + marks + ')' + date_filter, [*namespaces, *date_params]).fetchone()
    total, completed, unknown = row['total'], row['completed'] or 0, row['unknown'] or 0
    return dict(total=total, completed=None if unknown else completed, unknown=unknown,
                percent=completed / total * 100 if total and not unknown else None)


def plain(value):
    return html.unescape(re.sub('<[^>]*>', ' ', value or '')).strip()


def month(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.astimezone(KST).strftime('%Y-%m') if date.tzinfo else None
    except ValueError:
        return None


def project_case(db, namespace, raw, observed_at=None):
    import tc_source_catalog as catalog
    folder = raw.get('folder_path') or ''
    scope = catalog.folder_scope(folder)
    parts = [p.strip().lower() for p in folder.split('/') if p.strip()]
    inventory = 'excluded' if any('카운트 금지' in p for p in parts) else (
        'test' if any(re.match(r'^\[test\]|^(test|tests|테스트|테스트 폴더|테스트폴더)$', p) for p in parts) else 'active')
    labels = raw.get('labels') or []
    labels = [x if isinstance(x, str) else x.get('name', '') for x in labels if isinstance(x, (str, dict))]
    created = raw.get('createdOn')
    return (namespace, raw['key'], namespace.rsplit('/', 1)[-1], raw['name'], plain(raw.get('objective')), folder,
            scope['team'], inventory, catalog.kpi_membership(namespace, raw)['in_scope'], created if month(created) else None,
            month(created), int('ai_agent' in labels), registry.canonical(labels), int(not folder), observed_at)


def upsert(db, namespace, rows, observed_at=None):
    values = [project_case(db, namespace, row, observed_at) for row in rows]
    db.executemany('INSERT INTO dashboard_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(namespace,tc_id) DO UPDATE SET project=excluded.project,title=excluded.title,objective=excluded.objective,folder=excluded.folder,team=excluded.team,inventory=excluded.inventory,kpi=excluded.kpi,created_at=excluded.created_at,created_month=excluded.created_month,ai_agent=excluded.ai_agent,labels_json=excluded.labels_json,folder_missing=excluded.folder_missing,observed_at=excluded.observed_at', values)


def update_links(db, namespace, links, removed=()):
    for key in set(links) | set(removed):
        db.execute("DELETE FROM dashboard_links WHERE story_namespace=? AND story_key=? AND basis='zephyr_issue_link_api'", (namespace, key))
    for story, cases in links.items():
        db.executemany('INSERT OR IGNORE INTO dashboard_links VALUES (?,?,?,?,?,?)', [
            (namespace.rsplit('/', 1)[0] + '/' + tc['key'].rsplit('-T', 1)[0], tc['key'], namespace, story,
             'ISSUE_LINK', 'zephyr_issue_link_api') for tc in cases])


def recorded_links(db, namespace=None, tc_id=None):
    if not exists(db, 'story_link_reads') or not exists(db, 'story_heads'):
        return
    where, params = ('WHERE r.namespace=? AND r.tc_id=?', (namespace, tc_id)) if tc_id else ('', ())
    rows = db.execute('SELECT r.* FROM story_link_reads r ' + where + ' ORDER BY observed_at', params)
    identities = {}
    for row in db.execute('SELECT s.namespace,s.issue_key,s.raw_json FROM story_snapshots s JOIN story_heads h ON h.namespace=s.namespace AND h.issue_key=s.issue_key AND h.content_hash=s.content_hash'):
        identities[(row['namespace'], json.loads(row['raw_json'])['issue_id'])] = row['issue_key']
    if tc_id:
        db.execute("DELETE FROM dashboard_links WHERE namespace=? AND tc_id=? AND basis='recorded_readback'", (namespace, tc_id))
    else:
        db.execute("DELETE FROM dashboard_links WHERE basis='recorded_readback'")
    latest = {}
    for row in rows:
        latest[(row['namespace'], row['tc_id'])] = json.loads(row['payload_json']).get('links', [])
    for (ns, key), links in latest.items():
        for link in links:
            story_key = identities.get((ns, link['issue_id']))
            if story_key and link['type'] in {'COVERAGE', 'ISSUE_LINK'}:
                db.execute('INSERT OR IGNORE INTO dashboard_links VALUES (?,?,?,?,?,?)',
                           (ns, key, ns, story_key, link['type'], 'recorded_readback'))


def reclassify_teams(db):
    import tc_source_catalog as catalog
    rows = db.execute("SELECT namespace,tc_id,folder FROM dashboard_cases WHERE team='unclassified'").fetchall()
    counts = {'SQA': 0, 'SQE': 0, 'other': 0}
    for row in rows:
        team = catalog.folder_scope(row['folder'])['team']
        changed = db.execute("UPDATE dashboard_cases SET team=? WHERE namespace=? AND tc_id=? AND team='unclassified' AND folder=?",
                             (team, row['namespace'], row['tc_id'], row['folder'])).rowcount
        counts[team] += changed
    return dict(scanned=len(rows), updated=sum(counts.values()), teams=counts)


def initialize(db):
    fresh = not exists(db, 'dashboard_meta')
    fresh_status = not exists(db, 'dashboard_story_status')
    for sql in DDL:
        db.execute(sql)
    if fresh:
        rebuild(db)
    if fresh_status and exists(db, 'story_source_heads'):
        for row in db.execute('SELECT h.namespace,v.raw_json FROM story_source_heads h JOIN story_source_views v USING(namespace,issue_key,source_hash,view_hash)'):
            raw = json.loads(row['raw_json'])
            if raw.get('fields', {}).get('status'):
                upsert_story_status(db, row['namespace'], raw)


def rebuild(db):
    db.execute('DELETE FROM dashboard_cases')
    db.execute('DELETE FROM dashboard_links')
    if exists(db, 'source_test_cases'):
        for row in db.execute('SELECT namespace,raw_json FROM source_test_cases'):
            upsert(db, row['namespace'], [json.loads(row['raw_json'])])
    if exists(db, 'source_story_links'):
        for row in db.execute('SELECT namespace,issue_key,links_json FROM source_story_links'):
            update_links(db, row['namespace'], {row['issue_key']: json.loads(row['links_json'])})
    recorded_links(db)
    db.execute("INSERT OR REPLACE INTO dashboard_meta VALUES ('built_at',?)", (datetime.now(timezone.utc).isoformat(),))


def projects(db, allowed, data_dir=None):
    import tc_source_catalog as catalog
    namespaces = set()
    for table in ['source_sync', 'source_test_cases']:
        if exists(db, table):
            namespaces.update(row[0] for row in db.execute('SELECT DISTINCT namespace FROM ' + table))
    if not namespaces:
        for table in ['story_heads', 'cases']:
            if exists(db, table):
                namespaces.update(row[0] for row in db.execute('SELECT DISTINCT namespace FROM ' + table))
    configured = set()
    for path in {Path.cwd() / 'server.json', Path(data_dir).parent / 'server.json' if data_dir else Path.cwd() / 'server.json'}:
        if path.is_file():
            try:
                config = json.loads(path.read_text(encoding='utf-8-sig')).get('source_sync', {})
                host = urlsplit(config.get('site', '')).hostname
                configured.update(host + '/' + p for p in config.get('projects', []) if host and p in {'PX', 'PXW', 'PXM'})
            except (OSError, ValueError, TypeError):
                pass
    namespaces.update(configured)
    for prefix in {n.rsplit('/', 1)[0] for n in namespaces if not n.startswith('demo.')}:
        namespaces.update(prefix + '/' + p for p in ['PX', 'PXW', 'PXM'])
    result = [dict(namespace=ns, project=ns.rsplit('/', 1)[-1], **catalog.status(db, ns))
            for ns in sorted(namespaces) if (allowed == ['*'] or '*' in allowed or ns in allowed)
            and ns.rsplit('/', 1)[-1] in {'PX', 'PXW', 'PXM'}]
    for project in result:
        project['configured'] = project['namespace'] in configured
        if catalog.available(db, project['namespace']):
            row = db.execute('SELECT summary_json FROM source_sync WHERE namespace=?', (project['namespace'],)).fetchone()
            project['collection_partial'] = bool(json.loads(row[0]).get('partial'))
            if project.get('daily') and project['daily'].get('last_error'):
                project['status'] = 'refresh_failed'
    return result


def operational_state(data_dir):
    path = Path(data_dir) / 'daily-sync-state.json' if data_dir else None
    if not path or not path.is_file():
        return dict(status='not_recorded')
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('size')
        value = json.loads(path.read_text(encoding='utf-8'))
        return {key: value.get(key) for key in ['status', 'last_attempt', 'last_success', 'successful_day', 'phase']}
    except (OSError, ValueError):
        return dict(status='unavailable')


def api_health(data_dir, now=None):
    empty = dict(checked_at=None, stale=False, services={name: dict(status='not_checked') for name in ('jira', 'zephyr')})
    path = Path(data_dir) / 'source-api-health.json' if data_dir else None
    if not path or not path.is_file():
        return empty
    statuses = {'ok', 'auth_failed', 'forbidden', 'rate_limited', 'network_error', 'server_error', 'not_configured', 'invalid_response'}
    try:
        if path.stat().st_size > 16384:
            raise ValueError('size')
        value = json.loads(path.read_text(encoding='utf-8'))
        checked = datetime.fromisoformat(value['checked_at'])
        if checked.tzinfo is None:
            raise ValueError('timezone')
        result = dict(checked_at=checked.isoformat(), stale=((now or datetime.now(timezone.utc)) - checked).total_seconds() > 36 * 3600, services={})
        for name in ('jira', 'zephyr'):
            row = value['services'][name]
            if not isinstance(row, dict):
                raise ValueError('service')
            status = row['status'] if row['status'] in statuses else 'unavailable'
            code = row.get('http_status')
            result['services'][name] = dict(status=status, http_status=code if type(code) is int and 100 <= code <= 599 else None)
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return dict(empty, services={name: dict(status='unavailable') for name in ('jira', 'zephyr')})


def selected(db, args, allowed, data_dir=None):
    available = projects(db, allowed, data_dir)
    requested = args.get('projects') or ([args['namespace']] if args['namespace'] != 'all' else [])
    for ns in requested if isinstance(requested, list) else []:
        if isinstance(ns, str) and re.fullmatch(r'[^/]+/(PX|PXW|PXM)', ns) and ('*' in allowed or ns in allowed) and ns not in {p['namespace'] for p in available}:
            available.append(dict(namespace=ns, project=ns.rsplit('/', 1)[-1], status='not_collected', daily=None))
    namespaces = args.get('projects')
    if namespaces is None:
        namespaces = [args['namespace']] if args['namespace'] != 'all' else [p['namespace'] for p in available]
    if not isinstance(namespaces, list) or len(namespaces) > 20 or any(not isinstance(n, str) for n in namespaces):
        raise ValueError('projects 배열을 확인하세요')
    if any(n not in {p['namespace'] for p in available} for n in namespaces):
        raise ValueError('허용되거나 수집된 프로젝트를 선택하세요')
    return [p for p in available if p['namespace'] in namespaces]


def filters(args, namespaces, inventory_default='active'):
    team, inventory = args.get('team', 'SQA'), args.get('inventory', inventory_default)
    if team not in {'all', 'SQA', 'SQE', 'other', 'unclassified'} or inventory not in {'all', 'active', 'excluded', 'test'}:
        raise ValueError('팀/폴더 분류를 확인하세요')
    where, params = ["c.inventory != 'test'", 'c.namespace IN (' + ','.join('?' for _ in namespaces) + ')'], list(namespaces)
    for field, value in [('team', team), ('inventory', inventory)]:
        if field == 'team' and value in {'other', 'unclassified'}:
            where.append("c.team IN ('other','unclassified')")
        elif value != 'all':
            where.append('c.' + field + '=?'); params.append(value)
    if args.get('scope', 'all') not in {'all', 'px-kpi'}:
        raise ValueError('집계 범위를 확인하세요')
    if args.get('scope') == 'px-kpi':
        where.append('c.kpi=1')
    for flag in ['ai_agent', 'date_missing']:
        if flag in args and type(args[flag]) is not bool:
            raise ValueError(flag + '는 boolean이어야 합니다')
    if args.get('ai_agent'):
        where.append('c.ai_agent=1')
    if args.get('date_missing'):
        where.append('c.created_month IS NULL')
    if args.get('month'):
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args['month']):
            raise ValueError('월 형식을 확인하세요')
        where.append('c.created_month=?'); params.append(args['month'])
    if args.get('product'):
        if args['product'] not in {'VMS', 'Web', 'Mobile', 'all'}:
            raise ValueError('제품 범위를 확인하세요')
        vms = "(c.project='PX' AND (c.folder LIKE 'SQA Testcase%/ VMS Test Case%' OR c.folder LIKE 'SQE Test Case / SQE Full Test Case%' OR c.folder LIKE 'SQE Test Case / SQE BF TestCase%'))"
        products = {'VMS': vms, 'Web': "c.project='PXW'", 'Mobile': "c.project='PXM'"}
        where.append('(' + ' OR '.join(products.values()) + ')' if args['product'] == 'all' else products[args['product']])
    if args.get('created_from'):
        if not isinstance(args['created_from'], str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args['created_from']):
            raise ValueError('시작 월 형식을 확인하세요')
        where.append('c.created_month>=?'); params.append(args['created_from'])
    if args.get('created_through'):
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args['created_through']):
            raise ValueError('누적 기준 월 형식을 확인하세요')
        where.append('c.created_month<=?'); params.append(args['created_through'])
    if args.get('folder'):
        where.append('c.folder=?'); params.append(args['folder'])
    if args.get('label'):
        where.append('EXISTS (SELECT 1 FROM json_each(c.labels_json) WHERE value=?)'); params.append(args['label'])
    if args.get('q'):
        if not isinstance(args['q'], str) or len(args['q']) > 500:
            raise ValueError('검색어는 500자 이하여야 합니다')
        for term in args['q'].split():
            term = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append("(c.tc_id LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\' OR c.objective LIKE ? ESCAPE '\\')")
            params.extend(['%' + term + '%'] * 3)
    return ' AND '.join(where), params


def count(db, args, namespaces):
    where, params = filters(args, namespaces)
    return db.execute('SELECT COUNT(*) FROM dashboard_cases c WHERE ' + where, params).fetchone()[0]


def state(db, selection):
    times = [p.get('completed_at') for p in selection if p.get('completed_at')]
    complete = bool(selection) and all(p['status'] not in {'not_collected', 'targeted_snapshot', 'refresh_failed'} and not p.get('collection_partial') for p in selection)
    return dict(status='complete' if complete else 'partial' if times else 'not_collected',
                data_as_of=min(times) if times else None, projects=selection)


def execute(db, op, args, allowed, data_dir=None):
    import tc_source_catalog as catalog
    if op == 'dashboard.projects':
        return dict(items=projects(db, allowed, data_dir), collection_state=operational_state(data_dir), api_health=api_health(data_dir), built_at=db.execute("SELECT value FROM dashboard_meta WHERE key='built_at'").fetchone()[0])
    if op == 'catalog.get':
        ns = args['namespace']
        if '*' not in allowed and ns not in allowed:
            raise ValueError('접근할 수 없는 프로젝트입니다')
        row = db.execute('SELECT * FROM dashboard_cases WHERE namespace=? AND tc_id=?', (ns, args['id'])).fetchone()
        if not row or row['inventory'] == 'test':
            raise ValueError('수집된 TC를 찾을 수 없습니다')
        raw = json.loads(db.execute('SELECT raw_json FROM source_test_cases WHERE namespace=? AND tc_id=?', (ns, args['id'])).fetchone()[0])
        saved = db.execute('SELECT version,raw_json FROM cases WHERE namespace=? AND tc_id=? ORDER BY rowid DESC LIMIT 1', (ns, args['id'])).fetchone()
        scripts = raw.get('testScript') or {}
        steps = scripts.get('steps') if isinstance(scripts, dict) else None
        steps = steps if isinstance(steps, list) and all(isinstance(s, dict) for s in steps) else []
        reviews = [dict(r) for r in db.execute('SELECT version,decision,comment,actor_name,created_at FROM service_case_reviews WHERE namespace=? AND tc_id=? ORDER BY created_at DESC LIMIT 20', (ns, args['id']))] if exists(db, 'service_case_reviews') else []
        publications = [dict(json.loads(r['payload_json']), actor_name=r['actor_name'], created_at=r['created_at']) for r in db.execute('SELECT payload_json,actor_name,created_at FROM service_publications WHERE namespace=? AND tc_id=? ORDER BY ordinal DESC LIMIT 20', (ns, args['id']))] if exists(db, 'service_publications') else []
        check = checks.get(db, ns, args['id'])
        return dict(test_case=dict(row, check=check), check=check, objective=plain(raw.get('objective')), precondition=plain(raw.get('precondition')),
                    reviews=reviews, publications=publications,
                    steps=steps, script_collected=bool(steps), stored_version=(dict(version=saved['version'], test_case=json.loads(saved['raw_json'])) if saved else None),
                    source_url=original_url(ns, args['id']),
                    links=[dict(r) for r in db.execute('SELECT story_namespace,story_key,relation,basis FROM dashboard_links WHERE namespace=? AND tc_id=?', (ns, args['id']))],
                    source_status=catalog.status(db, ns))
    selection = selected(db, args, allowed, data_dir)
    namespaces = [p['namespace'] for p in selection]
    source_state = state(db, selection)
    if op == 'catalog.list':
        limit, offset = args.get('limit', 25), args.get('offset', 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('페이지 범위를 확인하세요')
        where, params = filters(args, namespaces)
        check_status, sort = args.get('check_status', 'all'), args.get('sort', 'default')
        if not isinstance(check_status, str) or check_status not in {'all', 'unchecked', 'checked'} or not isinstance(sort, str) or sort not in {'default', 'tc_asc', 'tc_desc', 'created_asc', 'created_desc', 'story_asc', 'story_desc', 'check_asc', 'check_desc'}:
            raise ValueError('확인 상태와 정렬 조건을 확인하세요')
        if 'unlinked' in args and type(args['unlinked']) is not bool:
            raise ValueError('unlinked는 boolean이어야 합니다')
        checked = "EXISTS (SELECT 1 FROM tc_human_checks h WHERE h.namespace=c.namespace AND h.tc_id=c.tc_id AND h.checked=1)" if checks.available(db) else '0'
        if check_status != 'all':
            where += ' AND c.ai_agent=1 AND ' + (checked if check_status == 'checked' else 'NOT (' + checked + ')')
        if args.get('unlinked'):
            where += " AND NOT EXISTS (SELECT 1 FROM dashboard_links l WHERE l.namespace=c.namespace AND l.tc_id=c.tc_id AND l.relation IN ('COVERAGE','ISSUE_LINK'))"
        key_fields = ['c.project', 'c.namespace', "CAST(substr(c.tc_id,instr(c.tc_id,'-T')+2) AS INTEGER)", 'c.tc_id']
        natural = ','.join(key_fields)
        order = {
            'default': natural, 'tc_asc': natural,
            'tc_desc': ','.join(field + ' DESC' for field in key_fields),
            'created_asc': 'julianday(c.created_at) IS NULL,julianday(c.created_at),' + natural,
            'created_desc': 'julianday(c.created_at) IS NULL,julianday(c.created_at) DESC,' + natural,
            'story_asc': 'story_count,' + natural, 'story_desc': 'story_count DESC,' + natural,
            'check_asc': 'CASE WHEN c.ai_agent!=1 THEN 2 WHEN ' + checked + ' THEN 1 ELSE 0 END,' + natural,
            'check_desc': 'CASE WHEN c.ai_agent!=1 THEN 2 WHEN ' + checked + ' THEN 0 ELSE 1 END,' + natural,
        }[sort]
        total = db.execute('SELECT COUNT(*) FROM dashboard_cases c WHERE ' + where, params).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT c.*,(SELECT COUNT(*) FROM (SELECT DISTINCT story_namespace,story_key FROM dashboard_links l WHERE l.namespace=c.namespace AND l.tc_id=c.tc_id)) AS story_count FROM dashboard_cases c WHERE ' + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', [*params, limit, offset])]
        states = checks.get_many(db, [(row['namespace'], row['tc_id']) for row in rows])
        for row in rows:
            row['check'] = states[(row['namespace'], row['tc_id'])]
            row['source_url'] = original_url(row['namespace'], row['tc_id'])
        folders = [r[0] for r in db.execute('SELECT DISTINCT c.folder FROM dashboard_cases c WHERE ' + where + ' ORDER BY c.folder LIMIT 200', params)]
        return dict(items=rows, total=total, limit=limit, offset=offset, folders=folders, source_state=source_state)
    now = datetime.now(KST)
    all_time = args.get('all_time', False)
    if type(all_time) is not bool or (all_time and any(k in args for k in ['year', 'months', 'start_month', 'end_month'])):
        raise ValueError('전체 기간과 다른 기간을 함께 선택할 수 없습니다')
    year = args.get('year')
    custom = 'start_month' in args or 'end_month' in args
    if custom:
        start, end = args.get('start_month'), args.get('end_month')
        if any(not isinstance(v, str) or not re.fullmatch(r'(19[7-9]\d|20\d{2})-(0[1-9]|1[0-2])', v) for v in [start, end]) or start > end or end > now.strftime('%Y-%m') or 'year' in args or 'months' in args:
            raise ValueError('시작·종료 월과 기간 선택을 확인하세요')
        first = int(start[:4])*12 + int(start[5:]) - 1
        last_month = int(end[:4])*12 + int(end[5:]) - 1
        if last_month-first >= 120:
            raise ValueError('최대 120개월까지 선택할 수 있습니다')
        month_keys = [f'{i//12:04d}-{i%12+1:02d}' for i in range(first,last_month+1)]
    elif year is not None:
        if 'months' in args or type(year) is not int or not 1970 <= year <= now.year:
            raise ValueError('연도는 1970년부터 올해까지 선택하며 기간과 함께 지정할 수 없습니다')
        month_keys = [f'{year:04d}-{m:02d}' for m in range(1, (now.month if year == now.year else 12) + 1)]
    else:
        months = args.get('months', 12)
        if type(months) is not int or not 1 <= months <= 60:
            raise ValueError('기간은 1~60개월이어야 합니다')
        month_keys = [f'{(now.year*12+now.month-1-i)//12:04d}-{(now.year*12+now.month-1-i)%12+1:02d}' for i in reversed(range(months))]
    earliest = now.year
    for table, field in [('dashboard_cases', 'created_month'), ('dashboard_story_dates', 'created_month')]:
        row = db.execute('SELECT MIN(' + field + ') FROM ' + table + ' WHERE namespace IN (' + ','.join('?' for _ in namespaces) + ') AND ' + field + ' IS NOT NULL', namespaces).fetchone()
        if row and row[0] and str(row[0])[:4].isdigit():
            earliest = min(earliest, max(1970, int(str(row[0])[:4])))
    if all_time:
        month_keys = [f'{y:04d}-{m:02d}' for y in range(earliest, now.year + 1) for m in range(1, (now.month if y == now.year else 12) + 1)]
    scoped = not all_time and (custom or 'year' in args or 'months' in args)
    chart_period = dict(scoped=scoped, all_time=all_time, year=year, years=list(range(now.year, earliest - 1, -1)), start=month_keys[0], end=month_keys[-1])
    base = {k: v for k, v in args.items() if k not in {'months', 'year', 'start_month', 'end_month', 'all_time', 'inventory', 'ai_agent', 'month', 'date_missing', 'scope'}}
    if scoped:
        base.update(created_from=month_keys[0], created_through=month_keys[-1])
    total = count(db, dict(base, inventory='all'), namespaces)
    eligible = count(db, dict(base, inventory='active'), namespaces)
    ai = count(db, dict(base, inventory='active', ai_agent=True), namespaces)
    excluded = {k: count(db, dict(base, inventory=k), namespaces) for k in ['excluded', 'test']}
    missing = count(db, dict(base, inventory='active', date_missing=True), namespaces)
    where, params = filters(dict(base, inventory='active'), namespaces)
    grouped = {r['created_month']: dict(r) for r in db.execute('SELECT c.created_month,COUNT(*) AS total,SUM(c.ai_agent) AS ai_agent FROM dashboard_cases c WHERE ' + where + ' GROUP BY c.created_month', params)}
    linked = db.execute('SELECT COUNT(*) FROM dashboard_cases c WHERE ' + where + ' AND EXISTS (SELECT 1 FROM dashboard_links l WHERE l.namespace=c.namespace AND l.tc_id=c.tc_id)', params).fetchone()[0]
    by_project = [dict(project=p['project'], namespace=p['namespace'], total=count(db, dict(base, inventory='all'), [p['namespace']]) if p['status'] != 'not_collected' else None, eligible=count(db, dict(base, inventory='active'), [p['namespace']]) if p['status'] != 'not_collected' else None, status=p['status']) for p in selection]
    completion = story_completion(db, namespaces, month_keys[0] if scoped else None, month_keys[-1] if scoped else None)
    stories = completion['total']
    import tc_kpi_dashboard
    kpi_report = tc_kpi_dashboard.measure(db, namespaces, [f'{now.year:04d}-{m:02d}' for m in range(1, now.month + 1)], month_keys, period_start=month_keys[0] if scoped else None)
    card_counts = dict(total=count(db, dict(base, team='all', inventory='all'), namespaces),
                       sqa=count(db, dict(base, team='SQA', inventory='active'), namespaces),
                       kpi=kpi_report['current'],
                       agent=count(db, dict(base, team='SQA', inventory='active', ai_agent=True), namespaces))
    if source_state['status'] == 'not_collected':
        card_counts = dict.fromkeys(card_counts)
    return dict(card_counts=card_counts, chart_period=chart_period, stories=stories, story_completion=completion, source_state=source_state, total=total if source_state['status'] != 'not_collected' else None,
                eligible=eligible if source_state['status'] != 'not_collected' else None, excluded=excluded if source_state['status'] != 'not_collected' else dict(excluded=None, test=None),
                ai_agent=ai if source_state['status'] != 'not_collected' else None, ai_share=ai/eligible if eligible else None, missing_created=missing if source_state['status'] != 'not_collected' else None,
                missing_folder=db.execute('SELECT COUNT(*) FROM dashboard_cases c WHERE c.namespace IN (' + ','.join('?' for _ in namespaces) + ') AND c.folder_missing=1', namespaces).fetchone()[0],
                monthly=[dict(month=m, total=grouped.get(m, {}).get('total', 0), ai_agent=grouped.get(m, {}).get('ai_agent', 0)) for m in month_keys] if source_state['status'] != 'not_collected' else [],
                linked_cases=linked, projects=by_project,
                kpi=kpi_report,
                registration_basis='Zephyr 생성일 · 한국시간 기준. 일괄 등록·복사가 포함될 수 있으며, 실제 작성 월이나 월별 누적 KPI를 뜻하지 않습니다.')


if __name__ == '__main__':
    import argparse
    from contextlib import closing
    import sqlite3
    import time
    parser = argparse.ArgumentParser(description='원본을 백업하고 재생성 가능한 대시보드 조회 테이블만 구축합니다.')
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--reclassify-teams', action='store_true', help='미분류 TC의 폴더 기반 팀 분류만 갱신합니다.')
    args = parser.parse_args()
    if not args.db.is_file() or args.backup.exists() or args.db.resolve() == args.backup.resolve():
        parser.error('기존 DB와 새 백업 경로를 지정하세요.')
    started = time.perf_counter()
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(args.backup)) as target:
        source.backup(target)
    with closing(registry.connect(args.db, create=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if args.reclassify_teams:
            result = dict(status='reclassified', **reclassify_teams(db))
        else:
            for sql in DDL:
                db.execute(sql)
            rebuild(db)
            result = dict(status='rebuilt', rows=db.execute('SELECT COUNT(*) FROM dashboard_cases').fetchone()[0])
    print(json.dumps(dict(result, seconds=round(time.perf_counter()-started, 3), backup=str(args.backup)), ensure_ascii=False))
