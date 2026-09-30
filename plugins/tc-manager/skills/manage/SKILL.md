---
name: manage
description: TC 관리 요청을 기존 TC 정리, 신규 작성, 테스트 준비로 구분한다. 재개와 수정 의견은 원래 업무로 연결한다.
user-invocable: false
---

# TC 관리 진입점

사용자 요청: $ARGUMENTS

약식 수행 준비·상태 조회·실행 피드백 반영 요청이면 `../../references/readiness-workflow.md`를 읽어 기존 TC 버전과 연결한다. 피드백에 TC 보완이 필요할 때만 아래 convert 절차로 이어간다. 실제 테스트를 수행하라는 요청은 실행 Agent용 handoff까지 준비하고, 실행 도구가 없으면 미수행을 명시한다.

그 외에는 아래 중 해당 Skill 하나에 요청·첨부·기존 결과를 그대로 전달한다. 명령 이름을 사용자에게 다시 고르라고 요구하지 않는다.

| 의도 | 호출 |
|---|---|
| 기존 TC를 변환·정리·보완·개정하거나 파일/폴더의 TC를 DB화 | `tc-manager:convert` |
| 요구사항·변경 티켓으로 필요한 TC를 작성 | `tc-manager:create` |
| 특정 기능·스프린트·릴리스의 시험 대상 선정·테스트 준비 | `tc-manager:plan` |
| 사용법, 빈 요청 | `tc-manager:help` |

수정 의견·재개 요청은 직전 작업의 workflow.json과 산출물에서 업무와 완료 항목을 확인한다. 새 업무를 만들거나 원격 생성부터 반복하지 않는다. 기존 TC가 요구사항의 참고 자료일 뿐이고 신규 작성이 목적이면 create이다. 복합 요청은 plan이 범위를 관리하고 필요한 작성만 수행한다. 명령 안에서 동일 Skill을 재귀 호출하지 않는다. 의도가 구분되지 않을 때만 목적을 한 번 질문한다.

이전 workflow.json의 mode가 prepare이면 plan 업무로 해석해 이어간다. 저장된 원격 key와 완료 상태는 유지한다.
