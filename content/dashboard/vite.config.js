import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/content': {
        target: 'http://127.0.0.1:9810',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:9810',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
