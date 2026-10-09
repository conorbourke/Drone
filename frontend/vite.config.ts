import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the FastAPI backend so cookies stay same-origin.
// In production the backend serves frontend/dist itself (hashed assets under /assets).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    emptyOutDir: true,
    // three.js lives in its own lazily loaded chunk (the 3D view, ~570 kB, ~140 kB gzip);
    // the warning threshold is raised just above it so a growing main bundle still warns.
    chunkSizeWarningLimit: 600,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
