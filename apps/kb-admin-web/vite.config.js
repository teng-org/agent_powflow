import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const port = Number(process.env.KB_ADMIN_WEB_PORT || 5180)

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port,
  },
})
