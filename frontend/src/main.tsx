import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { Toaster } from '@/components/ui/sonner'
import './index.css'

// 应用入口：单向数据流，SSE 事件为唯一数据源。
// 全局 Toaster 用于请求失败、超时、连接中断等反馈（沿用 artoo 的 sonner）。
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
    <Toaster position="top-center" />
  </React.StrictMode>,
)
