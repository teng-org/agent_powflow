import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const port = Number(process.env.WEB_ADMIN_PORT || 5174)

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port,
  },
})

