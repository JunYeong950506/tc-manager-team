"""Read-only SQA coverage from observed Story, Epic and active TC relationships."""
from datetime import datetime, timezone
import json
import re

import tc_epics as epics
import tc_stories as stories
import tc_candidates as candidates

DATES = {'created_from', 'created_through'}
OPERATIONS = {
    'coverage.summary': ({'namespace'}, DATES),
    'coverage.stories': ({'namespace'}, DATES | {'q', 'state', 'epic_key', 'key', 'limit', 'offset'}),
    'coverage.epics': ({'namespace'}, DATES | {'q', 'state', 'limit', 'offset'}),
}
STATES = {'all', 'covered', 'missing', 'other_team_only', 'unknown', 'no_epic', 'zero_tc', 'incomplete', 'status_unknown', 'needs_evidence', 'awaiting_implementation'}
RELATIONS = {'COVERAGE', 'ISSUE_LINK'}
NO_EPIC = {'no_parent', 'non_epic_parent'}


def validate(op, args, allowed):
    if op not in OPERATIONS or not isinstance(args, dict):
        raise ValueError('지원하지 않는 Coverage 명령입니다')
    required, optional = OPERATIONS[op]
    if set(args) - required - optional or required - set(args):
        raise ValueError('Coverage 명령의 필수/지원 필드를 확인하세요')
    if not isinstance(allowed, (list, tuple, set)) or any(not isinstance(value, str) for value in allowed):
        raise ValueError('허용 프로젝트 목록을 확인하세요')
    namespace = args['namespace']
    if namespace != 'all':
        epics.namespace_parts(namespace)
        if '*' not in allowed and namespace not in allowed:
            raise ValueError('접근할 수 없는 프로젝트입니다')
    for name in DATES:
        if name in args and (not isinstance(args[name], str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args[name])):
            raise ValueError('Story 생성 월은 YYYY-MM 형식이어야 합니다')
    if args.get('created_from', '') > args.get('created_through', '9999-12'):
        raise ValueError('Story 생성 시작 월이 종료 월보다 늦습니다')
    if 'q' in args and (not isinstance(args['q'], str) or len(args['q']) > 500):
        raise ValueError('검색어는 500자 이하 문자열이어야 합니다')
    if not isinstance(args.get('state', 'all'), str) or args.get('state', 'all') not in STATES:
        raise ValueError('Coverage 조회 상태를 확인하세요')
    for name in ('key', 'epic_key'):
        if name in args:
            epics.issue_key(args[name])
    for name, default in (('limit', 25), ('offset', 0)):
        value = args.get(name, default)
        if type(value) is not int or (name == 'limit' and not 1 <= value <= 100) or (name == 'offset' and value < 0):
            raise ValueError('페이지 범위를 확인하세요')


def permits(namespace, allowed):
    return '*' in allowed or namespace in allowed


def namespaces(db, requested, allowed, tables):
    if requested != 'all':
        return [requested]
    found = {value for value in allowed if value != '*'}
    for table in ('source_sync', 'story_heads', 'story_source_heads', 'story_epic_heads', 'epic_source_heads'):
        if table in tables:
            found.update(row[0] for row in db.execute('SELECT DISTINCT namespace FROM ' + table))
    return sorted(value for value in found if permits(value, allowed))


def rows_for(db, table, selection, columns='*'):
    if not selection:
        return []
    return db.execute('SELECT ' + columns + ' FROM ' + table + ' WHERE namespace IN (' + ','.join('?' for _ in selection) + ')', selection).fetchall()


def snapshot_states(db, tables, allowed):
    result = {}
    if 'source_sync' in tables:
        for row in db.execute('SELECT namespace,completed_at,summary_json FROM source_sync'):
            if not permits(row['namespace'], allowed):
                continue
            summary = json.loads(row['summary_json'])
            complete = (summary['catalog_complete'] is True if 'catalog_complete' in summary
                        else not summary.get('partial') and summary.get('scope_type') != 'notified_keys')
            result[row['namespace']] = dict(complete=complete, completed_at=row['completed_at'], last_error=None, summary=summary)
    if 'source_refresh_state' in tables:
        for row in db.execute('SELECT namespace,last_error,last_attempt,last_success FROM source_refresh_state'):
            if permits(row['namespace'], allowed):
                result.setdefault(row['namespace'], dict(complete=False, completed_at=None, summary={})).update(
                    last_error=row['last_error'], last_attempt=row['last_attempt'], last_success=row['last_success'])
    return result


