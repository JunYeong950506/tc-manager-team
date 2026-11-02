# 작성 상태·초안 보존과 재개 (1.6.0)

create/convert/plan에서 실제 TC 초안을 작성·보완한 경우 이 절차를 적용한다. 초안만 요청하면 폴더 선택·Zephyr prepare·등록을 요구하지 않는다. 기능/성능/안정성 관점·후보 선택은 기존 절차를 유지한다.

## 상태와 보존
- registered = 등록 완료: 최종 사람 검토 및 실제 등록·전체 재조회·Story Coverage 확인 후에만 지정한다.
- needs_evidence = 보완 대기: 기대 동작 누락·충돌 또는 구현 여부 확인이 필요하다. 실제 검색 후에도 남은 사유·관련 단계·다음 조회 대상을 reason/resume_when에 쓴다.
- awaiting_implementation = 구현 대기: 기대 동작은 명확하며 대상 버전 미구현이 근거로 확인된 경우다. Jira 상태만으로 판단하지 않는다.
- status:null = 초안 보존. 미등록이라는 이유로 보완/구현 대기로 표시하지 않는다. API·접속·등록 오류와 실제 시험 실패는 별도 작업 오류/시험 결과다.

각 TC 후보는 최초 UUID 기반 고정 id를 작업 폴더 candidate.json에 보존한다. 같은 Story의 여러 후보는 서로 다른 id다. 재개 시 새 id를 만들지 않는다. 같은 후보의 수정은 expected_revision을 증가시키며 새 operation_id를 부여한다. 응답 유실은 같은 payload·operation_id로 다시 저장한다. conflict면 최신 get으로 비교하고 타인의 변경을 덮어쓰지 않는다.

`tc_candidate.py`는 라이브러리와 동일한 --config/--site/--project를 사용한다. 공유 연결 실패를 로컬 성공으로 표시하지 않는다. 작업 폴더의 candidate.json/draft.json/evidence.json을 유지하고 서버 저장 실패·재전송 필요를 결과에 표시한다. 초기에는 DB 프로젝트만 식별하며 Zephyr 목적지 질문은 등록 직전으로 미룬다.

```text
python -X utf8 <plugin>/scripts/tc_candidate.py --site <site> --project <project> list --story <Jira키>
python -X utf8 <plugin>/scripts/tc_candidate.py --site <site> --project <project> --output <작업폴더>/candidate-history.json get <후보ID>
python -X utf8 <plugin>/scripts/tc_candidate.py --site <site> --project <project> save <작업폴더>/candidate.json
```

candidate.json은 아래 구조다. draft는 해당 작업의 전체 초안을 축약 없이, evidence는 실제 확보한 원문 객체 배열을 넣는다. 미완성 단계는 불확실성을 표시하며 억지로 양식 완료/원격 payload를 만들지 않는다.

```json
{
  "id":"<고정후보ID>","expected_revision":0,"operation_id":"<저장작업UUID>",
  "story_keys":["PX-123"],"status":"needs_evidence",
  "draft":{"title":"복구 후 알림 해제","objective":"...","precondition":"...","steps":[],"sources":[],"decisions":[],"blockers":["알림 해제 조건 미확인"]},
  "reason":"알림이 자동 해제되는지 요구사항에서 확인하지 못함. 복구 단계의 기대 결과 미확정.",
  "resume_when":"관련 Story 댓글·Confluence에서 해제 조건 확인 후 기대 결과 보완",
  "evidence":[],"target":null
}
```

## 재개와 등록 완료
1. 기존 후보 get으로 전체 초안·근거·이력을 읽는다. 이미 remote_key가 있거나 원격 생성 중 기록이 있으면 Zephyr부터 읽고 중복 생성하지 않는다.
2. 관련 Jira 본문/AC·변경 댓글·Confluence를 확인하고 영향받는 단계·기대 결과만 수정한다. 정상→장애→장애 유지→복구→복구 후 확인을 대조한다. 복구 동작·시간을 창작하지 않는다.
3. 해결되지 않으면 상태·사유·재개 조건을 갱신한다. Jira 완료/Ready for Testing만으로 완료 승격하지 않는다.
4. 해결되면 최종 사람 검토를 받는다. 이미 동일 최종 초안/범위에 대한 승인이 있으면 반복하지 않는다. review-confirmation.json에 approved:true, draft_hash(tc_draft.digest의 정확한 최종 초안 해시), 실제 승인 메시지 근거를 남긴다. 자체 점검을 사람 승인으로 바꾸지 않는다.
5. publish-new.md의 목적지 선택·등록·재조회·Traceability 확인을 완료한다. 같은 draft의 verified state.json이 있는 prepared 폴더를 --prepared로, 위 승인 기록을 --review로 지정하여 status:registered를 저장한다. 서버에는 재조회 결과를 제출한 기록이며 서버가 Zephyr를 직접 검증한 것으로 보고하지 않는다.

```text
python -X utf8 <plugin>/scripts/tc_candidate.py --site <site> --project <project> save <candidate.json> --prepared <prepared폴더> --review <review-confirmation.json>
```

이후 library.md에 따라 실제 TC 확인본 저장·sync-notify도 수행한다. candidate 저장 자체가 TC 실적이나 source 동기화가 아니다. 결과는 등록 완료 N건/보완 대기 N건/구현 대기 N건, 초안 N건과 각 사유·후보ID·공용 저장 성공 여부로 보고한다.

## 등록 직전 프로젝트·폴더 선택
작성만 요청하면 질문하지 않는다. 실제 등록을 요청한 때 BLAZE(PX)/BLAZE WEB(PXW)/BLAZE Mobile(PXM)/기타를 제시하고, 티켓에서 확인한 프로젝트를 추천한다. 사용자가 이미 지정한 목적지는 확인·재사용하며 반복 질문하지 않는다. 원본 Story와 다른 프로젝트를 선택하면 원본 Story 소속은 바꾸지 말고 대상 불일치를 확인하고 프로젝트별 작성 대상을 분리한다.

기본은 Story의 실제 Epic 키·제목을 조회하고 해당 프로젝트의 Epic명 폴더를 탐색한다. 폴더 ID는 추측하지 않으며 동명 후보는 전체 경로로 선택받는다. 대안은 직접 이름/경로/링크 지정 또는 최상위 임시 폴더다. Epic/대응 폴더가 없으면 이 두 대안을 제시한다. 임시 폴더명은 사용자가 주거나 `임시_TC작성_<기능명>`을 제안하고 해당 선택에 포함하여 확인한다. 실제 폴더 생성은 등록 직전에만 수행하고 기존 폴더 재정렬·TC 이동은 하지 않는다. 저장한 target은 재개 시 실존 여부·프로젝트를 다시 확인한다. 기존 ai_agent 라벨 병합·Story Traceability 연결·원격 충돌/중복 방지 절차를 유지한다.
