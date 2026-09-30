# 신규 TC 등록과 재조회

공통 workflow.md의 최종 검토 절차를 먼저 적용한다. 아래 경로는 기존 TC 수정이나 Cycle 생성용이 아니다.

## 등록 대상

목적지는 사용자 요청·TC/Jira 키·링크에서 확인한다. 프로젝트의 `.tc-manager/target.json`은 site·project_keys 후보 목록일 수 있으므로 등록 명령에 그대로 넘기지 않는다. 작업 폴더에 별도 target.json을 만들고 단일 `site`, `project_key`, `folder_id`, `folder_name`을 확정한다. `project_id`는 실제 Zephyr ID를 알 때만 넣는다. token_project_key는 토큰 페이지용으로 등록 대상이 아니다. 이름만으로 폴더 ID를 추측하지 않는다. 같은 이름의 다른 프로젝트·하위 폴더를 포함하지 않는다. 설정은 사용자 요청·권한을 대신하지 않는다. 팀원 계정·토큰·PC 경로·TC 키는 스킬에 고정하지 않는다.

`python -X utf8 <plugin>/scripts/tc_draft.py prepare draft.json --target <target.json> --output <작업폴더>/prepared`

이 명령은 구조 검사와 `report.md`, `create.json`, `steps.json`, `import-as-new.xml`, `state.json`을 만든다. 초안만 요청하면 여기서 결과를 보여준다. blockers가 있으면 보고서만 남기고 등록 payload를 만들지 않는다. 코드 검사는 내용의 진실성이나 실제 수행 가능성을 보증하지 않는다.

Precondition의 구역 제목과 각 항목은 별도 줄, 구역 사이는 빈 줄이어야 한다. 형식 오류가 나오면 초안의 줄 구분을 고쳐 다시 prepare한다. create.json의 objective/precondition은 HTML 줄바꿈을 포함한 전송용 값이다. MCP 호출 시 draft.json의 평문이나 plain() 결과로 바꾸지 않고 그대로 전달한다. verify의 `objective.layout`/`precondition.layout` 불일치는 줄 구분 유실이므로 같은 TC에서 수정·재조회하며 신규 생성하지 않는다.

## MCP로 신규 등록 — 기본

작성 후 등록까지 요청받았다면 같은 범위의 승인을 다시 묻지 않는다. 초안 작성은 인증 없이 진행 가능하고, 실제 쓰기 직전에 연결 상태·대상·중복을 확인한다.

