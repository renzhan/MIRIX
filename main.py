#!/usr/bin/env python3
"""
Mirix - AI Assistant Application
Entry point for the Mirix application.
"""

import sys
import argparse
import os
import time
import logging
import traceback
from pathlib import Path
from dotenv import load_dotenv

# 设置日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('mirix_startup.log')
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def main():
    """Main entry point for Mirix application."""
    start_time = time.time()
    logger.info("🚀 开始启动 Mirix 服务器...")
    
    parser = argparse.ArgumentParser(description='Mirix AI Assistant Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind the server to')
    parser.add_argument('--port', type=int, default=None, help='Port to bind the server to')
    
    args = parser.parse_args()
    
    # Determine port from command line, environment variable, or default
    port = args.port
    if port is None:
        port = int(os.environ.get('PORT', 47283))
    
    logger.info(f"📋 配置信息: Host={args.host}, Port={port}")
    
    # 记录环境变量
    logger.info("🔧 环境变量检查:")
    env_vars = ['MIRIX_PG_URI', 'MIRIX_DISABLE_MCP', 'PORT', 'LOG_LEVEL']
    for var in env_vars:
        value = os.environ.get(var, '未设置')
        if var == 'MIRIX_PG_URI' and value != '未设置':
            # 隐藏密码信息
            parsed = value.split('@')
            if len(parsed) > 1:
                safe_value = parsed[0].split(':')[0] + ':***@' + parsed[1]
            else:
                safe_value = '***'
            logger.info(f"  {var}: {safe_value}")
        else:
            logger.info(f"  {var}: {value}")
    
    logger.info("📦 开始导入 uvicorn 和 FastAPI 应用...")
    import_start = time.time()
    
    try:
        import uvicorn
        logger.info(f"✅ uvicorn 导入完成 (耗时: {time.time() - import_start:.2f}s)")
        
        from mirix.server import app
        logger.info(f"✅ FastAPI 应用导入完成 (耗时: {time.time() - import_start:.2f}s)")
        
        total_startup_time = time.time() - start_time
        logger.info(f"🎉 启动准备完成，总耗时: {total_startup_time:.2f}s")
        logger.info(f"🌐 启动服务器: {args.host}:{port}")
        
        uvicorn.run(app, host=args.host, port=port)
        
    except Exception as e:
        logger.error(f"❌ 启动失败: {str(e)}")
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main() 