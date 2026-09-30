"""Configure a workspace to use the common TC API after checking access."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys

from tc_shared import Remote


def configure(path, server, token_env, check_only=False):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    updated = dict(config, backend='shared', server_url=server, token_env=token_env)
    bindings = config.get('namespaces', [])
    if not bindings:
        raise ValueError('먼저 Install-TC-Manager.cmd로 프로젝트 매핑을 준비하세요')
    context = Remote(updated, bindings[0]['namespace']).rpc('context')
    if 'stories' not in context.get('capabilities', []):
        raise ValueError('공용 서버를 Story 조회를 지원하는 1.5.0 이상으로 먼저 업데이트하세요')
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
    parser.add_argument('--token-env', default='TC_MANAGER_TOKEN')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    server = args.server or input('공용 TC 서버 주소 (HTTPS): ').strip()
    if not os.environ.get(args.token_env):
        token = getpass.getpass('운영자가 발급한 개인 TC 서버 토큰 (숨김): ').strip()
        if not token:
            raise ValueError('취소했습니다')
        os.environ[args.token_env] = token
    result = configure(args.config, server, args.token_env, args.check_only)
    if sys.platform == 'win32' and not args.check_only:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            winreg.SetValueEx(key, args.token_env, 0, winreg.REG_SZ, os.environ[args.token_env])
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
