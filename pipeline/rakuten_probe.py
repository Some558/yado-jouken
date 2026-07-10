#!/usr/bin/env python3
"""A1 素振りスクリプト: 楽天トラベルAPIの実挙動を確定する。

目的(docs/RUNBOOK.md に確定事項を転記する):
  1. 新ドメイン openapi.rakuten.co.jp での正確なエンドポイントパス
  2. GetAreaClass で初期10エリアの area code 4階層を取得 → config/areas.json へ
  3. SimpleHotelSearch のレスポンスで affiliateUrl の形式・hotelMinCharge・review系の所在
  4. VacantHotelSearch の squeezeCondition 対応値(pet / onsen / breakfast 等)の有効性
  5. HotelDetailSearch の設備テキストフィールド(regex条件判定に使う)の所在

実行: RAKUTEN_APP_ID / RAKUTEN_AFFILIATE_ID を .env か環境変数で渡す
  cd pipeline && python3 rakuten_probe.py
生レスポンスは data/probe/ に保存(gitignore済み・キー値はURLごとマスクしてログ出力)。
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBE_DIR = ROOT / "data" / "probe"

# 2026-07-10 実測確定: 新APIは /engine/api/ プレフィックス+accessKey必須
# (旧 /services/api/ は 404、旧ドメインはUUID形式のapplicationIdを拒否)
BASE_CANDIDATES = [
    "https://openapi.rakuten.co.jp/engine/api",
]

REQUEST_INTERVAL_SEC = 1.1  # 規約: 1req/sec。全リクエストで厳守

# 2026-07-10 実測確定: 新APIは Referer/Origin 両ヘッダー必須で、
# Webアプリ型はアプリ登録の Allowed websites ドメインと一致が必要
SITE_ORIGIN = "https://yadoshibori.com"


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


def mask(url: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            url = url.replace(s, "***")
    return url


class Prober:
    def __init__(self, app_id: str, access_key: str, affiliate_id: str):
        self.app_id = app_id
        self.access_key = access_key
        self.affiliate_id = affiliate_id
        self.last_request_at = 0.0
        self.working_base: str | None = None

    def _throttle(self):
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self.last_request_at)
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()

    def get(self, base: str, endpoint: str, params: dict) -> tuple[int, dict | str]:
        q = {
            "applicationId": self.app_id,
            "accessKey": self.access_key,
            "affiliateId": self.affiliate_id,
            "format": "json",
            **params,
        }
        url = f"{base}/{endpoint}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(
            url, headers={"Referer": f"{SITE_ORIGIN}/", "Origin": SITE_ORIGIN}
        )
        self._throttle()
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                body = res.read().decode("utf-8")
                try:
                    return res.status, json.loads(body)
                except json.JSONDecodeError:
                    return res.status, body[:500]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")[:500]
        except urllib.error.URLError as e:
            return -1, str(e.reason)

    def probe(self, name: str, endpoint: str, params: dict) -> dict | None:
        """working_base が未確定なら候補を順に試し、確定後はそれだけ使う。"""
        bases = [self.working_base] if self.working_base else BASE_CANDIDATES
        for base in bases:
            status, payload = self.get(base, endpoint, params)
            print(f"[{name}] {status} {mask(f'{base}/{endpoint}', [self.app_id, self.access_key, self.affiliate_id])}")
            if status == 200 and isinstance(payload, dict):
                if not self.working_base:
                    self.working_base = base
                    print(f"  → working base 確定: {base}")
                out = PROBE_DIR / f"{name}.json"
                out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                print(f"  → 保存: {out.relative_to(ROOT)} / top-level keys: {sorted(payload.keys())}")
                return payload
            print(f"  → 失敗: {str(payload)[:200]}")
        return None


def main():
    env = load_env()
    app_id = env.get("RAKUTEN_APP_ID", "")
    access_key = env.get("RAKUTEN_ACCESS_KEY", "")
    affiliate_id = env.get("RAKUTEN_AFFILIATE_ID", "")
    if not app_id or not access_key:
        sys.exit("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定。~/Projects/yado-jouken/.env に両方を書いてください")
    if not affiliate_id:
        print("警告: RAKUTEN_AFFILIATE_ID 未設定。affiliateUrl 形式の確認はスキップされます")

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    p = Prober(app_id, access_key, affiliate_id)

    # 1) エリアマスタ(パス確定を兼ねる)
    p.probe("area_class", "Travel/GetAreaClass/20140210", {})

    # 2) 施設検索: 箱根で3件・responseType=large(全フィールド観察)
    simple = p.probe(
        "simple_hakone",
        "Travel/SimpleHotelSearch/20170426",
        {
            "largeClassCode": "japan",
            "middleClassCode": "kanagawa",
            "smallClassCode": "hakone",
            "hits": 3,
            "responseType": "large",
        },
    )

    # 3) squeezeCondition 候補の有効性(空室検索・直近の週末1泊で試す)
    #    有効値はエラー文言でわかる。候補は公式ドキュメント由来+推定を混在(A1で確定)
    for squeeze in ["pet", "onsen", "breakfast", "internet", "large_bath"]:
        p.probe(
            f"vacant_squeeze_{squeeze}",
            "Travel/VacantHotelSearch/20170426",
            {
                "largeClassCode": "japan",
                "middleClassCode": "kanagawa",
                "smallClassCode": "hakone",
                "checkinDate": env.get("PROBE_CHECKIN", "2026-08-01"),
                "checkoutDate": env.get("PROBE_CHECKOUT", "2026-08-02"),
                "adultNum": 2,
                "hits": 2,
                "squeezeCondition": squeeze,
            },
        )

    # 4) 施設詳細: 設備regexの対象フィールド観察(hotelNoはsimpleの1件目から)
    if simple:
        try:
            hotel_no = simple["hotels"][0]["hotel"][0]["hotelBasicInfo"]["hotelNo"]
            p.probe(
                "detail_first",
                "Travel/HotelDetailSearch/20170426",
                {"hotelNo": hotel_no, "responseType": "large"},
            )
        except (KeyError, IndexError) as e:
            print(f"[detail_first] hotelNo 抽出失敗({e}) — simple_hakone.json の構造を目視確認のこと")

    print("\n完了。data/probe/*.json を確認し、確定事項を docs/RUNBOOK.md へ転記する。")


if __name__ == "__main__":
    main()
