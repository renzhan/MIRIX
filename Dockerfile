# Dockerfile for Mirix AI Assistant - 支持前后端同时运行（修复 npm 404 构建失败）
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
# 创建用户和工作目录
# ---------------------------
RUN useradd --create-home --shell /bin/bash mirix
WORKDIR /app
RUN chown mirix:mirix /app

# 切换到mirix用户进行后续操作
USER mirix

# ---------------------------
# Python 环境
# ---------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# 复制并安装 Python 依赖（以mirix用户身份）
COPY requirements.txt pyproject.toml setup.py MANIFEST.in ./
RUN pip install --no-cache-dir --upgrade pip --user \
 && pip install --no-cache-dir -r requirements.txt --user

# 复制项目源码（以mirix用户身份，自动拥有正确权限）
COPY mirix/ ./mirix/
COPY main.py chat.py email_learning.py ./
COPY database/ ./database/
COPY assets/ ./assets/

# ---------------------------
# 前端依赖安装（以mirix用户身份）
# ---------------------------
WORKDIR /app/frontend

# 仅复制 lockfile 与 package.json 以充分利用缓存
COPY frontend/package.json frontend/package-lock.json ./

# 优化npm配置，加速安装
ENV NPM_CONFIG_PROGRESS=false \
    NPM_CONFIG_LOGLEVEL=warn \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

ARG NPM_REGISTRY=https://registry.npmjs.org/

# 使用更快的安装方式，跳过可选依赖和审计（以mirix用户身份）
RUN npm config set registry ${NPM_REGISTRY} \
 && npm config set fetch-retries 3 \
 && npm config set fetch-retry-factor 2 \
 && npm config set fetch-retry-mintimeout 10000 \
 && npm config set fetch-retry-maxtimeout 60000 \
 && npm ci --no-audit --no-fund --no-optional --silent --registry=${NPM_REGISTRY} \
 && npm install -g serve \
 && npm cache clean --force

# 再复制剩余前端源码（以mirix用户身份，自动拥有正确权限）
COPY frontend/ ./

# 构建前端生产版本
ENV PUBLIC_URL=/aiop-pams
RUN npm run build

# ---------------------------
# 回到应用根目录与运行配置
# ---------------------------
WORKDIR /app

# 必要目录（以mirix用户身份创建，自动拥有正确权限）
RUN mkdir -p /app/data /app/logs

# 环境变量
ENV BACKEND_PORT=47283 \
    BACKEND_HOST=0.0.0.0 \
    MIRIX_CONFIG_PATH=/app/data \
    MIRIX_DATA_PATH=/app/data \
    PUBLIC_URL=/aiop-pams

# 暴露端口（后端 47283；前端 dev/build 可使用 3000）
EXPOSE 47283 3000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:47283/health || exit 1

# 启动脚本（需要以root身份设置权限）
USER root
COPY start.sh ./
RUN chmod +x start.sh && chown mirix:mirix start.sh

# 切换回mirix用户
USER mirix

# 同时启动前后端（由 start.sh 实现）
CMD ["./start.sh"]
