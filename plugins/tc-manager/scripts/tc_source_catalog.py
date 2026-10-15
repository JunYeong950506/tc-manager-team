"""Latest source content for read-only discovery, separate from reviewed TC versions."""
import json
import re
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone

import tc_registry as registry

TEAMS = ('all', 'SQA', 'SQE', 'unclassified')
INVENTORIES = ('all', 'active', 'excluded', 'test')
SCOPES = ('all', 'px-kpi')
KPI = json.loads(Path(__file__).with_name('tc_kpi_scope.json').read_text(encoding='utf-8'))


def validate_scope(team='all', inventory='all', scope='all'):
    if team not in TEAMS or inventory not in INVENTORIES or scope not in SCOPES:
        raise ValueError('team은 all/SQA/SQE/unclassified, inventory는 all/active/excluded/test, scope는 all/px-kpi여야 합니다')


def kpi_membership(namespace, raw):
    if namespace != KPI['namespace']:
        return dict(in_scope=False, basis='different_project')
    ancestors = raw.get('folder_ancestor_ids')
    if isinstance(ancestors, list):
        return dict(in_scope=KPI['root_folder_id'] in ancestors, basis='source_folder_ids')
    cursor = (raw.get('folder') or {}).get('id')
    seen = set()
    while cursor is not None:
        if cursor == KPI['root_folder_id']:
            return dict(in_scope=True, basis='folder_snapshot_20260930')
        if cursor in seen or str(cursor) not in KPI['parents']:
            return dict(in_scope=None, basis='folder_not_resolved')
        seen.add(cursor)
        cursor = KPI['parents'][str(cursor)]
    return dict(in_scope=False, basis='folder_snapshot_20260930')


def kpi_map(db, namespace):
    if namespace != KPI['namespace']:
        raise ValueError('px-kpi는 hanwhavision.atlassian.net/PX에만 설정된 범위입니다')
    if not available(db, namespace):
        return {}
    return {row['tc_id']: kpi_membership(namespace, json.loads(row['raw_json'])) for row in db.execute(
        'SELECT tc_id,raw_json FROM source_test_cases WHERE namespace=?', (namespace,))}


@lru_cache(maxsize=8192)
def folder_scope(folder):
    parts = [part.strip() for part in folder.split('/') if part.strip()]
    labels = set(re.findall(r'(?<![A-Z0-9])(SQA|SQE)(?![A-Z0-9])', '/'.join(parts).upper()))
    team = next(iter(labels)) if len(labels) == 1 else 'unclassified'
    inventory = 'excluded' if any('카운트 금지' in part for part in parts) else (
        'test' if parts and parts[0].lower().startswith('[test]') else 'active')
    return dict(team=team, inventory_status=inventory, classification_basis='folder_path', folder=folder)


def in_scope(scope, team='all', inventory='all'):
    return (team == 'all' or scope['team'] == team) and (inventory == 'all' or scope['inventory_status'] == inventory)


def scope_map(db, namespace):
    if not available(db, namespace):
        return {}
    return {row['tc_id']: folder_scope(row['folder']) for row in db.execute(
        'SELECT tc_id,folder FROM source_test_cases WHERE namespace=?', (namespace,))}


def stored_scope(row):
    value = json.loads(row['raw_json']).get('source_folder', '')
    result = dict(folder_scope(value if isinstance(value, str) else ''))
    result['classification_basis'] = 'stored_folder' if value else 'folder_not_collected'
    return result


def team_summary(db, namespace):
    scopes = scope_map(db, namespace)
    source = 'source_catalog'
    if not available(db, namespace):
        source = 'stored_versions'
        for row in db.execute('SELECT tc_id,raw_json FROM cases WHERE namespace=? ORDER BY rowid', (namespace,)):
            scopes[row['tc_id']] = stored_scope(row)
    counts = {team: {kind: 0 for kind in INVENTORIES[1:]} for team in TEAMS[1:]}
    for scope in scopes.values():
        counts[scope['team']][scope['inventory_status']] += 1
    kpi = kpi_map(db, namespace) if namespace == KPI['namespace'] else None
    return dict(total=len(scopes), teams={team: dict(total=sum(value.values()), **value) for team, value in counts.items()},
                kpi=(dict(scope='px-kpi', root_folder_id=KPI['root_folder_id'], baseline_count=KPI['baseline_count'], baseline_date=KPI['baseline_date'],
                    current_count=sum(row['in_scope'] is True for row in kpi.values()) if available(db, namespace) else None,
                    unresolved_count=sum(row['in_scope'] is None for row in kpi.values())) if kpi is not None else None),
                scope_source=source, source_status=status(db, namespace),
                limitation='폴더 기준 분류입니다. 미분류는 다른 팀으로 간주하지 않으며, active는 검토 승인이나 실행 가능 판정이 아닙니다.')

