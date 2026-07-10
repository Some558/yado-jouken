# ヤドシボリ RUNBOOK

ヤドシボリ(完全自動・楽天トラベルAPI×楽天アフィリエイト)。設計SSoT = 承認済みプラン(2026-07-10)。

## A1 確定事項(2026-07-10 実測完了・fetch.py実装の前提)

- ✅ **エンドポイント**: `https://openapi.rakuten.co.jp/engine/api/Travel/<API名>/<版>`(旧 `/services/api/` は404)。版=SimpleHotelSearch/VacantHotelSearch/HotelDetailSearch は `20170426`、GetAreaClass のみ `20140210`
- ✅ **認証**: `applicationId`(UUID36字)+`accessKey`(46字トークン)の2点をクエリで渡す。**さらに `Referer` と `Origin` の両ヘッダー必須**(値=アプリ登録の Allowed websites ドメイン `https://yadoshibori.com`)。欠けると403 `REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING`
- ✅ **アプリ有効期限**: 2027-07-10 失効(新ポータルは1年更新制)→ **失効1ヶ月前の更新をルーティン化すること**
- ✅ **affiliateUrl**: `affiliateId` パラメータを渡すと hotelInformationUrl / planListUrl / reviewUrl 等が `https://hb.afl.rakuten.co.jp/hgc/<affiliateId>/...` に自動変換される(実測確認済み)
- ✅ **エリアコード**: 10エリア確定 → `pipeline/config/areas.json`(同名別地域に注意: 熱海=shizuoka・草津=gunma・軽井沢=nagano/karui)
- ✅ **squeezeCondition 有効値**: `kinen / internet / daiyoku / onsen / breakfast / dinner` の6つのみ(公式Doc+実測。pet/large_bath等は400)。VacantHotelSearch 専用で checkinDate/checkoutDate 必須 → 週次更新は「翌週末1泊」のローリング日付で叩く
- ✅ **設備判定**: HotelDetailSearch(responseType=large)の `hotelFacilitiesInfo.hotelFacilities[].item` と `aboutBath[].bathType` が**標準化語彙の構造化データ**(自由文regex不要・完全一致でよい)。実測例: 大浴場/サウナ/露天風呂/禁煙ルーム/家族風呂/温泉/天然温泉
- ✅ **8条件確定** → `pipeline/config/conditions.json`(squeeze系4+facility系4。ペット可・貸切風呂の表記はA2の全施設語彙ダンプで最終確認)
- ⚠️ **未解決**: affiliateId がポータル記載値とリンク作成ツール生成値で別値。ポータル値を採用中。公開前検証の「手動クリック→計上確認」で実効性を判定する
- ⚠️ **ドメイン**: コード上の SITE_ORIGIN / Allowed websites は `https://yadoshibori.com`。楽天アプリ側の Allowed websites を同ドメインへ更新するまでAPIは403になる(A5作業)

再実行方法: `.env` に RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / RAKUTEN_AFFILIATE_ID を書き `python3 pipeline/rakuten_probe.py`(生レスポンスは data/probe/)

## 運用(構築完了後にここへ追記)

- 日次: GitHub Actions cron JST 05:17 → fetch → validate(fail-closed) → commit → Cloudflare Pages
- 失敗時: commitされず前日データ維持+Slack 🔴 通知。手動リカバリは Actions の workflow_dispatch
- API制約: 1req/sec(実装1.1s)・クレジット表記義務・2026-05-14旧API廃止済み

## 障害時の連絡先・確認先

- 楽天ウェブサービス: https://webservice.rakuten.co.jp/
- Cloudflare Pages ダッシュボード / GitHub Actions タブ / Slack 通知チャンネル
