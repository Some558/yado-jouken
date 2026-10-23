"""楽天トラベルAPI クライアント(2026-07-10 A1実測仕様・docs/RUNBOOK.md が根拠)。

- 1req/sec規約 → 1.1s間隔をプロセス内で強制
- リトライ上限2(automation-harness §2.4 に合わせ粘らない)
- 認証: applicationId + accessKey + Referer/Origin両ヘッダー
"""

import http.client
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://openapi.rakuten.co.jp/engine/api"
SITE_ORIGIN = "https://yadoshibori.com"
REQUEST_INTERVAL_SEC = 1.1
MAX_RETRIES = 2
ENDPOINTS = {
    "Travel/SimpleHotelSearch/20170426",
    "Travel/VacantHotelSearch/20170426",
    "Travel/HotelDetailSearch/20170426",
    "Travel/GetAreaClass/20140210",
}
ERROR_CODES = {
    "not_found", "wrong_parameter", "too_many_requests", "system_error",
    "service_unavailable", "invalid_token", "invalid_request",
    "REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING",
}


def error_code(body: str) -> str:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return "unrecognized"
    code = payload.get("error") if isinstance(payload, dict) else None
    return code if isinstance(code, str) and code in ERROR_CODES else "unrecognized"


class RakutenApiError(Exception):
    def __init__(self, status: int, body: str, endpoint: str):
        self.status = status
        self.error_code = error_code(body)
        safe_endpoint = endpoint if endpoint in ENDPOINTS else "unknown"
        super().__init__(f"{safe_endpoint} -> HTTP {status} code={self.error_code}")


class RakutenTransportError(Exception):
    """Only a fixed failure category; urllib reasons can contain authenticated URLs."""


def diagnostic(endpoint: str, params: dict, **fields):
    # Never serialize params, request URLs, headers, bodies, or exception text.
    record = {"endpoint": endpoint if endpoint in ENDPOINTS else "unknown", **fields}
    if type(params.get("page")) is int:
        record["page"] = params["page"]
    print("[rakuten] " + json.dumps(record, sort_keys=True), flush=True)


def load_env() -> dict:
    env = dict(os.environ)
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


class RakutenClient:
    def __init__(self):
        env = load_env()
        self.app_id = env.get("RAKUTEN_APP_ID", "")
        self.access_key = env.get("RAKUTEN_ACCESS_KEY", "")
        self.affiliate_id = env.get("RAKUTEN_AFFILIATE_ID", "")
        if not (self.app_id and self.access_key and self.affiliate_id):
            raise SystemExit("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / RAKUTEN_AFFILIATE_ID が未設定")
        self._last_at = 0.0
        self.request_count = 0
        self.min_observed_interval: float | None = None

    def _throttle(self):
        now = time.monotonic()
        if self._last_at:
            elapsed = now - self._last_at
            wait = REQUEST_INTERVAL_SEC - elapsed
            if wait > 0:
                time.sleep(wait)
        sent_at = time.monotonic()
        if self._last_at:
            interval = sent_at - self._last_at
            if self.min_observed_interval is None or interval < self.min_observed_interval:
                self.min_observed_interval = interval
        self._last_at = sent_at

    def get(self, endpoint: str, params: dict) -> dict:
        q = {
            "applicationId": self.app_id,
            "accessKey": self.access_key,
            "affiliateId": self.affiliate_id,
            "format": "json",
            **params,
        }
        url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(
            url, headers={"Referer": f"{SITE_ORIGIN}/", "Origin": SITE_ORIGIN}
        )
        last_err: Exception | None = None
        for attempt in range(1 + MAX_RETRIES):
            self._throttle()
            self.request_count += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    payload = json.loads(res.read().decode("utf-8"))
                    invalid = not isinstance(payload, dict) or "error" in payload
                    if not invalid and endpoint in {
                        "Travel/SimpleHotelSearch/20170426", "Travel/VacantHotelSearch/20170426",
                    }:
                        paging = payload.get("pagingInfo")
                        invalid = (not isinstance(paging, dict)
                                   or not isinstance(payload.get("hotels"), list)
                                   or any(type(paging.get(k)) is not int or paging[k] < 0
                                          for k in ("recordCount", "pageCount")))
                    if invalid:
                        diagnostic(endpoint, params, attempt=attempt + 1,
                                   status=res.status, outcome="invalid_response")
                        raise RakutenTransportError("invalid_response") from None
                    paging = payload.get("pagingInfo") or {}
                    summary = {}
                    if isinstance(paging, dict):
                        for key in ("recordCount", "pageCount"):
                            if type(paging.get(key)) is int:
                                summary[key] = paging[key]
                    hotels = payload.get("hotels")
                    if isinstance(hotels, list):
                        summary["returnedHotels"] = len(hotels)
                    diagnostic(endpoint, params, attempt=attempt + 1,
                               status=res.status, outcome="ok", **summary)
                    return payload
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except (OSError, http.client.HTTPException):
                    body = ""
                safe_error = RakutenApiError(e.code, body, endpoint)
                retry = e.code in (429, 500, 502, 503) and attempt < MAX_RETRIES
                diagnostic(endpoint, params, attempt=attempt + 1, status=e.code,
                           outcome="not_found" if e.code == 404 and safe_error.error_code == "not_found" else "http_error",
                           code=safe_error.error_code, retry=retry)
                # 4xx はリトライ無意味(パラメータ/権限)。429/5xx のみ再試行
                if e.code not in (429, 500, 502, 503):
                    raise safe_error from None
                last_err = safe_error
            except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as e:
                category = ("invalid_json" if isinstance(e, (json.JSONDecodeError, UnicodeDecodeError))
                            else "timeout" if isinstance(e, TimeoutError) else "network_error")
                diagnostic(endpoint, params, attempt=attempt + 1, outcome=category,
                           retry=attempt < MAX_RETRIES)
                last_err = RakutenTransportError(category)
            if attempt < MAX_RETRIES:
                time.sleep(3.0 * (attempt + 1))
        raise last_err from None  # type: ignore[misc]

    # --- 検索の「該当なし(404 not_found)」を空結果として扱うヘルパ ---
    def search(self, endpoint: str, params: dict) -> dict | None:
        try:
            return self.get(endpoint, params)
        except RakutenApiError as e:
            if e.status == 404 and e.error_code == "not_found":
                return None
            raise
