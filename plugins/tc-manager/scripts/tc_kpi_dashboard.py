"""Current-source KPI reconciliation; unknown weights never become zero."""
import re
from collections import Counter


def product(project, folder):
    parts = [p.strip() for p in folder.split('/') if p.strip()]
    if project == 'PXM':
        return 'Mobile'
    if project == 'PXW':
        return 'Web'
    if project == 'PX' and len(parts) > 1 and parts[1] in {'VMS Test Case', 'SQE Full Test Case', 'SQE Full Test Case_AI', 'SQE BF TestCase'}:
        return 'VMS'
    return None


def measure(db, namespaces, months, chart_months=None, period_start=None):
    marks = ','.join('?' for _ in namespaces)
    rows = [dict(r) for r in db.execute('SELECT project,tc_id,folder,team,inventory,created_month,ai_agent FROM dashboard_cases WHERE namespace IN (' + marks + ") AND inventory='active'", namespaces)]
    chart_sqa = [dict(r, product=product(r['project'], r['folder'])) for r in rows if r['team'] == 'SQA']
    if period_start:
        chart_sqa = [r for r in chart_sqa if r['created_month'] and r['created_month'] >= period_start]
    rows = [dict(r, product=p) for r in rows if (p := product(r['project'], r['folder']))]
    sqa = [r for r in rows if r['team'] == 'SQA']
    totals = Counter(r['product'] for r in sqa)
    sitc = Counter(r['product'] for r in rows if r['team'] == 'SQE')
    mobile = [r for r in sqa if r['product'] == 'Mobile']
    mobile_weights = {}
    unresolved_mobile = []
    for row in mobile:
        folder = row['folder']
        if re.search(r'\(OS\s*2개.*X2', folder, re.I):
            mobile_weights[row['tc_id']] = 2
        elif '성능평가' in folder:
            leaf = folder.rsplit('/', 1)[-1]
            match = re.search(r'\((\d+)\)', leaf)
            if match:
                mobile_weights[row['tc_id']] = int(match[1])
            else:
                unresolved_mobile.append(row['tc_id'])
        else:
            unresolved_mobile.append(row['tc_id'])
    mobile_sstc = sum(mobile_weights.values()) if mobile and not unresolved_mobile else None
    web = [r for r in sqa if r['product'] == 'Web']
    web_nonfunctional = sum('(P2P)' in r['folder'] or '(Relay)' in r['folder'] for r in web)
    web_performance = {r['folder'].split('/')[1].strip() for r in web if len(r['folder'].split('/')) > 1 and '성능 (' in r['folder'].split('/')[1]}
    web_performance_total = sum(int(re.search(r'\((\d+)\)', f)[1]) for f in web_performance)
    products = [
        dict(product='VMS', tc_keys=totals['VMS'], sitc_keys=sitc['VMS'], sstc=None,
             status='needs_review', reason='OS 배수 적용 범위·중복 복사·성능 항목 수 확인 필요. 제목의 항목 수와 폴더 합계가 다릅니다.'),
        dict(product='Web', tc_keys=totals['Web'], sitc_keys=sitc['Web'], sstc=None,
             status='needs_review', candidate=web_nonfunctional + web_performance_total,
             reason=f'공용 SQA 폴더: 비기능 {web_nonfunctional} + 성능 {web_performance_total}. StarScream/WindBlade 구분 및 X3 적용 미확정.'),
        dict(product='Mobile', tc_keys=totals['Mobile'], sitc_keys=sitc['Mobile'], sstc=mobile_sstc,
             status='folder_basis' if mobile_sstc is not None else 'needs_review',
             reason='OS 2종 배수와 성능 하위 폴더의 명시 항목 수. 수기 3,962개와 산술 일치; 평가서 본문은 별도 관리.'),
    ]
    selected_projects = {n.rsplit('/', 1)[-1] for n in namespaces}
    products = [p for p in products if {'VMS': 'PX', 'Web': 'PXW', 'Mobile': 'PXM'}[p['product']] in selected_projects]
    story_dates = []
    has_dates = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='dashboard_story_dates'").fetchone())
    if has_dates:
        heads = [t for t in ['story_source_heads', 'story_heads'] if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (t,)).fetchone()]
        current = ' OR '.join('EXISTS (SELECT 1 FROM ' + t + ' h WHERE h.namespace=d.namespace AND h.issue_key=d.issue_key)' for t in heads) or '0'
        story_dates = [dict(r) for r in db.execute('SELECT d.namespace,d.issue_key,d.created_month FROM dashboard_story_dates d WHERE d.namespace IN (' + marks + ') AND (' + current + ')', namespaces)]
    has_dates = bool(story_dates)
    if period_start:
        story_dates = [r for r in story_dates if r['created_month'] and r['created_month'] >= period_start]
    monthly = []
    for key in sorted(set(months + (chart_months or []))):
        cumulative = [r for r in chart_sqa if r['created_month'] and r['created_month'] <= key]
        month_rows = [r for r in chart_sqa if r['created_month'] == key]
        monthly.append(dict(month=key, new_tc=len(month_rows),
                            new_products={p: sum(r['project'] == p for r in month_rows) for p in sorted(selected_projects)},
                            cumulative_products={p: sum(r['project'] == p for r in cumulative) for p in sorted(selected_projects)},
                            new_stories=sum(r['created_month'] == key for r in story_dates) if has_dates else None, cumulative_tc=len(cumulative),
                            new_agent=sum(r['ai_agent'] for r in month_rows),
                            cumulative_agent=sum(r['ai_agent'] for r in cumulative),
                            vms=sum(r['product'] == 'VMS' for r in cumulative),
                            web=sum(r['product'] == 'Web' for r in cumulative),
                            mobile=sum(r['product'] == 'Mobile' for r in cumulative),
                            mobile_sstc=sum(mobile_weights.get(r['tc_id'], 0) for r in cumulative if r['product'] == 'Mobile') if mobile_sstc is not None else None,
                            stories=sum(bool(r['created_month']) and r['created_month'] <= key for r in story_dates) if has_dates else None))
    complete = bool(products) and all(p['sstc'] is not None for p in products)
    return dict(status='folder_basis' if complete else 'partial', current=sum(p['sstc'] for p in products) if complete else None,
                chart_projects=[dict(project=n.rsplit('/', 1)[-1], namespace=n) for n in namespaces],
                chart_monthly=[m for m in monthly if m['month'] in (chart_months or months)],
                products=products, monthly=[m for m in monthly if m['month'] in months],
                registrations=[dict(month=m['month'], total=m['new_tc'], ai_agent=m['new_agent']) for m in monthly if m['month'] in (chart_months or months)],
                story_registrations=[dict(month=m['month'], total=m['new_stories']) for m in monthly if m['month'] in (chart_months or months)],
                measured_tc_keys=len(sqa), source='Zephyr createdOn / 최초 버전 생성일 · 한국시간',
                story_dates_collected=len(story_dates),
                reason='현재 보유 TC의 현재 분류·산식으로 생성일까지 역산. 당시 삭제·이동·산식 변경을 복원한 과거 확정 KPI가 아닙니다.',
                excluded=['Appliance', 'Re-ID', 'GenAI', 'Test', '카운트 금지'])
