"""Configure a workspace to use the common TC API after checking access."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from tc_shared import Remote


def configure(path, server, check_only=False):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    updated = dict(config, backend='shared', server_url=server)
    updated.pop('token_env', None)
    bindings = config.get('namespaces', [])
    if not bindings:
        raise ValueError('먼저 1-Install-TC-Manager.cmd로 프로젝트 매핑을 준비하세요')
    context = Remote(dict(updated, _outbox_dir=str(path.resolve().parent)), bindings[0]['namespace']).rpc('context')
    if not {'stories', 'source_sync', 'storage_v2'} <= set(context.get('capabilities', [])):
        raise ValueError('공용 서버를 DB 형식 2와 변경 알림을 지원하는 1.5.3 이상으로 먼저 업데이트하세요')
    missing = [row['namespace'] for row in bindings if '*' not in context.get('namespaces', []) and row['namespace'] not in context.get('namespaces', [])]
    if missing:
        raise ValueError('서버 운영자가 다음 namespace 권한/매핑을 확인해야 합니다: ' + ', '.join(missing))
    if not check_only:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        backup = path.with_name(path.name + '.before-shared-' + stamp + '.bak')
        backup.write_bytes(path.read_bytes())
        temp = path.with_name(path.name + '.pending-' + stamp)
        temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(path)
    return dict(status='checked' if check_only else 'configured', namespaces=[row['namespace'] for row in bindings], local_db_migrated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('.tc-manager/library.json'))
    parser.add_argument('--server')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    server = args.server or input('같은 사내망의 서버 주소 (1번 서버 창에 표시된 http://서버IP:5190): ').strip()
    result = configure(args.config, server, check_only=args.check_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.check_only:
        print('공유 연결 설정 완료. 기존 로컬 DB는 보존했으며 아직 이관하지 않았습니다. Claude 새 세션에서 connection을 확인하세요.')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, EOFError) as error:
        print('연결 설정 실패:', error)
        raise SystemExit(1)
