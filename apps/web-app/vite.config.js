import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const port = Number(process.env.WEB_APP_PORT || 5173)
const controlProxyTarget = process.env.CONTROL_PROXY_TARGET || 'http://localhost:8099'
const executionProxyTarget = process.env.EXECUTION_PROXY_TARGET || 'http://localhost:8091'
const chatProxyTarget = process.env.CHAT_PROXY_TARGET || 'http://localhost:8092'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port,
    proxy: {
      '/control': {
        target: controlProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/control/, ''),
      },
      '/execution': {
        target: executionProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/execution/, ''),
      },
      '/chat': {
        target: chatProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/chat/, ''),
      },
    },
  },
})
