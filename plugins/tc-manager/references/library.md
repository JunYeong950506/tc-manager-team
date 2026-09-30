# TC 저장과 후보 검색

## 공용 PC 연결

공용 서버의 등록 사용자는 모두 admin·전체 프로젝트 권한을 사용한다. 최초 접근하는 프로젝트는 요청에서 확인한 실제 Jira 사이트와 프로젝트 키로 `<사이트호스트>/<프로젝트키>` namespace를 결정한다. 기존 명시적 매핑은 그대로 우선한다. 서버 권한을 추가하거나 코드에 프로젝트 목록을 수정할 필요가 없다. Jira/Zephyr 자체의 접근 권한은 별개이며, 프로젝트 자동 구획 생성은 실제 자료 수집이나 원격 권한 확인을 뜻하지 않는다. 로컬 모드는 기존 매핑 정책을 유지한다.

기본은 기존 로컬 DB다. `Connect-TC-Library.cmd`를 실행하고 운영자가 알려준 HTTPS 주소와 개인 TC 서버 토큰을 입력하면 권한 확인 후 다음 설정을 추가한다. 원본 설정은 백업하며 db_path와 namespace는 보존한다. 토큰 자체는 JSON에 저장하지 않는다.

```json
{"backend":"shared","server_url":"https://tc-server.example.invalid:5190","token_env":"TC_MANAGER_TOKEN"}
```

아래 tc_library 명령은 이 설정에서 공용 API로 조회·저장한다. 장애 시 로컬 저장으로 전환하지 않는다. `connection`으로 현재 서버 접근 권한을 확인한다. `backend:local`로 복귀하면 보존된 로컬 DB를 사용하지만 공용 서버의 변경 사항이 자동 복사되지는 않는다. 공용 서버 설치와 기존 DB 이관은 서버 패키지의 README를 따른다. 플러그인 자동 업데이트만으로 서버 설치·로컬 데이터 이관이 완료되지 않는다.

공용 모드에서 tc_registry.py/tc_readiness.py에 로컬 --db를 넘기지 않는다. 원본 bundle 저장은 `tc_library.py ... ingest <bundle.json>`을 사용한다. readiness.get, readiness.record, cases.current, cases.review는 `tc_library.py ... shared-rpc <operation> <args.json>`으로 호출한다. 작업 인자는 기존 tc_service RPC 규격이며 namespace와 사용자 권한은 설정과 서버가 결정한다. readiness.get 인자는 id/version, readiness.record는 event 객체, cases.current는 id/version/expected_version/reason이다. 현재 버전 선택·승인은 reviewer/admin 권한이 필요하며 권한 부족을 우회하지 않는다. 검토 이벤트와 승인/실제 시험 결과를 구분한다.

공용 검색은 서버의 versions/current_version 표시를 보고 기준 버전을 구분한다. 과거 후보가 함께 반환될 수 있으므로 마지막 항목이나 버전 이름만으로 최신을 고르지 않는다. Story 조회·연결은 `story-traceability.md`를 따른다.

## 설정과 현재 데이터
`scripts/tc_library.py`는 기존 SQLite registry 스키마를 재사용한다. Python 3.10+ 표준 라이브러리만 필요하다. `tc_registry.py`와 `qa_manage.py`는 기존 구현을 변경 없이 포함한다. DB를 새로 만들어 과거 TC를 숨기거나 기존 DB를 초기화하지 않는다.
프로젝트 `.tc-manager/library.json`:
```json
{
  "db_path": "data/tc-registry.sqlite3",
  "namespaces": [
    {"site": "https://example.atlassian.net", "project_key": "DEMO", "namespace": "example.atlassian.net/DEMO"}
  ]
}
```
상대 db_path는 .tc-manager의 상위 프로젝트 폴더 기준이다. 기존 namespace가 있다면 사이트/프로젝트와의 실제 관계를 확인해 매핑하고 바꾸지 않는다. 한 namespace를 두 사이트/프로젝트에 재사용하지 않는다. 요청한 프로젝트 매핑이 없으면 다른 프로젝트를 대신 검색하지 않는다.

