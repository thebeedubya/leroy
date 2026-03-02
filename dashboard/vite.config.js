import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Leroy A2A server token -- dashboard is local-only, no public exposure
const LEROY_TOKEN = 'LEROY_A2A_TOKEN_REDACTED'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // All /api/* requests → Leroy A2A server on 9800
      // Strips /api prefix and injects Bearer token
      '/api': {
        target: 'http://127.0.0.1:9800',
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Authorization', `Bearer ${LEROY_TOKEN}`)
          })
        },
      },
    },
  },
})
