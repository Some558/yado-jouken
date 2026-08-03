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
- ✅ **条件確定** → `pipeline/config/conditions.json`（現在9条件: squeeze系5 + facility系4。`dinner`=夕食付きを2026-08追加）
- ⚠️ **未解決**: affiliateId がポータル記載値とリンク作成ツール生成値で別値。ポータル値を採用中
- ✅ **アフィリ導線実測**(2026-08-03): `scripts/affiliate-healthcheck.py` で全URL形式OK。Referer=`yadoshibori.com` で hb.afl→pt.afl に302・トラッキングCookie付与を確認済み。**残作業**: 楽天アフィリエイト管理画面で「手動1クリック→成果/クリック計上」を人目で確認
- ⚠️ **ドメイン**: コード上の SITE_ORIGIN は `https://yadoshibori.com`。楽天アプリ側の Allowed websites を同ドメインへ更新するまでAPIは403(A5)

再実行方法: `.env` に RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / RAKUTEN_AFFILIATE_ID を書き `PYTHONPATH=pipeline python3 pipeline/rakuten_probe.py`(生レスポンスは data/probe/)

## A4 自動化(2026-07-10)

- **日次** `scripts/daily-refresh.sh` / `.github/workflows/daily.yml`
  - cron UTC `17 20 * * *` (= JST 05:17) + `workflow_dispatch`
  - fetch daily → transform → validate(fail-closed) → promote `data/staged`→`data/latest` → commit+push `data/latest/` のみ → Slack ✅/🔴
- **週次** `scripts/weekly-refresh.sh` / `.github/workflows/weekly.yml`
  - cron UTC `17 19 * * 6` (= JST 日曜 04:17) + `workflow_dispatch`
  - fetch daily+weekly → transform → validate → promote → commit `data/latest/` + `data/cache/squeeze.json` + `data/facilities/`
- ⚠️ GitHub Actions の `on.schedule` に `timezone:` キーは使えない(無効YAML扱いで workflow_dispatch も消える)。JSTはUTC換算で書くこと
- **squeeze正本**: `data/cache/squeeze.json`(git管理)。`data/work/` はローカル作業用でgitignore
- **ローカル確認(APIなし)**: `./scripts/daily-refresh.sh --skip-fetch` → `cd site && npm run build`
- **GHA実走前提**: GitHub secrets `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` / `RAKUTEN_AFFILIATE_ID` / `SLACK_WEBHOOK_URL` / `CLOUDFLARE_DEPLOY_HOOK_URL` + 楽天 Allowed websites=`yadoshibori.com`
- **ハーネス**: automation-harness.md §1 台帳 + §2.1 例外e(プラン承認で決裁済・A4で台帳追記)

## 運用

- 失敗時: commitされず前日データ維持+Slack 🔴。手動リカバリは Actions の workflow_dispatch
- API制約: 1req/sec(実装1.1s)・クレジット表記義務・2026-05-14旧API廃止済み
- Cloudflare Pages: 日次/週次は data を commit 後に **Deploy Hook** (`CLOUDFLARE_DEPLOY_HOOK_URL`) で Git ビルドを発火し、本番の `データ取得日` が揃うまで待機。短命の wrangler OAuth には依存しない。手動: CF Dashboard の Deploy Hook POST、または `cd site && npm run build && npx wrangler pages deploy dist --project-name=yado-jouken --branch=main`

## グロース運用（伸ばすときの手順）

1. **アフィリ健全性**: `PYTHONPATH=pipeline python3 scripts/affiliate-healthcheck.py`
2. **楽天側の人目確認**(初回/月次): 本番の「プランを見る」を1回クリック → 楽天アフィリエイト管理画面でクリック/経由が付くか確認
3. **Search Console**: https://search.google.com/search-console で `yadoshibori.com` を追加。所有確認は HTML タグ方式 → `content` 値だけを `site/src/config/site.json` の `googleSiteVerification` に入れてデプロイ。確認後に sitemap `https://yadoshibori.com/sitemap.xml` を送信
4. **ページ拡張**: 条件は `pipeline/config/conditions.json`、エリアは `areas.json`。squeeze新規は週次更新でキャッシュが埋まる。editorial は `site/src/content/editorial/`
5. **次の拡張候補**: エリア追加（強需要温泉地）→ 条件は `heyashoku` 等は語彙確認後

## 障害時の連絡先・確認先

- 楽天ウェブサービス: https://webservice.rakuten.co.jp/
- Cloudflare Pages ダッシュボード / GitHub Actions タブ / Slack `#auto-blog-ops`
