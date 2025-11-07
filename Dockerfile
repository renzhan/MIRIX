# ========================================
# Stage 1: Python 依赖构建
# ========================================
FROM python:3.11-slim AS python-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev pkg-config git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 🔥 先复制依赖文件（缓存优化）
COPY requirements.txt ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt

# ========================================
# Stage 2: 前端构建
# ========================================
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

# 🔥 先复制 package.json（缓存优化）
COPY frontend/package*.json ./

ARG NPM_REGISTRY=https://registry.npmmirror.com/
ENV NPM_CONFIG_PROGRESS=false \
    NPM_CONFIG_LOGLEVEL=warn \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

RUN npm config set registry ${NPM_REGISTRY} \
 && npm ci --only=production --ignore-scripts \
 && npm cache clean --force

# 🔥 再复制源码并构建
COPY frontend/ ./
ENV PUBLIC_URL=/aiop-pams NODE_ENV=production
RUN npm run build

# ========================================
# Stage 3: 最终运行镜像
# ========================================
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg libpq5 ca-certificates unar \
 && rm -rf /var/lib/apt/lists/*

# 🔥 安装 Node.js（运行前端服务需要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g serve

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# 🔥 从构建阶段复制
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /app/frontend/build ./frontend/build
COPY --from=frontend-builder /app/frontend/package*.json ./frontend/

# 复制应用源码
COPY pyproject.toml setup.py MANIFEST.in ./
COPY mirix/ ./mirix/
COPY main.py chat.py email_learning.py ./
COPY database/ ./database/
COPY assets/ ./assets/
COPY start.sh ./

RUN chmod +x start.sh && mkdir -p /app/data /app/logs

ENV BACKEND_PORT=47283 \
    BACKEND_HOST=0.0.0.0 \
    MIRIX_CONFIG_PATH=/app/data \
    MIRIX_DATA_PATH=/app/data \
    PUBLIC_URL=/aiop-pams \
    PRODUCTION_BACKEND_URL=https://aiop-dev.item.pub/api

EXPOSE 47283 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:47283/pams/health || exit 1

CMD ["./start.sh"]
