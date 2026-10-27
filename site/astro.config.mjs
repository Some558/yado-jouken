import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';

export default defineConfig({
  site: 'https://yadoshibori.com',
  output: 'static',
  compressHTML: true,
  markdown: { processor: unified() },
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  vite: {
    server: {
      fs: {
        // data/latest (siteルート外) を読むため
        allow: ['..'],
      },
    },
  },
});
