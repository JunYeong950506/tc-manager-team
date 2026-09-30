"""Local TC inventory and lexical candidate search. No model, network, or duplicate merging."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

import qa_manage as qa


DB_VERSION = 1
EXTRACTOR_VERSION = "explicit-labels-1"
CATEGORIES = {"feature", "intent", "permission", "environment", "state", "client_type"}
LABELS = {"기능": "feature", "대상 기능": "feature", "검증 목적": "intent",
          "권한": "permission", "필요 권한": "permission", "환경": "environment",
          "초기 상태": "state", "클라이언트": "client_type", "Client": "client_type"}
LABEL_PATTERN = re.compile(r"(?m)^[ \t]*(" + "|".join(re.escape(k) for k in LABELS)
                           + r")[ \t]*[:：][ \t]*([^\s\r\n][^\r\n]*)")
CLIENT_TERM = re.compile(r"(웹|앱)(?:은|는|이|가|을|를|의|에|에서|으로|로|과|와|도|만|부터|까지|에서는|에서도|에는|에도)")
DDL = (
    "CREATE TABLE bundles (bundle_hash TEXT PRIMARY KEY, canonical_json TEXT NOT NULL)",
    "CREATE TABLE bundle_inputs (input_hash TEXT PRIMARY KEY, bundle_hash TEXT NOT NULL "
    "REFERENCES bundles, raw_json TEXT NOT NULL)",
    "CREATE TABLE sources (bundle_hash TEXT NOT NULL REFERENCES bundles, source_id TEXT NOT NULL, "
    "source_hash TEXT NOT NULL, raw_json TEXT NOT NULL, PRIMARY KEY(bundle_hash, source_id))",
    "CREATE TABLE cases (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, version TEXT NOT NULL, "
    "schema_version TEXT NOT NULL, content_hash TEXT NOT NULL, raw_json TEXT NOT NULL, "
    "sources_json TEXT NOT NULL, title TEXT NOT NULL, objective TEXT NOT NULL, "
    "precondition TEXT NOT NULL, script_json TEXT NOT NULL, script_hash TEXT NOT NULL, "
    "conditions_hash TEXT NOT NULL, PRIMARY KEY(namespace, tc_id, version))",
    "CREATE TABLE case_imports (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, version TEXT NOT NULL, "
    "bundle_hash TEXT NOT NULL REFERENCES bundles, PRIMARY KEY(namespace, tc_id, version, bundle_hash), "
    "FOREIGN KEY(namespace, tc_id, version) REFERENCES cases(namespace, tc_id, version))",
    "CREATE TABLE current_cases (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, version TEXT NOT NULL, "
    "PRIMARY KEY(namespace, tc_id), "
    "FOREIGN KEY(namespace, tc_id, version) REFERENCES cases(namespace, tc_id, version))",
    "CREATE TABLE current_events (event_id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, "
    "tc_id TEXT NOT NULL, previous_version TEXT, version TEXT NOT NULL, actor TEXT NOT NULL, "
    "reason TEXT NOT NULL, changed_at TEXT NOT NULL, "
    "FOREIGN KEY(namespace, tc_id, version) REFERENCES cases(namespace, tc_id, version))",
    "CREATE TABLE facets (namespace TEXT NOT NULL, tc_id TEXT NOT NULL, version TEXT NOT NULL, "
    "facet_hash TEXT NOT NULL, category TEXT NOT NULL, value TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status='candidate'), field_path TEXT NOT NULL, quote TEXT NOT NULL, "
    "extractor TEXT NOT NULL, extractor_version TEXT NOT NULL, raw_json TEXT NOT NULL, "
    "PRIMARY KEY(namespace, tc_id, version, facet_hash), "
    "FOREIGN KEY(namespace, tc_id, version) REFERENCES cases(namespace, tc_id, version))",
    "CREATE INDEX facet_lookup ON facets(namespace,category,value)",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: 비어 있지 않은 문자열이 필요합니다")
    return value


def connect(path, create=False):
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
    else:
        if not path.is_file():
            raise ValueError(f"DB가 없습니다: {path}")
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and create:
        try:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                    raise ValueError("기존의 다른 DB에는 TC registry를 생성하지 않습니다")
                for statement in DDL:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {DB_VERSION}")
                version = DB_VERSION
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            raise
    if version != DB_VERSION:
        db.close()
        raise ValueError(f"지원하지 않는 DB 버전: {version}; 지원 버전: {DB_VERSION}")
    return db


def condition_fields(tc):
    yield "/objective", tc["objective"]
    if "precondition" in tc:
        yield "/precondition", tc["precondition"]
    else:
        for index, item in enumerate(tc["preconditions"]):
            for name in ("condition", "target", "verification", "missing_handling"):
                yield f"/preconditions/{index}/{name}", item[name]


def projection(tc):
    if "precondition" in tc:
        return tc["precondition"], {"test_script": tc["test_script"]}
    precondition = "\n".join(" | ".join(row[name] for name in
                                        ("condition", "target", "verification", "missing_handling"))
                             for row in tc["preconditions"])
    return precondition, {key: tc[key] for key in
                          ("steps", "criteria", "repeat_count", "mappings", "closure")}


def referenced_sources(tc, sources):
    refs = set()

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "source_refs":
                    refs.update(value)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(tc)
    return [sources[key] for key in sorted(refs)]


def extract_facets(tc):
    result = []
    for path, field in condition_fields(tc):
        for match in LABEL_PATTERN.finditer(field):
            result.append(dict(tc_id=tc["id"], tc_version=tc["version"],
                               category=LABELS[match.group(1)], value=match.group(2).strip(),
                               field_path=path, quote=match.group(0).strip(), status="candidate",
                               extractor="explicit-labels", extractor_version=EXTRACTOR_VERSION))
    return result


def validate_facets(facets, cases):
    if not isinstance(facets, list):
        raise ValueError("facets는 배열이어야 합니다")
    fields = {"tc_id", "tc_version", "category", "value", "field_path", "quote", "status",
              "extractor", "extractor_version"}
    for facet in facets:
        if not isinstance(facet, dict) or set(facet) != fields:
            raise ValueError("facet 필수 항목 또는 알 수 없는 항목을 확인하십시오")
        for field, value in facet.items():
            required_text(value, "facet." + field)
        key = (facet["tc_id"], facet["tc_version"])
        if key not in cases:
            raise ValueError(f"facet가 이번 bundle에 없는 TC를 참조합니다: {key}")
        if facet["category"] not in CATEGORIES or facet["status"] != "candidate":
            raise ValueError("facet는 지원 분류와 candidate 상태만 허용합니다; 자동 확정하지 않습니다")
        fields_by_path = dict(condition_fields(cases[key]))
        if "source_folder" in cases[key]:
            fields_by_path["/source_folder"] = cases[key]["source_folder"]
            fields_by_path["/title"] = cases[key]["title"]
        source_text = fields_by_path.get(facet["field_path"])
        if source_text is None or facet["quote"] not in source_text:
            raise ValueError("facet 근거는 실제 필드 경로와 정확한 인용이어야 합니다")


def checked_raw_text(raw_text, bundle):
    if raw_text is None:
        return canonical(bundle)

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"중복 JSON 키: {key}")
            result[key] = value
        return result

    parsed = json.loads(raw_text.lstrip("\ufeff"), object_pairs_hook=unique_pairs)
    if canonical(parsed) != canonical(bundle):
        raise ValueError("raw_text와 검증한 bundle 내용이 다릅니다")
    return raw_text


def prepare_ingest(bundle, namespace, facets=None, raw_text=None):
    required_text(namespace, "namespace")
    errors = qa.validate(bundle)
    if errors:
        raise ValueError("\n".join(errors))
    raw_text = checked_raw_text(raw_text, bundle)
    cases = {(tc["id"], tc["version"]): tc for tc in bundle["test_cases"]}
    derived = [facet for tc in cases.values() for facet in extract_facets(tc)]
    supplied = [] if facets is None else facets
    validate_facets(supplied, cases)
    validate_facets(derived, cases)
    all_facets = derived + supplied
    source_lookup = {source["id"]: source for source in bundle["sources"]}
    records = []
    for tc in cases.values():
        sources = referenced_sources(tc, source_lookup)
        precondition, script = projection(tc)
        content_hash = digest(dict(schema_version=bundle["schema_version"], test_case=tc, sources=sources))
        records.append((namespace, tc["id"], tc["version"], bundle["schema_version"], content_hash,
                        canonical(tc), canonical(sources), tc["title"], tc["objective"], precondition,
                        canonical(script), digest(script), digest(precondition)))
    bundle_hash = digest(bundle)
    input_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    return dict(bundle=bundle, namespace=namespace, records=records, facets=all_facets,
                bundle_hash=bundle_hash, input_hash=input_hash, raw_text=raw_text)


def ingest_transaction(db, prepared):
    """Apply validated input to the caller's transaction without committing it."""
    if not db.in_transaction:
        raise ValueError("ingest_transaction에는 활성 transaction이 필요합니다")
    bundle, namespace, records, all_facets = (prepared[key] for key in ("bundle", "namespace", "records", "facets"))
    bundle_hash, input_hash, raw_text = (prepared[key] for key in ("bundle_hash", "input_hash", "raw_text"))
    inserted = 0
    for record in records:
        existing = db.execute("SELECT content_hash FROM cases WHERE namespace=? AND tc_id=? AND version=?",
                              record[:3]).fetchone()
        if existing and existing[0] != record[4]:
            raise ValueError(f"동일 TC ID/버전의 내용 또는 출처가 달라졌습니다: {record[:3]}; 새 버전이 필요합니다")
        inserted += existing is None
    db.execute("INSERT OR IGNORE INTO bundles VALUES (?,?)", (bundle_hash, canonical(bundle)))
    db.execute("INSERT OR IGNORE INTO bundle_inputs VALUES (?,?,?)", (input_hash, bundle_hash, raw_text))
    for source in bundle["sources"]:
        db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?)",
                   (bundle_hash, source["id"], digest(source), canonical(source)))
    for record in records:
        db.execute("INSERT OR IGNORE INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", record)
        db.execute("INSERT OR IGNORE INTO case_imports VALUES (?,?,?,?)", (*record[:3], bundle_hash))
    before_facets = db.total_changes
    for facet in all_facets:
        db.execute("INSERT OR IGNORE INTO facets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (namespace, facet["tc_id"], facet["tc_version"], digest(facet), facet["category"],
                    facet["value"], facet["status"], facet["field_path"], facet["quote"],
                    facet["extractor"], facet["extractor_version"], canonical(facet)))
    facet_count = db.total_changes - before_facets
    return dict(bundle_hash=bundle_hash, input_hash=input_hash, inserted_versions=inserted,
                unchanged_versions=len(records) - inserted, inserted_facets=facet_count,
                current_selection_changed=False, classification_status="candidate",
                current_selection="set-current로 명시 선택; 가져오기 순서나 버전 문자열로 결정하지 않음")


