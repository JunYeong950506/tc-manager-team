"""Validate and export management drafts. No model, network, or product execution."""

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
import sys


class Many:
    def __init__(self, item, minimum=0):
        self.item, self.minimum = item, minimum


class Nullable:
    def __init__(self, item):
        self.item = item


ANY = object()
TEXT = object()  # A string that may be empty, for an unused Test Data cell.
REFS = Many(str, 1)
SOURCE = dict(id=str, kind={"test_case", "change", "requirement", "bug", "document", "user_request"},
              locator=str, revision=str, text=str)
TC_REF = dict(id=str, version=str)
TC = dict(
    id=str, version=str, title=str, status={"draft", "needs_input"}, source_refs=REFS,
    objective=str, work_type={"AI 판정", "제품 조작·실행", "둘의 조합"},
    product_version=str, configuration=str,
    preconditions=Many(dict(condition=str, target=str, verification=str, missing_handling=str), 1),
    mappings=Many(dict(alias=str, target=str, initial_state=str, expected_state=str, source_refs=REFS)),
    repeat_count=int,
    steps=Many(dict(id=str, method=str, action=str, observation=str, expected=str,
                    evidence=str, source_refs=REFS), 1),
    criteria=Many(dict(id=str, text=str, method=str, insufficient_evidence=str, source_refs=REFS), 1),
    closure=dict(end_state=str, stop_conditions=str, open_questions=Many(str)),
)
TC_V2 = dict(
    id=str, version=str, title=str, status={"draft", "needs_input"}, source_refs=REFS,
    objective=str, precondition=str,
    test_script=Many(dict(id=str, step=str, test_data=TEXT, expected_result=str,
                         source_refs=REFS), 1),
    open_questions=Many(str),
)
PLAN = dict(
    id=str, version=str, status={"draft", "needs_input"}, target_release=str, baseline=str,
    scope=str, excluded_scope=str, source_refs=REFS,
    search_log=Many(dict(area=str, query=str,
                        status={"complete", "partial", "unavailable", "failed", "not_searched"},
                        source_refs=Many(str), limitation=str), 1),
    items=Many(dict(id=str, decision={"reuse", "revise", "new_tc", "needs_evidence", "exclude"},
                    tc_ref=Nullable(TC_REF), reason=str, basis={"fact", "inference"},
                    source_refs=REFS, priority=str, configuration=str, prerequisites=Many(str))),
    questions=Many(str),
    manual_overrides=Many(dict(target=str, value=ANY, reason=str)),
)
BUNDLE = dict(
    schema_version={"1.0"},
    run=dict(id=str, mode={"convert", "plan", "fill", "create"}, created_at=str, producer=str,
             model=str, plugin_version=str,
             review_mode={"subagent", "self_review", "not_run", "fixture"}),
    sources=Many(SOURCE, 1), test_cases=Many(TC), plan=Nullable(PLAN),
    review=dict(status={"pending", "changes_requested", "reviewed"}, rounds=int,
                findings=Many(dict(id=str, severity={"blocker", "warning"}, target=str,
                                   detail=str, source_refs=Many(str)))),
    changes=Many(dict(target=str, reason=str)),
    feedback=Many(dict(id=str, target=str,
                       kind={"tc_revision", "plan_revision", "plugin_improvement"}, comment=str)),
)
BUNDLE_V2 = dict(BUNDLE, schema_version={"2.0"}, test_cases=Many(TC_V2))
# Raw intake preserves empty source fields; it is not an executable authored TC.
TC_RAW = dict(TC_V2, objective=TEXT, precondition=TEXT,
              test_script=Many(dict(id=str, step=TEXT, test_data=TEXT,
                                    expected_result=TEXT, source_refs=REFS)),
              source_folder=str, source_labels=Many(str), source_script=ANY)
BUNDLE_RAW = dict(BUNDLE, schema_version={"zephyr-raw-1"}, test_cases=Many(TC_RAW))