def completion(category):
    return 'done' if category == 'done' else 'incomplete' if category in {'new', 'indeterminate'} else 'unknown'


def hierarchy_rows(db, selection, tables):
    relation_rows, epic_rows = {}, {}
    if 'epic_source_heads' in tables:
        for namespace in selection:
            epic_rows.update({(namespace, key): dict(value, namespace=namespace) for key, value in epics.current_epics(db, namespace).items()})
    for row in rows_for(db, 'epic_source_heads', selection) if 'epic_source_heads' in tables else []:
        identity = (row['namespace'], row['epic_key'])
        if identity in epic_rows:
            raw = db.execute('SELECT raw_json FROM epic_source_snapshots WHERE namespace=? AND epic_key=? AND source_hash=?',
                             (row['namespace'], row['epic_key'], row['source_hash'])).fetchone()
            status = json.loads(raw['raw_json'])['fields'].get('status') or {}
            epic_rows[identity]['completion'] = completion((status.get('statusCategory') or {}).get('key'))
            epic_rows[identity]['jira_status'] = status.get('name')
    if 'story_epic_heads' in tables and selection:
        query = ('SELECT h.namespace,h.story_key,h.observed_at,s.parent_json FROM story_epic_heads h '
                 'JOIN story_epic_snapshots s USING(namespace,story_key,source_hash) '
                 'WHERE h.active=1 AND h.namespace IN (' + ','.join('?' for _ in selection) + ')')
        for row in db.execute(query, selection):
            parent = json.loads(row['parent_json'])
            value = dict(status='no_parent', epic=None, observed_at=row['observed_at'])
            if parent is not None:
                value['parent'] = epics.parent_view(row['namespace'], parent)
                parent_ns = row['namespace'].rsplit('/', 1)[0] + '/' + parent['key'].rsplit('-', 1)[0]
                value['parent']['namespace'] = parent_ns
                if not epics.is_epic(parent['fields']['issuetype']):
                    value['status'] = 'non_epic_parent'
                elif parent_ns != row['namespace']:
                    value['status'] = 'cross_project_parent'
                else:
                    value['epic'] = epic_rows.get((parent_ns, parent['key']))
                    value['status'] = 'linked' if value['epic'] else 'epic_not_collected'
            relation_rows[(row['namespace'], row['story_key'])] = value
    return relation_rows, epic_rows


def story_rows(db, selection, tables, hierarchy):
    result = {}
    for namespace in selection:
        for key, row in stories.current_stories(db, namespace).items():
            issue_type = re.sub(r'\s+', '', row.get('issue_type', '')).casefold()
            if issue_type not in {'story', 'userstory', '스토리', '사용자스토리'} and (namespace, key) not in hierarchy:
                continue
            result[(namespace, key)] = dict(stories.content_view(row), namespace=namespace)
    for namespace, key in hierarchy:
        result.setdefault((namespace, key), dict(key=key, title=None, namespace=namespace,
            project=namespace.rsplit('/', 1)[1], source_url=f'https://{namespace.rsplit("/", 1)[0]}/browse/{key}',
            content_status='not_collected'))
    dates = {(row['namespace'], row['issue_key']): row for row in rows_for(db, 'dashboard_story_dates', selection)} if 'dashboard_story_dates' in tables else {}
    statuses = {(row['namespace'], row['issue_key']): row['status_category'] for row in rows_for(db, 'dashboard_story_status', selection)} if 'dashboard_story_status' in tables else {}
    for identity, story in result.items():
        story['completion'] = completion(statuses.get(identity))
        row = dates.get(identity)
        story['created_at'] = row['created_at'] if row else None
        story['created_month'] = row['created_month'] if row else None
    return result


def source_links(db, selection, tables):
    confirmed = set()
    if 'source_story_links' in tables:
        confirmed.update((row['namespace'], row['issue_key']) for row in rows_for(db, 'source_story_links', selection, 'namespace,issue_key'))
    rows = []
    if 'dashboard_links' in tables and 'dashboard_cases' in tables and selection:
        query = ('SELECT l.namespace,l.tc_id,l.story_namespace,l.story_key,l.relation,l.basis,'
                 'c.tc_id AS collected_id,c.title,c.folder,c.team,c.inventory,c.ai_agent,c.created_at '
                 'FROM dashboard_links l LEFT JOIN dashboard_cases c ON c.namespace=l.namespace AND c.tc_id=l.tc_id '
                 'WHERE l.story_namespace IN (' + ','.join('?' for _ in selection) + ") AND l.relation IN ('COVERAGE','ISSUE_LINK')")
        rows = [dict(row) for row in db.execute(query, selection)]
    elif 'source_story_links' in tables:
        for row in rows_for(db, 'source_story_links', selection):
            host = row['namespace'].rsplit('/', 1)[0]
            for link in json.loads(row['links_json']):
                rows.append(dict(namespace=host + '/' + link['key'].rsplit('-T', 1)[0], tc_id=link['key'],
                    story_namespace=row['namespace'], story_key=row['issue_key'], relation='ISSUE_LINK',
                    basis='zephyr_issue_link_api', collected_id=None))
    return confirmed, rows


