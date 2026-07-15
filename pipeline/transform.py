#!/usr/bin/env python3
"""data/work + data/facilities → 公開ページ用JSON(data/staged/)。決定的コードのみ。

出力:
  data/staged/pages/{condition}--{area}.json  本体ページ(条件×エリア)
  data/staged/hubs/{condition}.json           条件ハブ(エリア横比較)
  data/staged/hubs/area--{area}.json          エリアハブ(条件別件数)
  data/staged/meta.json                       更新時刻・ページ一覧・集計

方針(承認済みプラン):
- 施設3件未満の条件×エリアはページを生成しない(情報ゲインゼロ回避)
- squeeze系条件の分母=エリア総数(recordCount)・該当数=squeeze recordCount(全量の真値)
- facility系条件は掲載上位(daily取得分)に対する判定なので、分母=掲載施設数として明示
"""

import argparse
import datetime
import json
import pathlib
import statistics
import sys

from rakuten_client import ROOT

CONFIG = ROOT / "pipeline" / "config"
WORK = ROOT / "data" / "work"
CACHE = ROOT / "data" / "cache"
FACILITIES = ROOT / "data" / "facilities"

HOTELS_PER_PAGE = 20
MIN_HOTELS_PER_PAGE = 3

HOTEL_FIELDS = {
    "hotelNo": "hotelNo",
    "name": "hotelName",
    "kana": "hotelKanaName",
    "minCharge": "hotelMinCharge",
    "reviewAverage": "reviewAverage",
    "reviewCount": "reviewCount",
    "access": "access",
    "address1": "address1",
    "address2": "address2",
    "infoUrl": "hotelInformationUrl",
    "planUrl": "planListUrl",
    "reviewUrl": "reviewUrl",
    "thumbnail": "hotelThumbnailUrl",
    "imageUrl": "hotelImageUrl",
    "special": "hotelSpecial",
}


def load_json(path: pathlib.Path):
    return json.loads(path.read_text())


def facility_match(cond: dict, fac: dict) -> bool:
    values = set(cond["detect"]["values"])
    pool = set(fac.get("hotelFacilities") or [])
    if "aboutBath.bathType" in cond["detect"]["fields"]:
        pool |= {v for v in (fac.get("aboutBath") or []) if isinstance(v, str)}
    return bool(pool & values)


def slim_hotel(h: dict) -> dict:
    return {k: h.get(src) for k, src in HOTEL_FIELDS.items()}


