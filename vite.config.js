import { defineConfig } from 'vite'

// Relative base so the built `dist/` is fully portable — it can be opened from
// any static host (or `vite preview`) without path rewrites.
export default defineConfig({
  base: './',
  build: {
    target: 'es2020',
    outDir: 'dist',
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    host: true,
    port: 5173,
  },
})