def ingest(db_path, bundle, namespace, facets=None, raw_text=None):
    prepared = prepare_ingest(bundle, namespace, facets, raw_text)
    with closing(connect(db_path, create=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        return ingest_transaction(db, prepared)


def set_current(db_path, namespace, tc_id, version, *, expected_version=None, actor, reason):
    for name, value in (("namespace", namespace), ("tc_id", tc_id), ("version", version),
                        ("actor", actor), ("reason", reason)):
        required_text(value, name)
    if expected_version is not None:
        required_text(expected_version, "expected_version")
    if not Path(db_path).is_file():
        raise ValueError("DB가 없습니다; ingest로 TC를 먼저 가져오십시오")
    with closing(connect(db_path, create=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM cases WHERE namespace=? AND tc_id=? AND version=?",
                          (namespace, tc_id, version)).fetchone():
            raise ValueError("선택할 TC ID/버전이 없습니다")
        current = db.execute("SELECT version FROM current_cases WHERE namespace=? AND tc_id=?",
                             (namespace, tc_id)).fetchone()
        actual = current[0] if current else None
        if actual != expected_version:
            raise ValueError(f"current 버전이 예상값과 다릅니다: expected={expected_version!r}, actual={actual!r}")
        if actual == version:
            return dict(changed=False, namespace=namespace, tc_id=tc_id, current_version=version)
        db.execute("INSERT INTO current_cases VALUES (?,?,?) ON CONFLICT(namespace,tc_id) "
                   "DO UPDATE SET version=excluded.version", (namespace, tc_id, version))
        db.execute("INSERT INTO current_events(namespace,tc_id,previous_version,version,actor,reason,changed_at) "
                   "VALUES (?,?,?,?,?,?,?)", (namespace, tc_id, actual, version, actor, reason,
                                             datetime.now(timezone.utc).isoformat()))
    return dict(changed=True, namespace=namespace, tc_id=tc_id, previous_version=actual, current_version=version)


def select_case(db, namespace, tc_id, version):
    if version is None:
        row = db.execute("SELECT version FROM current_cases WHERE namespace=? AND tc_id=?",
                         (namespace, tc_id)).fetchone()
        if row is None:
            raise ValueError("current 버전 미선택; version을 지정하거나 set-current를 실행하십시오")
        version = row[0]
    row = db.execute("SELECT * FROM cases WHERE namespace=? AND tc_id=? AND version=?",
                     (namespace, tc_id, version)).fetchone()
    if row is None:
        raise ValueError("TC ID/버전이 없습니다")
    return row


def facets_for(db, namespace, tc_id, version):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT raw_json FROM facets WHERE namespace=? AND tc_id=? AND version=? ORDER BY facet_hash",
        (namespace, tc_id, version))]


def show(db_path, namespace, tc_id, version=None):
    with closing(connect(db_path)) as db:
        row = select_case(db, namespace, tc_id, version)
        key = (namespace, tc_id, row["version"])
        current = db.execute("SELECT version FROM current_cases WHERE namespace=? AND tc_id=?", key[:2]).fetchone()
        return dict(namespace=namespace, current_version=current[0] if current else None,
                    schema_version=row["schema_version"], content_hash=row["content_hash"],
                    test_case=json.loads(row["raw_json"]), sources=json.loads(row["sources_json"]),
                    facets=facets_for(db, *key),
                    history=[dict(v) for v in db.execute(
                        "SELECT version,content_hash FROM cases WHERE namespace=? AND tc_id=? ORDER BY version", key[:2])],
                    current_events=[dict(v) for v in db.execute(
                        "SELECT previous_version,version,actor,reason,changed_at FROM current_events "
                        "WHERE namespace=? AND tc_id=? ORDER BY event_id", key[:2])],
                    bundle_hashes=[v[0] for v in db.execute(
                        "SELECT bundle_hash FROM case_imports WHERE namespace=? AND tc_id=? AND version=? "
                        "ORDER BY bundle_hash", key)])


def tokens(text):
    result = set()
    for word in re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()):
        result.add(word)
        client_term = CLIENT_TERM.fullmatch(word)
        if client_term:
            result.add(client_term.group(1))
        if re.search("[가-힣]", word):
            result.update(word[index:index + 2] for index in range(len(word) - 1))
    return result


