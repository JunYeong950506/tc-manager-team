# TC Manager Git 마켓플레이스

이 디렉터리는 배포 전용입니다. `.claude-plugin/marketplace.json`의 이름은 `tc-manager-team`, 플러그인은 `plugins/tc-manager`에 있습니다. 현재 버전은 해당 폴더의 `.claude-plugin/plugin.json`으로 확인합니다.

## 현재 준비 상태

Git 원격 URL과 조직의 연결 방식은 별도로 지정해야 합니다. 이 파일이 있다는 것만으로 저장소 게시·Claude 연결·자동 업데이트가 완료된 것은 아닙니다. SQMA 작업 폴더나 팀원의 기존 설치 폴더 전체를 Git으로 올리지 않습니다.

## 업데이트 범위

| 대상 | Claude 플러그인 업데이트 |
|---|---|
| plugins/tc-manager 내부 스킬·참조·Python 코드 | 새 버전을 가져옴 |
| 프로젝트 .mcp.json, 사용자 ZEPHYR_API_TOKEN | 기존 설정 유지, 자동 등록하지 않음 |
| 로컬 TC DB·작업 결과·프로젝트 단축어 | 자동 동기화/교체하지 않음 |
| 저장소 루트의 설치·연결·업데이트 CMD와 scripts | 팀원 작업 폴더에 자동 복사하지 않음 |

새 PC는 별도 고정 작업 폴더에서 최초 설치와 Zephyr 연결이 필요합니다. [Windows 최초 설치 안내](SETUP-WINDOWS.md)를 참고하세요. Claude가 관리하는 플러그인/마켓플레이스 캐시 안에서 설치 CMD를 실행하거나 DB를 만들지 않습니다. 현재 Install-TC-Manager.cmd는 로컬 디렉터리 마켓플레이스를 등록하는 ZIP 설치기이며 Git 원격 등록기로 바뀐 것이 아닙니다. 현재 Update-TC-Manager.cmd도 ZIP 덮어쓰기 경로용입니다. Git 전환 후 스킬 업데이트는 아래 Claude 명령/UI를 사용하며, 설치 보조 스크립트가 바뀐 배포는 별도 적용합니다.

## Claude Code에 Git 원격 연결

등록 이름 `tc-manager-team`이 없는 경우, 실제 저장소 URL로 다음을 실행합니다. 토큰을 URL에 넣지 않습니다. 비공개 저장소는 각 PC의 Git 인증이 미리 되어 있어야 합니다.

```text
claude plugin marketplace add <Git-저장소-URL> --scope local
claude plugin install tc-manager@tc-manager-team --scope local
```

고정된 사용자 작업 폴더에서 실행합니다. `claude plugin marketplace list --json`으로 source가 원격 저장소인지 확인합니다. 이미 같은 이름의 로컬/다른 원격 마켓플레이스가 있으면 즉시 재등록하거나 remove하지 않습니다. remove는 설치된 플러그인까지 제거할 수 있으므로 설치 scope·활성 상태·연결 설정을 확인한 뒤 전환합니다. 실제 URL이 정해지면 기존 사용자 전환을 별도로 검증해야 합니다.

일반 업데이트:

```text
claude plugin marketplace update tc-manager-team
claude plugin update tc-manager@tc-manager-team --scope local
```

자동 업데이트는 Claude의 `/plugin` → Marketplaces → 해당 마켓플레이스 → Enable auto-update에서 켭니다. 사용자 지정 마켓플레이스는 기본적으로 꺼져 있습니다. 업데이트 후 새 세션을 시작하거나 지원되는 클라이언트에서 `/reload-plugins`를 사용하고 `/tc-manager:help`의 활성 버전을 확인합니다. 버전 태그/커밋에 고정하면 그 ref 이후의 배포를 따라가지 않으므로 지속 업데이트는 배포용 브랜치를 사용합니다.

## Claude 앱의 조직 배포

Claude Code CLI의 로컬 설치와 claude.ai 계정의 플러그인 라이브러리는 같은 등록 경로가 아닙니다. Team/Enterprise 관리자는 조직 설정 → Plugins & skills에서 GitHub/GitLab 저장소 동기화를 설정할 수 있습니다. GitHub 자동 동기화에는 저장소 접근 및 GitHub App/웹훅 관련 권한이 필요합니다. 사내 GitLab은 조직의 GitLab 설정과 접근 조건을 먼저 확인합니다. 개인용 저장소 추가 UI는 지원 호스트가 더 제한적이므로 사내 Git 주소가 그대로 지원된다고 가정하지 않습니다.

## 배포 담당자 절차

1. SQMA의 plugin.json과 help 버전을 함께 올리고 변경을 검증합니다. 같은 버전의 파일만 바꾸는 배포는 피합니다.
2. SQMA에서 `python scripts/build_tc_manager_git.py --output <배포전용폴더>`를 실행합니다. 허용된 파일만 내보내며 로컬 수정/알 수 없는 파일이 있으면 중단합니다. `.gitattributes`는 배포 해시와 PowerShell BOM/줄바꿈 보존용입니다.
3. 처음에는 이 배포 전용 폴더에만 Git 저장소를 초기화하고 승인된 사내/비공개 원격을 연결합니다. 실제 사용자 설정·토큰·DB·업무 산출물이 없음을 확인한 뒤 커밋·push합니다. 생성기는 Git 초기화·commit·push를 자동 수행하지 않습니다.
4. 마켓플레이스 갱신과 설치 버전/파일을 검증합니다. 조직 저장소 동기화 또는 각 PC의 자동 업데이트가 켜져 있어야 자동으로 전달됩니다.

## 근거 문서

- [Claude Code 설치·Git 등록·업데이트](https://code.claude.com/docs/en/discover-plugins)
- [Claude 조직 플러그인·저장소 동기화](https://support.claude.com/en/articles/13837433-manage-plugins-for-your-organization)
- [Claude 앱의 마켓플레이스 추가](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