def shape(value, spec, path, errors):
    if spec is ANY:
        return
    if spec is TEXT:
        if not isinstance(value, str):
            errors.append(f"{path}: 문자열이 필요합니다 (빈 문자열 허용)")
        return
    if isinstance(spec, Nullable):
        if value is not None:
            shape(value, spec.item, path, errors)
    elif isinstance(spec, Many):
        if not isinstance(value, list):
            errors.append(f"{path}: 배열이어야 합니다")
            return
        if len(value) < spec.minimum:
            errors.append(f"{path}: 최소 {spec.minimum}개가 필요합니다")
        for i, item in enumerate(value):
            shape(item, spec.item, f"{path}/{i}", errors)
    elif isinstance(spec, dict):
        if not isinstance(value, dict):
            errors.append(f"{path}: 객체여야 합니다")
            return
        for key in spec.keys() - value.keys():
            errors.append(f"{path}/{key}: 필수 항목 누락")
        for key in value.keys() - spec.keys():
            errors.append(f"{path}/{key}: 계약에 없는 항목")
        for key in spec.keys() & value.keys():
            shape(value[key], spec[key], f"{path}/{key}", errors)
    elif isinstance(spec, set):
        if not isinstance(value, str) or value not in spec:
            errors.append(f"{path}: 허용값 {sorted(spec)}")
    elif spec is str and (not isinstance(value, str) or not value.strip()):
        errors.append(f"{path}: 비어 있지 않은 문자열이 필요합니다")
    elif spec is int and type(value) is not int:
        errors.append(f"{path}: 정수가 필요합니다")


def unique(values, label, errors):
    if len(values) != len(set(values)):
        errors.append(f"{label}: 중복 ID/참조가 있습니다")


