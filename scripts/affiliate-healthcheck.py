#!/usr/bin/env python3
"""アフィリリンク健全性チェック。

検査:
  1. data/latest 全ページの infoUrl/planUrl が hb.afl.rakuten.co.jp かつ affiliateId を含む
  2. サンプル1件を Referer=yadoshibori.com で辿り、楽天アフィリ経由のリダイレクトを確認

usage: PYTHONPATH=pipeline python3 scripts/affiliate-healthcheck.py
終了コード: 0=全緑 / 1=CRIT
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from rakuten_client import load_env  # noqa: E402

SITE = "https://yadoshibori.com"
AFF_HOST = "https://hb.afl.rakuten.co.jp/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open_no_redirect(url: str):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "yadoshibori-affiliate-healthcheck/1.0",
            "Referer": f"{SITE}/",
        },
    )
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), ""
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.headers.get("Location") or ""


def main() -> int:
    env = load_env()
    aff_id = env.get("RAKUTEN_AFFILIATE_ID", "")
    if not aff_id:
        print("CRIT: RAKUTEN_AFFILIATE_ID 未設定")
        return 1

    pages = sorted((ROOT / "data" / "latest" / "pages").glob("*.json"))
    if not pages:
        print("CRIT: data/latest/pages が空")
        return 1

    errors: list[str] = []
    ok = 0
    sample_url = None
    for p in pages:
        d = json.loads(p.read_text())
        for h in d.get("hotels") or []:
            for key in ("infoUrl", "planUrl"):
                url = h.get(key) or ""
                if not url.startswith(AFF_HOST):
                    errors.append(f"非アフィリ {p.stem} {key}")
                elif aff_id not in url:
                    errors.append(f"affiliateId不一致 {p.stem} {key}")
                else:
                    ok += 1
                    if sample_url is None and key == "planUrl":
                        sample_url = url

    print(f"url_ok={ok} pages={len(pages)} affiliate_id_len={len(aff_id)}")
    if errors:
        for e in errors[:20]:
            print(f"CRIT: {e}")
        print(f"CRIT: {len(errors)}件")
        return 1

    if not sample_url:
        print("CRIT: サンプルURLなし")
        return 1

    status, headers, loc = _open_no_redirect(sample_url)
    print(f"sample_status={status}")
    if status not in (301, 302, 303, 307, 308) or not loc:
        print(f"CRIT: アフィリ入口がリダイレクトしない status={status}")
        return 1
    host = loc.split("/")[2] if loc.startswith("http") else loc
    print(f"sample_location_host={host[:80]}")
    if "rakuten.co.jp" not in host:
        print(f"CRIT: リダイレクト先が楽天系でない: {loc[:120]}")
        return 1

    status2, headers2, loc2 = _open_no_redirect(loc)
    print(f"hop2_status={status2}")
    tracking = headers2.get("X-RT-TRACKING-STATUS") or headers.get("X-RT-TRACKING-STATUS")
    if tracking:
        print(f"tracking_status={tracking}")
    cookie = headers2.get("Set-Cookie") or ""
    if "tg_af" in cookie:
        print("affiliate_cookie=1")
    if status2 not in (200, 301, 302, 303, 307, 308):
        print(f"CRIT: 2段目ステータス異常 {status2}")
        return 1

    print("affiliate-healthcheck: 全緑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
