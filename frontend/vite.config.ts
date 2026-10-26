import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  build: {
    // FastAPI serves the built page from app/static (not committed: built by `npm run build` or the Dockerfile)
    outDir: '../app/static',
    emptyOutDir: true,
  },
  server: {
    // In development, send API calls to the FastAPI server
    proxy: { '/v1': 'http://localhost:8000', '/health': 'http://localhost:8000' },
  },
})