def differences(reference, candidate):
    before = json.loads(reference["script_json"])
    after = json.loads(candidate["script_json"])
    result = dict(objective_changed=reference["objective"] != candidate["objective"],
                  precondition_changed=reference["precondition"] != candidate["precondition"],
                  script_changed=reference["script_hash"] != candidate["script_hash"],
                  schema_changed=reference["schema_version"] != candidate["schema_version"],
                  changed_script_sections=sorted(key for key in before.keys() | after.keys()
                                                 if before.get(key) != after.get(key)),
                  interpretation="문자열/구조 비교이며 의미상 동등 여부나 중복 판정이 아님")
    if "test_script" in before and "test_script" in after:
        old = {step["id"]: step for step in before["test_script"]}
        new = {step["id"]: step for step in after["test_script"]}
        result["changed_steps"] = [dict(id=step_id, fields=sorted(
            field for field in {"step", "test_data", "expected_result", "source_refs"}
            if old.get(step_id, {}).get(field) != new.get(step_id, {}).get(field)))
            for step_id in sorted(old.keys() | new.keys()) if old.get(step_id) != new.get(step_id)]
        result["step_order_changed"] = list(old) != list(new)
    return result


def search(db_path, namespace, query, *, limit=10, all_versions=False, compare_to=None,
           category=None, facet_value=None):
    required_text(namespace, "namespace")
    required_text(query, "query")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit는 1~100 정수여야 합니다")
    if category is not None and category not in CATEGORIES:
        raise ValueError("지원하지 않는 facet 분류입니다")
    if facet_value is not None:
        required_text(facet_value, "facet_value")
        if category is None:
            raise ValueError("facet_value 필터에는 category가 필요합니다")
    query_terms = tokens(query)
    if not query_terms:
        raise ValueError("검색 가능한 단어가 필요합니다")
    with closing(connect(db_path)) as db:
        reference = select_case(db, namespace, *compare_to) if compare_to else None
        sql = "SELECT c.* FROM cases c "
        if not all_versions:
            sql += "JOIN current_cases h ON c.namespace=h.namespace AND c.tc_id=h.tc_id AND c.version=h.version "
        sql += "WHERE c.namespace=?"
        params = [namespace]
        if category is not None:
            sql += " AND EXISTS (SELECT 1 FROM facets f WHERE f.namespace=c.namespace AND f.tc_id=c.tc_id "
            sql += "AND f.version=c.version AND f.category=?"
            params.append(category)
            if facet_value is not None:
                sql += " AND f.value=?"
                params.append(facet_value)
            sql += ")"
        candidates = []
        for row in db.execute(sql, params):
            facets = facets_for(db, namespace, row["tc_id"], row["version"])
            matches = {field: sorted(query_terms & tokens(row[field])) for field in ("objective", "precondition")}
            matches["facets"] = [dict(category=facet["category"], value=facet["value"], status=facet["status"],
                                      matched_terms=sorted(query_terms & tokens(facet["value"])))
                                  for facet in facets if query_terms & tokens(facet["value"])]
            matched = set(matches["objective"]) | set(matches["precondition"])
            matched.update(term for facet in matches["facets"] for term in facet["matched_terms"])
            if not matched:
                continue
            item = dict(tc_id=row["tc_id"], version=row["version"], title=row["title"],
                        score=round(len(matched) / len(query_terms), 6), matched_terms=sorted(matched),
                        objective=row["objective"], precondition=row["precondition"],
                        content_hash=row["content_hash"], script_hash=row["script_hash"],
                        matched_fields=matches, facets=facets)
            if reference is not None:
                item["differences"] = differences(reference, row)
            candidates.append(item)
        candidates.sort(key=lambda item: (-item["score"], item["tc_id"], item["version"]))
    return dict(namespace=namespace, query=query, scope="all_versions" if all_versions else "explicit_current",
                method="lexical-coverage-1: Objective/Precondition/후보 분류의 단어·한글 2글자 조각·웹/앱 조사 분리 후 검색어 포함 비율",
                facet_filter=dict(category=category, value=facet_value, status="candidate") if category else None,
                duplicate_decision="not_performed", total_candidates=len(candidates), candidates=candidates[:limit],
                limitation="동의어·의미 검색과 누락 없는 검색을 보장하지 않음; 후보의 조건·Script를 추가 검토해야 함")


