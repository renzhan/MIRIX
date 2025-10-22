#!/bin/bash

# 启动脚本 - 同时运行前端和后端服务

# 启动后端服务
echo "启动后端服务..."
python main.py --host 0.0.0.0 --port 47283 &
BACKEND_PID=$!

# 等待后端启动
sleep 5

# 启动前端开发服务器
echo "启动前端服务..."
cd frontend
npm start &
FRONTEND_PID=$!

# 回到根目录
cd ..

# 等待任一进程退出
wait $BACKEND_PID $FRONTEND_PID

echo "服务已停止"