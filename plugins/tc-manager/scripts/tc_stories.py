"""Requirement snapshots and observed Jira/TC links. Does not call Jira or Zephyr."""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

import tc_registry as registry

WRITE_OPS = {"stories.import", "stories.links.record"}
OPERATIONS = {
    "stories.import": ({"namespace", "stories", "scope"}, set()),
    "stories.search": ({"namespace"}, {"q", "limit", "offset", "unlinked"}),
    "stories.get": ({"namespace", "key"}, set()),
    "stories.for-case": ({"namespace", "tc_id"}, {"version"}),
    "stories.links.plan": ({"namespace", "tc_id", "version", "expected_keys", "links"}, set()),
    "stories.links.record": ({"namespace", "tc_id", "version", "content_hash", "observed_at", "links", "complete"}, {"expected_keys"}),
}
DDL = (
    "CREATE TABLE IF NOT EXISTS story_snapshots (namespace TEXT NOT NULL, issue_key TEXT NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, raw_json TEXT NOT NULL, actor TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(namespace,issue_key,content_hash), UNIQUE(namespace,issue_key,updated_at))",
    "CREATE TABLE IF NOT EXISTS story_heads (namespace TEXT NOT NULL, issue_key TEXT NOT NULL, content_hash TEXT NOT NULL, PRIMARY KEY(namespace,issue_key), FOREIGN KEY(namespace,issue_key,content_hash) REFERENCES story_snapshots(namespace,issue_key,content_hash))",
    "CREATE TABLE IF NOT EXISTS story_collections (namespace TEXT NOT NULL, collection_hash TEXT NOT NULL, scope_json TEXT NOT NULL, keys_json TEXT NOT NULL, actor TEXT NOT NULL, PRIMARY KEY(namespace,collection_hash))",
    "CREATE TABLE IF NOT EXISTS story_link_reads (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, version TEXT NOT NULL, observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, actor TEXT NOT NULL, PRIMARY KEY(namespace,tc_id,version,observed_at), FOREIGN KEY(namespace,tc_id,version) REFERENCES cases(namespace,tc_id,version))",
)


