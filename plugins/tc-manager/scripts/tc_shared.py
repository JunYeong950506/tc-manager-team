"""HTTPS transport for the existing authenticated TC service. No local fallback."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("공용 TC 서버 redirect는 허용하지 않습니다. 설정 주소를 확인하세요")


class Remote:
    def __init__(self, config, namespace):
        value = config.get("server_url", "")
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or not parsed.hostname:
            raise ValueError("공용 서버의 자격 증명 없는 기본 주소가 필요합니다")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("공용 TC 서버는 HTTPS, 개발 서버만 loopback HTTP를 사용합니다")
        self.url, self.namespace = value.rstrip("/"), namespace
        self.token_env = config.get("token_env")
        if not self.token_env and parsed.scheme == "https":
            raise ValueError("팀원별 공용 TC 서버 토큰 환경 변수 이름(token_env)이 필요합니다")
        if self.token_env and (not isinstance(self.token_env, str) or not self.token_env.isidentifier()):
            raise ValueError("token_env에는 환경 변수 이름만 지정하세요")

    def rpc(self, op, args=None):
        args = dict(args or {})
        if op != "context":
            if args.get("namespace", self.namespace) != self.namespace:
                raise ValueError("선택한 프로젝트와 다른 namespace 요청입니다")
            args["namespace"] = self.namespace
        headers = {"Content-Type": "application/json", "X-TC-Manager": "1"}
        if self.token_env:
            token = os.environ.get(self.token_env)
            if sys.platform == 'win32' and not token:
                import winreg
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                        saved, _ = winreg.QueryValueEx(key, self.token_env)
                        if isinstance(saved, str) and saved:
                            token = saved
                except OSError:
                    pass
            if not token:
                raise ValueError("공용 TC 서버 토큰 환경 변수가 없습니다. 로컬 DB로 대체하지 않았습니다")
            headers["Authorization"] = "Bearer " + token
        payload = json.dumps(dict(op=op, args=args), ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError("요청은 10 MiB 이하여야 합니다. 수집 묶음을 나누세요")
        req = urllib.request.Request(self.url + "/api/tc-manager/rpc", data=payload, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise ValueError(f"공용 TC 서버 HTTP {error.code}. 권한/서버 상태를 확인하세요. 로컬 DB로 대체하지 않았습니다") from None
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
