# Dockerfile for Mirix AI Assistant - 支持前后端同时运行（简化版，使用root用户）
FROM python:3.11-slim

# ---------------------------
# 基础系统依赖
# ---------------------------
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    git \
    libpq-dev \
    pkg-config \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------
# 安装 Node.js 20.x（NodeSource）
# ---------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node -v && npm -v

# ---------------------------
# 工作目录
# ---------------------------
WORKDIR /app

# ---------------------------
# Python 环境
# ---------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# 复制并安装 Python 依赖
COPY requirements.txt pyproject.toml setup.py MANIFEST.in ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 复制项目源码
COPY mirix/ ./mirix/
COPY main.py chat.py email_learning.py ./
COPY database/ ./database/
COPY assets/ ./assets/

# ---------------------------
# 前端依赖安装
# ---------------------------
WORKDIR /app/frontend

# 复制前端源码
COPY frontend/ ./

# 优化npm配置，加速安装
ENV NPM_CONFIG_PROGRESS=false \
    NPM_CONFIG_LOGLEVEL=warn \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

ARG NPM_REGISTRY=https://registry.npmjs.org/

# 安装依赖并构建
RUN npm config set registry ${NPM_REGISTRY} \
 && npm install \
 && npm cache clean --force

# 构建前端生产版本
ENV PUBLIC_URL=/aiop-pams
RUN npm run build

# ---------------------------
# 回到应用根目录与运行配置
# ---------------------------
WORKDIR /app

# 创建必要目录
RUN mkdir -p /app/data /app/logs

# 复制启动脚本
COPY start.sh ./
RUN chmod +x start.sh

# 环境变量
ENV BACKEND_PORT=47283 \
    BACKEND_HOST=0.0.0.0 \
    MIRIX_CONFIG_PATH=/app/data \
    MIRIX_DATA_PATH=/app/data \
    PUBLIC_URL=/aiop-pams \
    PRODUCTION_BACKEND_URL=https://aiop-dev.item.pub/api

# 暴露端口（后端 47283；前端 dev/build 可使用 3000）
EXPOSE 47283 3000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:47283/health || exit 1

# 同时启动前后端（由 start.sh 实现）
CMD ["./start.sh"]
