FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json /app/
# 使用国内镜像源 + 拉长 fetch 超时，避免外网偶发超时导致 npm ci 失败
RUN npm config set registry https://registry.npmmirror.com \
 && npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000 \
 && npm ci

COPY . /app
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
