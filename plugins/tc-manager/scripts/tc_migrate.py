"""Explicit, non-destructive local inventory export and shared-service import."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sys

import tc_library as library
from tc_shared import Remote


def export_local(config_path, site, project, output, source_namespace=None):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    _, namespace = library.settings(config_path, site, project)
    namespace = source_namespace or namespace
    db_path = Path(config['db_path'])
    if not db_path.is_absolute():
        db_path = config_path.resolve().parent.parent / db_path
    records = []
    with closing(library.registry.connect(db_path)) as db:
        for row in db.execute('SELECT DISTINCT b.bundle_hash,b.canonical_json FROM case_imports i JOIN bundles b USING(bundle_hash) WHERE i.namespace=? ORDER BY b.bundle_hash', (namespace,)):
            bundle = json.loads(row['canonical_json'])
            if any(not re.fullmatch(re.escape(project) + r'-T[1-9][0-9]*|DRAFT-[A-Za-z0-9_.-]+', tc['id']) for tc in bundle['test_cases']):
                raise ValueError('다른 프로젝트 TC가 포함된 묶음입니다. namespace 수집 범위를 검토하세요')
            records.append(('bundle', bundle))
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'story_snapshots' in tables:
            records += [('story', json.loads(row[0])) for row in db.execute('SELECT raw_json FROM story_snapshots WHERE namespace=? ORDER BY updated_at', (namespace,))]
        if 'story_link_reads' in tables:
            for row in db.execute('SELECT * FROM story_link_reads WHERE namespace=? ORDER BY observed_at', (namespace,)):
                records.append(('links', dict(tc_id=row['tc_id'], version=row['version'], content_hash=row['content_hash'], observed_at=row['observed_at'], links=json.loads(row['payload_json'])['raw'], complete=True)))
        history = {}
        for table in ('current_cases', 'current_events', 'readiness_events', 'service_case_reviews', 'story_collections'):
            if table in tables:
                history[table] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' WHERE namespace=?', (namespace,))]
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for index, (kind, value) in enumerate(records):
        name = f'{index:06d}-{kind}.json'
        content = library.registry.canonical(value).encode('utf-8')
        (output / name).write_bytes(content)
        entries.append(dict(file=name, kind=kind, sha256=hashlib.sha256(content).hexdigest()))
    (output / 'source-history.json').write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = dict(format='tc-migration-1', site=site.rstrip('/').lower(), project=project, source_namespace=namespace, entries=entries,
                    history_sha256=hashlib.sha256((output / 'source-history.json').read_bytes()).hexdigest(), current_and_reviews_auto_imported=False)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return dict(status='exported', records=len(records), source_namespace=namespace, source_changed=False)


def import_shared(config_path, site, project, directory):
    remote, namespace = library.settings(config_path, site, project)
    if not isinstance(remote, Remote):
        raise ValueError('공용 서버로 연결한 설정에서만 import를 실행하세요')
    directory = directory.resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'tc-migration-1' or manifest['site'] != site.rstrip('/').lower() or manifest['project'] != project:
        raise ValueError('이관 파일의 사이트/프로젝트가 다릅니다')
    records = []
    for entry in manifest['entries']:
        path = (directory / entry['file']).resolve()
        if path.parent != directory or entry['kind'] not in {'bundle', 'story', 'links'}:
            raise ValueError('이관 파일 경로/종류를 확인하세요')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('이관 파일 해시가 다릅니다')
        value = json.loads(content)
        if entry['kind'] == 'bundle' and any(not re.fullmatch(re.escape(project) + r'-T[1-9][0-9]*|DRAFT-[A-Za-z0-9_.-]+', tc['id']) for tc in value['test_cases']):
            raise ValueError('다른 프로젝트 TC를 이관할 수 없습니다')
        if entry['kind'] == 'story' and (not value['key'].startswith(project + '-') or value['source_url'] != site.rstrip('/') + '/browse/' + value['key']):
            raise ValueError('다른 사이트/프로젝트 Story를 이관할 수 없습니다')
        records.append((entry, value))
    if hashlib.sha256((directory / 'source-history.json').read_bytes()).hexdigest() != manifest['history_sha256']:
        raise ValueError('원본 이력 파일 해시가 다릅니다')
    context = remote.rpc('context')
    if '*' not in context['namespaces'] and namespace not in context['namespaces']:
        raise ValueError('공용 서버 프로젝트 접근 권한이 없습니다')
    journal = dict(status='in_progress', namespace=namespace, server=remote.url, completed=[], current_and_reviews_auto_imported=False)
    journal_path = directory / 'import-result.json'
    try:
        for entry, value in records:
            if entry['kind'] == 'bundle':
                result = remote.rpc('imports.add', dict(bundle=value, source_label='local-migration'))
            elif entry['kind'] == 'story':
                result = remote.rpc('stories.import', dict(stories=[value], scope=dict(query='local migration; source-history.json에 원본 범위 보존', observed_at=value['fetched_at'], complete=False)))
            else:
                result = remote.rpc('stories.links.record', value)
            journal['completed'].append(dict(file=entry['file'], result=result))
            journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
        journal['status'] = 'imported'
    except (ValueError, OSError):
        journal['status'] = 'partial'
        raise
    finally:
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('.tc-manager/library.json'))
    parser.add_argument('--site', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--source-namespace')
    parser.add_argument('command', choices=['export', 'import'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.command == 'export':
        result = export_local(args.config, args.site, args.project, args.directory, args.source_namespace)
    else:
        result = import_shared(args.config, args.site, args.project, args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps(dict(ok=False, error=str(error)), ensure_ascii=False))
        raise SystemExit(1)
