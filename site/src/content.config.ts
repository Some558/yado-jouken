import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// data/latest のスキーマ違反はビルド失敗にする(validate.py に続く二重ゲート)

const hotelSchema = z.object({
  hotelNo: z.number(),
  name: z.string().min(1),
  kana: z.string().nullable(),
  minCharge: z.number().nullable(),
  reviewAverage: z.number().nullable(),
  reviewCount: z.number().nullable(),
  access: z.string().nullable(),
  address1: z.string().nullable(),
  address2: z.string().nullable(),
  infoUrl: z.string().startsWith('https://hb.afl.rakuten.co.jp/'),
  planUrl: z.string().startsWith('https://hb.afl.rakuten.co.jp/'),
  reviewUrl: z.string().nullable(),
  thumbnail: z.string().nullable(),
  imageUrl: z.string().nullable(),
  special: z.string().nullable(),
});

const statsSchema = z.object({
  matchCount: z.number().int().positive(),
  matchRate: z.number().nullable(),
  denominator: z.object({
    type: z.enum(['area_total', 'listed_top']),
    value: z.number().int().positive(),
    label: z.string().min(1),
  }),
  minPrice: z.number().nullable(),
  medianPrice: z.number().nullable(),
  reviewAvgMean: z.number().nullable(),
  pricedCount: z.number().int(),
});

const conditionRef = z.object({ slug: z.string(), label: z.string().min(1) });
const areaRef = z.object({
  slug: z.string(),
  label: z.string().min(1),
  officialName: z.string().min(1),
});

const pages = defineCollection({
  loader: glob({ pattern: '*.json', base: '../data/latest/pages' }),
  schema: z.object({
    slug: z.string().regex(/^[a-z0-9-]+--[a-z0-9-]+$/),
    condition: conditionRef,
    area: areaRef,
    dataAsOf: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    detection: z.object({
      type: z.enum(['vacant', 'facility']),
      checkinDate: z.string().optional(),
      checkoutDate: z.string().optional(),
    }),
    stats: statsSchema,
    hotels: z.array(hotelSchema).min(3).max(20),
    listedMatchCount: z.number().int().min(3),
  }),
});

const conditionHubs = defineCollection({
  loader: glob({ pattern: ['*.json', '!area--*.json'], base: '../data/latest/hubs' }),
  schema: z.object({
    condition: conditionRef,
    dataAsOf: z.string(),
    areas: z.array(
      z.object({
        area: areaRef,
        slug: z.string(),
        matchCount: z.number(),
        matchRate: z.number().nullable(),
        minPrice: z.number().nullable(),
        medianPrice: z.number().nullable(),
        reviewAvgMean: z.number().nullable(),
      })
    ),
  }),
});

const areaHubs = defineCollection({
  loader: glob({ pattern: 'area--*.json', base: '../data/latest/hubs' }),
  schema: z.object({
    area: areaRef,
    dataAsOf: z.string(),
    recordCount: z.number(),
    conditions: z.array(
      z.object({ condition: z.string(), label: z.string(), slug: z.string() })
    ),
    hotels: z
      .array(
        hotelSchema.extend({
          conditions: z.array(z.string()),
        })
      )
      .default([]),
  }),
});

const editorial = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/editorial' }),
  schema: z.object({ title: z.string() }),
});

export const collections = { pages, conditionHubs, areaHubs, editorial };