def price_stats(hotels: list[dict]) -> dict:
    prices = [h["minCharge"] for h in hotels if isinstance(h.get("minCharge"), int) and h["minCharge"] > 0]
    reviews = [h["reviewAverage"] for h in hotels if h.get("reviewAverage")]
    return {
        "minPrice": min(prices) if prices else None,
        "medianPrice": int(statistics.median(prices)) if prices else None,
        "reviewAvgMean": round(statistics.mean(reviews), 2) if reviews else None,
        "pricedCount": len(prices),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(ROOT / "data" / "staged"))
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args()
    as_of = args.as_of or datetime.date.today().isoformat()

    staged = pathlib.Path(args.out)
    pages_dir = staged / "pages"
    hubs_dir = staged / "hubs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    hubs_dir.mkdir(parents=True, exist_ok=True)

    areas = load_json(CONFIG / "areas.json")["areas"]
    conditions = load_json(CONFIG / "conditions.json")["conditions"]
    squeeze_path = CACHE / "squeeze.json"
    if not squeeze_path.exists():
        squeeze_path = WORK / "squeeze.json"
    if not squeeze_path.exists():
        sys.exit("FATAL: squeeze.json がない(先に --mode weekly を実行するか data/cache/squeeze.json を配置)")
    squeeze = load_json(squeeze_path)

    # 施設キャッシュ読込
    fac_cache: dict[int, dict] = {}
    for f in FACILITIES.glob("*.json"):
        d = load_json(f)
        fac_cache[d["hotelNo"]] = d

    daily: dict[str, dict] = {}
    for area in areas:
        p = WORK / "daily" / f"{area['slug']}.json"
        if not p.exists():
            sys.exit(f"daily未取得: {p}")
        daily[area["slug"]] = load_json(p)

    written, skipped, page_index = [], [], {}
    for cond in conditions:
        cond_pages = []
        for area in areas:
            d = daily[area["slug"]]
            listed = d["hotels"]
            if cond["detect"]["method"] == "squeeze":
                sq = squeeze["areas"][area["slug"]][cond["slug"]]
                matched_nos = set(sq["hotelNos"])
                match_count = sq["recordCount"]
                denominator = {"type": "area_total", "value": d["recordCount"],
                               "label": f"{area['officialName']}エリアの掲載施設数"}
                note = {"type": "vacant",
                        "checkinDate": squeeze["checkinDate"],
                        "checkoutDate": squeeze["checkoutDate"]}
            else:
                matched_nos = {
                    h["hotelNo"] for h in listed
                    if h["hotelNo"] in fac_cache and facility_match(cond, fac_cache[h["hotelNo"]])
                }
                match_count = len(matched_nos)
                denominator = {"type": "listed_top", "value": len(listed),
                               "label": "当サイト掲載施設(人気上位)数"}
                note = {"type": "facility"}

            hotels = [slim_hotel(h) for h in listed if h["hotelNo"] in matched_nos]
            hotels.sort(key=lambda h: (-(h["reviewAverage"] or 0), h["minCharge"] or 10**9))
            if len(hotels) < MIN_HOTELS_PER_PAGE:
                skipped.append(f"{cond['slug']}--{area['slug']} ({len(hotels)}件)")
                continue

            stats = price_stats(hotels)
            rate = round(100 * match_count / denominator["value"], 1) if denominator["value"] else None
            page = {
                "slug": f"{cond['slug']}--{area['slug']}",
                "condition": {"slug": cond["slug"], "label": cond["label"]},
                "area": {k: area[k] for k in ("slug", "label", "officialName")},
                "dataAsOf": as_of,
                "detection": note,
                "stats": {"matchCount": match_count, "matchRate": rate,
                          "denominator": denominator, **stats},
                "hotels": hotels[:HOTELS_PER_PAGE],
                "listedMatchCount": len(hotels),
            }
            (pages_dir / f"{page['slug']}.json").write_text(
                json.dumps(page, ensure_ascii=False, indent=1)
            )
            written.append(page["slug"])
            cond_pages.append({
                "area": page["area"], "slug": page["slug"],
                "matchCount": match_count, "matchRate": rate,
                "minPrice": stats["minPrice"], "medianPrice": stats["medianPrice"],
                "reviewAvgMean": stats["reviewAvgMean"],
            })
        if cond_pages:
            (hubs_dir / f"{cond['slug']}.json").write_text(json.dumps({
                "condition": {"slug": cond["slug"], "label": cond["label"]},
                "dataAsOf": as_of, "areas": cond_pages,
            }, ensure_ascii=False, indent=1))
            page_index[cond["slug"]] = [p["slug"] for p in cond_pages]

    for area in areas:
        entries = [
            {"condition": c["slug"], "label": c["label"], "slug": f"{c['slug']}--{area['slug']}"}
            for c in conditions if f"{c['slug']}--{area['slug']}" in written
        ]
        if not entries:
            continue

        # エリア内AND絞り込み用: 掲載宿ごとに該当条件slugを付与
        listed = daily[area["slug"]]["hotels"]
        cond_nos: dict[str, set[int]] = {}
        for cond in conditions:
            if f"{cond['slug']}--{area['slug']}" not in written:
                continue
            if cond["detect"]["method"] == "squeeze":
                cond_nos[cond["slug"]] = set(squeeze["areas"][area["slug"]][cond["slug"]]["hotelNos"])
            else:
                cond_nos[cond["slug"]] = {
                    h["hotelNo"] for h in listed
                    if h["hotelNo"] in fac_cache and facility_match(cond, fac_cache[h["hotelNo"]])
                }
        area_hotels = []
        for h in listed:
            flags = [s for s, nos in cond_nos.items() if h["hotelNo"] in nos]
            row = slim_hotel(h)
            row["conditions"] = flags
            area_hotels.append(row)
        area_hotels.sort(key=lambda h: (-(h["reviewAverage"] or 0), h["minCharge"] or 10**9))

        (hubs_dir / f"area--{area['slug']}.json").write_text(json.dumps({
            "area": {k: area[k] for k in ("slug", "label", "officialName")},
            "dataAsOf": as_of,
            "recordCount": daily[area["slug"]]["recordCount"],
            "conditions": entries,
            "hotels": area_hotels,
        }, ensure_ascii=False, indent=1))

    (staged / "meta.json").write_text(json.dumps({
        "dataAsOf": as_of,
        "generatedPages": len(written),
        "skippedThin": skipped,
        "areaTotals": {a["slug"]: daily[a["slug"]]["recordCount"] for a in areas},
        "facilityCacheCount": len(fac_cache),
    }, ensure_ascii=False, indent=1))

    print(f"pages={len(written)} skipped_thin={len(skipped)} → {staged}")
    for s in skipped:
        print(f"  skip: {s}")


if __name__ == "__main__":
    main()