1. `zephyr-mcp-contract.md`와 현재 세션 도구 스키마를 확인한다. 프로젝트·폴더 기본값이 있으면 먼저 그 대상을 확인하고 전체 프로젝트를 다시 탐색하지 않는다. 폴더 조회는 프로젝트·타입으로 필터링한다. 대상 폴더의 TC 목록을 끝까지 확인해 같은 제목이 이미 있으면 현재 작업의 저장된 key·내용과 대조한다. 기존 TC를 자동 갱신하지 않는다.
2. `state.json`이 prepared일 때 `tc_draft.py pending <prepared>`로 생성 시도 전 기록한다. `create.json`을 실제 Create Test Case 도구에 전달한다. 응답을 `created-response.json`에 그대로 보존하고 `tc_draft.py created <prepared> --response created-response.json`으로 반환 key를 기록한다. **생성 응답을 잃었거나 pending 상태로 재개되면 Create를 반복하지 않는다.** 폴더에서 제목/생성 시각/본문을 찾아 실제 생성 여부부터 확인한다. 확정 못 하면 재시도하지 말고 남긴다.
3. 새로 반환된 key로 TC와 전체 현재 단계를 조회한다. 폴더가 대상과 일치하고 자동 생성된 빈 단계뿐인지 확인한다. 새 TC이므로 비었다고 가정해 APPEND하지 않는다. 내용이 있는 다른 단계·첨부가 있으면 덮어쓰지 않는다. `zephyr-mcp-contract.md`에 따라 최종 검토에서 전체 Script와 OVERWRITE 선택을 함께 확인하고, 이 작업에서 생성한 TC에 `steps.json`의 items와 `mode=OVERWRITE`, 실제 반환 key를 전달한다. 같은 대상·본문·mode의 기존 선택은 반복 확인하지 않는다. Create Test Script(plain/BDD)가 아니다. 기존 TC의 첨부 목록을 일괄 수집하는 구 절차는 신규 작성의 필수 조건이 아니다.
4. TC 상세와 전체 단계(모든 페이지)를 다시 조회해 `readback-metadata.json`, `readback-steps.json`으로 저장한다. `tc_draft.py verify <prepared> --metadata <파일> --steps <파일>`로 제목·Objective·Precondition·폴더·단계 수·Step/Test Data/Expected Result 전체를 비교한다. 차이가 없을 때만 등록 완료로 보고한다. API 응답 wrapper가 있으면 원응답도 보존하고 실제 데이터 객체/페이지를 파일로 분리한다.
5. API 토큰 인증 오류면 실제 만료·폐기·권한·프로세스 환경을 구분한다. 30분 OAuth 재인증으로 되돌리지 않는다. 파일과 실제 key를 유지하고 연결 후 같은 TC의 남은 단계부터 진행한다. 5xx/응답 유실은 쓰기 성공 여부를 재조회한 후 판단한다. 새 TC를 반복 생성하거나 scope를 넓히지 않는다. 단계 쓰기가 실패하면 `state.json`의 created와 실제 key를 보고한다. 생성 key에 대해 내용이 이미 일치하면 다시 쓰지 않고 verify한다. 빈 단계 등으로 수가 다르면 mismatch를 그대로 보존하며 등록 완료로 표시하지 않는다.

## 파일 가져오기 — 대안

MCP 연결을 사용할 수 없거나 사용자가 파일 방식을 선택하면 위에서 생성한 `import-as-new.xml`을 제공한다. 형식은 SmartBear 공식 XML 변환기의 project/testCases/testCase, testScript(type=steps) 구조다. 예시 입력/출력은 `../examples/`에 있다.

Zephyr의 대상 프로젝트·폴더에서 More → Import from file → Zephyr XML을 선택하고 파일을 가져온다. 실제 화면의 형식 이름과 매핑·목적지를 확인한다. **Import 대상은 진입한 폴더와 달리 Root가 기본일 수 있으므로 Destination folder를 명시적으로 선택한다.** 등록 직전 `pending`으로 시도를 기록한다. 생성 완료 후 **신규 key와 본문·단계 전체를 확인**한다. 화면에서 확인한 생성 key는 관찰 경로를 포함한 JSON으로 보존하고 `created`로 기록한다. 파일 생성만으로 가져오기 성공이라고 말하지 않는다. 한 TC를 MCP와 파일로 이중 등록하지 않는다. 가져오기를 시작한 후 실패/응답 유실이면 먼저 대상 폴더를 조회한다. 사용자가 이미 등록을 요청한 범위에서는 가져오기마다 재승인을 받지 않는다.

## 결과

XML 가져오기 후 MCP 재조회도 불가능하면 Zephyr UI에서 **생성된 그 TC만 선택해 Export to XML**로 다시 내보낸다. 원래 가져오기 파일을 재조회 근거로 쓰지 않는다. `tc_draft.py verify-xml <prepared> --export <실제내보내기.xml> --folder-path <UI에서확인한전체폴더경로>`로 원격 key·전체 폴더 경로·제목·본문·모든 단계의 일치를 확인한다. 이 방법은 XML 내보내기 대조이며 API 재조회로 표시하지 않는다. 성공 화면·실제 key·폴더 URL도 함께 보존한다.

제목·원격 key/링크·실제 목적지·본문/단계 대조 결과·사용한 경로(MCP/Import)·남은 질문을 짧게 보여준다. Markdown/JSON/XML 경로도 제공한다. 검증 완료 후 library.md에 따라 실제 원격 key의 확인본을 DB에 저장한다. 실제 테스트 실행은 수행하지 않았다고 명확히 구분한다.
