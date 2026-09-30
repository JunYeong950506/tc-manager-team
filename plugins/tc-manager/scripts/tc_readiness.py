"""Version-bound content review and lightweight execution handoff. Never executes tests."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import tc_registry as registry


CHECKS = {"source_fidelity", "source_support", "state_transitions", "observability", "recovery", "field_consistency"}
OUTCOMES = {"confirmed", "tc_defect", "environment_missing", "tool_limitation", "product_defect", "requirement_conflict"}
ROUTES = {"tc_defect": "revise_tc", "environment_missing": "prepare_environment", "tool_limitation": "change_tool_or_manual",
          "product_defect": "investigate_product", "requirement_conflict": "research_then_human_decision"}
DDL = """CREATE TABLE IF NOT EXISTS readiness_events (
    ordinal INTEGER PRIMARY KEY, namespace TEXT NOT NULL, event_id TEXT NOT NULL,
    tc_id TEXT NOT NULL, version TEXT NOT NULL, content_hash TEXT NOT NULL,
    action TEXT NOT NULL, payload_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(namespace,event_id), FOREIGN KEY(namespace,tc_id,version) REFERENCES cases(namespace,tc_id,version))"""


def obj(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("필수/지원 필드를 확인하십시오: " + ", ".join(sorted(required)))


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("비어 있지 않은 문자열(12000자 이하)이 필요합니다")
    return value


def strings(value, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value) or len(value) > 1000:
        raise ValueError("문자열 배열이 필요합니다")
    for item in value:
        text(item)
    if len(value) != len(set(value)):
        raise ValueError("중복된 항목입니다")
    return value


def steps_for(row):
    tc = json.loads(row["raw_json"])
    return [step["id"] for step in tc.get("test_script", tc.get("steps", []))]


def history(db, namespace, tc_id, version):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='readiness_events'").fetchone():
        return []
    return [dict(event_id=r["event_id"], action=r["action"], content_hash=r["content_hash"],
                 payload=json.loads(r["payload_json"]), actor=r["actor"], created_at=r["created_at"])
            for r in db.execute("SELECT * FROM readiness_events WHERE namespace=? AND tc_id=? AND version=? ORDER BY ordinal",
                                (namespace, tc_id, version))]


def status(db, namespace, tc_id, version):
    row = registry.select_case(db, namespace, tc_id, version)
    events = history(db, namespace, tc_id, row["version"])
    reviews = [e for e in events if e["action"] == "review"]
    requests = [e for e in events if e["action"] == "request"]
    feedback = [e for e in events if e["action"] == "feedback"]
    defects = [r for e in feedback for r in e["payload"]["results"] if r["outcome"] in {"tc_defect", "requirement_conflict"}]
    review = reviews[-1]["payload"]["decision"] if reviews else "not_reviewed"
    if defects:
        review = "changes_required"
    latest = requests[-1] if requests else None
    result = next((e for e in reversed(feedback) if latest and e["payload"]["request_id"] == latest["event_id"]), None)
    results = result["payload"]["results"] if result else []
    confirmed = [r["check_id"] for r in results if r["outcome"] == "confirmed"]
    requested = latest["payload"]["checks"] if latest else []
    pending = [c["id"] for c in requested if c["id"] not in confirmed]
    requested_steps = {s for c in requested for s in c["step_ids"]}
    checked_steps = sorted(s for s in requested_steps if all(c["id"] in confirmed for c in requested if s in c["step_ids"]))
    issues = [dict(r, route=ROUTES[r["outcome"]]) for r in results if r["outcome"] != "confirmed"]
    trial = "not_requested" if not latest else "awaiting_execution" if not result else "needs_attention" if issues else "partially_confirmed" if pending else "confirmed_scope"
    return dict(tc_id=tc_id, version=row["version"], content_hash=row["content_hash"], content_review=review,
                lightweight_execution=trial, formal_execution="not_recorded", request_id=latest["event_id"] if latest else None,
                confirmed_checks=confirmed, pending_checks=pending, checked_steps=checked_steps,
                unverified_steps=[s for s in steps_for(row) if s not in checked_steps],
                excluded_scope=latest["payload"]["excluded_scope"] if latest else [],
                issues=issues, revision_issues=defects, history=events,
                handoff=dict(schema_version="1.0", intent="lightweight_readiness_only",
                             namespace=namespace, tc_id=tc_id, version=row["version"], content_hash=row["content_hash"],
                             request_id=latest["event_id"], request=latest["payload"],
                             test_case=json.loads(row["raw_json"])) if latest else None,
                notice="실행 Agent가 제출한 기록 기준입니다. 약식 수행은 지정 범위의 수행 가능성 확인이며 제품 Pass/Fail 또는 정식 수행 완료가 아닙니다.")


def validate_review(payload, row):
    obj(payload, {"decision", "mode", "checks", "findings"})
    if payload["decision"] not in {"approved", "changes_required"} or payload["mode"] not in {"self_review", "independent_review"}:
        raise ValueError("검토 결정/방식이 잘못되었습니다")
    obj(payload["checks"], CHECKS)
    for check in payload["checks"].values():
        obj(check, {"result", "detail"})
        if check["result"] not in {"checked", "not_applicable", "issue"}:
            raise ValueError("검토 항목 결과가 잘못되었습니다")
        text(check["detail"])
    strings(payload["findings"], False)
    tc = json.loads(row["raw_json"])
    if payload["decision"] == "approved" and (payload["findings"] or tc.get("open_questions") or
            tc.get("closure", {}).get("open_questions") or any(c["result"] == "issue" for c in payload["checks"].values())):
        raise ValueError("미해결 질문/검토 결함이 남아 있어 내용 검토 완료로 기록할 수 없습니다")


def validate_request(payload, row, state):
    obj(payload, {"purpose", "environment", "checks", "excluded_scope", "limits"})
    if state["content_review"] != "approved":
        raise ValueError("현재 버전의 내용 검토 완료 후 약식 수행을 요청하십시오")
    text(payload["purpose"])
    text(payload["environment"])
    strings(payload["excluded_scope"])
    obj(payload["limits"], {"max_actions", "timeout_seconds"})
    for key, maximum in (("max_actions", 1000), ("timeout_seconds", 3600)):
        value = payload["limits"][key]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("약식 수행 한도는 양의 정수이며 최대 행동 1000회/대기 3600초입니다")
    if not isinstance(payload["checks"], list) or not 1 <= len(payload["checks"]) <= 100:
        raise ValueError("약식 수행 확인 항목은 1~100개여야 합니다")
    ids = []
    for check in payload["checks"]:
        obj(check, {"id", "step_ids", "action", "expected_observation"})
        ids.append(text(check["id"]))
        if set(strings(check["step_ids"])) - set(steps_for(row)):
            raise ValueError("현재 TC에 없는 단계입니다")
        text(check["action"])
        text(check["expected_observation"])
    strings(ids)


def validate_feedback(payload, events):
    obj(payload, {"request_id", "environment", "results"})
    text(payload["environment"])
    request = next((e for e in events if e["event_id"] == payload["request_id"] and e["action"] == "request"), None)
    if not request:
        raise ValueError("이 TC 버전에 연결된 약식 수행 요청이 없습니다")
    if any(e["action"] == "feedback" and e["payload"]["request_id"] == payload["request_id"] for e in events):
        raise ValueError("이미 종료된 요청입니다. 재수행은 새 요청으로 기록하십시오")
    if payload["environment"] != request["payload"]["environment"]:
        raise ValueError("요청한 환경과 다릅니다. 변경된 환경으로 새 요청을 만드십시오")
    if not isinstance(payload["results"], list) or not payload["results"]:
        raise ValueError("실제 확인 결과가 필요합니다")
    valid_ids = {c["id"] for c in request["payload"]["checks"]}
    ids = []
    for result in payload["results"]:
        obj(result, {"check_id", "outcome", "detail", "evidence"})
        ids.append(result["check_id"])
        if result["check_id"] not in valid_ids or result["outcome"] not in OUTCOMES:
            raise ValueError("요청에 없는 확인 항목 또는 지원하지 않는 결과입니다")
        text(result["detail"])
        strings(result["evidence"], result["outcome"] not in {"environment_missing", "tool_limitation"})
    strings(ids)


def record(db, namespace, event, actor):
    obj(event, {"event_id", "tc_id", "version", "content_hash", "action", "payload"})
    for key in ("event_id", "tc_id", "version", "content_hash"):
        text(event[key])
    row = registry.select_case(db, namespace, event["tc_id"], event["version"])
    if row["content_hash"] != event["content_hash"]:
        raise ValueError("검토/수행 대상 본문 해시가 다릅니다")
    db.execute(DDL)
    db.execute("CREATE INDEX IF NOT EXISTS readiness_case ON readiness_events(namespace,tc_id,version)")
    old = db.execute("SELECT * FROM readiness_events WHERE namespace=? AND event_id=?", (namespace, event["event_id"])).fetchone()
    serialized = registry.canonical(event["payload"])
    if old:
        if any(old[k] != event[k] for k in ("tc_id", "version", "content_hash", "action")) or old["payload_json"] != serialized:
            raise ValueError("같은 event_id로 다른 내용을 기록할 수 없습니다")
        return dict(duplicate=True, **status(db, namespace, event["tc_id"], event["version"]))
    state = status(db, namespace, event["tc_id"], event["version"])
    if event["action"] == "review":
        validate_review(event["payload"], row)
        if event["payload"]["decision"] == "approved" and state["revision_issues"]:
            raise ValueError("실행에서 발견한 TC/요구사항 문제를 새 버전에서 보완하십시오")
    elif event["action"] == "request":
        validate_request(event["payload"], row, state)
    elif event["action"] == "feedback":
        validate_feedback(event["payload"], state["history"])
    else:
        raise ValueError("지원하지 않는 준비 상태 작업입니다")
    db.execute("INSERT INTO readiness_events(namespace,event_id,tc_id,version,content_hash,action,payload_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (namespace, event["event_id"], event["tc_id"], event["version"], event["content_hash"], event["action"],
                serialized, text(actor), datetime.now(timezone.utc).isoformat()))
    return dict(duplicate=False, **status(db, namespace, event["tc_id"], event["version"]))


def main():
    from tc_library import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(".tc-manager/library.json"))
    parser.add_argument("--site", required=True)
    parser.add_argument("--project", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("status")
    p.add_argument("tc_id")
    p.add_argument("--version", required=True)
    p = commands.add_parser("record")
    p.add_argument("event", type=Path)
    p.add_argument("--actor", required=True)
    args = parser.parse_args()
    db_path, namespace = settings(args.config, args.site, args.project)
    if not db_path.is_file():
        raise ValueError("DB에 TC 버전을 먼저 저장하십시오")
    with closing(registry.connect(db_path, create=args.command == "record")) as db, db:
        db.execute("BEGIN IMMEDIATE" if args.command == "record" else "BEGIN")
        result = (record(db, namespace, json.loads(args.event.read_text(encoding="utf-8-sig")), args.actor)
                  if args.command == "record" else status(db, namespace, args.tc_id, args.version))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
