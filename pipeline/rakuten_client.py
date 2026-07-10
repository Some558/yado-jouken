"""楽天トラベルAPI クライアント(2026-07-10 A1実測仕様・docs/RUNBOOK.md が根拠)。

- 1req/sec規約 → 1.1s間隔をプロセス内で強制
- リトライ上限2(automation-harness §2.4 に合わせ粘らない)
- 認証: applicationId + accessKey + Referer/Origin両ヘッダー
"""

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


class RakutenApiError(Exception):
    def __init__(self, status: int, body: str, endpoint: str):
        self.status = status
        self.body = body[:300]
        super().__init__(f"{endpoint} -> {status}: {self.body}")


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
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                # 4xx はリトライ無意味(パラメータ/権限)。429/5xx のみ再試行
                if e.code not in (429, 500, 502, 503):
                    raise RakutenApiError(e.code, body, endpoint)
                last_err = RakutenApiError(e.code, body, endpoint)
                time.sleep(3.0 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(3.0 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    # --- 検索の「該当なし(404 not_found)」を空結果として扱うヘルパ ---
    def search(self, endpoint: str, params: dict) -> dict | None:
        try:
            return self.get(endpoint, params)
        except RakutenApiError as e:
            if e.status == 404 and "not_found" in e.body:
                return None
            raise
