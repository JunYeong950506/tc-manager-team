# User Story와 TC 조회·연결

사용자의 TC 작성 근거 조회, Story별 TC 조회, 기능별 Story/TC 검색, TC 없는 Story 탐색 요청에 적용한다. 기존 세 업무의 조회 기능이며 새 TC를 생성하라는 뜻이 아니다. 실제 Jira issue_type을 보존하고 Bug·Task를 Story라고 바꾸지 않는다. 검색어 유사성과 원격 Coverage 연결을 구분한다.

검색 DB는 관련 Story·TC 후보를 빠르게 찾는 가이드다. 저장된 본문을 최신 요구사항의 최종 근거로 단정하지 않는다. 요구사항 해석·TC 작성·재사용·누락 분석에는 해당 팀원 Claude 세션의 Atlassian MCP로 Jira·Confluence 원문을 확인하고 Zephyr MCP로 필요한 TC 상세를 읽는다. 단순 후보 목록 조회에는 프로젝트 전체 재수집을 요구하지 않는다. 원문에 접근하지 못하면 DB 저장본 기준임을 밝히고 최신 원문 확인 실패와 자료 없음을 구분한다. 서버 검색 갱신은 승인 절차와 별개이며 동일 수정 시각의 표현 차이를 Claude 검토 대상으로 만들지 않는다.

## 수집

조회 결과는 프로젝트·제목·요구사항 본문·인수 조건(AC)·원본 URL·실제 연결된 TC를 우선 표시한다. 수정/수집 시각·해시·편집 이력은 기본 답변에 나열하지 않는다. 이 값은 오래된 원문 덮어쓰기 방지와 갱신 상태 확인용 내부 정보다. AC를 식별하지 못한 상태는 요구사항이 없다는 뜻이 아니다. 본문을 보존하고 `content_status.acceptance_criteria=not_identified`로 구분한다.

공용 서버의 일일 원본 갱신을 사용하는 팀은 서버가 Jira/Zephyr를 읽어 최신 자료를 저장하고 팀원 Claude는 조회한다. 팀원 세션마다 프로젝트 전체를 다시 수집하거나 개인 토큰을 서버로 보내지 않는다. 공유 모드는 서버 운영자가 `2-Configure-DB-Sync.cmd`로 별도 원본 조회 계정을 설정한다. `source_status=stale`이면 하루 이상 지난 저장본이라고 알리고, 미수집/오류를 자료 없음과 구분한다. 사용자 요청 없이 조회를 원격 수정으로 바꾸지 않는다.

서버는 Story 본문·AC와 TC 제목·Objective·Precondition·폴더 및 Story별 실제 TC 연결을 하루 1회 다시 확인한다. Jira 수정 시각만으로 연결 변경을 추정하지 않는다. Test Script 전체 단계와 첨부파일 바이너리는 일일 수집 대상이 아니므로 판정 기준 대조·TC 재사용·누락된 검증 조건 분석에는 원문 단계를 별도로 조회한다. 원본 조회 카탈로그는 검토된 TC 버전과 분리되어 있으며 자동 승인/current 승격을 하지 않는다.

기능별 통합 조회는 `lookup "로그인"`, 특정 Story는 `story KEY`, TC의 원본은 버전 없는 `tc-stories KEY`, 미연결 후보는 `stories-search --unlinked`를 우선 사용한다. `lookup`의 `test_cases.items`는 키워드 후보이며 실제 연결 관계와 구분한다. `ISSUE_LINK`는 Zephyr의 Story별 연결 API에서 읽은 관계로서 임의로 COVERAGE나 요구사항 충족으로 바꾸지 않는다. 특정 검토 버전의 연결 증거를 볼 때만 `tc-stories --version`을 사용한다.

`library.md`의 site/project/namespace를 먼저 확인한다. Jira에서 실제 조회한 본문·Acceptance Criteria·updated·숫자 issue ID를 저장한다. AC가 별도 필드가 아니면 본문에 명시된 부분만 추출하고, 없으면 빈 문자열로 둔다. 읽지 못한 요구사항을 만들지 않는다. 수집 JSON 형식은 다음과 같다. 예시 값은 실제 자료로 대체한다.

```json
{"stories":[{"key":"DEMO-123","issue_id":"10123","issue_type":"Story","title":"실제 제목","description":"실제 본문","acceptance_criteria":"실제 AC","updated_at":"2026-09-01T00:00:00Z","fetched_at":"2026-09-02T00:00:00Z","source_url":"https://example.atlassian.net/browse/DEMO-123"}],"scope":{"query":"실제 JQL 또는 지정 키 목록","observed_at":"2026-09-02T00:00:00Z","complete":false}}
```

`complete`는 지정한 수집 범위의 페이지를 모두 읽었을 때만 true다. 한 번에 최대 1000건을 저장하고 큰 범위는 나눠 수집한다. 부분 수집을 프로젝트 전체 수집으로 보고하지 않는다. 같은 수정 시각의 본문 충돌은 실패하며, 과거 본문은 이력으로 남고 최신 수정본을 덮어쓰지 않는다.

```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-import <수집.json>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-search "로그인"
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> lookup "로그인"
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> story <Story키>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> tc-stories <TC키> --version <버전>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-search --unlinked
```

