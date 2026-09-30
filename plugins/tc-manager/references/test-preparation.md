# 시험 대상 선정과 구성

## 검색과 범위 판단
- DB 수집 범위·기준 버전·수집 시각을 먼저 확인한다. 폴더별 집계와 간결한 후보 검색을 사용하고 수천 개 원문 전체를 한 번에 컨텍스트에 넣지 않는다.
- 폴더 이름은 검색 단서다. 제목·Objective·Precondition과 필요한 Script로 선정/제외를 판단한다. 라이브·재생 시험이면 레이아웃·채널 제어·권한·녹화·스트림의 인접 후보도 확인한다. 관련 키워드 하나로 일괄 포함하거나 폴더명이 다르다는 이유로 제외하지 않는다.
- 클라이언트 OS, 서버 OS, 서버 토폴로지는 별개다. Linux 서버 조건은 Windows 클라이언트 제외 근거가 아니며 MAC 주소는 macOS 조건이 아니다. 웹·모바일 언급도 주된 시험 대상과 병행 조건을 원문으로 구분한다.
- 변경 티켓이 없으면 기능 회귀 범위로 표시한다. 변경 영향 분석을 했다고 주장하거나 일정·담당·공수를 지어내지 않는다.
- 흐름 검증/기존 TC 연결만 요청했다면 Script 부족은 실행 준비 문제로 남기고 일괄 변환하지 않는다. TC 보완이 요청 범위일 때만 해당 revise/new 항목을 작성한다.

## 검토할 선정안
selection.json에는 name, purpose, site, project_key, target_release, excluded_scope, grouping, search_coverage, items, gaps를 둔다.
- grouping: kind=cycle/folder_move/folder_copy, 목적지 folder_type·실제 id·name·parent_id. 생성 전 ID는 null로 두고 추측하지 않는다.
- items: 실제 tc_key 또는 draft_id, DB version, decision(reuse/revise/new/needs_evidence/exclude), reason, sources, priority, dependencies, remote_status.
- 대량 선정은 별도 TSV/JSON에 키별 decision·version·reason_group_id를 두어도 된다. reason_groups에는 실제 원문 locator와 판단 근거를 두고 모든 키를 연결한다. items_file은 selection 기준 실제 상대경로(예: source/full-items.tsv)이며 설명 문장을 경로에 섞지 않는다.
- gaps: 부족한 범위·이유·보완 결과·남은 결정. 검색 누락 가능성과 실행 준비 부족을 구분한다.
- search_coverage: DB/원격 수집 범위·시각·검색어·읽은 후보·미조회 영역. 빈 검색 결과를 전체 공백으로 단정하지 않는다.
- 선정 키 중복/다른 프로젝트 키/원격 미등록 draft를 검사한다. 전체 회귀는 선정+제외+판단 보류가 수집 범위를 설명하는지 대조한다. 두 묶음이 같은 TC를 재사용하는 것은 허용된다.

review.md에는 최종 목적·선정 수·제외 이유·보완 TC·부족한 범위·원격 변경 위치를 먼저 표시한다. 변경 전 숫자는 이력 절에 분리한다. 자체 점검은 독립 Agent 검토로 표시하지 않는다.

## 폴더와 Cycle 생성
1. 실제 MCP 스키마를 확인한다. 설정 파일은 사용자 권한이 아니다. `.tc-manager/target.json`의 folder_id를 종류 확인 없이 Cycle 폴더로 사용하지 않는다.
2. Test Cases는 TEST_CASE, Test Cycles는 TEST_CYCLE이다. 이름이 같아도 ID를 공유하지 않는다. 프로젝트와 folderType으로 모든 페이지를 조회해 id·name·parentId를 확인한다. 부모도 같은 타입인지 확인하며, 동명이면 부모 경로로 식별한다.
3. Cycle 폴더가 없으면 승인 범위에서 MCP create_folder(projectKey, name, folderType=TEST_CYCLE, parentId)로 생성하고 실제 ID를 기록한다. TC 폴더는 변경하지 않는다.
4. 같은 프로젝트의 Cycle 이름·폴더·기록된 key를 대조한다. 생성 직전 pending, 응답의 실제 key, 재조회 결과를 remote-journal.json에 저장한다. 응답 유실 시 재조회부터 한다. 동명 기존 Cycle을 사용자 승인 없이 재사용하지 않는다.
5. create_test_cycle의 실제 folderId를 사용하고 project·folder.id·name을 재조회한다. 일정/담당자는 실제 입력이 있을 때만 지정한다. 신규 TC가 필요하면 publish-new.md의 등록 검증 후 실제 키로 치환한다.