DDL = (
    "CREATE TABLE IF NOT EXISTS source_test_cases (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, title TEXT NOT NULL, objective TEXT NOT NULL, precondition TEXT NOT NULL, folder TEXT NOT NULL, raw_json TEXT NOT NULL, PRIMARY KEY(namespace,tc_id))",
    "CREATE TABLE IF NOT EXISTS source_story_links (namespace TEXT NOT NULL, issue_key TEXT NOT NULL, links_json TEXT NOT NULL, PRIMARY KEY(namespace,issue_key))",
    "CREATE TABLE IF NOT EXISTS source_sync (namespace TEXT PRIMARY KEY, completed_at TEXT NOT NULL, summary_json TEXT NOT NULL)",
)


def initialize(db):
    for sql in DDL:
        db.execute(sql)


def available(db, namespace):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='source_sync'").fetchone()
                and db.execute("SELECT 1 FROM source_sync WHERE namespace=?", (namespace,)).fetchone())


def status(db, namespace):
    daily = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_refresh_state'").fetchone():
        state = db.execute('SELECT last_attempt,last_success,last_error FROM source_refresh_state WHERE namespace=?', (namespace,)).fetchone()
        daily = dict(state) if state else None
    if not available(db, namespace):
        return dict(status='not_collected', daily=daily)
    row = db.execute("SELECT completed_at,summary_json FROM source_sync WHERE namespace=?", (namespace,)).fetchone()
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['completed_at'])).total_seconds()
    summary = json.loads(row['summary_json'])
    return dict(status='stale' if age >= 86400 else ('targeted_snapshot' if summary.get('partial') else 'daily_snapshot'), completed_at=row['completed_at'], daily=daily,
                scope=json.loads(row['summary_json']).get('scope_type', 'visible_to_server_account'), script_collected=False)


def replace(db, namespace, cases, links, completed_at, summary, protected_cases=(), protected_stories=(), partial=False):
    initialize(db)
    prepared = []
    seen = set()
    existing = {row['tc_id']: row['raw_json'] for row in db.execute('SELECT tc_id,raw_json FROM source_test_cases WHERE namespace=?', (namespace,))}
    old_links = {row['issue_key']: row['links_json'] for row in db.execute('SELECT issue_key,links_json FROM source_story_links WHERE namespace=?', (namespace,))}
    for row in cases:
        key = row["key"]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-T[1-9][0-9]*", key) or key in seen or key.rsplit('-T', 1)[0] != namespace.rsplit('/', 1)[-1]:
            raise ValueError("원본 TC 키가 중복되거나 프로젝트가 다릅니다")
        seen.add(key)
        if any(not isinstance(row[field], str) for field in ("name", "objective", "precondition", "folder_path")):
            raise ValueError("원본 TC 본문 형식을 확인하세요")
        if key in protected_cases:
            continue
        if existing.get(key) == registry.canonical(row):
            continue
        prepared.append((namespace, key, row['name'], row['objective'], row['precondition'], row['folder_path'], registry.storage.pack(db, registry.canonical(row))))
    for key, values in links.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", key) or key.rsplit('-', 1)[0] != namespace.rsplit('/', 1)[-1] or not isinstance(values, list):
            raise ValueError("원본 Story 연결 형식을 확인하세요")
        for row in values:
            if not isinstance(row, dict) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-T[1-9][0-9]*", row.get('key', '')) or type(row.get('version')) is not int or row['version'] < 1:
                raise ValueError("원본 Story 연결 응답의 TC 키/버전이 필요합니다")
    if not partial:
        db.executemany('DELETE FROM source_test_cases WHERE namespace=? AND tc_id=?', [(namespace, key) for key in existing if key not in seen and key not in protected_cases])
        db.executemany('DELETE FROM source_story_links WHERE namespace=? AND issue_key=?', [(namespace, key) for key in old_links if key not in links and key not in protected_stories])
    db.executemany('INSERT INTO source_test_cases VALUES (?,?,?,?,?,?,?) ON CONFLICT(namespace,tc_id) DO UPDATE SET title=excluded.title,objective=excluded.objective,precondition=excluded.precondition,folder=excluded.folder,raw_json=excluded.raw_json', prepared)
    changed_links = [(namespace, key, registry.storage.pack(db, registry.canonical(value))) for key, value in links.items()
                     if key not in protected_stories and old_links.get(key) != registry.canonical(value)]
    db.executemany('INSERT INTO source_story_links VALUES (?,?,?) ON CONFLICT(namespace,issue_key) DO UPDATE SET links_json=excluded.links_json', changed_links)
    summary['tc_rows_written'] = len(prepared)
    summary['link_rows_written'] = len(changed_links)
    if partial and available(db, namespace):
        return
    db.execute("INSERT INTO source_sync VALUES (?,?,?) ON CONFLICT(namespace) DO UPDATE SET completed_at=excluded.completed_at, summary_json=excluded.summary_json",
               (namespace, completed_at, registry.canonical(summary)))


