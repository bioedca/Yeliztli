import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Backend API port the dev server proxies `/api` to. Defaults to 8000; override
// with VITE_API_PORT (set by `make dev API_PORT=…`) to dodge a busy :8000 — e.g.
// the WSL2 case where a foreign Windows process holds 8000. `.env*` files are not
// loaded during config evaluation, so this reads the process env directly.
const apiPort = process.env.VITE_API_PORT || '8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
})
