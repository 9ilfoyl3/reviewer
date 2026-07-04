# Reviewer 前端镜像：多阶段构建。
#   1) build 阶段：node 安装依赖并产出静态资源（tsc && vite build → dist/）
#   2) 运行阶段：nginx 托管 dist/ 静态资源，并把 /api 反代到 API 进程
#      （含 SSE 所需的关闭缓冲配置，保证事件逐帧实时到达浏览器）

# ---- build 阶段 ----
FROM node:20-alpine AS build

WORKDIR /app

# 先装依赖，利用层缓存
COPY package.json package-lock.json ./
RUN npm ci

# 再拷贝源码与构建配置并构建
COPY . .
RUN npm run build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

# 自定义 nginx 配置（SPA 回退 + /api 反代 + SSE 关闭缓冲）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 静态产物
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