## 기존 TC 대량 연결: 공통 도구
반복 연결·재개는 설치된 플러그인의 `scripts/tc_cycle.py`를 현재 Claude 세션에서 실행한다. 추가 LLM Agent나 TC별 MCP 반복 호출, 일회성 연결 스크립트를 새로 만들지 않는다. MCP는 폴더/Cycle 생성·기본 조회를 맡고, 공통 도구는 기존 Cycle에 기존 TC를 연결한다. 토큰은 기존 ZEPHYR_API_TOKEN에서 읽으며 인증 설정을 바꾸지 않는다.

작업 폴더에 cycle-plan.json을 만든다. 아래 ID/키는 자리표시자이므로 실제 조회 값으로 바꾼다. TC 키 파일은 1행 1키다. 회사 사이트·개인 경로·토큰·이번 작업의 ID를 배포 코드에 하드코딩하지 않는다.

```json
{
  "schema_version": "tc-cycle-plan-1",
  "project_key": "DEMO",
  "folder": {"id": 42, "name": "Regression", "parent_id": null},
  "cycles": [
    {"key": "DEMO-R1", "name": "Sprint regression", "tc_keys_file": "source/selected-keys.txt"}
  ]
}
```

```text
python <plugin>/scripts/tc_cycle.py check --plan <task>/cycle-plan.json --out <task>/cycle-link
python <plugin>/scripts/tc_cycle.py apply --plan <task>/cycle-plan.json --out <task>/cycle-link --expected-plan-hash <check 결과의 plan_hash> --workers 4
python <plugin>/scripts/tc_cycle.py verify --plan <task>/cycle-plan.json --out <task>/cycle-link
```

- check/verify는 원격 읽기만 한다. 해시는 검토한 대상·키 파일의 변경 검사값이지 승인 자체가 아니다. review.md/workflow.json에 실제 사용자 승인 근거를 남기고 같은 범위를 재승인받지 않는다.
- apply는 TEST_CYCLE 폴더·Cycle 이름/프로젝트/폴더·실제 Not Executed 상태를 확인한 뒤 미연결 키만 처리한다. 원본 TC 이동·복제·수정이나 Pass/Fail 기록은 하지 않는다.
- 공식 0.41.0 계약: 조회 GET /testexecutions/nextgen, limit/startAtId → nextStartAtId; 생성 POST /testexecutions/. 조회와 POST 경로를 혼동하지 않는다. 모든 페이지를 조회하고 잘못된 cursor·빈 중간 페이지를 성공으로 처리하지 않는다.
- 최대 4개 worker로 연결하며 journal.jsonl을 직렬 기록한다. progress.json으로 진행 수를 확인한다. 같은 출력 폴더에서 동시 실행을 막지만 다른 PC/폴더까지 잠그지는 않으므로 팀원끼리 같은 Cycle에 동시 게시하지 않는다.
- 429는 Retry-After를 따른다. 네트워크/5xx 등 POST 오류 시 새 연결을 멈추고 재조회한다. pending/uncertain 항목이 원격에 있으면 재사용한다. 원격에 없더라도 미반영이 확정된 것은 아니므로 자동 재전송하지 않고 blocked로 남긴다. 확인 없이 기록 삭제/새 작업 폴더로 우회하지 않는다.
- 다른 키·중복·이미 실행된 상태가 있으면 덮어쓰거나 삭제하지 않는다. result.json의 verified=true이며 missing/unexpected/duplicates/wrong_status가 모두 비어야 연결 완료다.
- result.json, readbacks의 전 페이지 응답, journal/progress/state를 보존한다. 중단/오류 시 state만 믿지 말고 같은 명령의 check/verify로 재조회한다. 계획이 바뀌면 앞 작업의 불확실 항목을 해결한 후 새 출력 폴더를 사용한다.

## 폴더 이동/복사와 완료 보고
folder_move가 명시되었을 때만 원래 folder ID를 보존하고 승인된 키에 MCP update_test_case의 숫자 folder 필드로 이동한다. 이동 후 본문·labels·단계 보존을 확인한다. folder_copy는 검증된 복제 도구와 원본→복제 키 매핑이 필요하며 신규 작성으로 흉내 내지 않는다.

최종 workflow.json/review.md의 현재 folder_type/id·Cycle key·선정 수·연결 수·페이지 수·누락/중복·상태·실패/미검증을 갱신한다. 검토 대기 상태를 방치하지 않는다. 원격 결과를 DB/작업 이력에 출처로 연결하되 TC 원래 source_folder는 바꾸지 않는다. 원격 구성 성공과 선정 품질·제품 시험은 별개다. 실제 모델/Agent·사람 또는 외부 피드백 여부도 기록한다.
