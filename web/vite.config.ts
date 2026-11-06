import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { '/api': 'http://localhost:3000' } },
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: { environment: 'jsdom', include: ['src/**/*.test.ts'] },
});