def text(value, name, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > 200000:
        raise ValueError(name + ": 문자열 형식을 확인하세요")
    return value


def timestamp(value):
    parsed = datetime.fromisoformat(text(value, "timestamp").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("시각에는 시간대가 필요합니다")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def issue_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", value):
        raise ValueError("Jira 이슈 키가 필요합니다")
    return value


def snapshot(row):
    required = {"key", "issue_id", "issue_type", "title", "description", "acceptance_criteria", "updated_at", "fetched_at", "source_url"}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError("Story snapshot 필드: " + ", ".join(sorted(required)))
    row = dict(row)
    issue_key(row["key"])
    if not isinstance(row["issue_id"], str) or not re.fullmatch(r"[1-9][0-9]*", row["issue_id"]):
        raise ValueError("실제 Jira issue_id 문자열이 필요합니다")
    for field in ("title", "issue_type", "description", "acceptance_criteria"):
        text(row[field], field, empty=field in {"description", "acceptance_criteria"})
    url = urlsplit(text(row["source_url"], "source_url"))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/browse/" + row["key"]:
        raise ValueError("실제 Jira 이슈의 HTTPS browse 주소가 필요합니다")
    row["updated_at"], row["fetched_at"] = timestamp(row["updated_at"]), timestamp(row["fetched_at"])
    if row["fetched_at"] < row["updated_at"]:
        raise ValueError("조회 시각이 Jira 수정 시각보다 빠릅니다")
    return row


def import_stories(db, namespace, rows, scope, actor):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("한 번에 1000개 이하 Story 배열이 필요합니다")
    if not isinstance(scope, dict) or set(scope) != {"query", "observed_at", "complete"} or type(scope["complete"]) is not bool:
        raise ValueError("수집 범위 query, observed_at, complete가 필요합니다")
    scope = dict(scope, query=text(scope["query"], "scope.query"), observed_at=timestamp(scope["observed_at"]))
    rows = [snapshot(row) for row in rows]
    if len({row["key"] for row in rows}) != len(rows) or len({row["issue_id"] for row in rows}) != len(rows):
        raise ValueError("한 수집 묶음에 같은 Story가 중복되었습니다")
    inserted = 0
    for row in rows:
        for known in current_stories(db, namespace).values():
            if known["issue_id"] == row["issue_id"] and known["key"] != row["key"]:
                raise ValueError("같은 Jira ID에 다른 Story 키가 있습니다")
        content = {k: v for k, v in row.items() if k != "fetched_at"}
        digest = registry.digest(content)
        old = db.execute("SELECT content_hash FROM story_snapshots WHERE namespace=? AND issue_key=? AND updated_at=?", (namespace, row["key"], row["updated_at"])).fetchone()
        if old and old[0] != digest:
            raise ValueError("동일 Story 수정 시각의 내용이 다릅니다. 원문을 재조회하세요")
        identity = db.execute("SELECT raw_json FROM story_snapshots WHERE namespace=? AND issue_key=? LIMIT 1", (namespace, row["key"])).fetchone()
        if identity:
            previous = json.loads(identity[0])
            if previous["issue_id"] != row["issue_id"] or previous["source_url"] != row["source_url"]:
                raise ValueError("같은 namespace의 Story ID/사이트가 바뀌었습니다")
        inserted += db.execute("INSERT OR IGNORE INTO story_snapshots VALUES (?,?,?,?,?,?,?)", (namespace, row["key"], digest, row["updated_at"], registry.canonical(row), actor, row["fetched_at"])).rowcount
        newest = db.execute("SELECT content_hash FROM story_snapshots WHERE namespace=? AND issue_key=? ORDER BY updated_at DESC LIMIT 1", (namespace, row["key"])).fetchone()[0]
        db.execute("INSERT INTO story_heads VALUES (?,?,?) ON CONFLICT(namespace,issue_key) DO UPDATE SET content_hash=excluded.content_hash", (namespace, row["key"], newest))
    keys = [row["key"] for row in rows]
    collection_hash = registry.digest(dict(scope=scope, stories=rows))
    db.execute("INSERT OR IGNORE INTO story_collections VALUES (?,?,?,?,?)", (namespace, collection_hash, registry.canonical(scope), registry.canonical(keys), actor))
    return dict(inserted_snapshots=inserted, collected_keys=keys, scope=scope)


def current_stories(db, namespace):
    return {row["issue_key"]: dict(json.loads(row["raw_json"]), content_hash=row["content_hash"]) for row in db.execute(
        "SELECT s.* FROM story_heads h JOIN story_snapshots s USING(namespace,issue_key,content_hash) WHERE h.namespace=?", (namespace,))}


def issue_links(value):
    if not isinstance(value, dict) or "issues" not in value or not isinstance(value["issues"], list) or any(k in value for k in ("error", "errorCode", "next", "nextPage")):
        raise ValueError("성공한 전체 Get Test Case Links 응답의 issues 배열이 필요합니다")
    result = []
    for row in value["issues"]:
        if not isinstance(row, dict) or type(row.get("issueId")) is not int or row["issueId"] < 1:
            raise ValueError("links.issues[].issueId 형식이 잘못되었습니다")
        kind = row.get("type", "UNKNOWN")
        if kind not in {"COVERAGE", "RELATED", "BLOCKS", "UNKNOWN"}:
            raise ValueError("알 수 없는 이슈 연결 유형입니다")
        result.append(dict(issue_id=str(row["issueId"]), type=kind))
    return result


def plan(db, namespace, args):
    registry.select_case(db, namespace, args["tc_id"], args["version"])
    keys = args["expected_keys"]
    if not isinstance(keys, list) or not keys or len(set(keys)) != len(keys):
        raise ValueError("중복 없는 expected_keys 목록이 필요합니다")
    stories = current_stories(db, namespace)
    links = issue_links(args["links"])
    observed = {row["issue_id"] for row in links if row["type"] == "COVERAGE"}
    unknown = {row["issue_id"] for row in links if row["type"] == "UNKNOWN"}
    missing = []
    for key in keys:
        issue_key(key)
        if key not in stories:
            raise ValueError("Story snapshot을 먼저 수집하세요: " + key)
        if stories[key]["issue_id"] in unknown:
            raise ValueError("기존 연결 유형이 미확인입니다. 재조회 후 판단하세요: " + key)
        if stories[key]["issue_id"] not in observed:
            missing.append(dict(key=key, issue_id=stories[key]["issue_id"]))
    return dict(tc_id=args["tc_id"], expected_keys=keys, missing_links=missing, status="verified_links" if not missing else "missing_links", remote_write_performed=False)


def record_links(db, namespace, args, actor):
    tc = registry.select_case(db, namespace, args["tc_id"], args["version"])
    if args["complete"] is not True or tc["content_hash"] != args["content_hash"]:
        raise ValueError("전체 조회 완료와 해당 TC 버전의 내용 해시가 필요합니다")
    observed = timestamp(args["observed_at"])
    links = issue_links(args["links"])
    payload = dict(links=links, raw=args["links"])
    digest = registry.digest(payload)
    key = (namespace, args["tc_id"], args["version"], observed)
    old = db.execute("SELECT payload_hash FROM story_link_reads WHERE namespace=? AND tc_id=? AND version=? AND observed_at=?", key).fetchone()
    if old and old[0] != digest:
        raise ValueError("동일 연결 조회 시각에 다른 내용이 있습니다")
    db.execute("INSERT OR IGNORE INTO story_link_reads VALUES (?,?,?,?,?,?,?,?)", (*key, args["content_hash"], digest, registry.canonical(payload), actor))
    result = dict(status="recorded_observation", observed_at=observed, independent_server_verification=False)
    if args.get("expected_keys"):
        result["verification"] = plan(db, namespace, args)
    return result


def case_links(db, namespace, tc_id, version=None):
    tc = registry.select_case(db, namespace, tc_id, version)
    row = db.execute("SELECT * FROM story_link_reads WHERE namespace=? AND tc_id=? AND version=? ORDER BY observed_at DESC LIMIT 1", (namespace, tc_id, tc["version"])).fetchone()
    if not row:
        return dict(tc_id=tc_id, version=tc["version"], status="not_observed", stories=[])
    stories = current_stories(db, namespace)
    by_id = {story["issue_id"]: story for story in stories.values()}
    links = json.loads(row["payload_json"])["links"]
    return dict(tc_id=tc_id, version=tc["version"], status="observed", observed_at=row["observed_at"],
                stories=[dict(story=by_id[link["issue_id"]], relation=link["type"]) for link in links if link["issue_id"] in by_id],
                unresolved_links=[link for link in links if link["issue_id"] not in by_id],
                limitation="클라이언트가 제출한 조회 시점의 연결입니다. 현재 Zephyr 상태/요구사항 충족을 보증하지 않습니다.")


def case_index(db, namespace):
    result = {}
    rows = db.execute("SELECT c.tc_id,c.version,c.title,h.version AS current_version,r.observed_at,r.payload_json FROM cases c LEFT JOIN current_cases h ON c.namespace=h.namespace AND c.tc_id=h.tc_id JOIN story_link_reads r ON r.namespace=c.namespace AND r.tc_id=c.tc_id AND r.version=c.version WHERE c.namespace=? AND (h.version IS NULL OR h.version=c.version) AND r.observed_at=(SELECT MAX(x.observed_at) FROM story_link_reads x WHERE x.namespace=c.namespace AND x.tc_id=c.tc_id AND x.version=c.version)", (namespace,))
    for tc in rows:
        seen = set()
        for link in json.loads(tc["payload_json"])["links"]:
            identity = (link["issue_id"], link["type"])
            if identity in seen:
                continue
            seen.add(identity)
            result.setdefault(link["issue_id"], []).append(dict(tc_id=tc["tc_id"], version=tc["version"], title=tc["title"], relation=link["type"],
                observed_at=tc["observed_at"], basis="selected_current" if tc["current_version"] else "unselected_candidate"))
    return result


def related_cases(index, story):
    return [dict(case, requirements_changed=case["observed_at"] < story["updated_at"]) for case in index.get(story["issue_id"], [])]


def execute(db, namespace, op, args, actor="local-user"):
    if op not in OPERATIONS:
        raise ValueError("지원하지 않는 Story 명령입니다")
    required, optional = OPERATIONS[op]
    if set(args) - (required | optional) or (required - {"namespace"}) - set(args):
        raise ValueError("Story 명령의 필수/지원 필드를 확인하세요")
    if op in WRITE_OPS:
        for statement in DDL:
            db.execute(statement)
    elif not db.execute("SELECT 1 FROM sqlite_master WHERE name='story_snapshots'").fetchone():
        return dict(status="unavailable", reason="Story 미수집", items=[])
    if op == "stories.import":
        return import_stories(db, namespace, args["stories"], args["scope"], actor)
    if op == "stories.links.record":
        return record_links(db, namespace, args, actor)
    if op == "stories.links.plan":
        return plan(db, namespace, args)
    if op == "stories.for-case":
        return case_links(db, namespace, args["tc_id"], args.get("version"))
    stories = current_stories(db, namespace)
    index = case_index(db, namespace)
    if op == "stories.get":
        key = issue_key(args["key"])
        if key not in stories:
            raise ValueError("수집되지 않은 Story입니다")
        return dict(story=stories[key], cases=related_cases(index, stories[key]),
            history=[dict(row) for row in db.execute("SELECT content_hash,updated_at FROM story_snapshots WHERE namespace=? AND issue_key=? ORDER BY updated_at DESC", (namespace, key))])
    limit, offset = args.get("limit", 20), args.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0 or type(args.get("unlinked", False)) is not bool:
        raise ValueError("limit/offset/unlinked 형식을 확인하세요")
    query = text(args.get("q", ""), "q", empty=True)
    terms = registry.tokens(query)
    if query and not terms:
        raise ValueError("검색 가능한 단어가 필요합니다")
    items = []
    for story in sorted(stories.values(), key=lambda s: s["key"]):
        matched = terms & registry.tokens(" ".join(story[k] for k in ("key", "title", "description", "acceptance_criteria")))
        if terms and not matched:
            continue
        cases = related_cases(index, story)
        coverage = [case for case in cases if case["relation"] == "COVERAGE"]
        if args.get("unlinked") and coverage:
            continue
        items.append(dict(story=story, cases=cases, finding="observed_links" if coverage else "no_link_in_collected_data", matched_terms=sorted(matched)))
    return dict(status="searched", total=len(items), items=items[offset:offset + limit], offset=offset,
        collections=[dict(scope=json.loads(row[0]), keys=json.loads(row[1])) for row in db.execute("SELECT scope_json,keys_json FROM story_collections WHERE namespace=? ORDER BY rowid DESC LIMIT 10", (namespace,))],
        limitation="수집된 Story와 TC 버전의 관측 연결만 검색합니다. 연결 미확인은 Jira/Zephyr 전체의 TC 부재나 테스트 커버리지 부족 확정이 아닙니다.")