팀 설치기는 기본 사이트의 PX·PXW·PXM 매핑을 함께 준비한다. 요청의 TC/Jira 키·링크에서 프로젝트를 결정해 `--site`와 `--project`를 전달한다. 여러 프로젝트를 요청하면 각각 조회·저장하며 결과에도 프로젝트를 표시한다. 기존 DB 경로·namespace는 유지하고 없는 매핑만 추가한다. 매핑 생성은 TC 수집이나 접근 권한 확인이 아니며, DB가 비어 있으면 수집 범위를 명시한다.
이 파일은 로컬 설정이며 팀 배포물에 실제 값·DB·인증을 포함하지 않는다. 각 PC의 SQLite는 자동 공유되지 않는다. 공용 서비스 연결은 위 shared 설정을 사용하고, 로컬 DB를 공용으로 보고하지 않는다. 네트워크 공유 드라이브의 SQLite를 동시 사용하도록 제안하지 않는다.

## 검색
아래 <plugin>, <site>, <project>는 실제 설치 위치와 설정 값이다. 사용자에게 내부 CLI를 직접 조립하라고 요구하지 않고 Agent가 실행한다.
```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> stats
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> search "기능 목적 조건" --limit 10
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> show <TC키> --version <후보버전>
```
- 명시적으로 선택된 current가 있으면 그것을 검색한다. 없는 TC는 버전들을 묶어 unselected_candidate로 반환한다. 마지막 저장 시각이나 버전 이름으로 최신을 추정하지 않는다.
- 결과는 TC별 묶음이다. 버전 수를 TC 수로 부르지 않는다. title/objective/precondition/script의 검색어 일치를 보여주며 전체 Script와 근거는 show로 읽는다.
- 검색은 단어/한글 조각 후보 검색이다. 동의어·관련 기능을 몇 가지로 확장하고 목적·조건·입력·기대 결과를 직접 비교한다. 의미상 중복 판정이나 누락 없는 검색을 코드가 보증하지 않는다.
- DB 없음은 unavailable이다. 후보 없음은 조회 범위 내 결과이며 Zephyr에 존재하지 않는다는 뜻이 아니다.
- 수집 범위·폴더·시각·pagination 완료·실제 TC 키 목록을 작업의 coverage.json에 남긴다. 최초 지정 범위를 수집하고, 이후 변경된 대상과 사용하려는 후보를 최신 원격 조회로 보완한다. 마지막 변경 정보를 못 읽으면 증분 완료라고 주장하지 않는다. 현재 도구에는 자동 동기화 데몬이 없다.
- DB 후보가 허용된 쓰기 범위 밖이면 참고 자료로만 사용한다. 등록/수정/이동 전에 원격 key·프로젝트·폴더·실제 내용을 확인한다.

## 구획별 해석과 분류
Precondition 전체를 실행 전제조건으로 해석하지 않는다. Objective는 목적·기능·범위, `[사전 조건]`은 환경·권한·데이터·초기 상태, `[테스트 스텝]`은 행동·시나리오, `[판정 기준]`은 기대 결과·검증 목적의 근거로 읽는다. 제목 표기가 다른 과거 문서는 실제 의미를 확인하고 애매하면 미분류로 남긴다. 스텝에서 특정 권한을 거부하는 시험을 그 권한이 필수라는 조건으로 오분류하지 않는다.
검색은 기존 필드 전체의 단어 후보 검색이며 구획별 의미 분류를 자동 보증하지 않는다. Agent가 분류를 제안할 때 버전·실제 `/precondition` 또는 `/objective` 경로와 정확한 인용문을 연결하고, 어떤 구획에서 해석했는지 review.md에 남긴다. 실제 JSON에 없는 `/precondition/steps` 같은 경로를 만들지 않는다. 지원되는 facet 범주만 사용하고 원문·전체 Script와 대조한다. 기존 DB를 일괄 재작성하거나 분류를 자동 확정하지 않는다.

