# 多阶段构建 Dockerfile for Mirix AI Assistant
# 阶段1: 构建前端
FROM node:18-alpine AS frontend-builder

WORKDIR /app/frontend

# 复制前端依赖文件
COPY frontend/package*.json ./

# 安装前端依赖
RUN npm ci --only=production

# 复制前端源码
COPY frontend/ ./

# 构建前端
RUN npm run build

# 阶段2: Python后端
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ffmpeg \
    git \
    libpq-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 设置Python环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# 复制Python依赖文件
COPY requirements.txt pyproject.toml setup.py ./
COPY MANIFEST.in ./

# 安装Python依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制Python源码
COPY mirix/ ./mirix/
COPY main.py ./
COPY chat.py ./
COPY email_learning.py ./

# 复制数据库相关文件
COPY database/ ./database/

# 复制配置文件
COPY assets/ ./assets/

# 从前端构建阶段复制构建好的前端文件
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# 创建必要的目录
RUN mkdir -p /app/data /app/logs

# 设置环境变量
ENV PORT=47283
ENV HOST=0.0.0.0
ENV MIRIX_CONFIG_PATH=/app/data
ENV MIRIX_DATA_PATH=/app/data

# 暴露端口
EXPOSE 47283

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:47283/health || exit 1

# 创建非root用户
RUN useradd --create-home --shell /bin/bash mirix && \
    chown -R mirix:mirix /app

USER mirix

# 启动命令
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "47283"]