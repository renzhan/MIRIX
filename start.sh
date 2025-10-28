#!/bin/bash

# 启动脚本 - 同时运行前端和后端服务

# 设置后端环境变量
export BACKEND_PORT=${BACKEND_PORT:-47283}
export BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}

# 启动后端服务
echo "启动后端服务..."
python main.py --host ${BACKEND_HOST} --port ${BACKEND_PORT} &
BACKEND_PID=$!

# 等待后端启动
sleep 5

# 启动前端开发服务器
echo "启动前端服务..."
cd frontend

# 设置前端专用的环境变量，避免端口冲突
export PORT=3000  # 前端固定使用3000端口
export HOST=0.0.0.0
export PUBLIC_URL=${PUBLIC_URL:-/aiop-pams}
export REACT_APP_SERVER_URL="http://localhost:${BACKEND_PORT}/pams"

# 清除可能影响前端的环境变量
unset BACKEND_PORT
unset BACKEND_HOST

echo "前端将使用端口: ${PORT}"
echo "后端API地址: ${REACT_APP_SERVER_URL}"

# 检查是否存在构建后的文件
if [ -d "build" ]; then
    echo "检测到构建文件，启动生产模式前端服务..."

    # 检查是否存在嵌套的 aiop-pams 目录
    if [ -d "build/aiop-pams" ]; then
        echo "使用嵌套构建目录: build/aiop-pams"
        # 使用嵌套目录作为根目录
        serve -s build/aiop-pams -l 3000 &
    else
        echo "使用标准构建目录: build"
        # 使用标准构建目录
        serve -s build -l 3000 &
    fi
    FRONTEND_PID=$!
else
    echo "未找到构建文件，启动开发模式前端服务..."
    # 启动前端开发服务
    npm start &
    FRONTEND_PID=$!
fi

# 回到根目录
cd ..

# 等待任一进程退出
wait $BACKEND_PID $FRONTEND_PID

echo "服务已停止"