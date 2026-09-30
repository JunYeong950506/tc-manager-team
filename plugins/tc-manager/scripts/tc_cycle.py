"""Link reviewed existing TCs to existing cycles; never edits test cases."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://api.zephyrscale.smartbear.com/v2"
STATUS = "Not Executed"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_plan(path):
    path = Path(path).resolve()
    plan = load(path)
    if plan.get("schema_version") != "tc-cycle-plan-1":
        raise ValueError("schema_version must be tc-cycle-plan-1")
    project = plan.get("project_key", "")
    if not isinstance(project, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", project):
        raise ValueError("Invalid project_key")
    folder = plan.get("folder", {})
    if (type(folder.get("id")) is not int or folder["id"] <= 0
            or not isinstance(folder.get("name"), str) or not folder["name"].strip()
            or "parent_id" not in folder
            or (folder["parent_id"] is not None and
                (type(folder["parent_id"]) is not int or folder["parent_id"] <= 0))):
        raise ValueError("folder requires actual id, name and parent_id (null for root)")
    cycles = plan.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        raise ValueError("cycles must be nonempty")
    normalized = []
    for cycle in cycles:
        key = cycle.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(re.escape(project) + r"-R[0-9]+", key):
            raise ValueError("Cycle key must belong to project")
        if not isinstance(cycle.get("name"), str) or not cycle["name"].strip():
            raise ValueError("Cycle name is required")
        keys_path = (path.parent / cycle["tc_keys_file"]).resolve()
        if not keys_path.is_relative_to(path.parent):
            raise ValueError("TC key file must stay within the task folder")
        keys = [s.strip() for s in keys_path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("TC keys must be nonempty and unique")
        if any(not re.fullmatch(re.escape(project) + r"-T[0-9]+", k) for k in keys):
            raise ValueError("Only actual TC keys in the selected project are allowed")
        normalized.append({"key": key, "name": cycle["name"], "tc_keys": sorted(keys)})
    if len({c["key"] for c in normalized}) != len(normalized):
        raise ValueError("Duplicate cycle keys")
    result = {"project_key": project, "folder": folder, "cycles": normalized}
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result, digest


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_token():
    token = os.environ.get("ZEPHYR_API_TOKEN", "").strip()
    if not token and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token = winreg.QueryValueEx(key, "ZEPHYR_API_TOKEN")[0].strip()
        except OSError:
            pass
    if not token:
        raise ValueError("ZEPHYR_API_TOKEN is not configured")
    return token


def retry_delay(value):
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return 5.0


class Client:
    def __init__(self, token=None):
        self.token = token or api_token()

    def request(self, method, path, params=None, body=None):
        if not path.startswith("/") or "?" in path or ".." in path:
            raise ValueError("Invalid API path")
        if method not in {"GET", "POST"} or (method == "POST" and path != "/testexecutions/"):
            raise ValueError("Only test execution creation is allowed")
        url = BASE + path + (("?" + urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            req = Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with build_opener(NoRedirect()).open(req, timeout=45) as resp:
                    raw = resp.read()
                    result = json.loads(raw.decode("utf-8")) if raw else {}
                    if not isinstance(result, dict):
                        raise ValueError("Unexpected API response")
                    return result
            except HTTPError as exc:
                if exc.code == 429 and attempt < 5:
                    delay = retry_delay(exc.headers.get("Retry-After"))
                    if delay > 300:
                        raise RuntimeError("Rate limited; retry later") from None
                    time.sleep(delay)
                    continue
                if method == "GET" and exc.code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                # Never expose headers, credential values or arbitrary response bodies.
                raise RuntimeError(f"{method} {path}: HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError, ValueError):
                raise RuntimeError(f"{method} {path}: network or response error; reconcile before retry") from None

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def create(self, body):
        return self.request("POST", "/testexecutions/", body=body)


def pages(client, path, params, out, cursor=False):
    rows, seen, offset, count = [], set(), 0, 0
    while True:
        if offset in seen:
            raise ValueError("Repeated pagination cursor")
        seen.add(offset)
        paging = {"limit": 1000, "startAtId": offset} if cursor else {"maxResults": 1000, "startAt": offset}
        page = client.get(path, {**params, **paging})
        save(Path(out) / f"{count}.json", page)
        values = page.get("values")
        if not isinstance(values, list):
            raise ValueError("Missing page values")
        rows.extend(values)
        count += 1
        if cursor:
            if "nextStartAtId" not in page:
                raise ValueError("Missing nextgen cursor metadata")
            nxt = page["nextStartAtId"]
            if nxt is None:
                if page.get("next"):
                    raise ValueError("Next page exists without cursor")
                break
            if type(nxt) is not int or nxt <= offset or not values:
                raise ValueError("Invalid/incomplete nextgen page")
            offset = nxt
        else:
            total = page.get("total")
            last = page.get("isLast")
            if last is True or (type(total) is int and len(rows) >= total):
                if type(total) is int and len(rows) != total:
                    raise ValueError("Incomplete offset pagination")
                break
            if not values or (last is not False and type(total) is not int):
                raise ValueError("Missing/incomplete pagination metadata")
            offset += len(values)
    return rows, count


def membership(client, plan, cycle, status_id, out, remote):
    rows, count = pages(client, "/testexecutions/nextgen",
                        {"projectKey": plan["project_key"], "testCycle": cycle["key"]}, out, cursor=True)
    keys, bad_status, bad_rows = [], [], []
    for row in rows:
        if (row.get("testCycle", {}).get("id") != remote.get("id")
                or row.get("project", {}).get("id") != remote.get("project", {}).get("id")):
            raise ValueError("Execution belongs to another cycle/project")
        link = row.get("testCase", {}).get("self", "")
        match = re.fullmatch(r"/v2/testcases/(" + re.escape(plan["project_key"]) + r"-T[0-9]+)(?:/versions/[0-9]+)?/?", urlsplit(link).path)
        if not match:
            raise ValueError("Execution missing a valid TC reference")
        key = match[1]
        keys.append(key)
        if row.get("testExecutionStatus", {}).get("id") != status_id:
            bad_status.append(key)
        if not row.get("id"):
            bad_rows.append(key)
    actual, expected = set(keys), set(cycle["tc_keys"])
    return {"cycle_key": cycle["key"], "pages_read": count, "target_count": len(expected),
            "remote_count": len(rows), "linked_count": len(actual & expected),
            "missing": sorted(expected - actual), "unexpected": sorted(actual - expected),
            "duplicates": {k: v for k, v in Counter(keys).items() if v > 1},
            "wrong_status": bad_status, "invalid_execution_ids": bad_rows}


def preflight(client, plan, out):
    folders, _ = pages(client, "/folders", {"projectKey": plan["project_key"], "folderType": "TEST_CYCLE"}, out / "folders")
    wanted = plan["folder"]
    matching = [f for f in folders if f.get("id") == wanted["id"]]
    if len(matching) != 1:
        raise ValueError("Destination is not a TEST_CYCLE folder in this project")
    folder = matching[0]
    if (folder.get("folderType") != "TEST_CYCLE" or folder.get("name") != wanted["name"]
            or folder.get("parentId") != wanted["parent_id"] or not folder.get("project", {}).get("id")):
        raise ValueError("Folder type/name/parent/project mismatch")
    statuses, _ = pages(client, "/statuses", {"projectKey": plan["project_key"], "statusType": "TEST_EXECUTION"}, out / "statuses")
    candidates = [s for s in statuses if s.get("name") == STATUS and not s.get("archived", False)]
    if len(candidates) != 1 or not candidates[0].get("id"):
        raise ValueError("Unique active Not Executed status required")
    status_id = candidates[0]["id"]
    result = []
    for cycle in plan["cycles"]:
        remote = client.get("/testcycles/" + cycle["key"])
        save(out / (cycle["key"] + ".json"), remote)
        if (remote.get("key") != cycle["key"] or remote.get("name") != cycle["name"]
                or remote.get("folder", {}).get("id") != wanted["id"]
                or remote.get("project", {}).get("id") != folder["project"]["id"]):
            raise ValueError("Cycle key/name/folder/project mismatch")
        result.append(membership(client, plan, cycle, status_id, out / cycle["key"], remote))
    return status_id, result


def conflicts(result):
    return any(result[k] for k in ("unexpected", "duplicates", "wrong_status", "invalid_execution_ids"))


@contextmanager
def job_lock(out):
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a+b") as handle:
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("This output directory already has an active job") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def job_session(out, digest):
    with job_lock(out):
        state_path = out / "state.json"
        if state_path.exists() and load(state_path).get("plan_hash") != digest:
            raise ValueError("Existing job belongs to a different plan; use a new output folder")
        if (out / "journal.jsonl").exists() and not state_path.exists():
            raise ValueError("Journal has no matching plan state")
        try:
            yield
        except Exception as exc:
            blocked = {"plan_hash": digest, "status": "blocked", "verified": False,
                       "error": str(exc), "updated_at": stamp()}
            save(out / "result.json", blocked)
            save(state_path, blocked)
            raise


def execute(manifest, out, mode="check", expected_hash=None, workers=4, client=None):
    if mode not in {"check", "apply", "verify"} or not 1 <= workers <= 4:
        raise ValueError("Invalid mode or worker count (1..4)")
    plan, digest = read_plan(manifest)
    if mode == "apply" and expected_hash != digest:
        raise ValueError("Reviewed plan hash mismatch; run check on the final plan")
    out = Path(out).resolve()
    client = client or Client()
    with job_session(out, digest):
        state_path, journal_path = out / "state.json", out / "journal.jsonl"
        save(state_path, {"plan_hash": digest, "status": "checking", "updated_at": stamp()})
        run = out / "readbacks" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        status_id, before = preflight(client, plan, run / "before")
        # A missing pending/uncertain POST is not proof that the server never accepted it.
        unresolved = set()
        if journal_path.exists():
            for line in journal_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                pair = (row["cycle_key"], row["tc_key"])
                if row["phase"] in {"pending", "uncertain"}:
                    unresolved.add(pair)
        blocked = [(r["cycle_key"], k) for r in before for k in r["missing"] if (r["cycle_key"], k) in unresolved]
        can_apply = not blocked and not any(conflicts(r) for r in before)
        report = {"plan_hash": digest, "mode": mode, "status_id": status_id,
                  "checked_at": stamp(), "can_apply": can_apply, "unresolved_posts": blocked, "cycles": before}
        save(out / "check.json", report)
        if mode == "apply" and can_apply:
            save(state_path, {"plan_hash": digest, "status": "publishing", "updated_at": stamp()})
            pending = [(r["cycle_key"], k) for r in before for k in r["missing"]]
            lock = threading.Lock()
            stop = threading.Event()
            def record(row):
                with lock, journal_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"at": stamp(), **row}, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            def add(pair):
                if stop.is_set():
                    return None
                cycle_key, tc_key = pair
                row = {"cycle_key": cycle_key, "tc_key": tc_key}
                record({**row, "phase": "pending"})
                try:
                    response = client.create({"projectKey": plan["project_key"], "testCaseKey": tc_key,
                                              "testCycleKey": cycle_key, "statusName": STATUS})
                    if not response.get("id"):
                        raise ValueError("Creation response missing execution ID")
                except Exception:
                    record({**row, "phase": "uncertain"})
                    stop.set()
                    return False
                record({**row, "phase": "response", "execution_id": response["id"]})
                return True
            good = completed = uncertain = skipped = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(add, pair) for pair in pending]
                for future in as_completed(futures):
                    outcome = future.result()
                    good += int(outcome is True)
                    uncertain += int(outcome is False)
                    skipped += int(outcome is None)
                    completed += 1
                    if completed % 20 == 0 or completed == len(pending):
                        save(out / "progress.json", {"status": "publishing", "requested": len(pending),
                             "processed": completed, "response_ok": good, "uncertain": uncertain,
                             "skipped_after_error": skipped, "updated_at": stamp()})
            # Revalidate folder and cycle metadata as well as every membership page.
            _, report["cycles"] = preflight(client, plan, run / "after")
        report["verified"] = all(not r["missing"] and not conflicts(r) for r in report["cycles"])
        report["status"] = "verified" if report["verified"] else ("ready" if mode == "check" and can_apply else "blocked")
        report["finished_at"] = stamp()
        save(out / "result.json", report)
        save(state_path, {"plan_hash": digest, "status": report["status"], "updated_at": stamp()})
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "apply", "verify"])
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--expected-plan-hash")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    try:
        report = execute(args.plan, args.out, args.mode, args.expected_plan_hash, args.workers)
        print(json.dumps({k: report[k] for k in ("plan_hash", "status", "verified", "cycles")}, ensure_ascii=False))
        return 0 if report["status"] in {"ready", "verified"} else 2
    except Exception as exc:
        # All error messages emitted by this module exclude token/header/response text.
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
