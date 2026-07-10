#!/usr/bin/env node
// dist/ 全HTML走査の法令・品質ゲート(fail-closed)。承認済みプラン §5 の最終防衛線。
// 1件でも欠落があれば exit 1 = ビルド失敗 = Cloudflare Pages は前回デプロイを維持する。
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const siteDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const dist = path.join(siteDir, 'dist');
const dataLatest = path.join(siteDir, '..', 'data', 'latest');

const PR_TEXT = '本サイトはアフィリエイト広告(楽天アフィリエイト)を利用しています';
const CREDIT_TEXT = 'Supported by Rakuten Developers';
const AFL_HOST = 'https://hb.afl.rakuten.co.jp/';
const AFL_ID_PATTERN = /hb\.afl\.rakuten\.co\.jp\/hgc\/[0-9a-f]{8}\.[0-9a-f]{8}\.[0-9a-f]{8}\./;
const affiliateId = process.env.RAKUTEN_AFFILIATE_ID ?? '';

const errors = [];
const crit = (msg) => { errors.push(msg); console.error(`CRIT: ${msg}`); };

function* walkHtml(dir) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) yield* walkHtml(p);
    else if (name.endsWith('.html')) yield p;
  }
}

if (!existsSync(dist)) {
  console.error('CRIT: dist/ がない(build未実行)');
  process.exit(1);
}

const htmlFiles = [...walkHtml(dist)];
const internalHrefs = new Map(); // href -> 参照元

for (const file of htmlFiles) {
  const rel = path.relative(dist, file);
  const html = readFileSync(file, 'utf-8');

  // 1. PR表記(ステマ規制)・クレジット表記の全ページ100%
  if (!html.includes(PR_TEXT)) crit(`PR表記なし: ${rel}`);
  if (!html.includes(CREDIT_TEXT)) crit(`楽天クレジット表記なし: ${rel}`);

  // 2. アフィリリンクの構造検査(affiliateId形式・rel=sponsored)
  for (const m of html.matchAll(/<a\s[^>]*href="([^"]+)"[^>]*>/g)) {
    const [tag, href] = m;
    if (href.startsWith(AFL_HOST)) {
      if (!AFL_ID_PATTERN.test(href)) crit(`affiliateId形式不正: ${rel}: ${href.slice(0, 80)}`);
      if (affiliateId && !href.includes(affiliateId)) crit(`affiliateId不一致: ${rel}`);
      if (!/rel="[^"]*sponsored[^"]*"/.test(tag)) crit(`rel=sponsoredなし: ${rel}: ${href.slice(0, 80)}`);
    } else if (href.startsWith('/')) {
      if (!internalHrefs.has(href)) internalHrefs.set(href, rel);
    }
  }
}

// 3. 内部リンク404ゼロ
for (const [href, from] of internalHrefs) {
  const clean = href.split('#')[0].split('?')[0];
  const target = clean.endsWith('/')
    ? path.join(dist, clean, 'index.html')
    : path.join(dist, clean);
  if (!existsSync(target)) crit(`内部リンク切れ: ${href} (参照元: ${from})`);
}

// 4. ページ数の完全一致(データページ+ハブ+静的5+トップ)
const meta = JSON.parse(readFileSync(path.join(dataLatest, 'meta.json'), 'utf-8'));
const hubCount = readdirSync(path.join(dataLatest, 'hubs')).filter((f) => f.endsWith('.json')).length;
const expected = meta.generatedPages + hubCount + 5; // 静的=top/about/methodology/privacy/disclaimer
if (htmlFiles.length !== expected) {
  crit(`ページ数不一致: dist=${htmlFiles.length} 期待=${expected}(pages${meta.generatedPages}+hubs${hubCount}+静的5)`);
}

// 5. sitemap存在
if (!existsSync(path.join(dist, 'sitemap.xml'))) crit('sitemap.xml がない');

if (errors.length) {
  console.error(`\npostbuild-check: CRIT ${errors.length}件 → ビルド失敗(fail-closed)`);
  process.exit(1);
}
console.log(`postbuild-check: 全緑 (html=${htmlFiles.length}ページ・内部リンク${internalHrefs.size}本・アフィリ検査済み)`);
