---
name: create
description: 요구사항·변경 티켓에서 시나리오 후보를 도출하고 복수 선택한 TC를 작성한다. 근거 탐색·기존 DB 대조·최종 검토 후 Zephyr에 등록한다.
argument-hint: 요구사항 또는 Jira 링크
---

# 신규 TC 작성

사용자 요청: $ARGUMENTS

`../../references/workflow.md`와 `../../references/template.md`를 읽고 적용한다. 필요한 때만 아래 참조를 추가로 읽는다. 단건·여러 건을 같은 흐름으로 처리한다.

먼저 `../../references/scenario-selection.md`를 읽고 `AskUserQuestion`의 복수 선택 질문으로 **기본값·성능·안정성 + 직접 입력** 시나리오를 확인한다. 같은 작업에서 이미 지정한 관점은 재사용하고, 선택 결과를 workflow.json에 보존해 아래 탐색·중복 대조·작성·검토에 일관되게 적용한다.

1. 목적과 변경 범위·제품 버전을 파악한다. Jira 티켓과 연결된 Confluence를 실제 조회하고, 요구사항별 기대 동작·출처·개정을 evidence.json/search-log.json에 보존한다. 양쪽 탐색 결과와 접근 실패를 구분한다. 충분한 근거를 이미 확보했으면 같은 자료를 다시 읽지 않는다.
2. `../../references/library.md`의 DB 검색을 수행한다. DB가 없거나 미수집·기준 버전 미선택이면 명시하고 허용 범위의 Zephyr 자료로 보완한다. 제목 외에 목적·조건·입력·기대 결과를 대조하여 후보별 reuse/revise/new/exclude와 이유를 duplicates.json에 기록한다. 유사도를 중복 확정으로 사용하지 않는다. 기존 TC로 충족되면 신규를 강제하지 않는다.
3. `../../references/scenario-candidates.md`를 읽고 선택 관점별 시나리오 후보를 충분히 도출한다. 티켓당 1개로 제한하지 않으며 의미 있는 조건·상태·조작·판정의 차이로 나눈다. 2번의 DB 검색 결과로 후보별 중복을 대조하고 부족한 본문을 조회한다. 전체 후보·근거·신규/재사용/개정 판단을 보여준 뒤 복수 선택을 받아 scenario-candidates.json에 보존한다. 선택 응답 전에는 상세 TC 작성·원격 생성을 진행하지 않는다.
4. 선택된 TC만 draft.json으로 작성한다. Precondition에는 `[사전 조건]`·`[테스트 스텝]`·`[판정 기준]`으로 사람이 수행할 전체 시나리오를 쓰고 같은 내용을 Test Script에서 실행 입력·관찰·분기·판정으로 구체화한다. 자료가 부족하면 template.md의 근거 보완 절차를 따른다. 기존 TC 개정이 더 적절하면 해당 항목에만 convert 절차를 적용하고 신규 생성과 구분한다. 요구사항에 없는 기능·수치·제품 동작을 추가하지 않는다. 관찰·측정 방법은 Agent가 제안한다.
5. Precondition과 Script의 조건·행동·판정 대응 및 원문 충실성·근거·구체성·판정 가능성·중복과 `../../references/execution-readiness.md`의 단계 간 연결을 점검하고 한 번 보완한다. Script와 명시적 실행 입력만으로 준비·분기·판정·종료가 이어져야 한다. DB에 초안 버전을 저장하되 현재 버전으로 승격하지 않는다. review.md에 본문·중복 대조·출처·실행 준비 점검·남은 판단·등록 대상을 모아 최종 검토를 요청한다. 기존 최종 승인 범위가 있으면 다시 묻지 않는다.
6. 승인한 TC는 `../../references/publish-new.md`로 신규 등록하고 재조회한다. Precondition의 구역 제목·각 항목은 별도 줄, 구역 사이는 빈 줄로 작성하고 prepare의 HTML payload를 그대로 전달한다. 실제 key·본문·줄 구분·전 단계가 확인된 뒤 library.md로 DB 확인본을 저장한다. 인증·도구 제약이면 초안과 재개 위치를 남기고 성공으로 표시하지 않는다.

결과: 읽기용 TC + 출처 + 기존 TC와 차이 + DB 저장 결과 + Zephyr 실제 key/반영 상태. 테스트 실행과 Pass/Fail은 포함하지 않는다.

Jira 요구사항에서 작성한 TC는 `../../references/story-traceability.md`에 따라 원본 티켓의 다대다 Coverage 연결도 등록·재조회하고, 본문 검증과 링크 검증 결과를 분리해 표시한다.
