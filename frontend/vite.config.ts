import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// 端口与代理目标支持环境变量覆盖：默认值固定（前端 3100 / 后端 8100），
// 但实际监听端口与后端地址对外可配置，避免与本机其它服务（含 artoo）冲突。
//   FRONTEND_PORT         —— dev server 监听端口（默认 3100）
//   BACKEND_PROXY_TARGET  —— /api 反代的后端地址（默认 http://localhost:8100）
const frontendPort = Number(process.env.FRONTEND_PORT) || 3100
const backendTarget = process.env.BACKEND_PROXY_TARGET || 'http://localhost:8100'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: frontendPort,
    proxy: {
      // SSE 端点也走同一反代；关闭 buffering 由后端 headers 控制。
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
})
