# yado-jouken RUNBOOK

宿泊条件検索DB(完全自動・楽天トラベルAPI×楽天アフィリエイト)。設計SSoT = 承認済みプラン(2026-07-10)。

## A1 素振りで確定させる事項(未確定のうちは実装を進めない)

- [ ] 新ドメイン `openapi.rakuten.co.jp` での正確なエンドポイントパス(旧 app.rakuten.co.jp との差分)
- [ ] GetAreaClass による初期10エリアの4階層コード → `pipeline/config/areas.json` の null を埋める
- [ ] SimpleHotelSearch レスポンスの affiliateUrl 形式(affiliateId 含有の確認方法)
- [ ] squeezeCondition の実有効値(pet / onsen / breakfast / internet / large_bath 候補の生死)
- [ ] HotelDetailSearch の設備テキストフィールドの所在(facility_regex 条件の対象)
- [ ] 条件11候補 → 8個への絞り込み(判定確度と検索需要で選定)

実行方法: `.env` に `RAKUTEN_APP_ID=` / `RAKUTEN_AFFILIATE_ID=` を書き `python3 pipeline/rakuten_probe.py`

## 運用(構築完了後にここへ追記)

- 日次: GitHub Actions cron JST 05:17 → fetch → validate(fail-closed) → commit → Cloudflare Pages
- 失敗時: commitされず前日データ維持+Slack 🔴 通知。手動リカバリは Actions の workflow_dispatch
- API制約: 1req/sec(実装1.1s)・クレジット表記義務・2026-05-14旧API廃止済み

## 障害時の連絡先・確認先

- 楽天ウェブサービス: https://webservice.rakuten.co.jp/
- Cloudflare Pages ダッシュボード / GitHub Actions タブ / Slack 通知チャンネル
