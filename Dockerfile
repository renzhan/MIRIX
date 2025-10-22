# Dockerfile for Mirix AI Assistant - 支持前后端同时运行
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
    nodejs \
    npm \
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

# 复制前端源码
COPY frontend/ ./frontend/

# 安装前端依赖
WORKDIR /app/frontend
RUN npm ci

# 回到应用根目录
WORKDIR /app

# 创建必要的目录
RUN mkdir -p /app/data /app/logs

# 设置环境变量
ENV PORT=47283
ENV HOST=0.0.0.0
ENV MIRIX_CONFIG_PATH=/app/data
ENV MIRIX_DATA_PATH=/app/data

# 暴露端口
EXPOSE 47283 3000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:47283/health || exit 1

# 复制启动脚本并设置权限
COPY start.sh ./
RUN chmod +x start.sh

# 创建非root用户
RUN useradd --create-home --shell /bin/bash mirix && \
    chown -R mirix:mirix /app

USER mirix

# 启动命令 - 同时启动前端和后端
CMD ["./start.sh"]