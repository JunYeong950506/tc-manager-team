# 내용 검토 · 약식 수행 · 피드백

TC 작성/계획 기간과 정식 수행 기간은 다르다. 내용 검토는 기본 절차이고, 실제 환경이 필요한 확인만 별도의 약식 수행 요청으로 만든다. 약식 수행 미완료 때문에 초안 저장·계획·사람 검토를 전부 막지 않는다. 새 Agent나 자동 호출 루프를 추가하지 않는다.

## 상태와 저장 원칙
- `content_review`: not_reviewed / approved / changes_required. 자체 검토와 독립 검토 방식을 기록하며 사람의 승인과 구별한다.
- `lightweight_execution`: not_requested / awaiting_execution / partially_confirmed / confirmed_scope / needs_attention.
- `formal_execution=not_recorded`: 이 관리 도구에는 정식 수행 결과가 없다. Zephyr Test Cycle의 실제 실행 결과는 별도이다.
- namespace·TC ID·version·content_hash에 연결한다. 기존 결과를 새 버전에 복사하지 않는다. 원문 수집본의 draft, 재조회 verified를 내용 검토 완료로 해석하지 않는다.
- 기존 TC 본문/Zephyr 양식은 변경하지 않는다. 관리 기록은 DB의 readiness_events에 추가하며 원문과 이전 기록을 보존한다. 오래된 review.md는 자동 승인 이관하지 않고, 실제 본문을 다시 점검했을 때만 새 기록을 남긴다.

## 작성/변환 후 내용 검토
DB 저장 후 `tc_library.py show`에서 해당 버전과 content_hash를 얻는다. review.md와 아래 검토 이벤트를 함께 기록한다. 작성한 초안의 해시와 다른 본문을 검토한 것으로 보고하지 않는다.

필수 검토 항목(각 항목에 관련 단계·근거·보완 결과를 detail로 적는다):
| 키 | 점검 |
|---|---|
| source_fidelity | 원문 목적·수치·기존 사용자 결정 보존 |
| source_support | 보완 기준의 실제 Jira/Confluence 출처, 접근 실패와 미발견 구별 |
| state_transitions | 단계 전제·종료 상태, 이전 결과에 따른 다음 단계 진입·중단 분기 |
| observability | 관찰 대상·기록 시작/종료·측정식·실제로 수집 가능한 증거 |
| recovery | 복구·재접속·재시도에서 최초 실패 보존, 독립 조건 재준비 |
| field_consistency | Precondition 세 구획과 Script 내용·숫자·단계 참조 일치 |

각 checks 값은 `{"result":"checked|not_applicable|issue","detail":"실제 점검 내용"}`이다. 관련 없는 항목은 이유를 적고 not_applicable로 남긴다. findings는 미해결 내용의 문자열 배열이다. 미해결 질문/issue가 있으면 decision=changes_required로 저장한다. 구조 검사는 검토 내용 자체의 진실성이나 정확성을 증명하지 않는다.

공통 이벤트 형식:
```json
{
  "event_id": "작업 안에서 고유한 이벤트 ID",
  "tc_id": "실제 TC 키 또는 DRAFT-ID",
  "version": "실제로 저장한 버전",
  "content_hash": "show로 읽은 해시",
  "action": "review",
  "payload": {
    "decision": "approved",
    "mode": "self_review",
    "checks": {
      "source_fidelity": {"result":"checked","detail":"관련 단계와 대조한 원문"},
      "source_support": {"result":"checked","detail":"실제로 읽은 자료와 미확인 범위"},
      "state_transitions": {"result":"checked","detail":"시작/종료 상태와 분기"},
      "observability": {"result":"checked","detail":"관찰 방법과 증거 수집 절차"},
      "recovery": {"result":"checked","detail":"복구와 최초 결과 보존"},
      "field_consistency": {"result":"checked","detail":"사람용 시나리오와 Script 대조"}
    },
    "findings": []
  }
}
```
예시의 detail을 그대로 복사해 검토했다고 하지 않는다. 독립 Agent를 실제 호출했을 때만 independent_review를 쓴다.

## 약식 수행 요청
사용자가 요청했거나 내용 검토 중 실제 환경 확인 필요성이 드러나면 내용 검토를 마친 버전에 action=request 이벤트를 만든다. 요청 생성은 실제 수행 시작이 아니다. 환경이 없으면 미수행으로 남긴다. 내용 결함이 있으면 먼저 보완하고 새 버전을 검토한다.

