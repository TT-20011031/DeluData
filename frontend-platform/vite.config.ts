import path from "path"
import { defineConfig, loadEnv } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const appBase = env.VITE_APP_BASE || "/"
  const adminProxyTarget = env.VITE_DEV_ADMIN_PROXY_TARGET || "http://localhost:8032"

  return {
    base: appBase,
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      proxy: {
        "/api": {
          target: adminProxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
