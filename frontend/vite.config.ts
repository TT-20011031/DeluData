import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const adminProxyTarget = env.VITE_DEV_ADMIN_PROXY_TARGET || 'http://localhost:8031'

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    // pdfjs-dist Worker 配置
    optimizeDeps: {
      include: ['pdfjs-dist'],
    },
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: adminProxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
