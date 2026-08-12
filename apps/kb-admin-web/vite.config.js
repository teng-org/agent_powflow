import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const port = Number(process.env.KB_ADMIN_WEB_PORT || 5180)
const controlProxyTarget = process.env.CONTROL_PROXY_TARGET || 'http://localhost:8099'
const executionProxyTarget = process.env.EXECUTION_PROXY_TARGET || 'http://localhost:8090'

export default defineConfig({
  base: '/kb-workbench/',
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port,
    proxy: {
      '/kb-workbench/control': {
        target: controlProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/kb-workbench\/control/, ''),
      },
      '/kb-workbench/execution': {
        target: executionProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/kb-workbench\/execution/, ''),
      },
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
    },
  },
})
