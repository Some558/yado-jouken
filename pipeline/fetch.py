#!/usr/bin/env python3
"""データ取得。決定的コードのみ・LLM不使用。

daily  : エリアごとに SimpleHotelSearch 3ページ(90施設) → data/work/daily/{area}.json
weekly : (1) squeeze系4条件 × 10エリアの該当hotelNo集合(VacantHotelSearch・翌週土曜1泊)
         (2) daily一覧の全施設の設備キャッシュ(HotelDetailSearch・7日超のみ再取得)
         → data/cache/squeeze.json(+ data/work/squeeze.json) / data/facilities/{hotelNo}.json

usage: PYTHONPATH=pipeline python3 pipeline/fetch.py --mode daily|weekly [--areas hakone,kusatsu] [--as-of 2026-07-10]
"""

import argparse
import datetime
import json
import pathlib
import sys
import time

from rakuten_client import ROOT, RakutenClient

CONFIG = ROOT / "pipeline" / "config"
WORK = ROOT / "data" / "work"
CACHE = ROOT / "data" / "cache"
FACILITIES = ROOT / "data" / "facilities"

DAILY_PAGES = 3          # 30件×3=上位90施設
SQUEEZE_MAX_PAGES = 10   # squeeze該当の全量把握は最大300施設まで
FACILITY_TTL_DAYS = 7


def load_areas(only: set[str] | None):
    areas = json.loads((CONFIG / "areas.json").read_text())["areas"]
    return [a for a in areas if not only or a["slug"] in only]


def load_conditions():
    return json.loads((CONFIG / "conditions.json").read_text())["conditions"]


def area_params(area: dict) -> dict:
    return {
        "largeClassCode": "japan",
        "middleClassCode": area["middleClassCode"],
        "smallClassCode": area["smallClassCode"],
    }


def next_saturday(as_of: datetime.date) -> datetime.date:
    days = (5 - as_of.weekday()) % 7
    return as_of + datetime.timedelta(days=days or 7)


def fetch_daily(client: RakutenClient, areas: list[dict]) -> None:
    out_dir = WORK / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    for area in areas:
        hotels, record_count = [], 0
        for page in range(1, DAILY_PAGES + 1):
            res = client.search(
                "Travel/SimpleHotelSearch/20170426",
                {**area_params(area), "hits": 30, "page": page, "responseType": "small"},
            )
            if res is None:
                break
            record_count = res["pagingInfo"]["recordCount"]
            for h in res.get("hotels", []):
                hotels.append(h["hotel"][0]["hotelBasicInfo"])
            if page >= res["pagingInfo"]["pageCount"]:
                break
        (out_dir / f"{area['slug']}.json").write_text(
            json.dumps(
                {"area": area["slug"], "recordCount": record_count, "hotels": hotels},
                ensure_ascii=False,
            )
        )
        print(f"[daily] {area['slug']}: {len(hotels)}件取得 / エリア総数{record_count}")


def fetch_squeeze(client: RakutenClient, areas: list[dict], as_of: datetime.date) -> None:
    conds = [c for c in load_conditions() if c["detect"]["method"] == "squeeze"]
    checkin = next_saturday(as_of)
    checkout = checkin + datetime.timedelta(days=1)
    result = {
        "checkinDate": checkin.isoformat(),
        "checkoutDate": checkout.isoformat(),
        "areas": {},
    }
    for area in areas:
        result["areas"][area["slug"]] = {}
        for cond in conds:
            hotel_nos, record_count = [], 0
            for page in range(1, SQUEEZE_MAX_PAGES + 1):
                res = client.search(
                    "Travel/VacantHotelSearch/20170426",
                    {
                        **area_params(area),
                        "checkinDate": checkin.isoformat(),
                        "checkoutDate": checkout.isoformat(),
                        "adultNum": 2,
                        "hits": 30,
                        "page": page,
                        "responseType": "small",
                        "squeezeCondition": cond["detect"]["value"],
                    },
                )
                if res is None:
                    break
                record_count = res["pagingInfo"]["recordCount"]
                for h in res.get("hotels", []):
                    hotel_nos.append(h["hotel"][0]["hotelBasicInfo"]["hotelNo"])
                if page >= res["pagingInfo"]["pageCount"]:
                    break
            result["areas"][area["slug"]][cond["slug"]] = {
                "recordCount": record_count,
                "hotelNos": hotel_nos,
            }
            print(f"[squeeze] {area['slug']}×{cond['slug']}: 該当{record_count}件")
    WORK.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False)
    (WORK / "squeeze.json").write_text(payload)
    (CACHE / "squeeze.json").write_text(payload)  # CI/日次用にgit管理する正本


def fetch_facilities(client: RakutenClient, areas: list[dict], as_of: datetime.date) -> None:
    FACILITIES.mkdir(parents=True, exist_ok=True)
    targets = []
    for area in areas:
        daily_file = WORK / "daily" / f"{area['slug']}.json"
        if not daily_file.exists():
            sys.exit(f"daily未取得: {daily_file}(先に --mode daily を実行)")
        for h in json.loads(daily_file.read_text())["hotels"]:
            targets.append(h["hotelNo"])
    fetched = skipped = failed = 0
    for hotel_no in dict.fromkeys(targets):  # 順序維持で重複排除
        cache = FACILITIES / f"{hotel_no}.json"
        if cache.exists():
            cached = json.loads(cache.read_text())
            age = (as_of - datetime.date.fromisoformat(cached["fetchedAt"])).days
            if age < FACILITY_TTL_DAYS:
                skipped += 1
                continue
        res = client.search(
            "Travel/HotelDetailSearch/20170426",
            {"hotelNo": hotel_no, "responseType": "large"},
        )
        if res is None:
            failed += 1
            continue
        blocks = {}
        for part in res["hotels"][0]["hotel"]:
            blocks.update(part)
        fac = blocks.get("hotelFacilitiesInfo", {})
        cache.write_text(
            json.dumps(
                {
                    "hotelNo": hotel_no,
                    "fetchedAt": as_of.isoformat(),
                    "hotelFacilities": [x.get("item") for x in fac.get("hotelFacilities") or []],
                    "aboutBath": [
                        v for x in (fac.get("aboutBath") or []) for v in x.values()
                    ],
                },
                ensure_ascii=False,
            )
        )
        fetched += 1
        if fetched % 50 == 0:
            print(f"[facility] {fetched}件取得済み...")
    print(f"[facility] 取得{fetched} / キャッシュ利用{skipped} / 取得不可{failed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["daily", "weekly"])
    ap.add_argument("--areas", default="")
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args()

    as_of = (
        datetime.date.fromisoformat(args.as_of)
        if args.as_of
        else datetime.date.today()
    )
    only = set(args.areas.split(",")) if args.areas else None
    areas = load_areas(only)
    client = RakutenClient()
    started = time.monotonic()

    if args.mode == "daily":
        fetch_daily(client, areas)
    else:
        fetch_squeeze(client, areas, as_of)
        fetch_facilities(client, areas, as_of)

    mins = (time.monotonic() - started) / 60
    print(
        f"完了 mode={args.mode} requests={client.request_count} "
        f"所要{mins:.1f}分 最小間隔={client.min_observed_interval and round(client.min_observed_interval, 3)}s"
    )
    if client.min_observed_interval is not None and client.min_observed_interval < 1.0:
        sys.exit("FATAL: リクエスト間隔が1.0sを下回った(規約違反リスク)")


if __name__ == "__main__":
    main()
