# API 토큰 방식 Zephyr MCP 계약

기준: 공식 로컬 `@smartbear/mcp` 0.41.0 설치 코드 확인. 현재 세션의 도구 스키마가 달라지면 실제 스키마를 우선한다. 아래는 도구의 동작 계약이며 현재 계정·모든 프로젝트의 권한이나 실제 업무 완료를 보증하지 않는다.

## 연결과 조회
- `ZEPHYR_API_TOKEN`을 사용하는 로컬 stdio MCP가 기본이다. 폐기한 원격 OAuth의 30분 갱신 절차를 재사용하지 않는다. 연결 실패 시 실제 오류를 확인하고 API 토큰의 만료·폐기·권한·프로세스 환경 변수 반영 여부를 구분한다. API 토큰이라는 이유만으로 영구 유효하다고 단정하지 않는다.
- 도구 수(예: 37개), JWT 접두사, transport만으로 인증 성공을 판단하지 않는다. 실제 읽기 성공과 요청 대상의 권한을 확인한다. 토큰 자체는 출력하지 않는다.
- 프로젝트와 폴더 설정이 있으면 먼저 그 값을 확인한다. 매번 전체 프로젝트·전체 폴더를 나열하지 않는다. `get_folders`는 `projectKey`, TC 관리면 `folderType=TEST_CASE`, Cycle 구성이면 `folderType=TEST_CYCLE`로 범위를 좁혀 pagination한다. 동명 폴더의 ID를 다른 타입에 재사용하지 않는다. 이름뿐 아니라 프로젝트·타입·parent ID·folder ID를 대조한다. 한 페이지에 안 나온 것을 폴더 없음으로 처리하지 않는다.

## 실제 입력 필드와 응답
| 작업 | 현재 MCP 입력 | 처리와 확인 |
|---|---|---|
| 신규 TC | `create_test_case`: `projectKey`, `name`, `folderId`, 필요한 본문 필드 | 반환된 실제 key를 기록하고 본문·폴더·단계를 조회 |
| 기존 TC 수정 | `update_test_case`: `testCaseKey`와 변경할 필드만 | 서버 도구가 현재 TC를 GET한 뒤 병합해 PUT. 미지정 필드는 보존. 빈 응답도 가능하므로 재조회 필수 |
| TC 폴더 이동 | `update_test_case`: `testCaseKey`, `folder: <양의 정수 ID>` | 생성용 `folderId` 또는 REST용 `{id: ...}` 객체를 MCP 입력으로 혼용하지 않음. 실제 folder.id 재조회 |
| 폴더 생성 | `create_folder`: `projectKey`, `name`, `folderType=TEST_CASE`, `parentId` | 최상위는 null, 하위는 실제 parent ID. 반환 ID를 저장하고 부모·타입·프로젝트를 검증 |
| 단계 작성 | `create_test_case_steps`: `testCaseKey`, `mode`, `items[].inline` | description/testData/expectedResult 3필드. 단계 수·순서·모든 필드를 전 페이지 재조회 |
| Cycle 폴더 생성 | `create_folder`: `projectKey`, `name`, `folderType=TEST_CYCLE`, `parentId` | TC 폴더와 별도 ID. 부모도 TEST_CYCLE인지 검증 |
| Cycle 생성 | `create_test_cycle`: `projectKey`, `name`, `folderId` | 실제 key와 project·folder.id·name 재조회 |
| 기존 TC 연결 | `create_test_execution`: `projectKey`, `testCaseKey`, `testCycleKey`, `statusName=Not Executed` | 실제 미실행 상태를 조회. 반복 연결은 tc_cycle.py 사용 |

Cycle 연결 REST 계약은 조회 `GET /testexecutions/nextgen`의 `limit/startAtId/nextStartAtId`와 생성 `POST /testexecutions/`를 구분한다. 목록의 한 페이지나 POST 성공 개수만으로 전체 완료를 판단하지 않는다. test-preparation.md의 전 페이지 key 집합·중복·상태 검증을 따른다.

수정에서 null은 삭제 의도이며 빈 필드를 자동으로 null로 채우지 않는다. labels 같은 배열을 보내면 기존 배열을 대체하므로, 추가 요청이면 최신 기존 값과 합친 배열을 전달한다. REST가 PUT이라는 이유로 MCP에 모든 필드를 다시 보내지 않는다. MCP 병합이 동시 수정 충돌을 원자적으로 막아 주는 것은 아니므로 최신 원문 대조는 유지한다.

0.41.0의 update-test-case 구현과 웹 문서 main의 파라미터 설명은 다를 수 있다. 이번 확인에서는 설치 코드가 폴더 이동 입력을 숫자 `folder`로 정의한다. REST 주소에 직접 보낼 객체와 혼동하지 않는다.

## 빈 단계와 입력 방식
새 TC라도 단계가 0개라고 가정하지 않는다. 자동 생성된 빈 단계가 남아 있을 수 있다. 먼저 전체 단계를 조회하고 내용·위임 단계·첨부 등 보존 대상을 확인한다.
- 검토한 전체 Script를 반영하는 신규 TC: 자동 빈 단계만 확인되면 `OVERWRITE`를 제안한다. 이미 검토본과 같으면 쓰지 않고 재조회 검증한다.
- 실제 기존 단계 뒤에 새 단계를 추가하는 요청: `APPEND`를 제안한다. 예상 최종 단계는 기존 단계 + 추가 단계이며, 기존 단계의 보존까지 비교한다. 전체 교체용 steps.json을 APPEND로 임의 전환하지 않는다.
- 실내용/첨부/위임 단계가 발견되면 보존 또는 교체 범위를 확인한다. 빈 단계 확인을 첨부 없음 확인으로 대신하지 않는다.

현재 단계 도구 설명은 사용자에게 APPEND/OVERWRITE 선택을 확인하도록 요구한다. 최종 검토 문서에 대상 key, 현재 단계 수, 최종 단계 수, 보존 대상, 제안 mode를 포함하고 사용자 선택을 그 자리에서 함께 받는다. 동일 대상·본문·mode의 선택이 이미 있으면 반복 질문하지 않는다. 단순 'TC 작성해줘'를 단계 삭제 방식 선택으로 간주하지 않는다.

의도한 6단계에 빈 단계 1개가 더 붙어 7개라면 단계 쓰기 동작은 성공했어도 최종 Script 검증은 mismatch이다. '등록 완료'로 넘기지 않고 실제 key와 남은 차이를 기록한다. 빈 단계 정리는 명시한 교체 범위 안에서 같은 TC에 수행하며 신규 TC를 다시 생성하지 않는다.

## 검증 상태 구분
기본 조회·신규 생성·수정·단계 쓰기·폴더 생성·이동의 성공은 그 도구 동작에 대한 근거다. Jira/Confluence 탐색, DB 유사 비교, 최종 검토, 본문 재조회, DB 확인본 저장까지의 convert/create/plan 전체 흐름과 별도로 기록한다. 폴더 생성·이동 성공을 Test Cycle 구성 성공으로 바꾸지 않는다.

현재 MCP에 delete 도구가 없다는 사실을 Zephyr REST API 전체에 삭제 기능이 없다는 결론으로 확대하지 않는다. 이번 스킬은 삭제를 자동 정리 단계로 사용하지 않는다.
