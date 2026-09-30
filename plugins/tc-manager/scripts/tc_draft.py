"""Offline TC preparation and full readback comparison. Never calls a remote API."""

import argparse
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def rich(value):
    return html.escape(value.replace("\r\n", "\n").replace("\r", "\n")).replace("\n", "<br>")


class RenderedLines(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(re.sub(r"\s+", " ", data))

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "ul", "ol"}:
            self.parts.append("\n\n" if tag == "p" else "\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "ul", "ol"}:
            self.parts.append("\n\n" if tag == "p" else "\n")


def layout_matches(remote, desired):
    parser = RenderedLines()
    parser.feed(remote or "")

    def lines(text):
        result, gap = [], False
        for line in text.splitlines():
            if line.strip():
                result.append((" ".join(line.split()), gap))
                gap = False
            else:
                gap = True
        return result

    actual, expected = lines("".join(parser.parts)), lines(desired)
    return len(actual) == len(expected) and all(
        a[0] == e[0] and (index == 0 or not e[1] or a[1])
        for index, (a, e) in enumerate(zip(actual, expected)))


def validate_scenario_layout(value):
    headings = ("[사전 조건]", "[테스트 스텝]", "[판정 기준]")
    lines = value.splitlines()
    for heading in headings:
        if heading not in value:
            continue
        matches = [i for i, line in enumerate(lines) if line.strip() == heading]
        if len(matches) != 1:
            raise ValueError("Precondition 구역 제목은 별도 줄에 작성하세요: " + heading)
        index = matches[0]
        if index and lines[index - 1].strip():
            raise ValueError("Precondition 구역 사이에 빈 줄이 필요합니다: " + heading)
        if index + 1 == len(lines) or not lines[index + 1].strip() or lines[index + 1].strip() in headings:
            raise ValueError("Precondition 구역 제목 다음 줄에 내용을 작성하세요: " + heading)
    if any(heading in value for heading in headings):
        for line in lines:
            if len(re.findall(r"(?:^|\s)\d+[.)]\s+", line)) > 1:
                raise ValueError("Precondition 번호 항목은 각각 별도 줄에 작성하세요")


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")


def plain(value):
    parser = Plain()
    parser.feed(value or "")
    return " ".join("".join(parser.parts).split())


def validate(draft, target):
    for name in ("title", "objective", "precondition"):
        if not isinstance(draft.get(name), str) or not draft[name].strip():
            raise ValueError("필수 본문 누락: " + name)
    validate_scenario_layout(draft["precondition"])
    if not isinstance(draft.get("steps"), list) or not draft["steps"]:
        raise ValueError("Test Script가 필요합니다")
    for step in draft["steps"]:
        for name in ("step", "test_data", "expected_result"):
            if not isinstance(step.get(name), str) or (name != "test_data" and not step[name].strip()):
                raise ValueError("단계 필드 누락: " + name)
    if not isinstance(draft.get("sources"), list) or not draft["sources"]:
        raise ValueError("실제 출처가 필요합니다")
    for source in draft["sources"]:
        if not all(isinstance(source.get(k), str) and source[k].strip() for k in ("locator", "note")):
            raise ValueError("출처 locator/note가 필요합니다")
    for name in ("decisions", "blockers"):
        if not isinstance(draft.get(name), list) or any(not isinstance(v, str) for v in draft[name]):
            raise ValueError(name + " 문자열 배열이 필요합니다")
    url = urlsplit(target.get("site", ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("site에는 자격 증명 없는 HTTPS 사이트 주소가 필요합니다")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", target.get("project_key", "")):
        raise ValueError("project_key가 필요합니다")
    if type(target.get("folder_id")) is not int or target["folder_id"] <= 0 or not target.get("folder_name", "").strip():
        raise ValueError("확인한 folder_id와 folder_name이 필요합니다")


def report(draft):
    text = f"# {draft['title']}\n\n## Objective\n{draft['objective']}\n\n## Precondition\n{draft['precondition']}\n\n## Test Script\n"
    for i, step in enumerate(draft["steps"], 1):
        text += f"\n### {i}\n**Step**\n{step['step']}\n\n**Test Data**\n{step['test_data']}\n\n**Expected Result**\n{step['expected_result']}\n"
    text += "\n## 출처\n" + "\n".join(f"- {s['locator']}: {s['note']}" for s in draft["sources"])
    text += "\n\n## 작성 결정\n" + "\n".join("- " + s for s in draft["decisions"])
    text += "\n\n## 미해결 합격 기준\n" + ("\n".join("- " + s for s in draft["blockers"]) or "없음") + "\n"
    return text


def prepare(draft, target, output):
    validate(draft, target)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "draft.json", draft)
    save(output / "target.json", target)
    (output / "report.md").write_text(report(draft), encoding="utf-8")
    state = {"status": "blocked" if draft["blockers"] else "prepared", "draft_hash": digest(draft),
             "target_hash": digest(target), "updated_at": stamp(), "remote_key": None}
    save(output / "state.json", state)
    if draft["blockers"]:
        return state
    create = {"projectKey": target["project_key"], "folderId": target["folder_id"], "name": draft["title"],
              "objective": rich(draft["objective"]), "precondition": rich(draft["precondition"])}
    steps = {"mode": "OVERWRITE", "items": [{"inline": {"description": rich(s["step"]),
              "testData": rich(s["test_data"]), "expectedResult": rich(s["expected_result"])}} for s in draft["steps"]]}
    save(output / "create.json", create)
    save(output / "steps.json", steps)
    project = ET.Element("project")
    ET.SubElement(project, "modelVersion").text = "1.0"
    ET.SubElement(project, "exportDate").text = stamp()
    case = ET.SubElement(ET.SubElement(project, "testCases"), "testCase")
    for key in ("name", "objective", "precondition"):
        ET.SubElement(case, key).text = create[key]
    rows = ET.SubElement(ET.SubElement(case, "testScript", {"type": "steps"}), "steps")
    for i, item in enumerate(steps["items"]):
        row = ET.SubElement(rows, "step", {"index": str(i)})
        for key, value in item["inline"].items():
            ET.SubElement(row, key).text = value
    ET.indent(project)
    ET.ElementTree(project).write(output / "import-as-new.xml", encoding="utf-8", xml_declaration=True)
    return state


def context(folder):
    folder = Path(folder)
    state, draft, target = (load(folder / name) for name in ("state.json", "draft.json", "target.json"))
    if state["draft_hash"] != digest(draft) or state["target_hash"] != digest(target):
        raise ValueError("준비 이후 본문/대상이 바뀌었습니다. 새 폴더에 prepare하세요")
    return folder, state, draft, target


def checkpoint(folder, response=None):
    folder, state, draft, target = context(folder)
    if response is None:
        if state["status"] != "prepared":
            raise ValueError("생성 재시도 금지: 실제 생성 여부와 저장된 key부터 확인하세요")
        state["status"] = "create_pending"
    else:
        if state["status"] != "create_pending":
            raise ValueError("생성 전 pending 기록이 필요합니다")
        key = response.get("key", "")
        if not re.fullmatch(re.escape(target["project_key"]) + r"-T[1-9][0-9]*", key):
            raise ValueError("실제 응답 key와 대상 프로젝트가 다릅니다")
        save(folder / "created-response.json", response)
        state.update(status="created", remote_key=key)
    state["updated_at"] = stamp()
    save(folder / "state.json", state)
    return state


def all_steps(pages):
    pages = pages if isinstance(pages, list) else [pages]
    result = []
    if not pages:
        raise ValueError("단계 조회 페이지가 없습니다")
    total = None
    for index, page in enumerate(pages):
        if page.get("startAt") != len(result) or not isinstance(page.get("values"), list):
            raise ValueError("단계 페이지 누락/중복 또는 지원하지 않는 응답입니다")
        if "total" in page:
            if total is not None and total != page["total"]:
                raise ValueError("단계 조회 도중 total이 변경되었습니다")
            total = page["total"]
        if page.get("isLast") is True and index != len(pages) - 1:
            raise ValueError("마지막 페이지 뒤 추가 페이지가 있습니다")
        result.extend(page["values"])
    if pages[-1].get("isLast") is False or (total is not None and total != len(result)):
        raise ValueError("전체 단계 조회가 끝나지 않았습니다")
    if total is None and pages[-1].get("isLast") is not True:
        raise ValueError("단계 조회 완료 근거가 없습니다")
    return result


def verify(folder, metadata, pages):
    folder, state, draft, target = context(folder)
    if state["status"] not in {"created", "mismatch", "verified"} or metadata.get("key") != state["remote_key"]:
        raise ValueError("이 작업에서 생성한 TC의 실제 key가 필요합니다")
    steps = all_steps(pages)
    differences = []
    for remote, local in (("name", "title"), ("objective", "objective"), ("precondition", "precondition")):
        if plain(metadata.get(remote)) != " ".join(draft[local].split()):
            differences.append(remote)
        elif remote != "name" and not layout_matches(metadata.get(remote), draft[local]):
            differences.append(remote + ".layout")
    if (metadata.get("folder") or {}).get("id") != target["folder_id"]:
        differences.append("folder")
    if target.get("project_id") and (metadata.get("project") or {}).get("id") != target["project_id"]:
        differences.append("project")
    if len(steps) != len(draft["steps"]):
        differences.append("step_count")
    for i, (actual, desired) in enumerate(zip(steps, draft["steps"]), 1):
        for remote, local in (("description", "step"), ("testData", "test_data"), ("expectedResult", "expected_result")):
            if plain((actual.get("inline") or {}).get(remote)) != " ".join(desired[local].split()):
                differences.append(f"step[{i}].{remote}")
    save(folder / "readback-metadata.json", metadata)
    save(folder / "readback-steps.json", pages)
    state.update(status="mismatch" if differences else "verified", differences=differences,
                 readback_hash=digest({"metadata": metadata, "steps": pages}), updated_at=stamp())
    save(folder / "state.json", state)
    return state


def verify_xml(folder, exported, expected_folder_path):
    folder, state, draft, target = context(folder)
    if state["status"] not in {"created", "mismatch", "verified"} or not state.get("remote_key"):
        raise ValueError("먼저 실제 생성 key를 기록하세요")
    raw = Path(exported).read_bytes()
    if len(raw) > 5 * 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("지원하지 않는 XML 크기/선언입니다")
    root = ET.fromstring(raw)
    if root.tag != "project":
        raise ValueError("Zephyr export XML이 아닙니다")
    cases = [c for c in root.findall("testCases/testCase") if c.get("key") == state["remote_key"]]
    if len(cases) != 1:
        raise ValueError("내보내기에서 생성 key를 유일하게 찾을 수 없습니다")
    case = cases[0]
    differences = []
    for remote, local in (("name", "title"), ("objective", "objective"), ("precondition", "precondition")):
        value = case.findtext(remote, "")
        if (" ".join(value.split()) if remote == "name" else plain(value)) != " ".join(draft[local].split()):
            differences.append(remote)
        elif remote != "name" and not layout_matches(value, draft[local]):
            differences.append(remote + ".layout")
    expected = expected_folder_path.strip().strip("/")
    if not expected or expected.split("/")[-1] != target["folder_name"]:
        raise ValueError("실제 UI에서 확인한 대상의 전체 폴더 경로가 필요합니다")
    if case.findtext("folder", "").strip().strip("/") != expected:
        differences.append("folder_path")
    script = case.find("testScript")
    if script is None or script.get("type") != "steps":
        raise ValueError("구조화된 Test Script 내보내기가 필요합니다")
    rows = script.findall("steps/step")
    if [r.get("index") for r in rows] != [str(i) for i in range(len(rows))]:
        raise ValueError("내보낸 단계 순서가 잘못되었습니다")
    if len(rows) != len(draft["steps"]):
        differences.append("step_count")
    for i, (actual, desired) in enumerate(zip(rows, draft["steps"]), 1):
        for remote, local in (("description", "step"), ("testData", "test_data"), ("expectedResult", "expected_result")):
            if plain(actual.findtext(remote, "")) != " ".join(desired[local].split()):
                differences.append(f"step[{i}].{remote}")
    (folder / "readback-export.xml").write_bytes(raw)
    state.update(status="mismatch" if differences else "verified", differences=differences,
                 verification_method="zephyr_xml_export", folder_path=expected,
                 readback_hash=hashlib.sha256(raw).hexdigest(), updated_at=stamp())
    save(folder / "state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("draft")
    prep.add_argument("--target", required=True)
    prep.add_argument("--output", required=True)
    pending = sub.add_parser("pending")
    pending.add_argument("folder")
    created = sub.add_parser("created")
    created.add_argument("folder")
    created.add_argument("--response", required=True)
    check = sub.add_parser("verify")
    check.add_argument("folder")
    check.add_argument("--metadata", required=True)
    check.add_argument("--steps", required=True)
    exported = sub.add_parser("verify-xml")
    exported.add_argument("folder")
    exported.add_argument("--export", dest="exported", required=True)
    exported.add_argument("--folder-path", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(load(args.draft), load(args.target), args.output)
        elif args.command == "verify":
            result = verify(args.folder, load(args.metadata), load(args.steps))
        elif args.command == "verify-xml":
            result = verify_xml(args.folder, args.exported, args.folder_path)
        else:
            result = checkpoint(args.folder, load(args.response) if args.command == "created" else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["status"] in {"blocked", "mismatch"} else 0
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
