# 기존 TC에 변환본 반영

신규 생성으로 기존 TC 수정을 대신하지 않는다. workflow.md의 최종 검토와 범위를 적용한다. 실제 노출된 MCP 도구와 스키마를 확인하고, 없는 동작은 지원한다고 가정하지 않는다.

`zephyr-mcp-contract.md`를 함께 읽는다. 확인한 MCP 0.41.0의 update_test_case는 현재 원격 값을 읽어 병합하므로 미지정 필드는 유지한다. 변경 필드만 보내고 null/배열 교체 의미를 구분한다. 빈 응답 `{}`도 가능한 정상 응답이지만 실제 반영 판정은 재조회로 한다. 이 계약은 REST PUT 전체의 동작을 일반화한 설명이 아니다.

1. 원본 metadata와 모든 단계 페이지를 저장한다. 실제 project/folder/key, Script 유형과 보존해야 할 첨부·위임 단계·사용자 정의 필드를 기록한다. 필요한 필드만 바꾸는 계획(update-plan.json: key, before, after, changed_fields, source hashes)을 작성한다.
2. Objective/Precondition만 바뀌면 단계 쓰기를 하지 않는다. 단계가 바뀌면 Script 유형과 첨부·call-to-test 구조를 실제 조회/화면으로 확인한다. 단계 조회에 첨부 필드가 없다는 이유로 빈 첨부라고 추정하지 않는다. 보존 수단을 확인 못 했으면 해당 단계 교체만 차단하고 이유를 남긴다.
3. 쓰기 직전에 같은 원격 key의 본문·전체 단계를 다시 조회해 계획의 before와 대조한다. 타인의 변경, 프로젝트/폴더 변경, 예상 밖 Script가 있으면 쓰지 않는다. 과거 승인/접미사를 근거로 다른 TC까지 쓰지 않는다.
4. journal에 key·대상·작업·before/after·pending을 저장한 뒤 실제 Update Test Case 도구로 승인된 필드만 수정한다. 필수 필드가 있는 스키마라면 새로 조회한 값을 보존하여 전달한다. 단계 변경은 구조화된 Step/Test Data/Expected Result 도구로 수행한다. 일반 텍스트/BDD용 Create Test Script로 바꾸지 않는다. 전체 교체 시 실제 스키마의 OVERWRITE를 사용하고 APPEND로 중복시키지 않는다.
5. 응답과 메타데이터·전 단계를 재조회해 after와 비교한다. 표시 HTML의 차이는 tc_draft.plain으로 정규화하되 값·순서·단계 수를 빠뜨리지 않는다. Objective/Precondition은 평문 초안을 tc_draft.rich로 변환해 전송하고, 재조회 HTML과 평문 초안을 tc_draft.layout_matches로 대조하여 줄 구분 유실도 확인한다. plain 결과를 게시하거나 내용만 같다고 표시 형식까지 통과시키지 않는다. 첨부 등 보존 대상도 확인한다. 일부만 반영되면 partial로 보고하고 실패 지점부터 재개한다. 원본 백업이 있다는 이유로 자동 rollback하여 동시 수정 내용을 잃게 하지 않는다.
6. 확인본과 실제 응답을 DB에 새 버전으로 저장하고 기준 선택 시 compare-and-set(--expected-version)을 사용한다. 제목만 바뀌었는데 본문 변환 완료라고 보고하지 않는다.

MCP에 수정 기능이 없으면 변경 전후와 준비 파일을 제공하고 원격 미반영을 명시한다. API/브라우저 대안을 쓰려면 실제 제공 기능과 동일한 허용 범위를 확인한다. 계정·인증 도구 설정을 임의 교체하지 않는다. 새 TC용 import-as-new.xml은 기존 TC 갱신 파일이 아니다.
