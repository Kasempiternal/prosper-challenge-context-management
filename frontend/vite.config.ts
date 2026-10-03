/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = 'http://localhost:7860'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/start': backend,
      '/sessions': backend,
      '/status': backend,
    },
  },
  test: {
    setupFiles: ['./src/test/setup.ts'],
  },
})
