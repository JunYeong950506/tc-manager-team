"""TC and Story storage adapter. Local SQLite or the authenticated shared service."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import tc_registry as registry
import tc_draft as draft_core
import tc_stories as stories
from tc_shared import Remote


def settings(path, site, project):
    path = Path(path).resolve()
    config = draft_core.load(path)
    parsed = urlsplit(site)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("site에는 자격 증명 없는 HTTPS 사이트 주소가 필요합니다")
    site = site.rstrip("/").lower()
    bindings = config.get("namespaces", [])
    identities, namespaces = set(), set()
    for row in bindings:
        identity = (row["site"].rstrip("/").lower(), row["project_key"])
        if identity in identities or row["namespace"] in namespaces:
            raise ValueError("사이트/프로젝트 또는 namespace 중복 매핑입니다")
        identities.add(identity)
        namespaces.add(row["namespace"])
    match = [row for row in bindings if (row["site"].rstrip("/").lower(), row["project_key"]) == (site, project)]
    if not match and config.get("backend") == "shared":
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", project):
            raise ValueError("실제 Jira 프로젝트 키가 필요합니다")
        namespace = parsed.hostname.lower() + "/" + project
        if namespace in namespaces:
            raise ValueError("새 프로젝트 namespace가 기존 매핑과 충돌합니다")
        return Remote(config, namespace), namespace
    if len(match) != 1:
        raise ValueError("요청한 사이트/프로젝트의 DB 매핑이 없습니다. 다른 프로젝트를 대신 검색하지 않습니다")
    backend = config.get("backend", "local")
    if backend == "shared":
        return Remote(config, match[0]["namespace"]), match[0]["namespace"]
    if backend != "local":
        raise ValueError("backend는 local 또는 shared여야 합니다")
    db = Path(config["db_path"])
    if not db.is_absolute():
        db = path.parent.parent / db
    return db.resolve(), match[0]["namespace"]


def search(db_path, namespace, query, limit=10):
    if isinstance(db_path, Remote):
        return db_path.rpc("cases.list", dict(q=query, limit=limit, group_by_id=True))
    if not 1 <= limit <= 100 or not registry.tokens(query):
        raise ValueError("검색어와 1~100 사이 limit가 필요합니다")
    if not Path(db_path).is_file():
        return {"status": "unavailable", "reason": "DB 미생성", "candidates": [], "duplicate_decision": "not_performed"}
    terms = registry.tokens(query)
    grouped = {}
    with closing(registry.connect(db_path)) as db:
        rows = db.execute(
            "SELECT c.*, h.version AS selected_version FROM cases c LEFT JOIN current_cases h "
            "ON c.namespace=h.namespace AND c.tc_id=h.tc_id WHERE c.namespace=? "
            "AND (h.version IS NULL OR h.version=c.version)", (namespace,))
        for row in rows:
            fields = {name: sorted(terms & registry.tokens(row[name]))
                      for name in ("title", "objective", "precondition", "script_json")}
            matched = set(term for values in fields.values() for term in values)
            if not matched:
                continue
            item = grouped.setdefault(row["tc_id"], {"tc_id": row["tc_id"], "versions": [], "score": 0})
            score = len(matched) / len(terms)
            item["score"] = max(item["score"], score)
            item["versions"].append({"version": row["version"], "title_excerpt": row["title"][:100],
                "content_hash": row["content_hash"], "matched_fields": fields,
                "basis": "selected_current" if row["selected_version"] else "unselected_candidate"})
    candidates = sorted(grouped.values(), key=lambda row: (-row["score"], row["tc_id"]))
    for item in candidates:
        item["score"] = round(item["score"], 6)
        item["versions"].sort(key=lambda row: row["version"])
    return {"status": "searched", "namespace": namespace, "query": query,
        "inventory": registry.stats(db_path, namespace), "total_candidates": len(candidates),
        "candidates": candidates[:limit], "duplicate_decision": "not_performed",
        "remote_coverage": "unknown_until_scope_evidence_checked",
        "limitation": "단어/한글 조각 후보 검색이며 의미상 중복 판정이 아닙니다. 미선택 버전은 최신본이 아닙니다. 후보 전체 Script와 출처를 확인하세요."}


def bundle_from_draft(draft, evidence, tc_id, version, model, producer, mode, review_mode):
    for name in ("decisions", "blockers"):
        if not isinstance(draft.get(name), list) or any(not isinstance(v, str) for v in draft[name]):
            raise ValueError(name + " 문자열 배열이 필요합니다")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("조회한 근거 snapshot 배열이 필요합니다")
    locators = {row.get("locator") for row in evidence}
    if not draft.get("sources") or any(row.get("locator") not in locators for row in draft["sources"]):
        raise ValueError("draft.sources의 모든 locator에 실제 근거 snapshot이 필요합니다")
    record_id = "TC-MANAGER-DRAFT-RECORD"
    if record_id in {row.get("id") for row in evidence}:
        raise ValueError("예약된 출처 ID입니다")
    selected = {row["locator"] for row in draft["sources"]}
    sources = [row for row in evidence if row.get("locator") in selected] + [{"id": record_id, "kind": "document", "locator": "draft-record:" + tc_id,
        "revision": version, "text": registry.canonical(draft)}]
    refs = [row["id"] for row in sources]
    step_refs = []
    for step in draft["steps"]:
        locators = step.get("source_locators")
        if locators is None:
            step_refs.append(refs)
            continue
        if not isinstance(locators, list) or not locators or any(not isinstance(v, str) or v not in selected for v in locators):
            raise ValueError("단계 source_locators는 draft.sources에 있는 실제 출처의 비어 있지 않은 배열이어야 합니다")
        step_refs.append([row["id"] for row in sources if row["locator"] in locators] + [record_id])
    tc = {"id": tc_id, "version": version, "title": draft["title"],
        "status": "needs_input" if draft["blockers"] else "draft", "source_refs": refs,
        "objective": draft["objective"], "precondition": draft["precondition"],
        "test_script": [{"id": f"S{i}", "step": step["step"], "test_data": step["test_data"],
            "expected_result": step["expected_result"], "source_refs": step_refs[i - 1]}
            for i, step in enumerate(draft["steps"], 1)], "open_questions": draft["blockers"]}
    return {"schema_version": "2.0", "run": {"id": tc_id + ":" + version, "mode": mode,
        "created_at": draft_core.stamp(), "producer": producer, "model": model, "plugin_version": json.loads((Path(__file__).resolve().parents[1] / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"],
        "review_mode": review_mode}, "sources": sources, "test_cases": [tc], "plan": None,
        "review": {"status": "pending", "rounds": 0, "findings": []},
        "changes": [{"target": "/test_cases/0", "reason": text} for text in draft["decisions"] if text.strip()],
        "feedback": []}


def store(db_path, namespace, draft, evidence, **kwargs):
    bundle = bundle_from_draft(draft, evidence, **kwargs)
    result = db_path.rpc("imports.add", dict(bundle=bundle)) if isinstance(db_path, Remote) else registry.ingest(db_path, bundle, namespace)
    result["review_status"] = "pending; tc_readiness.py로 본문 해시에 연결한 검토 기록을 저장하십시오. 사람 승인은 별도입니다"
    return result


def select_verified(db_path, namespace, prepared, version, expected_version=None):
    folder, state, draft, target = draft_core.context(prepared)
    if state["status"] != "verified":
        raise ValueError("원격 재조회 verified 상태만 기준 버전으로 선택할 수 있습니다")
    key = state["remote_key"]
    existing = (db_path.rpc("cases.get", dict(id=key, version=version)) if isinstance(db_path, Remote) else registry.show(db_path, namespace, key, version))["test_case"]
    if any(existing[name] != draft[name] for name in ("title", "objective", "precondition")):
        raise ValueError("DB 본문과 재조회 확인본이 다릅니다")
    script = [{k: step[k] for k in ("step", "test_data", "expected_result")}
              for step in existing.get("test_script", [])]
    if script != [{k: step[k] for k in ("step", "test_data", "expected_result")} for step in draft["steps"]]:
        raise ValueError("DB 단계와 재조회 확인본이 다릅니다")
    if isinstance(db_path, Remote):
        return db_path.rpc("cases.current", dict(id=key, version=version, expected_version=expected_version, reason="재조회 검증본 " + state["draft_hash"]))
    return registry.set_current(db_path, namespace, key, version, expected_version=expected_version,
        actor="tc-manager/readback", reason="재조회 검증본 " + state["draft_hash"])


def story_call(db_path, namespace, op, args):
    if isinstance(db_path, Remote):
        return db_path.rpc(op, args)
    if not db_path.is_file() and op not in stories.WRITE_OPS:
        return dict(status="unavailable", reason="DB 미생성")
    with closing(registry.connect(db_path, create=op in stories.WRITE_OPS)) as db, db:
        db.execute("BEGIN IMMEDIATE" if op in stories.WRITE_OPS else "BEGIN")
        return stories.execute(db, namespace, op, args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(".tc-manager/library.json"))
    parser.add_argument("--site", required=True)
    parser.add_argument("--project", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    commands.add_parser("stats")
    commands.add_parser("connection")
    p = commands.add_parser("shared-rpc")
    p.add_argument("operation", choices=("readiness.get", "readiness.record", "cases.current", "cases.review"))
    p.add_argument("file", type=Path)
    p = commands.add_parser("stories-import")
    p.add_argument("file", type=Path)
    p = commands.add_parser("stories-search")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--unlinked", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p = commands.add_parser("story")
    p.add_argument("key")
    p = commands.add_parser("tc-stories")
    p.add_argument("tc_id")
    p.add_argument("--version")
    for command in ("link-plan", "link-record", "ingest"):
        p = commands.add_parser(command)
        p.add_argument("file", type=Path)
    p = commands.add_parser("show")
    p.add_argument("tc_id")
    p.add_argument("--version")
    p = commands.add_parser("store")
    p.add_argument("draft", type=Path)
    p.add_argument("--evidence", type=Path, required=True)
    for name in ("tc-id", "version", "model", "producer"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--mode", choices=("convert", "create"), required=True)
    p.add_argument("--review-mode", choices=("not_run", "self_review", "subagent", "fixture"), default="not_run")
    p = commands.add_parser("select-verified")
    p.add_argument("prepared", type=Path)
    p.add_argument("--version", required=True)
    p.add_argument("--expected-version")
    args = parser.parse_args()
    db, namespace = settings(args.config, args.site, args.project)
    if args.command == "connection":
        result = db.rpc("context") if isinstance(db, Remote) else dict(backend="local", namespace=namespace, db_path=str(db))
        if isinstance(db, Remote) and "*" not in result.get("namespaces", []) and namespace not in result.get("namespaces", []):
            raise ValueError("공용 서버에 이 프로젝트의 namespace 권한이 없습니다")
    elif args.command == "shared-rpc":
        if not isinstance(db, Remote):
            raise ValueError("shared-rpc는 공용 서버 설정에서만 사용합니다")
        payload = draft_core.load(args.file)
        if not isinstance(payload, dict) or "principal" in payload:
            raise ValueError("서버가 사용자 권한을 결정합니다. 작업 인자 객체만 전달하세요")
        result = db.rpc(args.operation, payload)
    elif args.command == "stories-import":
        payload = draft_core.load(args.file)
        for row in payload.get("stories", []):
            if not row["key"].startswith(args.project + "-") or row["source_url"] != args.site.rstrip("/") + "/browse/" + row["key"]:
                raise ValueError("Story 사이트/프로젝트가 선택한 DB와 다릅니다")
        result = story_call(db, namespace, "stories.import", payload)
    elif args.command == "stories-search":
        result = story_call(db, namespace, "stories.search", dict(q=args.query, limit=args.limit, offset=args.offset, unlinked=args.unlinked))
    elif args.command == "story":
        result = story_call(db, namespace, "stories.get", dict(key=args.key))
    elif args.command == "tc-stories":
        result = story_call(db, namespace, "stories.for-case", dict(tc_id=args.tc_id, version=args.version))
    elif args.command in {"link-plan", "link-record"}:
        result = story_call(db, namespace, "stories.links." + ("plan" if args.command == "link-plan" else "record"), draft_core.load(args.file))
    elif args.command == "ingest":
        bundle = draft_core.load(args.file)
        if any(not re.fullmatch(re.escape(args.project) + r"-T[1-9][0-9]*|DRAFT-[A-Za-z0-9_.-]+", tc["id"]) for tc in bundle["test_cases"]):
            raise ValueError("이관 TC의 프로젝트가 설정과 다릅니다")
        result = db.rpc("imports.add", dict(bundle=bundle)) if isinstance(db, Remote) else registry.ingest(db, bundle, namespace)
    elif args.command == "search":
        result = search(db, namespace, args.query, args.limit)
    elif args.command == "stats":
        result = db.rpc("overview") if isinstance(db, Remote) else registry.stats(db, namespace) if db.is_file() else {"status": "unavailable", "reason": "DB 미생성"}
    elif args.command == "show":
        result = db.rpc("cases.get", dict(id=args.tc_id, version=args.version)) if isinstance(db, Remote) else registry.show(db, namespace, args.tc_id, args.version)
    elif args.command == "store":
        if not re.fullmatch(re.escape(args.project) + r"-T[1-9][0-9]*|DRAFT-[A-Za-z0-9_.-]+", args.tc_id):
            raise ValueError("TC ID가 요청 프로젝트 또는 DRAFT- 식별자가 아닙니다")
        result = store(db, namespace, draft_core.load(args.draft), draft_core.load(args.evidence),
            tc_id=args.tc_id, version=args.version, model=args.model, producer=args.producer,
            mode=args.mode, review_mode=args.review_mode)
    else:
        _, _, _, target = draft_core.context(args.prepared)
        if target["site"].rstrip("/").lower() != args.site.rstrip("/").lower() or target["project_key"] != args.project:
            raise ValueError("재조회 대상과 DB 사이트/프로젝트가 다릅니다")
        result = select_verified(db, namespace, args.prepared, args.version, args.expected_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
