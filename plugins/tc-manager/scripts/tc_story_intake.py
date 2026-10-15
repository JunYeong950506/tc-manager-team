"""Normalize complete Jira search pages into content-first Story import batches."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import tc_stories as stories

AC_NAMES = {"acceptancecriteria", "acceptancecriterion", "인수조건", "인수기준", "수용기준", "수락기준", "완료조건", "ac"}
EXTRACTOR = 'jira-search-text-2'


def plain(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("본문은 문자열 또는 Jira ADF 문서여야 합니다")
    kind = value["type"]
    if kind == "text":
        result = value.get("text", "")
        for mark in value.get("marks", []):
            if mark.get("type") == "link":
                href = mark.get("attrs", {}).get("href", "")
                if href and href != result:
                    result += " (" + href + ")"
        return result
    if kind == "hardBreak":
        return "\n"
    if kind in {"inlineCard", "blockCard"}:
        return value.get("attrs", {}).get("url", "") + ("\n" if kind == "blockCard" else "")
    if kind == "mention":
        return value.get("attrs", {}).get("text", "[mention]")
    if kind in {"media", "mediaInline"}:
        return "[첨부 자료: " + value.get("attrs", {}).get("alt", "별도 조회 필요") + "]"
    if kind == "emoji":
        return value.get("attrs", {}).get("text") or value.get("attrs", {}).get("shortName", "")
    if kind == "date":
        stamp = value.get("attrs", {}).get("timestamp", "")
        if not re.fullmatch(r'-?\d+', str(stamp)):
            raise ValueError('JIRA_ADF_INVALID_DATE')
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(stamp))).date().isoformat()
    supported = {"doc", "paragraph", "heading", "bulletList", "orderedList", "listItem", "blockquote", "codeBlock", "panel",
                 "table", "tableRow", "tableCell", "tableHeader", "rule", "mediaSingle", "mediaGroup", "expand", "nestedExpand", "status", "taskList", "taskItem"}
    if kind not in supported:
        raise ValueError("지원하지 않는 ADF 노드입니다. 원문을 보존하고 변환기를 확인하세요: " + kind)
    content = "".join(plain(child) for child in value.get("content", []))
    if kind == "taskItem":
        state = value.get('attrs', {}).get('state')
        if state not in {'TODO', 'DONE'}:
            raise ValueError('JIRA_ADF_INVALID_TASK_STATE')
        return ('[x] ' if state == 'DONE' else '[ ] ') + content.rstrip() + '\n'
    if kind == "status":
        return value.get("attrs", {}).get("text", "")
    if kind == "listItem":
        return "* " + content.rstrip() + "\n"
    if kind == "heading":
        return "#" * max(1, min(6, int(value.get("attrs", {}).get("level", 1)))) + " " + content + "\n"
    if kind in {"tableCell", "tableHeader"}:
        return content.strip() + "\t"
    if kind in {"paragraph", "heading", "codeBlock", "blockquote", "tableRow", "rule"}:
        return content.rstrip("\t") + "\n"
    return content


def ac_name(value):
    return re.sub(r"[\s_\-:：\[\]#*]+", "", value).casefold()


def searchable(value, warnings):
    """Keep readable fragments and a visible gap; the untouched source is stored separately."""
    try:
        return plain(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        warnings.add('UNSUPPORTED_DOCUMENT_FRAGMENT')
        if isinstance(value, dict):
            content = value.get('content')
            if isinstance(content, list):
                return '[원문 형식 추가 확인 필요]\n' + ''.join(searchable(child, warnings) for child in content) + '\n'
            if isinstance(value.get('text'), str):
                return '[원문 형식 추가 확인 필요] ' + value['text']
        return '[원문 형식 추가 확인 필요]\n'


def description_ac(description):
    lines = description.splitlines()
    start, level = None, None
    for i, line in enumerate(lines):
        heading = re.match(r'^\s*(#{1,6})\s+', line)
        label = re.sub(r'^\s*#{1,6}\s+', '', line).strip().strip('*[] ')
        label = re.sub(r'^\d+[.)]\s*', '', label)
        if ac_name(label) in AC_NAMES:
            start, level = i + 1, len(heading[1]) if heading else None
            break
    if start is None:
        return ""
    result = []
    for line in lines[start:]:
        heading = re.match(r'^\s*(#{1,6})\s+', line)
        if (heading and (level is None or len(heading[1]) <= level)) or (level is None and re.match(r'^\[[^]]+\]\s*$', line)):
            break
        result.append(line)
    return "\n".join(result).strip()


def search_page(response):
    if any(k in response for k in ("error", "errorMessages", "errors")):
        raise ValueError("성공한 Jira 검색 응답이 필요합니다")
    issues = response.get("issues")
    if isinstance(issues, dict):
        info = issues.get("pageInfo", {})
        if not isinstance(issues.get("nodes"), list) or type(info.get("hasNextPage")) is not bool:
            raise ValueError("MCP 검색 응답의 nodes와 페이지 완료 표시가 필요합니다")
        if info["hasNextPage"] and not info.get("endCursor"):
            raise ValueError("MCP 다음 페이지 커서가 누락되었습니다")
        return dict(issues=issues["nodes"], isLast=not info["hasNextPage"],
                    nextPageToken=info.get("endCursor") if info["hasNextPage"] else None,
                    total=issues.get("totalCount"))
    return response


def normalize(capture, site, project, allow_omitted_ac=False, preserve_source=False):
    url = urlsplit(site)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("Jira 사이트의 HTTPS 기본 주소가 필요합니다")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", project):
        raise ValueError("프로젝트 키를 확인하세요")
    observed = stories.timestamp(capture["observed_at"])
    query = stories.text(capture["query"], "query")
    pages = capture["pages"]
    if not isinstance(pages, list) or not pages:
        raise ValueError("실제 Jira 검색 응답 pages가 필요합니다")
    pages = [dict(item, response=search_page(item["response"])) for item in pages]
    names = capture.get("field_names", {})
    ac_fields = {key for key, name in names.items() if ac_name(name) in AC_NAMES}
    rows, seen, tokens = [], set(), set()
    omitted_ac = {}
    source_records = {}
    for number, item in enumerate(pages):
        page = item["response"]
        request_token = item.get("request_token")
        if number == 0 and request_token:
            raise ValueError("첫 페이지부터 수집해야 합니다")
        if number and request_token != pages[number - 1]["response"].get("nextPageToken"):
            raise ValueError("Jira 페이지 연결이 누락되었습니다")
        if not isinstance(page.get("issues"), list) or any(k in page for k in ("error", "errorMessages", "errors")):
            raise ValueError("성공한 Jira issues 응답이 필요합니다")
        if number < len(pages) - 1:
            token = page.get("nextPageToken")
            if not token or token in tokens or page.get("isLast") is True:
                raise ValueError("Jira 페이지 토큰이 반복되거나 잘못되었습니다")
            tokens.add(token)
        elif page.get("isLast") is not True or page.get("nextPageToken"):
            raise ValueError("마지막 페이지가 확인되지 않았습니다. 부분 수집으로 완료할 수 없습니다")
        for issue in page["issues"]:
            key, fields = issue["key"], issue["fields"]
            if key in seen or not key.startswith(project + "-") or fields["project"]["key"] != project:
                raise ValueError("중복 또는 다른 프로젝트의 Jira 이슈입니다")
            seen.add(key)
            if "description" not in fields:
                raise ValueError("JIRA_DESCRIPTION_MISSING")
            missing_ac = ac_fields - fields.keys()
            if missing_ac and not allow_omitted_ac:
                raise ValueError("본문/AC 필드가 누락되었습니다. 요청 fields를 확인하세요")
            for field in missing_ac:
                omitted_ac[field] = omitted_ac.get(field, 0) + 1
            warnings = set()
            extract = (lambda value: searchable(value, warnings)) if preserve_source else plain
            description = extract(fields["description"]).strip()
            ac = "\n\n".join(extract(fields[field]).strip() for field in sorted(ac_fields) if fields.get(field) is not None).strip()
            if not ac:
                ac = description_ac(description)
            row = dict(key=key, issue_id=str(issue["id"]), issue_type=fields["issuetype"]["name"], title=fields["summary"],
                description=description, acceptance_criteria=ac, updated_at=fields["updated"], fetched_at=observed,
                source_url=site.rstrip("/") + "/browse/" + key,
                content_status=dict(description="present" if description else "empty", acceptance_criteria="present" if ac else "not_identified"))
            rows.append(stories.snapshot(row))
            if preserve_source:
                source_records[key] = dict(key=key, raw_issue=issue, extractor=EXTRACTOR, warnings=sorted(warnings))
    totals = {item['response']['total'] for item in pages if type(item['response'].get('total')) is int}
    if totals and totals != {len(rows)}:
        raise ValueError("Jira 전체 건수와 수집 건수가 다릅니다. 수집 중 변경 또는 누락을 확인하세요")
    scope = dict(query=query, observed_at=observed, complete=True)
    batches = [dict(stories=rows[i:i + 1000], scope=dict(scope, complete=len(rows) <= 1000)) for i in range(0, len(rows), 1000)]
    if not batches:
        batches = [dict(stories=[], scope=scope)]
    if preserve_source:
        for batch in batches:
            batch['source_records'] = [source_records[row['key']] for row in batch['stories']]
    return batches, dict(project=project, count=len(rows), scope=scope, acceptance_criteria_fields=sorted(ac_fields),
        omitted_acceptance_criteria_fields=omitted_ac,
        partial_text_count=sum(bool(row['warnings']) for row in source_records.values()),
        description_empty=sum(row['content_status']['description'] == 'empty' for row in rows),
        ac_not_identified=sum(row["content_status"]["acceptance_criteria"] == "not_identified" for row in rows),
        raw_pages_preserved=False, tc_links_collected=False, remote_writes=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_text(encoding="utf-8-sig")
    batches, report = normalize(json.loads(raw), args.site, args.project)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "jira-source.json").write_text(raw, encoding="utf-8")
    report['raw_pages_preserved'] = True
    for index, batch in enumerate(batches):
        (args.output / f"stories-{index:04}.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
