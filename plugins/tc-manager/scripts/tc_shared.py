"""HTTPS or private LAN HTTP transport for the common TC service. No local fallback."""
import ipaddress
import json
from pathlib import Path
from tc_outbox import Outbox
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("공용 TC 서버 redirect는 허용하지 않습니다. 설정 주소를 확인하세요")


def lan_http_host(host):
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.IPv4Address(host)
        return any(address in ipaddress.IPv4Network(network) for network in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    except ipaddress.AddressValueError:
        return False


class RemoteError(ValueError):
    def __init__(self, status, code, message):
        self.status, self.code = status, code
        super().__init__(message)


class Remote:
    def __init__(self, config, namespace):
        value = config.get("server_url", "")
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or not parsed.hostname:
            raise ValueError("공용 서버의 자격 증명 없는 기본 주소가 필요합니다")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and lan_http_host(parsed.hostname)):
            raise ValueError("같은 사내망의 사설 IPv4 주소는 HTTP를 사용할 수 있습니다. 그 외 주소는 HTTPS가 필요합니다")
        self.url, self.namespace = value.rstrip("/"), namespace
        self.outbox = Outbox(Path(config.get('_outbox_dir', '.tc-manager')) / 'sync-outbox.sqlite3', self.url, namespace)

    def rpc(self, op, args=None):
        self._flush_notifications()
        return self._rpc(op, args)

    def _flush_notifications(self):
        results = {}
        for namespace in self.outbox.namespaces():
            if namespace == self.namespace:
                results = self.outbox.flush(self._rpc)
            else:
                remote = Remote(dict(server_url=self.url, _outbox_dir=str(self.outbox.path.parent)), namespace)
                remote.outbox.flush(remote._rpc)
        return results

    def show(self, tc_id, version=None):
        args = dict(id=tc_id)
        if version is not None:
            args['version'] = version
        try:
            return self.rpc('cases.get', args)
        except RemoteError as error:
            if version is not None or error.code not in {'conflict', 'not_found'}:
                raise
            try:
                result = self.rpc('catalog.get', dict(id=tc_id))
            except ValueError:
                raise error from None
            if result.get('stored_version') is not None:
                raise error
            return dict(result, detail_kind='source_catalog', current_version=None)

    def notify(self, tc_key, event_id, story_keys=()):
        payload = dict(tc_key=tc_key, event_id=event_id, story_keys=sorted(set(story_keys)))
        self.outbox.put(payload)
        result = self._flush_notifications().get(event_id)
        return dict(zephyr_status='등록·수정 및 재조회 완료', shared_status='공용 DB 반영 완료' if result and result['status'] == 'completed' else '공용 DB 반영 대기',
                    notification=result or dict(status='local_pending', event_id=event_id))

    def _rpc(self, op, args=None):
        args = dict(args or {})
        if op != "context":
            if args.get("namespace", self.namespace) != self.namespace:
                raise ValueError("선택한 프로젝트와 다른 namespace 요청입니다")
            args["namespace"] = self.namespace
        headers = {"Content-Type": "application/json", "X-TC-Manager": "1"}
        payload = json.dumps(dict(op=op, args=args), ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError("요청은 10 MiB 이하여야 합니다. 수집 묶음을 나누세요")
        req = urllib.request.Request(self.url + "/api/tc-manager/rpc", data=payload, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            code = 'http_error'
            try:
                body = json.loads(error.read(65536))
                value = body.get('error', {}).get('code', '')
                if isinstance(value, str) and value.replace('_', '').isalnum():
                    code = value[:80]
            except (ValueError, AttributeError, OSError):
                pass
            raise RemoteError(error.code, code, f"공용 TC 서버 HTTP {error.code} ({code}). 로컬 DB로 대체하지 않았습니다") from None
        except (urllib.error.URLError, OSError):
            raise ValueError("공용 TC 서버 연결 실패. 산출물은 보존하고 재연결 후 조회부터 재개하세요. 로컬 DB로 대체하지 않았습니다") from None
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("공용 TC 서버 응답 크기 제한 초과")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError("공용 TC 서버 응답이 JSON이 아닙니다") from None
        if not isinstance(result, dict) or result.get("ok") is not True or "data" not in result:
            code = result.get("error", {}).get("code", "invalid_response") if isinstance(result, dict) and isinstance(result.get("error", {}), dict) else "invalid_response"
            raise ValueError("공용 TC 서버 요청 실패: " + str(code)[:80])
        return result["data"]
