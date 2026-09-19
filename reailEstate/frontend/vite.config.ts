import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { randomBytes } from 'node:crypto'

const nonce = randomBytes(24).toString('base64')
const policy = (development: boolean) => `default-src 'self'; script-src 'self'${development ? ` 'nonce-${nonce}'` : ''}; style-src 'self'${development ? ` 'nonce-${nonce}'` : ''}; img-src 'self' data: https://tile.openstreetmap.org; font-src 'self'; connect-src 'self'${development ? ' ws://127.0.0.1:*' : ''}; worker-src ${development ? "'self' blob:" : "'none'"}; frame-src 'none'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'`
export default defineConfig(({ command }) => ({
  plugins: [react()],
  build: { assetsInlineLimit: 0 },
  html: command === 'serve' ? { cspNonce: nonce } : undefined,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8002', changeOrigin: false } },
    headers: { 'Referrer-Policy': 'strict-origin-when-cross-origin', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': policy(true) },
  },
  preview: {
    host: '127.0.0.1', port: 5174, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8002', changeOrigin: false } },
    headers: { 'Referrer-Policy': 'strict-origin-when-cross-origin', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': policy(false) },
  },
}))
