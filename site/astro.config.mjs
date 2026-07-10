import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://yado-jouken.com',
  output: 'static',
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
