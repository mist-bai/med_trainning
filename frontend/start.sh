#!/bin/bash
set -e

echo "=== 配置 npm 镜像源 ==="
npm config set registry https://registry.npmmirror.com
npm config set fetch-timeout 600000
npm config set fetch-retries 10

echo "=== 开始安装依赖 ==="

# 如果 node_modules 已存在且包含必要文件，跳过安装
if [ -d "node_modules/next" ] && [ -d "node_modules/react" ]; then
    echo "依赖已存在，跳过安装"
else
    echo "清理旧的安装..."
    rm -rf node_modules package-lock.json 2>/dev/null || true
    npm cache clean --force 2>/dev/null || true
    
    echo "安装核心依赖..."
    npm install next@14.0.0 react@18.2.0 react-dom@18.2.0 --legacy-peer-deps --no-save || echo "核心依赖安装部分失败"
    
    echo "安装 TypeScript..."
    npm install typescript@5.0.0 @types/node@20.0.0 @types/react@18.2.0 @types/react-dom@18.2.0 --legacy-peer-deps --save-dev || echo "TypeScript 安装部分失败"
    
    echo "安装 Tailwind CSS..."
    npm install tailwindcss@3.3.0 postcss@8.4.0 autoprefixer@10.4.0 --legacy-peer-deps --save-dev || echo "Tailwind 安装部分失败"
    
    echo "尝试安装剩余依赖..."
    npm install --legacy-peer-deps || echo "部分依赖可能安装失败，但继续启动..."
fi

echo "=== 检查关键依赖 ==="
if [ ! -d "node_modules/next" ]; then
    echo "⚠️ 警告: Next.js 未安装，尝试重新安装..."
    npm install next@14.0.0 react@18.2.0 react-dom@18.2.0 --legacy-peer-deps
fi

echo "=== 启动开发服务器 ==="
npm run dev