def stats(db_path, namespace=None):
    with closing(connect(db_path)) as db:
        where, params = (" WHERE namespace=?", (namespace,)) if namespace else ("", ())
        versions = db.execute("SELECT COUNT(*) FROM cases" + where, params).fetchone()[0]
        identities = db.execute("SELECT COUNT(*) FROM (SELECT namespace,tc_id FROM cases" + where
                                + " GROUP BY namespace,tc_id)", params).fetchone()[0]
        selected = db.execute("SELECT COUNT(*) FROM current_cases" + where, params).fetchone()[0]
        facets = db.execute("SELECT COUNT(*) FROM facets" + where, params).fetchone()[0]
        coverage_sql = "SELECT COUNT(*) FROM cases c WHERE NOT EXISTS (SELECT 1 FROM facets f "
        coverage_sql += "WHERE f.namespace=c.namespace AND f.tc_id=c.tc_id AND f.version=c.version)"
        if namespace:
            coverage_sql += " AND c.namespace=?"
        unclassified = db.execute(coverage_sql, params).fetchone()[0]
        return dict(db_schema_version=DB_VERSION, namespace=namespace, test_case_ids=identities,
                    versions=versions, selected_current=selected, unselected_ids=identities - selected,
                    versions_without_facets=unclassified,
                    candidate_facets=facets, approved_facets=0,
                    stored_bundles_global=db.execute("SELECT COUNT(*) FROM bundles").fetchone()[0],
                    stored_inputs_global=db.execute("SELECT COUNT(*) FROM bundle_inputs").fetchone()[0])


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("ingest")
    add.add_argument("bundle", type=Path)
    add.add_argument("--namespace", required=True)
    add.add_argument("--facets", type=Path)
    find = commands.add_parser("search")
    find.add_argument("query")
    find.add_argument("--namespace", required=True)
    find.add_argument("--all-versions", action="store_true")
    find.add_argument("--limit", type=int, default=10)
    find.add_argument("--compare-id")
    find.add_argument("--compare-version")
    find.add_argument("--category", choices=sorted(CATEGORIES))
    find.add_argument("--facet-value")
    read = commands.add_parser("show")
    read.add_argument("tc_id")
    read.add_argument("--namespace", required=True)
    read.add_argument("--version")
    choose = commands.add_parser("set-current")
    choose.add_argument("tc_id")
    choose.add_argument("--namespace", required=True)
    choose.add_argument("--version", required=True)
    choose.add_argument("--expected-version")
    choose.add_argument("--actor", required=True)
    choose.add_argument("--reason", required=True)
    count = commands.add_parser("stats")
    count.add_argument("--namespace")
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            result = ingest(args.db, qa.load(args.bundle), args.namespace,
                            qa.load(args.facets) if args.facets else None,
                            args.bundle.read_bytes().decode("utf-8"))
        elif args.command == "set-current":
            result = set_current(args.db, args.namespace, args.tc_id, args.version,
                                 expected_version=args.expected_version, actor=args.actor, reason=args.reason)
        elif args.command == "show":
            result = show(args.db, args.namespace, args.tc_id, args.version)
        elif args.command == "search":
            if args.compare_version and not args.compare_id:
                parser.error("--compare-version에는 --compare-id가 필요합니다")
            result = search(args.db, args.namespace, args.query, limit=args.limit,
                            all_versions=args.all_versions,
                            compare_to=(args.compare_id, args.compare_version) if args.compare_id else None,
                            category=args.category, facet_value=args.facet_value)
        else:
            result = stats(args.db, args.namespace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