def story_cases(db, namespace, issue_key):
    if not available(db, namespace):
        return None
    row = db.execute("SELECT links_json FROM source_story_links WHERE namespace=? AND issue_key=?", (namespace, issue_key)).fetchone()
    if row is None:
        return None
    result = []
    for link in json.loads(row[0]):
        target_namespace = namespace.rsplit('/', 1)[0] + '/' + link['key'].rsplit('-T', 1)[0]
        tc = db.execute("SELECT title,objective,precondition,folder FROM source_test_cases WHERE namespace=? AND tc_id=?", (target_namespace, link['key'])).fetchone()
        result.append(dict(tc_id=link['key'], source_version=link['version'], relation='ISSUE_LINK',
            team_scope=folder_scope(tc['folder']) if tc else folder_scope(''),
            basis='zephyr_issue_link_api', **(dict(tc) if tc else {'title': None, 'content_status': 'not_collected'})))
    return result


def case_stories(db, namespace, tc_id):
    if not available(db, namespace):
        return None
    if not db.execute("SELECT 1 FROM source_test_cases WHERE namespace=? AND tc_id=?", (namespace, tc_id)).fetchone():
        return None
    return [dict(namespace=row['namespace'], key=row['issue_key'])
            for row in db.execute("SELECT namespace,issue_key,links_json FROM source_story_links")
            if row['namespace'].rsplit('/', 1)[0] == namespace.rsplit('/', 1)[0] and any(link['key'] == tc_id for link in json.loads(row['links_json']))]


def search(db, namespace, query, limit, team='all', inventory='all', scope='all'):
    validate_scope(team, inventory, scope)
    if not available(db, namespace):
        return dict(status='not_collected', items=[], source_status=status(db, namespace))
    if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('검색어와 limit(1~100)을 확인하세요')
    terms = registry.tokens(query)
    items = []
    kpi = kpi_map(db, namespace) if scope == 'px-kpi' else {}
    for row in db.execute("SELECT tc_id,title,objective,precondition,folder FROM source_test_cases WHERE namespace=?", (namespace,)):
        classification = folder_scope(row['folder'])
        if not in_scope(classification, team, inventory):
            continue
        if scope == 'px-kpi' and kpi.get(row['tc_id'], {}).get('in_scope') is not True:
            continue
        matched = registry.matched_tokens(' '.join(row), terms)
        if terms and not matched:
            continue
        items.append(dict(row, team_scope=classification, score=len(matched) / len(terms) if terms else 0, matched_terms=sorted(matched), relation='keyword_candidate'))
    items.sort(key=lambda item: (-item['score'], item['tc_id']))
    return dict(status='searched', total=len(items), items=items[:limit], team=team, inventory=inventory, scope=scope,
                unresolved_scope_count=sum(row['in_scope'] is None for row in kpi.values()), script_status='not_collected', source_status=status(db, namespace))