def pointer(data, target):
    if not target.startswith("/"):
        raise ValueError("JSON Pointer는 /로 시작해야 합니다")
    current = data
    for part in target[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdigit():
                raise ValueError("잘못된 배열 위치")
            current = current[int(key)]
        elif isinstance(current, dict):
            current = current[key]
        else:
            raise ValueError("잘못된 참조 경로")
    return current


def validate(data, previous=None):
    errors = []
    spec = {"2.0": BUNDLE_V2, "zephyr-raw-1": BUNDLE_RAW}.get(data.get("schema_version"), BUNDLE) if isinstance(data, dict) else BUNDLE
    shape(data, spec, "", errors)
    if errors:
        return sorted(errors)
    source_ids = {s["id"] for s in data["sources"]}
    unique([s["id"] for s in data["sources"]], "sources", errors)

    def references(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "value" and path.startswith("/plan/manual_overrides/"):
                    continue
                if key == "source_refs":
                    unique(value, path + "/source_refs", errors)
                    for ref in value:
                        if ref not in source_ids:
                            errors.append(f"{path}/source_refs: 존재하지 않는 출처 {ref}")
                else:
                    references(value, path + "/" + key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                references(value, f"{path}/{i}")

    references(data)
    try:
        when = datetime.fromisoformat(data["run"]["created_at"].replace("Z", "+00:00"))
        if when.utcoffset() is None:
            raise ValueError()
    except ValueError:
        errors.append("run/created_at: 시간대 포함 ISO 8601 시각이 필요합니다")
    tc_keys = [(t["id"], t["version"]) for t in data["test_cases"]]
    unique(tc_keys, "test_cases", errors)
    for tc in data["test_cases"]:
        modern = data["schema_version"] in {"2.0", "zephyr-raw-1"}
        for key in (("test_script",) if modern else ("steps", "criteria")):
            unique([i["id"] for i in tc[key]], f"{tc['id']}/{key}", errors)
        if not modern and tc["repeat_count"] < 1:
            errors.append(f"{tc['id']}: repeat_count는 1 이상이어야 합니다")
        questions = tc["open_questions"] if modern else tc["closure"]["open_questions"]
        if tc["status"] == "needs_input" and not questions:
            errors.append(f"{tc['id']}: 미정 상태의 구체적인 질문이 필요합니다")
    review = data["review"]
    unique([f["id"] for f in review["findings"]], "review/findings", errors)
    unique([f["id"] for f in data["feedback"]], "feedback", errors)
    if not 0 <= review["rounds"] <= 2:
        errors.append("review/rounds: 이번 작업의 검토 횟수는 0~2입니다")
    if data["run"]["review_mode"] in {"not_run", "fixture"}:
        if review["status"] != "pending" or review["rounds"] != 0:
            errors.append("review: 미수행/예제는 검토 완료로 표시할 수 없습니다")
    if review["status"] == "reviewed":
        if review["rounds"] == 0 or any(f["severity"] == "blocker" for f in review["findings"]):
            errors.append("review: 미검토 또는 blocker가 남은 문서를 검토 완료로 표시했습니다")
    plan = data["plan"]
    if data["run"]["mode"] in {"plan", "fill"} and plan is None:
        errors.append("plan/fill 모드에는 계획이 필요합니다")
    if data["run"]["mode"] == "convert" and not data["test_cases"]:
        errors.append("convert 모드에는 변환한 TC가 필요합니다")
    if data["run"]["mode"] == "create" and not data["test_cases"]:
        errors.append("create 모드에는 작성한 TC가 필요합니다")
    if plan:
        unique([i["id"] for i in plan["items"]], "plan/items", errors)
        unique([o["target"] for o in plan["manual_overrides"]], "plan/manual_overrides", errors)
        if plan["status"] == "needs_input" and not plan["questions"]:
            errors.append("plan: 미정 상태의 구체적인 질문이 필요합니다")
        for item in plan["items"]:
            ref = item["tc_ref"]
            if item["decision"] in {"reuse", "revise"} and ref is None:
                errors.append(f"{item['id']}: 기존 TC 참조가 필요합니다")
            if ref and (ref["id"], ref["version"]) not in tc_keys:
                errors.append(f"{item['id']}: TC ID/버전이 bundle에 없습니다")
        for override in plan["manual_overrides"]:
            try:
                if not override["target"].startswith("/plan/") or pointer(data, override["target"]) != override["value"]:
                    errors.append(f"수동 조정 값 불일치: {override['target']}")
            except (KeyError, IndexError, ValueError):
                errors.append(f"수동 조정 대상이 없습니다: {override['target']}")
    if previous is not None:
        prior_errors = validate(previous)
        if prior_errors:
            errors.append("이전 번들이 유효하지 않습니다: " + prior_errors[0])
        else:
            errors.extend(check_revision(data, previous))
    return errors


def check_revision(data, previous):
    errors = []
    if data["run"]["id"] == previous["run"]["id"]:
        errors.append("개정 결과에는 새 run ID가 필요합니다")
    change_targets = {c["target"] for c in data["changes"]}
    current = {(t["id"], t["version"]): t for t in data["test_cases"]}
    for old in previous["test_cases"]:
        key = (old["id"], old["version"])
        if data["schema_version"] in {"2.0", "zephyr-raw-1"} and any(
                t["id"] == old["id"] and t["title"] != old["title"] for t in data["test_cases"]):
            errors.append(f"{old['id']}: 기존 TC 제목을 유지해야 합니다")
        if key in current and current[key] != old:
            errors.append(f"{old['id']}: 같은 TC 버전의 내용이 변경됐습니다")
        if key not in current and f"tc:{old['id']}" not in change_targets:
            errors.append(f"{old['id']}: TC 개정/제외 이유가 changes에 필요합니다")
    old_plan, new_plan = previous["plan"], data["plan"]
    if old_plan and old_plan != new_plan:
        if not new_plan:
            errors.append("이전 계획을 제거할 수 없습니다")
        else:
            if new_plan["id"] != old_plan["id"] or new_plan["version"] == old_plan["version"]:
                errors.append("계획 개정 시 ID를 유지하고 버전을 변경해야 합니다")
            if f"plan:{old_plan['id']}" not in change_targets:
                errors.append("계획 변경 이유가 changes에 필요합니다")
    if old_plan and new_plan:
        for override in old_plan["manual_overrides"]:
            if override not in new_plan["manual_overrides"]:
                errors.append(f"이전 수동 조정이 변경/누락됐습니다: {override['target']}")
            tokens = override["target"].split("/")
            if len(tokens) >= 5 and tokens[1:3] == ["plan", "items"]:
                try:
                    if pointer(previous, "/".join(tokens[:4]) + "/id") != pointer(data, "/".join(tokens[:4]) + "/id"):
                        errors.append("수동 조정된 계획 항목의 순서/ID가 바뀌었습니다")
                except (KeyError, IndexError, ValueError):
                    errors.append("수동 조정된 계획 항목이 제거됐습니다")
    for entry in previous["feedback"]:
        if entry not in data["feedback"]:
            errors.append(f"이전 피드백이 변경/누락됐습니다: {entry['id']}")
    return errors


LABELS = {
    "sources": "원문과 출처", "test_cases": "Test Cases", "plan": "Test Plan",
    "review": "문서 검토", "changes": "변경 내역", "feedback": "피드백",
    "objective": "Objective", "preconditions": "Precondition", "mappings": "대상 매핑",
    "steps": "Test Script (구형 양식)", "test_script": "Test Script", "precondition": "Precondition",
    "criteria": "Expected Result / 판정 기준", "closure": "종료 및 원상 복구",
    "search_log": "검색 범위와 결과", "items": "계획 항목", "questions": "미확정 질문",
    "manual_overrides": "사용자 조정", "repeat_count": "반복 횟수",
}


def label(key):
    return LABELS.get(key, key)


TC_TABLES = [
    ("Precondition", "preconditions", [("condition", "필요한 조건·자료"), ("target", "실제 대상 또는 기준"), ("verification", "준비 확인 방법"), ("missing_handling", "부족할 때의 처리")]),
    ("대상 매핑", "mappings", [("alias", "별칭"), ("target", "실제 대상"), ("initial_state", "초기 상태"), ("expected_state", "기대 상태"), ("source_refs", "근거")]),
    ("Test Script (구형 양식)", "steps", [("id", "Step"), ("method", "수행 방법"), ("action", "수행할 행동"), ("observation", "관찰할 대상"), ("expected", "기대 결과"), ("evidence", "필요한 증거"), ("source_refs", "근거")]),
    ("Expected Result / 판정 기준", "criteria", [("id", "기준 ID"), ("text", "기준"), ("method", "판정 방법"), ("insufficient_evidence", "근거가 부족할 때"), ("source_refs", "근거")]),
]
SCRIPT_COLUMNS = [("step", "Step"), ("test_data", "Test Data"),
                  ("expected_result", "Expected Result")]


def display(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "없음" if value is None else str(value)


def render_html(data):
    def node(value):
        if isinstance(value, dict):
            return "<dl>" + "".join(f"<dt>{html.escape(label(k))}</dt><dd>{node(v)}</dd>" for k, v in value.items()) + "</dl>"
        if isinstance(value, list):
            return "<ol>" + "".join(f"<li>{node(v)}</li>" for v in value) + "</ol>" if value else "<span>없음</span>"
        return "<span>" + html.escape("없음" if value is None else str(value)) + "</span>"

    def table(items, columns):
        if not items:
            return "<p>해당 없음</p>"
        head = "".join(f"<th>{html.escape(title)}</th>" for _, title in columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(display(item[key]))}</td>" for key, _ in columns) + "</tr>" for item in items)
        return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    sections = ""
    for tc in data["test_cases"]:
        if data["schema_version"] in {"2.0", "zephyr-raw-1"}:
            sections += f"<section><h2>{html.escape(tc['title'])}</h2><p>{html.escape(tc['id'])} · {html.escape(tc['version'])} · {html.escape(tc['status'])}</p>"
            sections += f"<h3>Objective</h3><p class='prose'>{html.escape(tc['objective'])}</p>"
            sections += f"<h3>Precondition</h3><p class='prose'>{html.escape(tc['precondition'])}</p>"
            sections += "<h3>Test Script</h3>" + table(tc["test_script"], SCRIPT_COLUMNS)
            meta = {"미확정 질문": tc["open_questions"], "TC 출처": tc["source_refs"],
                    "단계별 출처": [{"id": s["id"], "source_refs": s["source_refs"]} for s in tc["test_script"]]}
            sections += "<details><summary>관리 정보 · 출처와 미확정 질문</summary>" + node(meta) + "</details></section>"
            continue
        meta = {"TC": tc["id"], "버전": tc["version"], "상태": tc["status"], "업무 유형": tc["work_type"],
                "대상 제품·버전": tc["product_version"], "실행 구성": tc["configuration"], "반복 횟수": tc["repeat_count"]}
        sections += f"<section><h2>{html.escape(tc['title'])}</h2>{node(meta)}<h3>Objective</h3><p class='prose'>{html.escape(tc['objective'])}</p>"
        for title, key, columns in TC_TABLES:
            sections += f"<h3>{title}</h3>" + table(tc[key], columns)
        sections += "<h3>종료 및 원상 복구</h3>" + node({"종료 상태": tc["closure"]["end_state"], "중단 조건": tc["closure"]["stop_conditions"], "확정할 사항": tc["closure"]["open_questions"]})
        sections += "<h3>실행 결과</h3><p>이번 작업에서 실행하지 않음. 실행 결과는 별도 실행기가 작성합니다.</p></section>"
    for key in ("review", "plan", "changes", "feedback"):
        if data[key]:
            sections += f"<section><h2>{label(key)}</h2>{node(data[key])}</section>"
    for key in ("run", "sources"):
        sections += f"<section><details><summary>{html.escape(label(key))} · 펼쳐서 확인</summary>{node(data[key])}</details></section>"
    return '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>SQMA Test 관리 초안</title><style>
body{font:16px/1.65 system-ui,sans-serif;color:#19324a;background:#f4f7fa;margin:0;padding:32px}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dce4ec;padding:10px;vertical-align:top;min-width:80px;white-space:pre-wrap}th{background:#eef4f9;text-align:left}.prose{white-space:pre-wrap}
main{max-width:1000px;margin:auto}h1{margin:0}h2{font-size:21px}section{background:white;border:1px solid #dce4ec;border-radius:12px;padding:22px;margin:20px 0}
dl{display:grid;grid-template-columns:minmax(140px,22%) minmax(0,1fr);gap:8px 16px}dt{font-weight:600}dd{margin:0;min-width:0;white-space:pre-wrap;overflow-wrap:anywhere}dd dl{display:block}dd dt{color:#546779}ol{padding-left:24px;margin:0}li+li{border-top:1px solid #eee;margin-top:14px;padding-top:10px}.notice{background:#fff2cd;padding:16px;border-radius:8px}
@media(max-width:650px){body{padding:12px}dl{display:block}dd{margin-bottom:12px}}@media print{body{padding:0;background:white}section{break-inside:auto}}
</style><main><h1>SQMA · Test 관리 초안</h1><p class="notice">실제 테스트 실행·Jira 등록 결과가 아닙니다. 구조 검사는 원문 의미와 검색 완전성을 보증하지 않습니다. 생성 주체와 검토 상태를 확인하세요.</p>''' + sections + "</main></html>"


def render_markdown(data):
    def safe(value):
        text = html.escape(str(value))
        for ch in ("\\", "`", "*", "_", "[", "]", "#", "|", "!"):
            text = text.replace(ch, "\\" + ch)
        return text.replace("\r", "").replace("\n", "<br>")

    def rows(value, indent=""):
        if isinstance(value, dict):
            result = []
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    result.append(f"{indent}- **{safe(label(k))}**")
                    result.extend(rows(v, indent + "  "))
                else:
                    result.append(f"{indent}- **{safe(label(k))}**: {safe(v)}")
            return result
        if isinstance(value, list):
            result = []
            for i, item in enumerate(value, 1):
                result.append(f"{indent}- 항목 {i}")
                result.extend(rows(item, indent + "  "))
            return result or [indent + "- 없음"]
        return [indent + "- " + safe("없음" if value is None else value)]

    lines = ["# SQMA Test 관리 초안", "", "실제 테스트 실행·Jira 등록 결과가 아닙니다. 구조 검사는 원문 의미와 검색 완전성을 보증하지 않습니다.", ""]
    for tc in data["test_cases"]:
        if data["schema_version"] in {"2.0", "zephyr-raw-1"}:
            lines += ["## " + safe(tc["title"]), "", f"{safe(tc['id'])} · {safe(tc['version'])} · {safe(tc['status'])}",
                      "", "### Objective", "", safe(tc["objective"]), "",
                      "### Precondition", "", safe(tc["precondition"]), "", "### Test Script", "",
                      "| Step | Test Data | Expected Result |", "| --- | --- | --- |"]
            for step in tc["test_script"]:
                lines.append("| " + " | ".join(safe(step[k]) for k, _ in SCRIPT_COLUMNS) + " |")
            meta = {"미확정 질문": tc["open_questions"], "TC 출처": tc["source_refs"],
                    "단계별 출처": [{"id": s["id"], "source_refs": s["source_refs"]} for s in tc["test_script"]]}
            lines += ["", "<details><summary>관리 정보 · 출처와 미확정 질문</summary>", ""] + rows(meta) + ["", "</details>", ""]
            continue
        lines += ["## " + safe(tc["title"]), "", f"- TC: {safe(tc['id'])} / {safe(tc['version'])}",
                  f"- 상태: {safe(tc['status'])}", f"- 업무 유형: {safe(tc['work_type'])}",
                  f"- 대상 제품·버전: {safe(tc['product_version'])}", f"- 실행 구성: {safe(tc['configuration'])}",
                  f"- 반복 횟수: {tc['repeat_count']}", "", "### Objective", "", safe(tc["objective"]), ""]
        for title, field, columns in TC_TABLES:
            lines += ["### " + title, ""]
            if tc[field]:
                lines += ["| " + " | ".join(safe(t) for _, t in columns) + " |",
                          "| " + " | ".join("---" for _ in columns) + " |"]
                for item in tc[field]:
                    lines.append("| " + " | ".join(safe(display(item[k])) for k, _ in columns) + " |")
            else:
                lines.append("해당 없음")
            lines.append("")
        lines += ["### 종료 및 원상 복구", ""] + rows(tc["closure"]) + ["", "### 실행 결과", "", "이번 작업에서 실행하지 않음. 실행 결과는 별도 실행기가 작성합니다.", ""]
    for key, value in data.items():
        if key == "test_cases":
            continue
        lines += ["## " + label(key), ""] + rows(value) + [""]
    return "\n".join(lines)


def load(path):
    def reject_constant(value):
        raise ValueError(f"허용되지 않은 JSON 상수: {value}")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"중복 JSON 키: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=reject_constant, object_pairs_hook=unique_keys)


def export(data, output, previous=None):
    errors = validate(data, previous)
    if errors:
        raise ValueError("\n".join(errors))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    files = {"bundle.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
             "report.html": render_html(data), "report.md": render_markdown(data)}
    for name, text in files.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return list(files)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "export"])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = load(args.bundle)
        previous = load(args.previous) if args.previous else None
        errors = validate(data, previous)
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        if args.command == "export":
            if not args.output:
                parser.error("export에는 --output이 필요합니다")
            names = export(data, args.output, previous)
            print(json.dumps({"valid": True, "output": str(args.output.resolve()), "files": names}, ensure_ascii=False))
        else:
            print(json.dumps({"valid": True, "scope": "구조·참조·이력 검사. 의미 검토/검색 완전성/실행 결과 검증 아님"}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