## 저장
`zephyr-raw-1`은 최초 수집 원문이며 작성 완료 TC가 아니다. 빈 Objective/Precondition/Script를 그대로 허용하고, `source_folder`, `source_labels`, `source_script`와 API 원본 출처를 보존한다. `remote-` 기준본 선택은 수집 기준을 뜻하며 AI 변환·품질 승인으로 해석하지 않는다. 폴더 분류와 원문 용어 일치 태그는 근거가 있는 검색 후보이며 의미상 중복 확정이 아니다. 변환 시에는 이 원문을 읽고 아래 작성 양식으로 별도 버전을 만든다.

draft.json은 template.md 및 기존 core 형식(title/objective/precondition/steps/sources/decisions/blockers)을 유지한다. examples/draft.json을 참고한다.
evidence.json은 실제 조회 내용의 배열이며 아래 형식을 쓴다:
```json
[{"id":"SRC-1","kind":"requirement","locator":"실제 링크 또는 파일 경로","revision":"실제 개정 또는 조회 시각","text":"실제로 읽은 관련 내용"}]
```
kind는 test_case/change/requirement/bug/document/user_request 중 실제 자료 유형이다. draft.sources의 locator마다 evidence가 있어야 한다. 예시를 실제 근거로 사용하지 않는다.

공통 evidence 배열을 재사용해도 각 draft.sources에는 해당 TC가 실제로 사용한 자료만 넣는다. 저장 도구는 이 locator에 해당하는 snapshot만 포함한다. 단계별로 근거가 다르면 해당 step 객체에 `source_locators: ["실제 locator"]`를 추가하며 반드시 draft.sources의 부분집합이어야 한다. 생략하면 TC 수준 근거를 상속하므로 정확한 단계별 인용이라고 주장하지 않는다. 작성 결정 기록은 별도 내부 출처로 보존한다. 이 필드는 관리용이며 Zephyr Test Script 열에 출력하지 않는다.
```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> store <draft.json> --evidence <evidence.json> --tc-id <실제TC키또는DRAFT-ID> --version <새버전> --model <실제모델또는unknown> --producer <실제작성주체> --mode convert
```
create 작업은 --mode create다. --review-mode는 not_run/self_review/subagent/fixture 중 실제 수행 방식이며 기본 not_run이다. 이 명령은 내용 검토나 사람 검토 완료를 선언하지 않는다. 검토 기록은 review.md/workflow.json에 보존하고 `readiness-workflow.md`의 review 이벤트를 해당 DB 버전·해시에 연결한다. 약식 수행 결과도 같은 규격으로 관리한다.
TC ID/버전/원문·출처와 해시를 저장한다. 같은 버전의 내용 변경은 거부하며 전체 입력을 rollback한다. 원격 키가 없는 초안은 DRAFT-로 시작하는 안정된 ID를 쓰고, 등록 후 실제 key와 초안의 관계를 workflow.json에 남긴다. 초안은 current를 덮어쓰지 않는다.

원격 조회본은 export 버전과 로컬 작성 버전을 구분한다. 본문·단계 재조회가 끝난 신규 TC는 실제 key로 확인본을 store하고:
```text
python -X utf8 <plugin>/scripts/tc_library.py --site <site> --project <project> select-verified <prepared> --version <DB확인본버전> --expected-version <현재기준버전>
```
처음 기준을 선택할 때 --expected-version은 생략한다. verified 상태·해시·원격 key·DB 본문과 전체 단계를 검사한다. 동시 변경 시 실패하며 덮어쓰지 않는다. 검증된 원격 확인본의 선택이지 새로운 승인 부여가 아니다.
기존 TC 조회/수정본은 전체 재조회·대조 자료를 남긴 후 bundled tc_registry.py set-current를 사용한다. show의 current_version을 --expected-version에 그대로 넣는다. 불완전한 원문·미검토 초안을 원격 확인본으로 승격하지 않는다. 기존 2.0 bundle이 있으면 tc_registry.py ingest로 원문 그대로 저장할 수 있다.

DB의 기존 bundle 스키마 status=draft는 문서 모델 필드이며 Zephyr의 Draft/Approved 상태가 아니다. 원격 최신 여부, 사람 승인, 등록 여부는 보존된 실제 증거로 따로 판단한다.
