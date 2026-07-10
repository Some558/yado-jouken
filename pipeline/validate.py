#!/usr/bin/env python3
"""fail-closed 機械ゲート。非0終了=公開中止(前日データ維持)。

検査(承認済みプラン §4):
 1. 全ページの JSON Schema 適合
 2. 件数下限: 生成ページ数 >= 前回の80% / エリア総施設数合計 >= 300
 3. 価格異常値: minCharge <= 0 または > 1,000,000 の混入ゼロ
 4. アフィリURL: infoUrl/planUrl 全件が hb.afl.rakuten.co.jp かつ(環境にIDがあれば)affiliateId を含む
 5. 前回比: ページ中央値価格の変動 > 30% があれば fail(初回はスキップ)

usage: python3 pipeline/validate.py [staged_dir] [--baseline data/latest]
"""

import argparse
import json
import pathlib
import sys

from rakuten_client import ROOT, load_env

try:
    import jsonschema
except ImportError:
    sys.exit("FATAL: jsonschema 未インストール (pip install -r pipeline/requirements.txt)")

SCHEMA = json.loads((ROOT / "pipeline" / "schema" / "page.schema.json").read_text())
MIN_TOTAL_HOTELS = 300
PAGE_KEEP_RATIO = 0.8
MEDIAN_DIVERGENCE = 0.30

errors: list[str] = []


def err(msg: str):
    errors.append(msg)
    print(f"CRIT: {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("staged", nargs="?", default=str(ROOT / "data" / "staged"))
    ap.add_argument("--baseline", default=str(ROOT / "data" / "latest"))
    args = ap.parse_args()
    staged = pathlib.Path(args.staged)
    baseline = pathlib.Path(args.baseline)

    meta_path = staged / "meta.json"
    if not meta_path.exists():
        sys.exit("FATAL: meta.json がない(transform未実行)")
    meta = json.loads(meta_path.read_text())
    pages = sorted((staged / "pages").glob("*.json"))

    # 1. Schema
    validator = jsonschema.Draft202012Validator(SCHEMA)
    page_data = {}
    for p in pages:
        d = json.loads(p.read_text())
        page_data[p.stem] = d
        for e in validator.iter_errors(d):
            err(f"schema {p.name}: {e.json_path}: {e.message[:100]}")

    # 2. 件数下限
    total_hotels = sum(meta["areaTotals"].values())
    if total_hotels < MIN_TOTAL_HOTELS:
        err(f"エリア総施設数 {total_hotels} < {MIN_TOTAL_HOTELS}")
    prev_meta_path = baseline / "meta.json"
    prev_pages = None
    if prev_meta_path.exists():
        prev_pages = json.loads(prev_meta_path.read_text())["generatedPages"]
        if len(pages) < prev_pages * PAGE_KEEP_RATIO:
            err(f"生成ページ数 {len(pages)} < 前回{prev_pages}の80%")

    # 3. 価格異常値
    for slug, d in page_data.items():
        for h in d["hotels"]:
            c = h.get("minCharge")
            if c is not None and (c <= 0 or c > 1_000_000):
                err(f"価格異常 {slug} hotelNo={h['hotelNo']}: {c}")

    # 4. アフィリURL
    affiliate_id = load_env().get("RAKUTEN_AFFILIATE_ID", "")
    for slug, d in page_data.items():
        for h in d["hotels"]:
            for key in ("infoUrl", "planUrl"):
                url = h.get(key) or ""
                if not url.startswith("https://hb.afl.rakuten.co.jp/"):
                    err(f"非アフィリURL {slug} hotelNo={h['hotelNo']} {key}")
                elif affiliate_id and affiliate_id not in url:
                    err(f"affiliateId不一致 {slug} hotelNo={h['hotelNo']} {key}")

    # 5. 前回比の中央値ダイバージェンス
    if baseline.exists():
        for slug, d in page_data.items():
            prev_file = baseline / "pages" / f"{slug}.json"
            if not prev_file.exists():
                continue
            prev = json.loads(prev_file.read_text())
            a, b = prev["stats"]["medianPrice"], d["stats"]["medianPrice"]
            if a and b and abs(b - a) / a > MEDIAN_DIVERGENCE:
                err(f"中央値価格が{MEDIAN_DIVERGENCE:.0%}超変動 {slug}: {a}→{b}")

    if errors:
        print(f"\nvalidate: CRIT {len(errors)}件 → 公開中止(fail-closed)")
        sys.exit(1)
    baseline_note = f"前回{prev_pages}p比OK" if prev_pages is not None else "初回(前回比スキップ)"
    print(f"validate: 全緑 pages={len(pages)} hotels合計={total_hotels} {baseline_note}")


if __name__ == "__main__":
    main()
