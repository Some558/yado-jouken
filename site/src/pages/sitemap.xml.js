import { getCollection } from 'astro:content';

export async function GET() {
  const site = 'https://yado-jouken.com';
  const pages = await getCollection('pages');
  const condHubs = await getCollection('conditionHubs');
  const areaHubs = await getCollection('areaHubs');
  const asOf = pages[0]?.data.dataAsOf ?? new Date().toISOString().split('T')[0];

  const urls = [
    { loc: `${site}/`, lastmod: asOf, changefreq: 'daily', priority: '1.0' },
    ...condHubs.map((h) => ({
      loc: `${site}/${h.data.condition.slug}/`,
      lastmod: h.data.dataAsOf, changefreq: 'daily', priority: '0.8',
    })),
    ...areaHubs.map((h) => ({
      loc: `${site}/area/${h.data.area.slug}/`,
      lastmod: h.data.dataAsOf, changefreq: 'daily', priority: '0.6',
    })),
    ...pages.map((p) => ({
      loc: `${site}/${p.data.condition.slug}/${p.data.area.slug}/`,
      lastmod: p.data.dataAsOf, changefreq: 'daily', priority: '0.8',
    })),
    { loc: `${site}/methodology/`, lastmod: asOf, changefreq: 'monthly', priority: '0.4' },
    { loc: `${site}/about/`, lastmod: asOf, changefreq: 'monthly', priority: '0.3' },
    { loc: `${site}/disclaimer/`, lastmod: asOf, changefreq: 'monthly', priority: '0.1' },
    { loc: `${site}/privacy/`, lastmod: asOf, changefreq: 'monthly', priority: '0.1' },
  ];

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls
  .map(
    (u) => `  <url>
    <loc>${u.loc}</loc>
    <lastmod>${u.lastmod}</lastmod>
    <changefreq>${u.changefreq}</changefreq>
    <priority>${u.priority}</priority>
  </url>`
  )
  .join('\n')}
</urlset>`;

  return new Response(xml, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
}