def tc_url(namespace, tc_id):
    import tc_dashboard
    original_url = getattr(tc_dashboard, 'original_url', None)
    return original_url(namespace, tc_id) if original_url else None


def checks_for(db, identities):
    try:
        import tc_checks
    except ModuleNotFoundError as error:
        if error.name != 'tc_checks':
            raise
        return {}
    return tc_checks.get_many(db, sorted(identities))


def source_state(selection, states, story_identities):
    projects = []
    for namespace in selection:
        value = states.get(namespace, {})
        time = value.get('completed_at')
        story_known = any(ns == namespace for ns, _ in story_identities) or value.get('summary', {}).get('tc_links_collected') is True
        status = ('not_collected' if not time else 'refresh_failed' if value.get('last_error')
                  else 'partial' if not value.get('complete') or not story_known else 'complete')
        stale = False
        if time:
            try:
                parsed = datetime.fromisoformat(time.replace('Z', '+00:00'))
                stale = parsed.tzinfo is None or (datetime.now(timezone.utc) - parsed).total_seconds() >= 86400
            except (ValueError, TypeError):
                stale = True
        projects.append(dict(namespace=namespace, project=namespace.rsplit('/', 1)[1], status=status,
            completed_at=time, stale=stale, last_error=value.get('last_error'), complete=bool(value.get('complete'))))
    times = [row['completed_at'] for row in projects if row['completed_at']]
    complete = bool(projects) and all(row['status'] == 'complete' for row in projects)
    return dict(status='complete' if complete else 'partial' if times else 'not_collected',
                stale=any(row['stale'] for row in projects), data_as_of=min(times) if times else None, projects=projects,
                scope=dict(team='SQA', inventory='active', date_basis='story_created'))


