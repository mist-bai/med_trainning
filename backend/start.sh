#!/bin/bash
set -e

echo "=== 开始安装依赖 ==="

# 升级 pip（可选，如果超时可以跳过）
pip install --upgrade pip --default-timeout=300 --retries=5 || echo "pip 升级失败，继续使用当前版本"

# 配置 pip 使用阿里云镜像源
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.trusted-host mirrors.aliyun.com
pip config set global.timeout 600

# 先安装基础包（小包）
echo "=== 安装基础包 ==="
pip install --default-timeout=600 --retries=10 \
    fastapi \
    uvicorn \
    pydantic-settings \
    python-multipart \
    redis \
    pymysql \
    sqlalchemy

# 安装 LangChain 相关包
echo "=== 安装 LangChain 相关包 ==="
pip install --default-timeout=600 --retries=10 \
    langgraph \
    langchain-openai \
    langchain-community \
    langchain-deepseek \
    qdrant-client

# 最后安装大包（torch 和 sentence-transformers）
# 安装时禁用缓存，并在安装过程中实时清理缓存
echo "=== 安装大包（torch 和 sentence-transformers）==="
echo "安装前清理 pip 缓存..."
pip cache purge || rm -rf ~/.cache/pip/* 2>/dev/null || true

echo "=== 安装 torch（禁用缓存以节省空间）==="
pip install --no-cache-dir --default-timeout=1200 --retries=15 torch
echo "torch 安装完成，清理缓存..."
pip cache purge || rm -rf ~/.cache/pip/* 2>/dev/null || true

echo "=== 安装 sentence-transformers（禁用缓存以节省空间）==="
pip install --no-cache-dir --default-timeout=1200 --retries=15 sentence-transformers
echo "sentence-transformers 安装完成，清理缓存..."
pip cache purge || rm -rf ~/.cache/pip/* 2>/dev/null || true

echo "=== 依赖安装完成，启动服务 ==="
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
