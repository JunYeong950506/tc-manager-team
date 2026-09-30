# User Story와 TC 조회·연결

사용자의 TC 작성 근거 조회, Story별 TC 조회, 기능별 Story/TC 검색, TC 없는 Story 탐색 요청에 적용한다. 기존 세 업무의 조회 기능이며 새 TC를 생성하라는 뜻이 아니다. 실제 Jira issue_type을 보존하고 Bug·Task를 Story라고 바꾸지 않는다. 검색어 유사성과 원격 Coverage 연결을 구분한다.

## 수집

`library.md`의 site/project/namespace를 먼저 확인한다. Jira에서 실제 조회한 본문·Acceptance Criteria·updated·숫자 issue ID를 저장한다. AC가 별도 필드가 아니면 본문에 명시된 부분만 추출하고, 없으면 빈 문자열로 둔다. 읽지 못한 요구사항을 만들지 않는다. 수집 JSON 형식은 다음과 같다. 예시 값은 실제 자료로 대체한다.

```json
{"stories":[{"key":"DEMO-123","issue_id":"10123","issue_type":"Story","title":"실제 제목","description":"실제 본문","acceptance_criteria":"실제 AC","updated_at":"2026-09-01T00:00:00Z","fetched_at":"2026-09-02T00:00:00Z","source_url":"https://example.atlassian.net/browse/DEMO-123"}],"scope":{"query":"실제 JQL 또는 지정 키 목록","observed_at":"2026-09-02T00:00:00Z","complete":false}}
```

`complete`는 지정한 수집 범위의 페이지를 모두 읽었을 때만 true다. 한 번에 최대 1000건을 저장하고 큰 범위는 나눠 수집한다. 부분 수집을 프로젝트 전체 수집으로 보고하지 않는다. 같은 수정 시각의 본문 충돌은 실패하며, 과거 본문은 이력으로 남고 최신 수정본을 덮어쓰지 않는다.

```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-import <수집.json>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-search "로그인"
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> story <Story키>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> tc-stories <TC키> --version <버전>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stories-search --unlinked
```

TC 쪽 키워드 검색도 함께 수행하고 Story·TC별 출처와 연결 여부를 구분한다. `--limit`, `--offset`으로 Story 검색의 나머지를 읽는다. `--unlinked`는 **수집된 자료에 확인된 COVERAGE 연결이 없는 후보**다. Zephyr 전체에서 TC가 없다고 단정하지 않는다. RELATED/BLOCKS/UNKNOWN은 Coverage가 아니다. 연결 관찰 뒤 요구사항이 바뀌면 `requirements_changed`를 보고하고 재검토한다. 새 TC 버전은 이전 버전의 연결 확인을 물려받지 않는다. 여러 프로젝트는 각 namespace에서 조회하며 해석되지 않은 issue ID는 그대로 표시한다.

## 신규 TC의 원본 Jira 연결

승인한 등록 범위에 원본 Jira 티켓이 있다면 본문·Script 등록 후 다음 절차를 수행한다. Story 하나에 여러 TC, TC 하나에 여러 Story를 지원한다. 연결 도구의 실제 세션 스키마는 `zephyr-mcp-contract.md`와 대조한다.

1. 실제 TC key·본문·전 단계 재조회 확인본을 DB에 저장하고 그 버전과 content_hash를 읽는다. 원본 Jira 티켓도 위 수집 절차로 저장한다.
2. Get Test Case Links의 실제 응답을 보존한다. `link-plan` 입력은 `tc_id`, `version`, `expected_keys`(원본 티켓 키 배열), `links`(응답의 issues 배열을 가진 객체)다. 반환된 missing_links의 숫자 issue_id로만 Create Test Case Issue Link를 호출한다. 이미 있는 COVERAGE는 다시 만들지 않는다. 관계를 추측하거나 기존 연결을 삭제하지 않는다.
3. 원격 링크를 다시 끝까지 조회하고 `link-record`에 `tc_id`, `version`, `content_hash`, `observed_at`, `links`, `complete:true`, `expected_keys`를 전달한다. `verification.status=verified_links`인지 확인한다. 실패·응답 유실이면 실제 링크를 재조회한 후 같은 TC에서 재개한다.
4. 본문·단계 검증과 링크 검증을 각각 보고한다. 링크 실패 시 TC key를 보존하고 부분 완료로 표시하며 TC를 새로 만들지 않는다. 로컬에 링크 기록을 저장한 것만으로 원격 연결 성공이라고 주장하지 않는다.

```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> link-plan <계획입력.json>
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> link-record <재조회기록.json>
```

DB의 연결 기록은 클라이언트가 실제 읽은 응답과 관찰 시각을 보존한다. 공용 서버가 Jira/Zephyr를 독립적으로 조회한 증거는 아니다. 조회 전용 요청에서 오래된 연결을 발견해도 사용자 요청 없이 원격 링크를 수정하지 않는다.