def model(db, args, allowed):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selection = namespaces(db, args['namespace'], allowed, tables)
    states = snapshot_states(db, tables, allowed)
    hierarchy, epic_rows = hierarchy_rows(db, selection, tables)
    source_stories = story_rows(db, selection, tables, hierarchy)
    candidate_items = candidates.all_items(db, args['namespace'], allowed)
    candidate_map = {}
    for candidate in candidate_items:
        for key in candidate['story_keys']:
            identity = (candidate['namespace'], key)
            candidate_map.setdefault(identity, []).append(candidates.summary(candidate))
            source_stories.setdefault(identity, dict(namespace=identity[0],key=key,title='Story 내용 미수집',description='',acceptance_criteria='',completion='unknown',content_status='not_collected',created_month=None,source_url='https://' + identity[0].split('/')[0] + '/browse/' + key))
    confirmed, links = source_links(db, selection, tables)
    grouped = {}
    for link in links:
        identity = (link['story_namespace'], link['story_key'])
        if identity not in source_stories:
            continue
        target = (link['namespace'], link['tc_id'])
        if (link['basis'] == 'recorded_readback' and not link['collected_id']
                and states.get(link['namespace'], {}).get('complete')):
            continue
        previous = grouped.setdefault(identity, {}).get(target)
        if previous is None or link['basis'] == 'zephyr_issue_link_api':
            grouped[identity][target] = link
    agent_ids = {(link['namespace'], link['tc_id']) for values in grouped.values() for link in values.values()
                 if permits(link['namespace'], allowed) and link['collected_id'] and link['inventory'] == 'active' and link['ai_agent']}
    checks = checks_for(db, agent_ids)
    rows, unknown_dates = [], 0
    for identity, story in sorted(source_stories.items()):
        month = story['created_month']
        if not month:
            unknown_dates += 1
        if ('created_from' in args or 'created_through' in args) and (not month or month < args.get('created_from', '') or month > args.get('created_through', '9999-12')):
            continue
        cases, unresolved, ignored = [], [], set()
        sqa, other, miscellaneous, ambiguous = set(), set(), set(), False
        for target, link in grouped.get(identity, {}).items():
            namespace, tc_id = target
            if not permits(namespace, allowed):
                unresolved.append(dict(namespace=namespace, tc_id=tc_id, status='outside_namespace', scope='outside_allowed_namespaces'))
                ambiguous = True
                continue
            if not link['collected_id']:
                unresolved.append(dict(namespace=namespace, tc_id=tc_id, status='not_collected', scope='allowed_namespace'))
                ambiguous = True
                continue
            target_state = states.get(namespace, {})
            if not target_state.get('complete') or target_state.get('last_error'):
                ambiguous = True
            if link['inventory'] in {'test', 'excluded'}:
                ignored.add(target)
                continue
            if link['inventory'] != 'active':
                ambiguous = True
                unresolved.append(dict(namespace=namespace, tc_id=tc_id, status='inventory_unknown', scope='allowed_namespace'))
                continue
            if link['team'] == 'SQA':
                sqa.add(target)
            elif link['team'] == 'SQE':
                other.add(target)
            elif link['team'] in {'other', 'unclassified'}:
                miscellaneous.add(target)
            else:
                ambiguous = True
            check = checks.get(target, dict(checked=False, checked_by=None, checked_at=None, revision=0))
            cases.append(dict(namespace=namespace, tc_id=tc_id, title=link['title'], folder=link['folder'], team='other' if link['team'] == 'unclassified' else link['team'], inventory=link['inventory'],
                ai_agent=bool(link['ai_agent']), created_at=link['created_at'], source_url=tc_url(namespace, tc_id), check=check,
                relation=link['relation'], basis=link['basis'], coverage_eligible=link['team'] == 'SQA'))
        state = states.get(identity[0], {})
        can_confirm_absence = (state.get('complete') and not state.get('last_error') and identity in confirmed
                               and story.get('content_status') != 'not_collected')
        status = ('covered' if sqa else 'unknown' if ambiguous or not can_confirm_absence
                  else 'other_team_only' if other or miscellaneous else 'missing')
        relation = hierarchy.get(identity, dict(status='not_collected', epic=None))
        rows.append(dict(authoring=dict(items=candidate_map.get(identity, []), counts={name: sum(item['status'] == name for item in candidate_map.get(identity, [])) for name in candidates.STATUSES}), story=story, hierarchy=relation, coverage=dict(status=status, tc_count=len(sqa), other_team_tc_count=len(other),
            other_tc_count=len(miscellaneous), unassigned_folder_tc_count=sum(case['team'] == 'other' and (not case['folder'] or case['folder'] == 'Root') for case in cases),
            unchecked_agent_count=sum(case['coverage_eligible'] and case['ai_agent'] and not case['check']['checked'] for case in cases)),
            cases=sorted(cases, key=lambda case: (case['namespace'], case['tc_id'])), unresolved_links=unresolved, ignored_link_count=len(ignored)))
    state = source_state(selection, states, source_stories)
    state['story_content_missing_count'] = sum(story.get('content_status') == 'not_collected' for story in source_stories.values())
    if state['story_content_missing_count'] and state['status'] == 'complete':
        state['status'] = 'partial'
    return rows, epic_rows, state, unknown_dates


def parent_key(row):
    hierarchy = row['hierarchy']
    value = hierarchy.get('epic') or hierarchy.get('parent')
    return value.get('key') if value and hierarchy['status'] not in NO_EPIC else None


def matches(query, values):
    text = ' '.join(value for value in values if isinstance(value, str)).casefold()
    return all(term in text for term in query.casefold().split())


def totals(rows):
    status = [row['coverage']['status'] for row in rows]
    pure_missing, other = status.count('missing'), status.count('other_team_only')
    return dict(total_stories=len(rows), missing_story_count=pure_missing + other, no_link_story_count=pure_missing,
                other_team_only_count=other, unknown_story_count=status.count('unknown'), covered_story_count=status.count('covered'),
                no_epic_story_count=sum(row['hierarchy']['status'] in NO_EPIC for row in rows))