TC 쪽 키워드 검색도 함께 수행하고 Story·TC별 출처와 연결 여부를 구분한다. `--limit`, `--offset`으로 Story 검색의 나머지를 읽는다. `--unlinked`는 **수집된 자료에 확인된 COVERAGE/ISSUE_LINK 연결이 없는 후보**다. 서버 조회 계정에 보이는 수집 범위 밖까지 TC 부재를 단정하지 않는다. RELATED/BLOCKS/UNKNOWN은 Coverage가 아니다. 연결 관찰 뒤 요구사항이 바뀌면 `requirements_changed`를 보고하고 재검토한다. 새 TC 버전은 이전 버전의 연결 확인을 물려받지 않는다. 여러 프로젝트는 각 namespace에서 조회하며 해석되지 않은 issue ID는 그대로 표시한다. 편집 이력은 명시적으로 요청했을 때만 `story KEY --include-history`로 확인한다.

Jira 검색 응답을 초기 데이터로 변환할 때는 `tc_story_intake.py --site <site> --project <project> --input capture.json --output <새폴더>`를 사용한다. capture에는 실제 `query`, `observed_at`, Jira 필드 ID→이름의 `field_names`, `pages` 배열을 넣는다. 각 페이지는 `request_token`과 원본 `response`를 포함한다. 도구 오류를 빈 issues로 바꾸지 않는다. ADF 문단·목록·링크와 원본 JSON을 보존하며, 불완전한 페이지/지원하지 않는 ADF 구조는 완료로 처리하지 않는다. 생성된 `stories-*.json`만 stories-import에 전달한다. 입력 자료와 초기 DB에는 회사 정보가 있으므로 공개 Git/팀 플러그인 ZIP에 넣지 않는다.

## 신규 TC의 원본 Jira 연결

승인한 등록 범위에 원본 Jira 티켓이 있다면 본문·Script 등록 후 다음 절차를 수행한다. Story 하나에 여러 TC, TC 하나에 여러 Story를 지원한다. 연결 도구의 실제 세션 스키마는 `zephyr-mcp-contract.md`와 대조한다.

제목의 대괄호에 있는 Jira 티켓 키를 연결 대상 점검에 반드시 포함한다. 예를 들어 `[PXW-754] 다수 레이아웃 잠금/해제 반복 응답 성능`은 실제 TC의 Traceability → Work Items → Coverage에 `PXW-754`가 있어야 한다. 여러 키가 있으면 모두 확인하고 중복 키는 한 번만 처리한다. `[성능]` 같은 분류 문구는 티켓으로 취급하지 않는다. Jira 원문에서 키·숫자 issue ID·작성 근거를 확인하고, 검증한 제목 키와 그 밖의 원본 요구사항 키를 합쳐 `expected_keys`로 사용한다. 제목 키가 잘못됐거나 작성 근거와 다르면 임의 연결하지 않고 제목/근거 불일치로 남긴다. 제목 표기, 본문 URL, 로컬 DB의 연결 기록만으로 원격 Coverage가 생성됐다고 판단하지 않는다.

DB 저장이 불가능하더라도 원격 연결 확인을 생략하지 않는다. 실제 Jira 조회로 확인한 숫자 issue ID와 Get Test Case Links 응답을 작업 파일에 보존하고, 아래와 동일하게 누락된 Coverage만 생성한 뒤 재조회한다. DB 저장은 별도 미완료로 보고하며, `link-plan`/`link-record`를 실행하지 못했다면 실행한 것처럼 보고하지 않는다.

1. 실제 TC key·본문·전 단계 재조회 확인본을 DB에 저장하고 그 버전과 content_hash를 읽는다. 원본 Jira 티켓도 위 수집 절차로 저장한다.
2. Get Test Case Links의 실제 응답을 보존한다. `link-plan` 입력은 `tc_id`, `version`, `expected_keys`(원본 티켓 키 배열), `links`(응답의 issues 배열을 가진 객체)다. 반환된 missing_links의 숫자 issue_id로만 Create Test Case Issue Link를 호출한다. 이미 있는 COVERAGE는 다시 만들지 않는다. 관계를 추측하거나 기존 연결을 삭제하지 않는다.
3. 원격 링크를 다시 끝까지 조회하고 `link-record`에 `tc_id`, `version`, `content_hash`, `observed_at`, `links`, `complete:true`, `expected_keys`를 전달한다. `verification.status=verified_links`인지 확인한다. 실패·응답 유실이면 실제 링크를 재조회한 후 같은 TC에서 재개한다.
4. 본문·단계 검증과 링크 검증을 각각 보고한다. TC별로 실제 TC key, 제목의 Jira 키, 재조회로 확인한 Coverage 키, 누락/확인 불가 키를 표시한다. 제목 키를 포함한 `expected_keys` 전체의 Coverage를 확인해야 연결 완료다. 링크 실패 시 TC key를 보존하고 부분 완료로 표시하며 TC를 새로 만들지 않는다. 로컬에 링크 기록을 저장한 것만으로 원격 연결 성공이라고 주장하지 않는다.

```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> link-plan <계획입력.json>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> link-record <재조회기록.json>
```

DB의 연결 기록은 클라이언트가 실제 읽은 응답과 관찰 시각을 보존한다. 공용 서버가 Jira/Zephyr를 독립적으로 조회한 증거는 아니다. 조회 전용 요청에서 오래된 연결을 발견해도 사용자 요청 없이 원격 링크를 수정하지 않는다.