payload 예시(실제 환경·단계·한도로 대체):
```json
{
  "purpose": "장시간 시험 전에 로그 수집과 종료 절차의 수행 가능성 확인",
  "environment": "실제 시험 환경 식별자와 제품 빌드; 비밀번호 제외",
  "checks": [{"id":"C1","step_ids":["S1"],"action":"수집을 시작하고 기록 파일을 확인한 뒤 종료한다","expected_observation":"타임스탬프가 있는 기록을 얻고 원래 상태로 복구할 수 있다"}],
  "excluded_scope": ["24시간 안정성과 제품 합격/불합격"],
  "limits": {"max_actions":20,"timeout_seconds":300}
}
```
- 한도는 실행 정책이며 제품 성능 합격 기준이 아니다. 코드가 실행을 강제 중지하지 않으므로 수신 실행 Agent가 이 한도를 적용해야 한다.
- `status` 결과의 handoff를 별도 JSON으로 저장해 실행 Agent에 전달한다. 스냅샷·버전·해시·요청 범위·미확인 범위를 포함한다. 대시보드에서도 내려받을 수 있다.
- 관리 Agent가 실행 Agent를 자동 기동하는 기능은 없다. 연결된 실행 경로가 없으면 handoff 준비까지 보고하고 실행했다고 하지 않는다.
- 동작/메뉴 접근, 실행 입력 연결, 관찰·수집 방법, 필요한 복구만 제한해 확인한다. 무작위 몇 단계 통과로 전체 TC 정상 판정하지 않는다. 장시간·부하·동시성 시험 전체를 축약 수행한 것으로 처리하지 않는다.

## 실행 피드백 수신
같은 공통 이벤트에 action=feedback, payload는 다음 형식이다:
```json
{
  "request_id":"요청 event_id",
  "environment":"요청과 동일한 실제 환경 식별자와 빌드",
  "results":[{"check_id":"C1","outcome":"confirmed","detail":"실제로 수행하고 관찰한 내용","evidence":["실제 로그 또는 스크린샷 위치"]}]
}
```
제품 Pass/Fail은 outcome으로 받지 않는다. confirmed도 실행 Agent가 제출한 증거 참조 기준이며 서버가 화면이나 외부 파일의 진실성을 독립 판정한 것은 아니다. Agent는 원문 증거를 실제로 읽고 판단해야 한다. 미제출 항목은 미확인으로 남기며 제출된 결과를 덮어쓰지 않는다. 다시 시도하려면 새 request_id로 남기고 최초 실패를 보존한다.

| outcome | 관리 Agent의 후속 조치 |
|---|---|
| confirmed | 해당 확인 항목/환경만 확인됨. 정식 수행은 별도 |
| tc_defect | 누락·모순을 보완한 새 버전 저장 → 바뀐 내용 검토 → 필요한 범위만 약식 재확인 |
| environment_missing | 계정·장비·환경 준비 요청. TC가 틀렸다고 재작성하지 않음 |
| tool_limitation | 다른 도구/수동 수행 방법 제안. 사람 업무 기준 질문으로 바꾸지 않음 |
| product_defect | 현상·재현 근거 보존, 제품 결함 조사. 기대 결과를 실제 오류에 맞춰 수정하지 않음 |
| requirement_conflict | Jira·Confluence 재탐색 → 출처가 충돌하는 최종 선택만 사람에게 요청 |

TC/요구사항 결함이 기록된 버전은 다시 approved로 표시하지 않는다. 필요하면 근거와 결정 기록을 보완한 새 버전에서 검토한다. 보완은 기본 1회이며 남은 문제는 구체적으로 보고한다. 모든 피드백을 사람 검토 대기열로 보내지 않는다. 환경/도구 준비와 제품 결함은 각 원인으로 보고하고, 사람의 승인·기준 결정이 필요한 때만 기존 작업의 waiting_review/waiting_input을 사용한다.

## Agent가 사용하는 저장 명령
사용자는 `/tc-manager 이 TC의 약식 수행을 준비해줘`, `이 약식 수행 결과를 반영해줘`처럼 짧게 요청한다. 내부 JSON과 CLI는 Agent가 작성한다.
```text
python -X utf8 <plugin>/scripts/tc_readiness.py --site <site> --project <project> record <event.json> --actor <실제기록주체>
python -X utf8 <plugin>/scripts/tc_readiness.py --site <site> --project <project> status <TC키> --version <저장버전>
```
로컬 DB는 library.json의 기존 매핑을 재사용한다. 공용 서비스는 인증된 `/api/tc-manager/rpc`에 `readiness.record` args=`{"namespace":"설정된 namespace","event":<이벤트>}`, 조회는 `readiness.get` args=`{"namespace":"설정된 namespace","id":"TC키","version":"버전"}`를 사용한다. 인증 주체는 서버가 정하며 payload로 위장하지 않는다. viewer는 쓰지 못하고 약식 요청/피드백은 runner/admin만 기록한다. 팀원이 서버 DB를 직접 열거나 개인 DB를 공용 DB처럼 보고하지 않는다.