def execute(db, op, args, allowed):
    validate(op, args, allowed)
    rows, epic_rows, source, unknown_dates = model(db, args, allowed)
    common = dict(source_state=source, unknown_created_story_count=unknown_dates,
                  date_scope=dict(created_from=args.get('created_from'), created_through=args.get('created_through'), basis='story_created'),
                  limitation='SQA 일반 폴더의 확인된 연결 기준입니다. 연결 부재는 테스트 미실행이나 요구사항 미충족 판정이 아닙니다.')
    date_filtered = bool(DATES & set(args))
    visible_epics = {identity: epic for identity, epic in epic_rows.items()
                     if not date_filtered or any(row['hierarchy'].get('epic') == epic for row in rows)}
    if op == 'coverage.summary':
        cases = {(case['namespace'], case['tc_id']) for row in rows for case in row['cases'] if case['coverage_eligible']}
        return dict(totals(rows), total_epics=len(visible_epics), total_tcs=len(cases),
                    zero_tc_story_count=sum(row['coverage']['tc_count'] == 0 and row['coverage']['status'] != 'unknown' for row in rows),
                    incomplete_story_count=sum(row['story']['completion'] == 'incomplete' for row in rows),
                    incomplete_epic_count=sum(epic.get('completion') == 'incomplete' for epic in visible_epics.values()),
                    status_unknown_story_count=sum(row['story']['completion'] == 'unknown' for row in rows),
                    status_unknown_epic_count=sum(epic.get('completion', 'unknown') == 'unknown' for epic in visible_epics.values()), **common)
    limit, offset = args.get('limit', 25), args.get('offset', 0)
    query = args.get('q', '')
    if op == 'coverage.stories':
        state = args.get('state', 'all')
        result = []
        for row in rows:
            story, coverage = row['story'], row['coverage']
            if args.get('key') and story['key'] != args['key']:
                continue
            if args.get('epic_key') and parent_key(row) != args['epic_key']:
                continue
            if state in {'needs_evidence','awaiting_implementation'} and not row['authoring']['counts'].get(state):
                continue
            if state == 'zero_tc' and (coverage['tc_count'] != 0 or coverage['status'] == 'unknown'):
                continue
            if state == 'incomplete' and story['completion'] != 'incomplete':
                continue
            if state == 'status_unknown' and story['completion'] != 'unknown':
                continue
            if state == 'no_epic' and row['hierarchy']['status'] not in NO_EPIC:
                continue
            if state == 'missing' and coverage['status'] not in {'missing', 'other_team_only'}:
                continue
            if state not in {'all', 'missing', 'no_epic', 'zero_tc', 'incomplete', 'status_unknown', 'needs_evidence', 'awaiting_implementation'} and coverage['status'] != state:
                continue
            parent = row['hierarchy'].get('epic') or row['hierarchy'].get('parent') or {}
            if not matches(query, [story['key'], story.get('title'), story.get('description'), story.get('acceptance_criteria'), parent.get('title')]):
                continue
            result.append(row)
        return dict(items=result[offset:offset + limit], total=len(result), limit=limit, offset=offset, **common)
    groups = {identity: [] for identity in epic_rows}
    for row in rows:
        hierarchy = row['hierarchy']
        if hierarchy['status'] == 'linked' and hierarchy.get('epic'):
            epic = hierarchy['epic']
            groups.setdefault((epic['namespace'], epic['key']), []).append(row)
    items = []
    date_filtered = bool(DATES & set(args))
    for identity, grouped in sorted(groups.items()):
        if args.get('state') == 'incomplete' and epic_rows[identity].get('completion') != 'incomplete':
            continue
        if args.get('state') == 'status_unknown' and epic_rows[identity].get('completion', 'unknown') != 'unknown':
            continue
        if date_filtered and not grouped:
            continue
        epic = epic_rows[identity]
        if not matches(query, [epic['key'], epic['title'], epic.get('description')]):
            continue
        counts = totals(grouped)
        cases = {(case['namespace'], case['tc_id']): case for row in grouped for case in row['cases'] if case['coverage_eligible']}
        items.append(dict(authoring_counts={name: len({item['id'] for row in grouped for item in row['authoring']['items'] if item['status'] == name}) for name in candidates.STATUSES}, epic=epic, story_count=len(grouped), missing_story_count=counts['missing_story_count'],
            other_team_only_count=counts['other_team_only_count'], unknown_story_count=counts['unknown_story_count'],
            tc_count=len(cases), zero_tc_story_count=sum(row['coverage']['tc_count'] == 0 and row['coverage']['status'] != 'unknown' for row in grouped), incomplete_story_count=sum(row['story']['completion'] == 'incomplete' for row in grouped), unchecked_agent_count=sum(case['ai_agent'] and not case['check']['checked'] for case in cases.values())))
    return dict(items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset, **common)
