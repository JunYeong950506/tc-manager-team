---
name: plan
description: 특정 기능·변경·릴리스의 테스트를 준비한다. DB에서 TC를 선정하고 부족한 TC를 보완하여 최종 검토 후 Test Cycle 또는 명시한 폴더를 구성한다.
argument-hint: 시험 목적·범위·릴리스 또는 변경 티켓
---

# 테스트 준비

사용자 요청: $ARGUMENTS

`../../references/workflow.md`와 `../../references/library.md`를 읽는다. 제품 테스트를 실행하지 않는다.

1. 요청에서 제품·프로젝트·시험 목적·변경 범위·버전·제외 범위를 파악한다. 결정에 필요한 값만 질문하며 나머지는 관련 Jira·Confluence에서 확인한다. 일정·담당자가 없다고 TC 선정을 중단하지 않는다. 미정 일정이나 시간을 임의로 확정하지 않는다.
2. DB 수집 범위와 기준 버전 상태를 확인하고 기능·목적·조건·기대 결과를 검색한다. 폴더 이름만으로 선정/제외를 끝내지 않는다. 제목·Objective·Precondition·필요한 Script와 인접 기능 후보를 대조하고 클라이언트 OS와 서버 OS, MAC 주소와 macOS를 구분한다. 요청 범위의 신규·변경 티켓도 대조한다. 후보 원문을 확인해 reuse/revise/new/needs_evidence/exclude로 분류하고 선정/제외 근거를 남긴다. 검색 결과의 누락 가능성을 표시한다.
3. `../../references/test-preparation.md`의 selection.json과 review.md를 작성한다. 원격에 없는 로컬 초안을 기존 Zephyr TC 키처럼 넣지 않는다. 선정된 TC에서 중요한 실행 입력·분기·판정 공백이 발견되면 `../../references/execution-readiness.md`를 기준으로 revise/needs_evidence에 표시한다. 중요한 검증 범위 공백은 신규 작성 절차(create)를 그 항목에만 적용한다. 요청 범위 내에서 기존 TC 보완과 신규 작성까지 진행하고 최종 결과를 한 번에 검토받는다. 범위를 크게 넓히는 추가 요구는 별도로 제안한다.
4. 기본은 원본 TC 위치를 유지하며 시험용 Test Cycle에 연결하는 제안이다. 사용자가 폴더를 명시했으면 의도를 그대로 기록한다. 일회성 시험 묶음인지 영구 분류 이동인지 불명확할 때만 확인한다. 폴더를 요청했는데 몰래 Cycle로 대체하지 않는다.
5. 최종 검토 후 `test-preparation.md`에 따라 실제 제공 도구로 구성한다. Test Cases와 Test Cycles의 동명 폴더는 다른 대상이다. TEST_CYCLE 폴더와 실제 Cycle key를 확인하고 반복 연결은 번들 `scripts/tc_cycle.py`의 check → apply → verify를 사용한다. TC마다 모델/MCP 호출을 반복하거나 임시 연결 코드를 새로 만들지 않는다. 기존 승인된 범위를 재승인받지 않는다. 생성 key·TC별 연결 결과·재조회·실패 지점을 기록한다. 신규 작성 TC는 등록 완료 후 실제 키로 포함한다.

결과: 시험 목적·선정된 TC·선정/제외 이유·보완한 TC·부족한 범위·실제 Cycle/폴더 위치. 자동 Test Plan 객체·Confluence 문서 발행·제품 실행은 요청한 경우에만 별도 범위로 다룬다.
