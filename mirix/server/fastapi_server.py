# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import logging.config
import os
import queue
import traceback
import uuid
import threading
import time
import hashlib
import multiprocessing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
import redis
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.agent_wrapper import AgentWrapper
from ..agent.message_queue import MessageQueue
from ..functions.mcp_client import StdioServerConfig, get_mcp_client_manager
from ..prompts import gpt_system
from ..services.mcp_marketplace import get_mcp_marketplace
from ..services.mcp_tool_registry import get_mcp_tool_registry
from ..utils import parse_json
from ..schemas.mirix_message import MessageType


def _setup_logging():
    """Configure logging with flexible output options (console/file/both)."""
    try:
        import os

        # 日志配置环境变量
        log_output = os.getenv('LOG_OUTPUT', 'both').lower()  # console, file, both
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        log_dir = os.getenv('LOG_DIR', './')

        # 创建日志目录（如果需要文件输出）
        if log_output in ['file', 'both']:
            os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, 'api_backend.log')

        # 基础配置
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s",
                },
                "simple": {
                    "format": "%(asctime)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {},
            "root": {
                "level": log_level,
                "handlers": [],
            },
            "loggers": {
                "uvicorn": {"level": "INFO", "propagate": True},
                "uvicorn.error": {"level": "INFO", "propagate": True},
                "uvicorn.access": {"level": "INFO", "propagate": True},
            },
        }

        # 根据配置添加处理器
        if log_output in ['console', 'both']:
            config["handlers"]["console"] = {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            }
            config["root"]["handlers"].append("console")

        if log_output in ['file', 'both']:
            config["handlers"]["rotating_file"] = {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "standard",
                "filename": log_file,
                "maxBytes": 52428800,  # 50MB
                "backupCount": 10,
                "encoding": "utf-8",
            }
            config["root"]["handlers"].append("rotating_file")

        # 应用配置
        logging.config.dictConfig(config)

        # 记录日志配置信息
        logger = logging.getLogger(__name__)
        logger.info("=== 邮件回复服务日志配置完成 ===")
        logger.info(f"日志输出模式: {log_output}")
        logger.info(f"日志级别: {log_level}")

        if log_output in ['file', 'both']:
            logger.info(f"日志文件路径: {log_file}")
            logger.info(f"日志文件最大大小: 50MB，保留备份数: 10")

        if log_output == 'console':
            logger.info("仅输出到控制台")
        elif log_output == 'file':
            logger.info("仅输出到文件")
        else:
            logger.info("同时输出到控制台和文件")

    except Exception as e:
        # Fall back gracefully without crashing the server if logging config fails
        logging.basicConfig(level=logging.INFO)
        print(f"日志配置失败，使用基础配置: {e}")


_setup_logging()

logger = logging.getLogger(__name__)


# User context switching utilities
def switch_user_context(agent_wrapper, user_id: str):
    """Switch agent's user context and manage user status"""
    if agent_wrapper and agent_wrapper.client:

        # Set current user to inactive
        if agent_wrapper.client.user:
            current_user = agent_wrapper.client.user
            agent_wrapper.client.server.user_manager.update_user_status(
                current_user.id, "inactive"
            )

        # Get and set new user to active
        user = agent_wrapper.client.server.user_manager.get_user_by_id(user_id)
        agent_wrapper.client.server.user_manager.update_user_status(user_id, "active")
        agent_wrapper.client.user = user
        return user
    return None


def get_user_or_default(agent_wrapper, user_id: Optional[str] = None):
    """Get user by ID or return current user"""
    if user_id:
        return agent_wrapper.client.server.user_manager.get_user_by_id(user_id)
    elif agent_wrapper and agent_wrapper.client.user:
        return agent_wrapper.client.user
    else:
        return agent_wrapper.client.server.user_manager.get_default_user()


async def handle_gmail_connection(
        client_id: str, client_secret: str, server_name: str
) -> bool:
    """
    Handle Gmail OAuth2 authentication and MCP connection
    Using EXACT same logic as /Users/yu.wang/work/Gmail/single_user_gmail.py
    """
    import os

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    # Use all required Gmail scopes for full functionality
    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    try:
        print(f"🔐 Starting Gmail OAuth for {server_name}")

        # Set up token file path (same pattern as original)
        token_file = os.path.expanduser("~/.mirix/gmail_token.json")
        os.makedirs(os.path.dirname(token_file), exist_ok=True)

        # Create client config - EXACT same structure as original
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [
                    "http://localhost:8080/",
                    "http://localhost:8081/",
                    "http://localhost:8082/",
                ],
            }
        }

        creds = None

        # Load existing token if available - EXACT same logic
        if os.path.exists(token_file):
            try:
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            except Exception:
                print("🔄 Refreshing Gmail credentials (previous token expired)")
                os.remove(token_file)
                creds = None

        # If there are no (valid) credentials available, let the user log in - EXACT same logic
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Error refreshing credentials: {e}")
                    creds = None

            if not creds:
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

                print("\n🔐 Starting OAuth authentication...")
                print("Opening browser for Google authentication...")

                # Try specific ports that match redirect URIs - EXACT same logic
                for port in [8080, 8081, 8082]:
                    try:
                        creds = flow.run_local_server(port=port, open_browser=True)
                        break
                    except OSError:
                        if port == 8082:
                            # If all ports fail, use automatic port selection
                            creds = flow.run_local_server(port=0, open_browser=True)

            # Save the credentials for the next run - EXACT same logic
            with open(token_file, "w") as token:
                token.write(creds.to_json())

        # Build the Gmail service - EXACT same logic
        service = build("gmail", "v1", credentials=creds)
        print("✅ Successfully authenticated with Gmail API")

        # Gmail service built successfully - ready for email sending
        print("✅ Gmail API connected successfully")

        # Now create the MCP client and add it to the manager
        from ..functions.mcp_client import (
            GmailMCPClient,
            GmailServerConfig,
            get_mcp_client_manager,
        )

        config = GmailServerConfig(
            server_name=server_name,
            client_id=client_id,
            client_secret=client_secret,
            token_file=token_file,
        )

        # Create Gmail MCP client directly
        client = GmailMCPClient(config)
        client.gmail_service = service
        client.credentials = creds
        client.initialized = True

        # Add to MCP manager
        mcp_manager = get_mcp_client_manager()
        mcp_manager.clients[server_name] = client
        mcp_manager.server_configs[server_name] = config

        # Save configuration to disk for persistence (this was missing!)
        mcp_manager._save_persistent_connections()

        print(
            f"✅ Gmail MCP client added to manager as '{server_name}' and saved to disk"
        )
        return True

    except Exception as e:
        print(f"❌ Error in Gmail OAuth flow: {str(e)}")
        logger.error(f"Gmail connection error: {str(e)}")
        return False


"""
VOICE RECORDING STRATEGY & ARCHITECTURE:

Current Implementation:
- Frontend records audio in 5-second chunks (CHUNK_DURATION = 5000ms)
- Chunks are accumulated locally until a screenshot is sent
- Raw voice files are sent to the agent for accumulation and processing
- Agent accumulates voice files alongside images until TEMPORARY_MESSAGE_LIMIT is reached
- Voice processing happens in agent.absorb_content_into_memory()

Recommended Alternative Strategy:
Instead of 5-second chunks, you can:
1. Send 1-second micro-chunks to reduce latency
2. Agent accumulates chunks until TEMPORARY_MESSAGE_LIMIT is reached
3. This aligns perfectly with how images are accumulated in agent.py

Benefits of 1-second chunks:
- Lower latency for real-time feedback
- More granular control over audio processing
- Better alignment with the existing image accumulation pattern
- Smoother user experience
- Voice processing happens in batches during memory absorption

Implementation changes needed:
- Frontend: Change CHUNK_DURATION from 5000 to 1000
- Agent: Handles voice file accumulation and processing during memory absorption
- Server: Passes raw voice files to agent without processing

FFPROBE WARNING:
The warning about ffprobe/avprobe is harmless and expected if FFmpeg isn't in your system PATH.
To fix it, install FFmpeg:
- Windows: Download from https://ffmpeg.org and add to PATH
- macOS: brew install ffmpeg  
- Linux: sudo apt install ffmpeg

The warning doesn't affect functionality as pydub falls back gracefully.
"""

app = FastAPI(title="Mirix Agent API", version="0.1.5", root_path="/pams")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def register_mcp_tools_for_restored_connections():
    """Register tools for MCP connections that were restored on startup"""
    try:
        mcp_manager = get_mcp_client_manager()
        connected_servers = mcp_manager.list_servers()

        if connected_servers and agent and agent.client.user:
            logger.info(
                f"Re-registering tools for {len(connected_servers)} restored MCP servers"
            )

            mcp_tool_registry = get_mcp_tool_registry()
            current_user = agent.client.user

            for server_name in connected_servers:
                try:
                    # Register tools for this server
                    registered_tools = mcp_tool_registry.register_mcp_tools(
                        current_user, [server_name]
                    )

                    # Add MCP tool to the current chat agent if available
                    if hasattr(agent, "agent_states"):
                        agent.client.server.agent_manager.add_mcp_tool(
                            agent_id=agent.agent_states.agent_state.id,
                            mcp_tool_name=server_name,
                            tool_ids=list(
                                set(
                                    [tool.id for tool in registered_tools]
                                    + [
                                        tool.id
                                        for tool in agent.client.server.agent_manager.get_agent_by_id(
                                            agent.agent_states.agent_state.id,
                                            actor=agent.client.user,
                                        ).tools
                                    ]
                                )
                            ),
                            actor=agent.client.user,
                        )

                    logger.info(
                        f"Re-registered {len(registered_tools)} tools for server {server_name}"
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to re-register tools for server {server_name}: {str(e)}"
                    )

    except Exception as e:
        logger.error(f"Error re-registering MCP tools: {str(e)}")


@app.on_event("startup")
async def startup_event():
    """Initialize and restore MCP connections on startup"""
    global agent

    try:
        # Re-assert logging config in case a runner (e.g., Uvicorn) overwrote it
        _setup_logging()
        logger.info("Starting up Mirix FastAPI server...")

        # Initialize the agent
        import sys
        from pathlib import Path

        if getattr(sys, "frozen", False):
            # Running in PyInstaller bundle
            bundle_dir = Path(sys._MEIPASS)
            config_path = bundle_dir / "mirix" / "configs" / "mirix_gpt4o.yaml"
        else:
            # Running in development
            config_path = Path("mirix/configs/mirix_gpt4o.yaml")

        agent = AgentWrapper(str(config_path))
        print("Agent initialized successfully")

        # Initialize the MCP client manager (this will auto-restore connections)
        print("🚀 Initializing MCP client manager...")
        mcp_manager = get_mcp_client_manager()
        connected_servers = mcp_manager.list_servers()
        logger.info(
            f"MCP client manager initialized with {len(connected_servers)} restored connections: {connected_servers}"
        )
        print(
            f"🔄 MCP Manager: Restored {len(connected_servers)} connections: {connected_servers}"
        )

        # Debug: Check if the configuration file exists
        config_file = os.path.expanduser("~/.mirix/mcp_connections.json")
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                configs = json.load(f)
                print(
                    f"📋 Found MCP config file with {len(configs)} entries: {list(configs.keys())}"
                )
        else:
            print(f"📋 No MCP config file found at {config_file}")

        # 启动邮件回复工作线程池
        start_email_reply_workers()

        # 恢复邮件回复队列中的任务
        recover_email_reply_tasks()

        # 清理过期的邮件回复任务
        cleanup_expired_email_tasks()

        # Tool registration will happen later when agent is available

    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on server shutdown"""
    try:
        logger.info("Shutting down Mirix FastAPI server...")

        # 停止邮件回复工作线程池
        stop_email_reply_workers()

        logger.info("Server shutdown completed")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")


# Global agent instance
agent = None
# Global storage for confirmation queues keyed by confirmation_id
confirmation_queues = {}
# Flag to track if MCP tools have been registered for restored connections
_mcp_tools_registered = False


def process_email_reply_task(task_id: str, email_content: str, category_list: str, user_id: str, email_basic_id: str,
                             callback_url: str):
    """后台处理邮件回复任务"""
    processing_start_time = time.time()

    try:
        logger.info(f"开始处理邮件回复任务: {task_id}")

        # 从Redis获取任务状态
        task_key = f"email_reply_task:{task_id}"
        task_data = redis_client.hgetall(task_key)

        if not task_data:
            logger.error(f"任务不存在: {task_id}")
            return

        # 计算等待时间
        created_at = datetime.fromisoformat(task_data.get("created_at"))
        wait_time = processing_start_time - created_at.timestamp()

        logger.info(f"任务 {task_id} 等待处理时间: {wait_time:.2f}秒")

        # 更新任务状态为处理中
        redis_client.hset(task_key, "status", "processing")
        redis_client.hset(task_key, "updated_at", datetime.now().isoformat())
        redis_client.hset(task_key, "processing_start_time", datetime.now().isoformat())

        # 带标签
        absorb_content = f"""
        {email_content}
        
        请根据上述邮件内容，作为Meta Memory Manager进行分析并协调相应的记忆管理器。
        {f'📌 注意：此邮件属于"{category_list}"分类，请在相关记忆中使用此分类作为 source_category 标签。' if category_list and category_list != '未分类' else ''}
        """
        # 异步执行absorb，不等待结果
        threading.Thread(
            target=lambda: agent.send_message(
                message=absorb_content,
                memorizing=True,
                force_absorb_content=True,
                user_id=user_id
            ),
            daemon=True
        ).start()

        # 执行邮件回复生成
        absorb_content = f"""
        {email_content}
             
        {f'  📌 注意：此邮件类别为："{category_list}"， 若有必要Procedural记忆则优先按照在{category_list}类别查找相关处理流程。' if category_list and category_list != '未分类' else ''}

        """

        response, _ = agent.message_queue.send_message_in_queue(
            agent.client,
            agent.agent_states.email_reply_agent_state.id,
            {
                "user_id": user_id,
                "message": email_content,
                "force_response": True
            },
            agent_type="email_reply",
        )

        # 处理响应
        if response == "ERROR":
            actual_processing_time = time.time() - processing_start_time
            total_time = time.time() - created_at.timestamp()
            logger.info(
                f"任务 {task_id} 处理失败 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

            result = {
                "status": "error",
                "error": "邮件回复生成失败",
                "task_id": task_id,
                "email_basic_id": email_basic_id,
                "category_list": category_list,
                "timing": {
                    "wait_time": round(wait_time, 2),
                    "processing_time": round(actual_processing_time, 2),
                    "total_time": round(total_time, 2)
                }
            }
        elif not hasattr(response, "messages") or len(response.messages) < 2:
            actual_processing_time = time.time() - processing_start_time
            total_time = time.time() - created_at.timestamp()
            logger.info(
                f"任务 {task_id} 响应结构无效 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

            result = {
                "status": "error",
                "error": "响应结构无效",
                "task_id": task_id,
                "email_basic_id": email_basic_id,
                "category_list": category_list,
                "timing": {
                    "wait_time": round(wait_time, 2),
                    "processing_time": round(actual_processing_time, 2),
                    "total_time": round(total_time, 2)
                }
            }
        else:
            try:
                # 解析响应
                num_tools_called = 0
                for message in response.messages[::-1]:
                    if message.message_type == MessageType.tool_return_message:
                        num_tools_called += 1
                    else:
                        break

                if not hasattr(response.messages[-(num_tools_called * 2 + 1)], "tool_call"):
                    actual_processing_time = time.time() - processing_start_time
                    total_time = time.time() - created_at.timestamp()
                    logger.info(
                        f"任务 {task_id} 缺少工具调用 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

                    result = {
                        "status": "error",
                        "error": "缺少工具调用",
                        "task_id": task_id,
                        "email_basic_id": email_basic_id,
                        "category_list": category_list,
                        "timing": {
                            "wait_time": round(wait_time, 2),
                            "processing_time": round(actual_processing_time, 2),
                            "total_time": round(total_time, 2)
                        }
                    }
                else:
                    tool_call = response.messages[-(num_tools_called * 2 + 1)].tool_call
                    parsed_args = parse_json(tool_call.arguments)

                    if "message" not in parsed_args:
                        actual_processing_time = time.time() - processing_start_time
                        total_time = time.time() - created_at.timestamp()
                        logger.info(
                            f"任务 {task_id} 缺少消息内容 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

                        result = {
                            "status": "error",
                            "error": "缺少消息内容",
                            "task_id": task_id,
                            "email_basic_id": email_basic_id,
                            "category_list": category_list,
                            "timing": {
                                "wait_time": round(wait_time, 2),
                                "processing_time": round(actual_processing_time, 2),
                                "total_time": round(total_time, 2)
                            }
                        }
                    else:
                        # 计算处理时间
                        actual_processing_time = time.time() - processing_start_time
                        total_time = time.time() - created_at.timestamp()

                        logger.info(f"任务 {task_id} 实际处理时间: {actual_processing_time:.2f}秒")
                        logger.info(f"任务 {task_id} 总处理时间: {total_time:.2f}秒")

                        result = {
                            "status": "completed",
                            "reply_content": parsed_args["message"],
                            "task_id": task_id,
                            "email_basic_id": email_basic_id,
                            "category_list": category_list,
                            "timing": {
                                "wait_time": round(wait_time, 2),
                                "processing_time": round(actual_processing_time, 2),
                                "total_time": round(total_time, 2)
                            }
                        }
            except Exception as e:
                actual_processing_time = time.time() - processing_start_time
                total_time = time.time() - created_at.timestamp()
                logger.info(
                    f"任务 {task_id} 解析响应失败 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

                result = {
                    "status": "error",
                    "error": f"解析响应失败: {str(e)}",
                    "task_id": task_id,
                    "email_basic_id": email_basic_id,
                    "category_list": category_list,
                    "timing": {
                        "wait_time": round(wait_time, 2),
                        "processing_time": round(actual_processing_time, 2),
                        "total_time": round(total_time, 2)
                    }
                }

        # 发送回调
        try:
            callback_response = requests.post(
                callback_url,
                json=result,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            logger.info(f"回调发送成功: {task_id}, 状态码: {callback_response.status_code}")
        except Exception as e:
            logger.error(f"回调发送失败: {task_id}, 错误: {str(e)}")
            # 即使回调失败，也要更新Redis状态
            redis_client.hset(task_key, "callback_error", str(e))
        logger.info(f"Url:{callback_url} \n  Body: {json.dumps(result, ensure_ascii=False, indent=2)}")

        # 清除Redis记录
        redis_client.delete(task_key)
        final_time = time.time() - processing_start_time
        logger.info(f"任务处理完成并清除: {task_id}, 最终处理时间: {final_time:.2f}秒")

    except Exception as e:
        actual_processing_time = time.time() - processing_start_time
        # 如果created_at未定义，使用当前时间作为fallback
        try:
            total_time = time.time() - created_at.timestamp()
        except:
            total_time = actual_processing_time

        logger.error(f"处理邮件回复任务失败: {task_id}, 错误: {str(e)}")
        logger.info(
            f"任务 {task_id} 异常失败 - 实际处理时间: {actual_processing_time:.2f}秒, 总时间: {total_time:.2f}秒")

        # 更新任务状态为失败
        task_key = f"email_reply_task:{task_id}"
        redis_client.hset(task_key, "status", "failed")
        redis_client.hset(task_key, "error", str(e))
        redis_client.hset(task_key, "updated_at", datetime.now().isoformat())

        # 尝试发送失败回调
        try:
            callback_data = {
                "status": "error",
                "error": str(e),
                "task_id": task_id,
                "email_basic_id": email_basic_id,
                "category_list": category_list,
                "timing": {
                    "processing_time": round(actual_processing_time, 2),
                    "total_time": round(total_time, 2)
                }
            }
            requests.post(
                callback_url,
                json=callback_data,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
        except:
            pass


class MessageRequest(BaseModel):
    message: Optional[str] = None
    image_uris: Optional[List[str]] = None
    sources: Optional[List[str]] = None  # Source names corresponding to image_uris
    voice_files: Optional[List[str]] = None  # Base64 encoded voice files
    memorizing: bool = False
    is_screen_monitoring: Optional[bool] = False
    user_id: Optional[str] = None  # User ID for multi-user support


class MessageResponse(BaseModel):
    response: str
    status: str = "success"


class ProcessMysqlEmailRequest(BaseModel):
    email_data: Dict[str, Any]
    user_id: Optional[str] = None


class ProcessMysqlEmailResponse(BaseModel):
    status: str
    message: str
    email_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_email_account: Optional[str] = None
    agents_triggered: int = 0
    triggered_memory_types: List[str] = []
    processing_time: str = "N/A"


class ConfirmationRequest(BaseModel):
    confirmation_id: str
    confirmed: bool


class PersonaDetailsResponse(BaseModel):
    personas: Dict[str, str]


class UpdatePersonaRequest(BaseModel):
    text: str
    user_id: Optional[str] = None


class UpdatePersonaResponse(BaseModel):
    success: bool
    message: str


class UpdateCoreMemoryRequest(BaseModel):
    label: str
    text: str


class UpdateCoreMemoryResponse(BaseModel):
    success: bool
    message: str


class ApplyPersonaTemplateRequest(BaseModel):
    persona_name: str
    user_id: Optional[str] = None


class CoreMemoryPersonaResponse(BaseModel):
    text: str


class SetModelRequest(BaseModel):
    model: str


class AddCustomModelRequest(BaseModel):
    model_name: str
    model_endpoint: str
    api_key: str
    temperature: float = 0.7
    max_tokens: int = 4096
    maximum_length: int = 32768


class AddCustomModelResponse(BaseModel):
    success: bool
    message: str


class ListCustomModelsResponse(BaseModel):
    models: List[str]


class SetModelResponse(BaseModel):
    success: bool
    message: str
    missing_keys: List[str]
    model_requirements: Dict[str, Any]


class GetCurrentModelResponse(BaseModel):
    current_model: str


class SetTimezoneRequest(BaseModel):
    timezone: str


class SetTimezoneResponse(BaseModel):
    success: bool
    message: str


class GetTimezoneResponse(BaseModel):
    timezone: str


class ScreenshotSettingRequest(BaseModel):
    include_recent_screenshots: bool


class ScreenshotSettingResponse(BaseModel):
    success: bool
    include_recent_screenshots: bool
    message: str


class WorkflowExtractionRequest(BaseModel):
    content: str
    email_account: str


class WorkflowExtractionResponse(BaseModel):
    workflow_result: Any  # 可以是字典或字符串


# API Key validation functionality
def get_required_api_keys_for_model(model_endpoint_type: str) -> List[str]:
    """Get required API keys for a given model endpoint type"""
    api_key_mapping = {
        "openai": ["OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "azure": ["AZURE_API_KEY", "AZURE_BASE_URL", "AZURE_API_VERSION"],
        "google_ai": ["GEMINI_API_KEY"],
        "groq": ["GROQ_API_KEY"],
        "together": ["TOGETHER_API_KEY"],
        "bedrock": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
    }
    return api_key_mapping.get(model_endpoint_type, [])


def check_missing_api_keys(agent) -> Dict[str, List[str]]:
    """Check for missing API keys based on the agent's configuration"""

    if agent is None:
        return {"error": ["Agent not initialized"]}

    try:
        # Use the new AgentWrapper method instead of the old logic
        status = agent.check_api_key_status()

        return {
            "missing_keys": status["missing_keys"],
            "model_type": status.get("model_requirements", {}).get(
                "current_model", "unknown"
            ),
        }

    except Exception as e:
        print(f"Error in check_missing_api_keys: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return {"error": [f"Error checking API keys: {str(e)}"]}


class ApiKeyRequest(BaseModel):
    key_name: str
    key_value: str


class ApiKeyCheckResponse(BaseModel):
    missing_keys: List[str]
    model_type: str
    requires_api_key: bool


class ApiKeyUpdateResponse(BaseModel):
    success: bool
    message: str


# Memory endpoint response models
class EpisodicMemoryItem(BaseModel):
    timestamp: str
    content: str
    context: Optional[str] = None
    emotions: Optional[List[str]] = None


class KnowledgeSkillItem(BaseModel):
    title: str
    type: str  # "semantic" or "procedural"
    content: str
    proficiency: Optional[str] = None
    tags: Optional[List[str]] = None


class DocsFilesItem(BaseModel):
    filename: str
    type: str
    summary: str
    last_accessed: Optional[str] = None
    size: Optional[str] = None


class CoreUnderstandingItem(BaseModel):
    aspect: str
    understanding: str
    confidence: Optional[float] = None
    last_updated: Optional[str] = None


class CredentialItem(BaseModel):
    name: str
    type: str
    content: str  # Will be masked
    tags: Optional[List[str]] = None
    last_used: Optional[str] = None


class ClearConversationResponse(BaseModel):
    success: bool
    message: str
    messages_deleted: int


class CleanupDetachedMessagesResponse(BaseModel):
    success: bool
    message: str
    cleanup_results: Dict[str, int]


class ExportMemoriesRequest(BaseModel):
    file_path: str
    memory_types: List[str]
    include_embeddings: bool = False
    user_id: Optional[str] = None


class ExportMemoriesResponse(BaseModel):
    success: bool
    message: str
    exported_counts: Dict[str, int]
    total_exported: int
    file_path: str


class ReflexionRequest(BaseModel):
    pass  # No parameters needed for now


class ReflexionResponse(BaseModel):
    success: bool
    message: str
    processing_time: Optional[float] = None


class EmailReplyRequest(BaseModel):
    email_content: str
    category_list: str
    email_account: str
    email_basic_id: str
    callback_url: str  # 回调地址


class EmailReplyResponse(BaseModel):
    task_id: str
    status: str
    message: str


# Redis连接
redis_port_str = os.getenv('REDIS_PORT', '6379')

if ':' in redis_port_str and not redis_port_str.isdigit():
    redis_port_str = redis_port_str.split(':')[-1]  # 'tcp://172.30.1.193:6379' → '6379'

redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(redis_port_str),
    password=os.getenv('REDIS_PASSWORD', 'aiop123456'),
    db=int(os.getenv('REDIS_DB', 0)),
    decode_responses=True
)

# Redis队列名称
EMAIL_REPLY_QUEUE = "email_reply_queue"

# 工作线程池
worker_threads = []
worker_shutdown_event = threading.Event()


def generate_task_id(user_id: str, email_basic_id: str, email_content: str) -> str:
    """根据用户ID和邮件内容生成MD5任务ID"""
    content = f"{user_id}:{email_basic_id}:{email_content}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def email_reply_worker():
    """邮件回复工作线程"""
    logger.info(f"邮件回复工作线程启动: {threading.current_thread().name}")

    while not worker_shutdown_event.is_set():
        try:
            # 阻塞式从Redis队列获取任务，超时5秒
            task_data = redis_client.blpop(EMAIL_REPLY_QUEUE, timeout=5)

            if task_data is None:
                # 超时，继续循环
                continue

            # 解析任务数据
            _, task_json = task_data
            task_info = json.loads(task_json)

            task_id = task_info['task_id']
            email_content = task_info['email_content']
            category_list = task_info['category_list']
            user_id = task_info['user_id']
            email_basic_id = task_info['email_basic_id']
            callback_url = task_info['callback_url']

            logger.info(f"工作线程 {threading.current_thread().name} 开始处理任务: {task_id}")

            # 验证任务是否仍然有效（防止处理已删除的任务）
            task_key = f"email_reply_task:{task_id}"
            current_task = redis_client.hgetall(task_key)

            if not current_task:
                logger.warning(f"任务 {task_id} 已不存在，跳过处理")
                continue

            current_status = current_task.get("status", "unknown")
            if current_status not in ["queued", "processing"]:
                logger.warning(f"任务 {task_id} 状态为 {current_status}，跳过处理")
                continue

            # 处理任务
            process_email_reply_task(task_id, email_content, category_list, user_id, email_basic_id, callback_url)

        except json.JSONDecodeError as e:
            logger.error(f"解析任务数据失败: {str(e)}")
        except Exception as e:
            logger.error(f"工作线程异常: {str(e)}")
            logger.error(f"错误堆栈: {traceback.format_exc()}")

    logger.info(f"邮件回复工作线程退出: {threading.current_thread().name}")


def start_email_reply_workers():
    """启动邮件回复工作线程池"""
    global worker_threads

    # 计算线程数：CPU核数的2倍，最少2个
    cpu_count = multiprocessing.cpu_count()
    thread_count = max(2, cpu_count * 2)

    logger.info(f"启动 {thread_count} 个邮件回复工作线程")

    for i in range(thread_count):
        thread = threading.Thread(
            target=email_reply_worker,
            name=f"EmailReplyWorker-{i + 1}",
            daemon=True
        )
        thread.start()
        worker_threads.append(thread)

    logger.info(f"邮件回复工作线程池启动完成，共 {len(worker_threads)} 个线程")


def stop_email_reply_workers():
    """停止邮件回复工作线程池"""
    global worker_threads

    logger.info("正在停止邮件回复工作线程池...")
    worker_shutdown_event.set()

    # 等待所有线程结束
    for thread in worker_threads:
        thread.join(timeout=10)

    worker_threads.clear()
    logger.info("邮件回复工作线程池已停止")


def recover_email_reply_tasks():
    """恢复邮件回复队列中的任务"""
    try:
        logger.info("🔄 开始恢复邮件回复队列中的任务...")

        # 获取所有邮件回复任务的键
        task_keys = redis_client.keys("email_reply_task:*")

        if not task_keys:
            logger.info("✅ 没有需要恢复的邮件回复任务")
            return

        recovered_count = 0
        requeued_count = 0

        for task_key in task_keys:
            try:
                task_data = redis_client.hgetall(task_key)

                if not task_data:
                    continue

                task_id = task_data.get("task_id")
                status = task_data.get("status", "unknown")

                logger.info(f"发现任务: {task_id}, 状态: {status}")

                # 处理不同状态的任务
                if status in ["queued", "processing"]:
                    # 重新排队执行
                    queue_data = {
                        "task_id": task_data.get("task_id"),
                        "email_content": task_data.get("email_content"),
                        "category_list": task_data.get("category_list"),
                        "user_id": task_data.get("user_id"),
                        "email_basic_id": task_data.get("email_basic_id"),
                        "callback_url": task_data.get("callback_url")
                    }

                    # 检查必要字段是否存在
                    if all(queue_data.values()):
                        # 更新任务状态为重新排队
                        redis_client.hset(task_key, "status", "queued")
                        redis_client.hset(task_key, "updated_at", datetime.now().isoformat())
                        redis_client.hset(task_key, "recovered_at", datetime.now().isoformat())

                        # 重新放入队列
                        redis_client.rpush(EMAIL_REPLY_QUEUE, json.dumps(queue_data))

                        requeued_count += 1
                        logger.info(f"✅ 任务 {task_id} 已重新排队 (原状态: {status})")
                    else:
                        logger.warning(f"⚠️ 任务 {task_id} 数据不完整，跳过恢复")
                        # 标记为失败
                        redis_client.hset(task_key, "status", "failed")
                        redis_client.hset(task_key, "error", "任务数据不完整，无法恢复")
                        redis_client.hset(task_key, "updated_at", datetime.now().isoformat())

                elif status in ["completed", "failed"]:
                    # 已完成或失败的任务，不需要恢复
                    logger.info(f"ℹ️ 任务 {task_id} 已完成 (状态: {status})，无需恢复")

                else:
                    logger.warning(f"⚠️ 任务 {task_id} 状态未知: {status}")

                recovered_count += 1

            except Exception as e:
                logger.error(f"恢复任务失败 {task_key}: {str(e)}")
                continue

        logger.info(f"✅ 邮件回复队列恢复完成: 检查了 {recovered_count} 个任务，重新排队 {requeued_count} 个任务")

        # 显示当前队列长度
        queue_length = redis_client.llen(EMAIL_REPLY_QUEUE)
        logger.info(f"📊 当前邮件回复队列长度: {queue_length}")

    except Exception as e:
        logger.error(f"恢复邮件回复队列失败: {str(e)}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")


def cleanup_expired_email_tasks():
    """清理过期的邮件回复任务"""
    try:
        logger.info("🧹 开始清理过期的邮件回复任务...")

        # 获取所有邮件回复任务的键
        task_keys = redis_client.keys("email_reply_task:*")

        if not task_keys:
            logger.info("✅ 没有需要清理的任务")
            return

        current_time = datetime.now()
        expired_count = 0

        # 设置过期时间阈值（24小时）
        expiry_hours = 24

        for task_key in task_keys:
            try:
                task_data = redis_client.hgetall(task_key)

                if not task_data:
                    continue

                created_at_str = task_data.get("created_at")
                status = task_data.get("status", "unknown")
                task_id = task_data.get("task_id")

                if not created_at_str:
                    continue

                # 解析创建时间
                created_at = datetime.fromisoformat(created_at_str)
                age_hours = (current_time - created_at).total_seconds() / 3600

                # 清理超过24小时的已完成或失败任务
                if age_hours > expiry_hours and status in ["completed", "failed"]:
                    redis_client.delete(task_key)
                    expired_count += 1
                    logger.info(f"🗑️ 清理过期任务: {task_id} (状态: {status}, 年龄: {age_hours:.1f}小时)")

                # 清理超过1小时的处理中任务（可能是僵尸任务）
                elif age_hours > 1 and status == "processing":
                    logger.warning(f"⚠️ 发现可能的僵尸任务: {task_id} (处理中超过1小时)")
                    # 将其标记为失败而不是删除，以便调试
                    redis_client.hset(task_key, "status", "failed")
                    redis_client.hset(task_key, "error", "任务处理超时，可能是僵尸任务")
                    redis_client.hset(task_key, "updated_at", current_time.isoformat())

            except Exception as e:
                logger.error(f"清理任务失败 {task_key}: {str(e)}")
                continue

        logger.info(f"✅ 过期任务清理完成: 清理了 {expired_count} 个过期任务")

    except Exception as e:
        logger.error(f"清理过期任务失败: {str(e)}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring server status"""
    try:
        # 检查邮件回复队列状态
        queue_length = redis_client.llen(EMAIL_REPLY_QUEUE)
        worker_count = len(worker_threads)

        # 统计任务状态
        task_keys = redis_client.keys("email_reply_task:*")
        task_stats = {"queued": 0, "processing": 0, "completed": 0, "failed": 0, "unknown": 0}

        for task_key in task_keys:
            task_data = redis_client.hgetall(task_key)
            status = task_data.get("status", "unknown")
            task_stats[status] = task_stats.get(status, 0) + 1

        return {
            "status": "healthy",
            "agent_initialized": agent is not None,
            "timestamp": datetime.now().isoformat(),
            "email_reply_queue": {
                "queue_length": queue_length,
                "worker_threads": worker_count,
                "task_statistics": task_stats
            }
        }
    except Exception as e:
        return {
            "status": "healthy",
            "agent_initialized": agent is not None,
            "timestamp": datetime.now().isoformat(),
            "email_reply_queue": {
                "error": f"无法获取队列状态: {str(e)}"
            }
        }


@app.post("/send_message")
async def send_message_endpoint(request: MessageRequest):
    """Send a message to the agent and get the response"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    # Register tools for restored MCP connections (one-time only)
    global _mcp_tools_registered
    if not _mcp_tools_registered:
        register_mcp_tools_for_restored_connections()
        _mcp_tools_registered = True

    # Check for missing API keys
    api_key_check = check_missing_api_keys(agent)
    if "error" in api_key_check:
        raise HTTPException(status_code=500, detail=api_key_check["error"][0])

    if api_key_check["missing_keys"]:
        # Return a special response indicating missing API keys
        return MessageResponse(
            response=f"Missing API keys for {api_key_check['model_type']} model: {', '.join(api_key_check['missing_keys'])}. Please provide the required API keys.",
            status="missing_api_keys",
        )

    try:
        # Handle user context switching if user_id is provided
        if request.user_id:
            switch_user_context(agent, request.user_id)

        # 简化日志输出：只显示消息长度和关键参数
        message_preview = request.message[:100] + "..." if len(request.message) > 100 else request.message
        print(
            f"📨 API请求: user_id={request.user_id} | memorizing={request.memorizing} | message_len={len(request.message)} | preview={message_preview}"
        )

        # Run the blocking agent.send_message() in a background thread to avoid blocking other requests
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            lambda: agent.send_message(
                message=request.message,
                image_uris=request.image_uris,
                sources=request.sources,  # Pass sources to agent
                voice_files=request.voice_files,  # Pass voice files to agent
                memorizing=request.memorizing,
                user_id=request.user_id,
            ),
        )

        # 简化响应日志：只显示前100个字符
        response_preview = response[:100] + "..." if response and len(response) > 100 else response
        print(f"✅ API响应: {response_preview}")

        if response == "ERROR":
            raise HTTPException(status_code=500, detail="Agent returned an error")

        # Handle case where agent returns None
        if response is None:
            if request.memorizing:
                # When memorizing=True, None response is expected (no response needed)
                response = ""
            else:
                # When memorizing=False, None response is an error
                response = "I received your message but couldn't generate a response. Please try again."

        return MessageResponse(response=response)

    except Exception as e:
        print(f"Error in send_message_endpoint: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Error processing message: {str(e)}"
        )


@app.post("/workflow/extract", response_model=WorkflowExtractionResponse)
async def extract_workflow(request: WorkflowExtractionRequest):
    """
    工作流程提取接口

    一次性完成：
    1. 分析邮件/请求内容
    2. 提取关键问题和信息
    3. 从 procedural_memory 查询匹配的工作流程
    4. 返回结构化的完整工作流程
    """
    try:
        # 参数验证
        if not request.email_account.strip():
            raise HTTPException(status_code=400, detail="email_account不能为空")
        if not request.content.strip():
            raise HTTPException(status_code=400, detail="content不能为空")

        logger.info(
            f"[WORKFLOW_API] 开始处理工作流程提取 - email_account: {request.email_account}, content_length: {len(request.content)}")

        # 检查 agent 是否已初始化
        if agent is None:
            raise HTTPException(status_code=500, detail="Agent未初始化")

        # 解析/创建用户并在后台线程中调用 workflow_agent
        user = agent.client.server.user_manager.get_or_create_user_by_email(request.email_account)
        resolved_user_id = user.id

        loop = asyncio.get_event_loop()
        workflow_result = await loop.run_in_executor(
            None,
            lambda: agent.extract_workflow(
                content=request.content,
                user_id=resolved_user_id
            )
        )

        # 处理响应
        if workflow_result is None or (isinstance(workflow_result, str) and workflow_result.strip() == ""):
            logger.error("[WORKFLOW_API] 返回空响应")
            raise HTTPException(status_code=500, detail="工作流程提取失败：返回空内容")

        if isinstance(workflow_result, str) and workflow_result.startswith("ERROR"):
            logger.error(f"[WORKFLOW_API] 返回错误: {workflow_result}")
            raise HTTPException(status_code=500, detail=f"工作流程提取失败: {workflow_result}")

        logger.info(f"[WORKFLOW_API] 工作流程提取成功 - 类型: {type(workflow_result).__name__}")

        return WorkflowExtractionResponse(workflow_result=workflow_result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[WORKFLOW_API] 处理失败: {str(e)}")
        logger.error(f"[WORKFLOW_API] 错误堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"工作流程提取失败: {str(e)}")


@app.post("/send_streaming_message")
async def send_streaming_message_endpoint(request: MessageRequest):
    """Send a message to the agent and stream intermediate messages and final response"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    # Register tools for restored MCP connections (one-time only)
    global _mcp_tools_registered
    if not _mcp_tools_registered:
        register_mcp_tools_for_restored_connections()
        _mcp_tools_registered = True

    # Check for missing API keys
    api_key_check = check_missing_api_keys(agent)
    if "error" in api_key_check:
        raise HTTPException(status_code=500, detail=api_key_check["error"][0])

    if api_key_check["missing_keys"]:
        # Return a special SSE event for missing API keys
        async def missing_keys_response():
            yield f"data: {json.dumps({'type': 'missing_api_keys', 'missing_keys': api_key_check['missing_keys'], 'model_type': api_key_check['model_type']})}\n\n"

        return StreamingResponse(
            missing_keys_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            },
        )

    agent.update_chat_agent_system_prompt(request.is_screen_monitoring)

    # Create a queue to collect intermediate messagess
    message_queue = queue.Queue()

    def display_intermediate_message(message_type: str, message: str):
        """Callback function to capture intermediate messages"""
        message_queue.put(
            {"type": "intermediate", "message_type": message_type, "content": message}
        )

    def request_user_confirmation(confirmation_type: str, details: dict) -> bool:
        """Request confirmation from user and wait for response"""
        import uuid

        confirmation_id = str(uuid.uuid4())

        # Create a queue for this specific confirmation
        confirmation_result_queue = queue.Queue()
        confirmation_queues[confirmation_id] = confirmation_result_queue

        # Put confirmation request in message queue
        message_queue.put(
            {
                "type": "confirmation_request",
                "confirmation_type": confirmation_type,
                "confirmation_id": confirmation_id,
                "details": details,
            }
        )

        # Wait for confirmation response with timeout
        try:
            result = confirmation_result_queue.get(timeout=300)  # 5 minute timeout
            return result.get("confirmed", False)
        except queue.Empty:
            # Timeout - default to not confirmed
            return False
        finally:
            # Clean up the queue
            confirmation_queues.pop(confirmation_id, None)

    async def generate_stream():
        """Generator function for streaming responses"""
        try:
            # Start the agent processing in a separate thread
            result_queue = queue.Queue()

            async def run_agent():
                try:
                    # find the current active user
                    users = agent.client.server.user_manager.list_users()
                    active_user = next(
                        (user for user in users if user.status == "active"), None
                    )
                    current_user_id = active_user.id if active_user else None

                    # Run agent.send_message in a background thread to avoid blocking
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None,  # Use default ThreadPoolExecutor
                        lambda: agent.send_message(
                            message=request.message,
                            image_uris=request.image_uris,
                            sources=request.sources,  # Pass sources to agent
                            voice_files=request.voice_files,  # Pass raw voice files
                            memorizing=request.memorizing,
                            display_intermediate_message=display_intermediate_message,
                            request_user_confirmation=request_user_confirmation,
                            is_screen_monitoring=request.is_screen_monitoring,
                            user_id=current_user_id,
                        ),
                    )
                    # Handle various response cases
                    if response is None:
                        if request.memorizing:
                            result_queue.put({"type": "final", "response": ""})
                        else:
                            print("[DEBUG] Agent returned None response")
                            result_queue.put(
                                {"type": "error", "error": "Agent returned no response"}
                            )
                    elif isinstance(response, str) and response.startswith("ERROR_"):
                        # Handle specific error types from agent wrapper
                        print(f"[DEBUG] Agent returned specific error: {response}")
                        if response == "ERROR_RESPONSE_FAILED":
                            print("[DEBUG] - Message queue response failed")
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Message processing failed in agent queue",
                                }
                            )
                        elif response == "ERROR_INVALID_RESPONSE_STRUCTURE":
                            print(
                                "[DEBUG] - Response structure invalid (missing messages or insufficient count)"
                            )
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Invalid response structure from agent",
                                }
                            )
                        elif response == "ERROR_NO_TOOL_CALL":
                            print(
                                "[DEBUG] - Expected message missing tool_call attribute"
                            )
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Agent response missing required tool call",
                                }
                            )
                        elif response == "ERROR_NO_MESSAGE_IN_ARGS":
                            print("[DEBUG] - Tool call arguments missing 'message' key")
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Agent tool call missing message content",
                                }
                            )
                        elif response == "ERROR_PARSING_EXCEPTION":
                            print(
                                "[DEBUG] - Exception occurred during response parsing"
                            )
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Failed to parse agent response",
                                }
                            )
                        else:
                            print(f"[DEBUG] - Unknown error type: {response}")
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": f"Unknown agent error: {response}",
                                }
                            )
                    elif response == "ERROR":
                        print("[DEBUG] Agent returned generic ERROR string")
                        result_queue.put(
                            {"type": "error", "error": "Agent processing failed"}
                        )
                    elif not response or (
                            isinstance(response, str) and response.strip() == ""
                    ):
                        if request.memorizing:
                            print(
                                "[DEBUG] Agent returned empty response - expected for memorizing=True"
                            )
                            result_queue.put({"type": "final", "response": ""})
                        else:
                            print("[DEBUG] Agent returned empty response unexpectedly")
                            result_queue.put(
                                {
                                    "type": "error",
                                    "error": "Agent returned empty response",
                                }
                            )
                    else:
                        print(
                            f"[DEBUG] Agent returned successful response (length: {len(str(response))})"
                        )
                        result_queue.put({"type": "final", "response": response})

                except Exception as e:
                    print(f"[DEBUG] Exception in run_agent: {str(e)}")
                    print(f"Traceback: {traceback.format_exc()}")
                    result_queue.put({"type": "error", "error": str(e)})

            # Start agent processing as async task
            agent_task = asyncio.create_task(run_agent())

            # Keep track of whether we've sent the final result
            final_result_sent = False

            # Stream intermediate messages and wait for final result
            while not final_result_sent:
                # Check for intermediate messages first
                try:
                    intermediate_msg = message_queue.get_nowait()
                    yield f"data: {json.dumps(intermediate_msg)}\n\n"
                    continue  # Continue to next iteration to check for more messages
                except queue.Empty:
                    pass

                # Check for final result with timeout
                try:
                    # Use a short timeout to allow for intermediate messages
                    final_result = result_queue.get(timeout=0.1)
                    if final_result["type"] == "error":
                        yield f"data: {json.dumps({'type': 'error', 'error': final_result['error']})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'final', 'response': final_result['response']})}\n\n"
                    final_result_sent = True
                    break
                except queue.Empty:
                    # If no result yet, check if task is still running
                    if agent_task.done():
                        # Task is done but no result - this shouldn't happen, but handle it
                        try:
                            # Check if the task raised an exception
                            agent_task.result()
                        except Exception as e:
                            yield f"data: {json.dumps({'type': 'error', 'error': f'Agent processing failed: {str(e)}'})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'error', 'error': 'Agent processing completed unexpectedly without result'})}\n\n"
                        final_result_sent = True
                        break
                    # Otherwise continue the loop to check for more intermediate messages
                    await asyncio.sleep(0.1)  # Yield control to allow other operations

            # Make sure task completes
            if not agent_task.done():
                try:
                    await asyncio.wait_for(agent_task, timeout=5.0)
                except asyncio.TimeoutError:
                    agent_task.cancel()
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Agent processing timed out'})}\n\n"

        except Exception as e:
            print(f"Traceback: {traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    try:
        return StreamingResponse(
            generate_stream(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            },
        )
    except Exception as e:
        print(f"Error in send_streaming_message_endpoint: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")


@app.get("/personas", response_model=PersonaDetailsResponse)
async def get_personas(user_id: Optional[str] = None):
    """Get all personas with their details (name and text)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        persona_details = agent.get_persona_details()
        return PersonaDetailsResponse(personas=persona_details)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting personas: {str(e)}")


@app.post("/personas/update", response_model=UpdatePersonaResponse)
async def update_persona(request: UpdatePersonaRequest):
    """Update the agent's core memory persona text"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        agent.update_core_memory_persona(request.text)
        return UpdatePersonaResponse(
            success=True, message="Core memory persona updated successfully"
        )
    except Exception as e:
        return UpdatePersonaResponse(
            success=False, message=f"Error updating core memory persona: {str(e)}"
        )


@app.post("/personas/apply_template", response_model=UpdatePersonaResponse)
async def apply_persona_template(request: ApplyPersonaTemplateRequest):
    """Apply a persona template to the agent"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        agent.apply_persona_template(request.persona_name)
        return UpdatePersonaResponse(
            success=True,
            message=f"Persona template '{request.persona_name}' applied successfully",
        )
    except Exception as e:
        return UpdatePersonaResponse(
            success=False, message=f"Error applying persona template: {str(e)}"
        )


@app.post("/core_memory/update", response_model=UpdateCoreMemoryResponse)
async def update_core_memory(request: UpdateCoreMemoryRequest):
    """Update a specific core memory block with new text"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        agent.update_core_memory(text=request.text, label=request.label)
        return UpdateCoreMemoryResponse(
            success=True,
            message=f"Core memory block '{request.label}' updated successfully",
        )
    except Exception as e:
        return UpdateCoreMemoryResponse(
            success=False, message=f"Error updating core memory: {str(e)}"
        )


@app.get("/personas/core_memory", response_model=CoreMemoryPersonaResponse)
async def get_core_memory_persona(user_id: Optional[str] = None):
    """Get the core memory persona text"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        persona_text = agent.get_core_memory_persona()
        return CoreMemoryPersonaResponse(text=persona_text)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting core memory persona: {str(e)}"
        )


@app.get("/models/current", response_model=GetCurrentModelResponse)
async def get_current_model():
    """Get the current model being used by the agent"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        current_model = agent.get_current_model()
        return GetCurrentModelResponse(current_model=current_model)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting current model: {str(e)}"
        )


@app.post("/models/set", response_model=SetModelResponse)
async def set_model(request: SetModelRequest):
    """Set the model for the agent"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Check if this is a custom model
        custom_models_dir = Path.home() / ".mirix" / "custom_models"
        custom_config = None

        if custom_models_dir.exists():
            # Look for a config file that matches this model name
            for config_file in custom_models_dir.glob("*.yaml"):
                try:
                    with open(config_file, "r") as f:
                        config = yaml.safe_load(f)
                        if config and config.get("model_name") == request.model:
                            custom_config = config
                            print(
                                f"Found custom model config for '{request.model}' at {config_file}"
                            )
                            break
                except Exception as e:
                    print(f"Error reading custom model config {config_file}: {e}")
                    continue

        # Set the model with custom config if found, otherwise use standard method
        if custom_config:
            result = agent.set_model(request.model, custom_agent_config=custom_config)
        else:
            result = agent.set_model(request.model)

        return SetModelResponse(
            success=result["success"],
            message=result["message"],
            missing_keys=result.get("missing_keys", []),
            model_requirements=result.get("model_requirements", {}),
        )
    except Exception as e:
        return SetModelResponse(
            success=False,
            message=f"Error setting model: {str(e)}",
            missing_keys=[],
            model_requirements={},
        )


@app.get("/models/memory/current", response_model=GetCurrentModelResponse)
async def get_current_memory_model():
    """Get the current model being used by the memory manager"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        current_model = agent.get_current_memory_model()
        return GetCurrentModelResponse(current_model=current_model)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting current memory model: {str(e)}"
        )


@app.post("/models/memory/set", response_model=SetModelResponse)
async def set_memory_model(request: SetModelRequest):
    """Set the model for the memory manager"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Check if this is a custom model
        custom_models_dir = Path.home() / ".mirix" / "custom_models"
        custom_config = None

        if custom_models_dir.exists():
            # Look for a config file that matches this model name
            for config_file in custom_models_dir.glob("*.yaml"):
                try:
                    with open(config_file, "r") as f:
                        config = yaml.safe_load(f)
                        if config and config.get("model_name") == request.model:
                            custom_config = config
                            print(
                                f"Found custom model config for memory model '{request.model}' at {config_file}"
                            )
                            break
                except Exception as e:
                    print(f"Error reading custom model config {config_file}: {e}")
                    continue

        # Set the memory model with custom config if found, otherwise use standard method
        if custom_config:
            result = agent.set_memory_model(
                request.model, custom_agent_config=custom_config
            )
        else:
            result = agent.set_memory_model(request.model)

        return SetModelResponse(
            success=result["success"],
            message=result["message"],
            missing_keys=result.get("missing_keys", []),
            model_requirements=result.get("model_requirements", {}),
        )
    except Exception as e:
        return SetModelResponse(
            success=False,
            message=f"Error setting memory model: {str(e)}",
            missing_keys=[],
            model_requirements={},
        )


@app.post("/models/custom/add", response_model=AddCustomModelResponse)
async def add_custom_model(request: AddCustomModelRequest):
    """Add a custom model configuration"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Create config file for the custom model
        config = {
            "agent_name": "mirix",
            "model_name": request.model_name,
            "model_endpoint": request.model_endpoint,
            "api_key": request.api_key,
            "model_provider": "local_server",
            "generation_config": {
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "context_window": request.maximum_length,
            },
        }

        # Create custom models directory if it doesn't exist
        custom_models_dir = Path.home() / ".mirix" / "custom_models"
        custom_models_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from model name (sanitize for filesystem)
        safe_model_name = "".join(
            c for c in request.model_name if c.isalnum() or c in ("-", "_", ".")
        ).rstrip()
        config_filename = f"{safe_model_name}.yaml"
        config_file_path = custom_models_dir / config_filename

        # Save config to YAML file
        with open(config_file_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, indent=2)

        # Also set the model in the agent
        agent.set_model(request.model_name, custom_agent_config=config)

        return AddCustomModelResponse(
            success=True,
            message=f"Custom model '{request.model_name}' added successfully and saved to {config_file_path}",
        )

    except Exception as e:
        print(f"Error adding custom model: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return AddCustomModelResponse(
            success=False, message=f"Error adding custom model: {str(e)}"
        )


@app.get("/models/custom/list", response_model=ListCustomModelsResponse)
async def list_custom_models():
    """List all available custom models"""
    try:
        custom_models_dir = Path.home() / ".mirix" / "custom_models"
        models = []

        if custom_models_dir.exists():
            for config_file in custom_models_dir.glob("*.yaml"):
                try:
                    with open(config_file, "r") as f:
                        config = yaml.safe_load(f)
                        if config and "model_name" in config:
                            models.append(config["model_name"])
                except Exception as e:
                    print(f"Error reading custom model config {config_file}: {e}")
                    continue

        return ListCustomModelsResponse(models=models)

    except Exception as e:
        print(f"Error listing custom models: {e}")
        return ListCustomModelsResponse(models=[])


@app.get("/timezone/current", response_model=GetTimezoneResponse)
async def get_current_timezone():
    """Get the current timezone of the agent"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        if not target_user:
            raise HTTPException(status_code=404, detail="No user found")

        current_timezone = target_user.timezone
        return GetTimezoneResponse(timezone=current_timezone)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting current timezone: {str(e)}"
        )


@app.post("/timezone/set", response_model=SetTimezoneResponse)
async def set_timezone(request: SetTimezoneRequest):
    """Set the timezone for the agent"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        if not target_user:
            return SetTimezoneResponse(success=False, message="No user found")

        # Update the timezone for the active user
        agent.client.server.user_manager.update_user_timezone(
            user_id=target_user.id, timezone_str=request.timezone
        )

        return SetTimezoneResponse(
            success=True,
            message=f"Timezone '{request.timezone}' set successfully for user {target_user.name}",
        )
    except Exception as e:
        return SetTimezoneResponse(
            success=False, message=f"Error setting timezone: {str(e)}"
        )


@app.get("/screenshot_setting", response_model=ScreenshotSettingResponse)
async def get_screenshot_setting():
    """Get the current screenshot setting"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    return ScreenshotSettingResponse(
        success=True,
        include_recent_screenshots=agent.include_recent_screenshots,
        message="Screenshot setting retrieved successfully",
    )


@app.post("/screenshot_setting/set", response_model=ScreenshotSettingResponse)
async def set_screenshot_setting(request: ScreenshotSettingRequest):
    """Set whether to include recent screenshots in messages"""

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        agent.set_include_recent_screenshots(request.include_recent_screenshots)
        return ScreenshotSettingResponse(
            success=True,
            include_recent_screenshots=request.include_recent_screenshots,
            message=f"Screenshot setting updated: {'enabled' if request.include_recent_screenshots else 'disabled'}",
        )
    except Exception as e:
        return ScreenshotSettingResponse(
            success=False,
            include_recent_screenshots=False,
            message=f"Error updating screenshot setting: {str(e)}",
        )


@app.get("/api_keys/check", response_model=ApiKeyCheckResponse)
async def check_api_keys():
    """Check for missing API keys based on current agent configuration"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Use the new AgentWrapper method
        api_key_status = agent.check_api_key_status()

        return ApiKeyCheckResponse(
            missing_keys=api_key_status["missing_keys"],
            model_type=api_key_status.get("model_requirements", {}).get(
                "current_model", "unknown"
            ),
            requires_api_key=len(api_key_status["missing_keys"]) > 0,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error checking API keys: {str(e)}"
        )


@app.post("/api_keys/update", response_model=ApiKeyUpdateResponse)
async def update_api_key(request: ApiKeyRequest):
    """Update an API key value"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Use the new AgentWrapper method which handles .env file saving
        result = agent.provide_api_key(request.key_name, request.key_value)

        # Also update environment variable and model_settings for backwards compatibility
        if result["success"]:
            os.environ[request.key_name] = request.key_value

            from mirix.settings import model_settings

            setting_name = request.key_name.lower()
            if hasattr(model_settings, setting_name):
                setattr(model_settings, setting_name, request.key_value)
        else:
            # If AgentWrapper doesn't support this key type, fall back to manual .env saving
            if "Unsupported API key type" in result["message"]:
                # Save to .env file manually for non-Gemini keys
                _save_api_key_to_env_file(request.key_name, request.key_value)
                os.environ[request.key_name] = request.key_value

                from mirix.settings import model_settings

                setting_name = request.key_name.lower()
                if hasattr(model_settings, setting_name):
                    setattr(model_settings, setting_name, request.key_value)

                result["success"] = True
                result["message"] = (
                    f"API key '{request.key_name}' saved to .env file successfully"
                )

        return ApiKeyUpdateResponse(
            success=result["success"], message=result["message"]
        )
    except Exception as e:
        return ApiKeyUpdateResponse(
            success=False, message=f"Error updating API key: {str(e)}"
        )


def _save_api_key_to_env_file(key_name: str, api_key: str):
    """
    Helper function to save API key to .env file for non-AgentWrapper keys.
    """
    from pathlib import Path

    # Find the .env file (look in current directory and parent directories)
    env_file_path = None
    current_path = Path.cwd()

    # Check current directory and up to 3 parent directories
    for _ in range(4):
        potential_env_path = current_path / ".env"
        if potential_env_path.exists():
            env_file_path = potential_env_path
            break
        current_path = current_path.parent

    # If no .env file found, create one in the current working directory
    if env_file_path is None:
        env_file_path = Path.cwd() / ".env"

    # Read existing .env file content
    env_content = {}
    if env_file_path.exists():
        with open(env_file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_content[key.strip()] = value.strip()

    # Update the API key
    env_content[key_name] = api_key

    # Write back to .env file
    with open(env_file_path, "w") as f:
        for key, value in env_content.items():
            f.write(f"{key}={value}\n")

    print(f"API key {key_name} saved to {env_file_path}")


# Memory endpoints
@app.get("/memory/episodic")
async def get_episodic_memory(user_id: Optional[str] = None):
    """Get episodic memory (past events)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        # Access the episodic memory manager through the client
        client = agent.client
        episodic_manager = client.server.episodic_memory_manager

        # Get episodic events using the correct method name
        events = episodic_manager.list_episodic_memory(
            agent_state=agent.agent_states.episodic_memory_agent_state,
            actor=target_user,
            limit=50,
            timezone_str=target_user.timezone,
        )

        # Transform to frontend format
        episodic_items = []
        for event in events:
            episodic_items.append(
                {
                    "timestamp": event.occurred_at.isoformat()
                    if event.occurred_at
                    else None,
                    "summary": event.summary,
                    "details": event.details,
                    "event_type": event.event_type,
                    "tree_path": event.tree_path if hasattr(event, "tree_path") else [],
                }
            )

        return episodic_items

    except Exception as e:
        print(f"Error retrieving episodic memory: {str(e)}")
        # Return empty list if no memory or error
        return []


@app.get("/memory/semantic")
async def get_semantic_memory(user_id: Optional[str] = None):
    """Get semantic memory (knowledge)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        client = agent.client
        semantic_items_list = []

        # Get semantic memory items
        try:
            semantic_manager = client.server.semantic_memory_manager
            semantic_items = semantic_manager.list_semantic_items(
                agent_state=agent.agent_states.semantic_memory_agent_state,
                actor=target_user,
                limit=50,
                timezone_str=target_user.timezone,
            )

            for item in semantic_items:
                semantic_items_list.append(
                    {
                        "title": item.name,
                        "type": "semantic",
                        "summary": item.summary,
                        "details": item.details,
                        "tree_path": item.tree_path
                        if hasattr(item, "tree_path")
                        else [],
                    }
                )
        except Exception as e:
            print(f"Error retrieving semantic memory: {str(e)}")

        return semantic_items_list

    except Exception as e:
        print(f"Error retrieving semantic memory: {str(e)}")
        return []


@app.get("/memory/procedural")
async def get_procedural_memory(user_id: Optional[str] = None):
    """Get procedural memory (skills and procedures)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        client = agent.client
        procedural_items_list = []

        # Get procedural memory items
        try:
            procedural_manager = client.server.procedural_memory_manager
            procedural_items = procedural_manager.list_procedures(
                agent_state=agent.agent_states.procedural_memory_agent_state,
                actor=target_user,
                limit=50,
                timezone_str=target_user.timezone,
            )

            for item in procedural_items:
                # Parse steps if it's a JSON string
                steps = item.steps
                if isinstance(steps, str):
                    try:
                        steps = json.loads(steps)
                        # Extract just the instruction text for simpler frontend display
                        if (
                                isinstance(steps, list)
                                and steps
                                and isinstance(steps[0], dict)
                        ):
                            steps = [
                                step.get("instruction", str(step)) for step in steps
                            ]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        # If parsing fails, keep as string and split by common delimiters
                        if isinstance(steps, str):
                            steps = [
                                s.strip()
                                for s in steps.replace("\n", "|").split("|")
                                if s.strip()
                            ]
                        else:
                            steps = []

                procedural_items_list.append(
                    {
                        "title": item.entry_type,
                        "type": "procedural",
                        "summary": item.summary,
                        "steps": steps if isinstance(steps, list) else [],
                        "tree_path": item.tree_path
                        if hasattr(item, "tree_path")
                        else [],
                    }
                )

        except Exception as e:
            print(f"Error retrieving procedural memory: {str(e)}")

        return procedural_items_list

    except Exception as e:
        print(f"Error retrieving procedural memory: {str(e)}")
        return []


@app.get("/memory/resources")
async def get_resource_memory(user_id: Optional[str] = None):
    """Get resource memory (docs and files)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        client = agent.client
        resource_manager = client.server.resource_memory_manager

        # Get resource memory items using correct method name
        resources = resource_manager.list_resources(
            agent_state=agent.agent_states.resource_memory_agent_state,
            actor=target_user,
            limit=50,
            timezone_str=target_user.timezone,
        )

        # Transform to frontend format
        docs_files = []
        for resource in resources:
            docs_files.append(
                {
                    "filename": resource.title,
                    "type": resource.resource_type,
                    "summary": resource.summary
                               or (
                                   resource.content[:200] + "..."
                                   if len(resource.content) > 200
                                   else resource.content
                               ),
                    "last_accessed": resource.updated_at.isoformat()
                    if resource.updated_at
                    else None,
                    "size": resource.metadata_.get("size")
                    if resource.metadata_
                    else None,
                    "tree_path": resource.tree_path
                    if hasattr(resource, "tree_path")
                    else [],
                }
            )

        return docs_files

    except Exception as e:
        print(f"Error retrieving resource memory: {str(e)}")
        return []


@app.get("/memory/core")
async def get_core_memory(user_id: Optional[str] = None):
    """Get core memory (understanding of user)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        # Directly query blocks table by user_id
        from mirix.orm import Block
        from mirix.server.server import db_context
        from sqlalchemy import select

        print(f"🔍 查询核心记忆 - user_id: {target_user.id}, user_name: {target_user.name}")

        with db_context() as session:
            stmt = select(Block).where(Block.user_id == target_user.id)
            blocks = session.execute(stmt).scalars().all()

            print(f"🔍 查询到 {len(blocks)} 个 blocks:")
            for b in blocks:
                value_preview = b.value[:100] if b.value else 'None'
                print(f"  - label={b.label}, user_id={b.user_id}, value前100字={value_preview}")

        core_understanding = []
        total_characters = 0

        # Extract understanding from memory blocks (skip persona block)
        for block in blocks:
            if block.value and block.value.strip() and block.label.lower() != "persona":
                block_chars = len(block.value)
                total_characters += block_chars

                core_item = {
                    "aspect": block.label,
                    "understanding": block.value,
                    "character_count": block_chars,
                    "total_characters": total_characters,
                    "max_characters": block.limit,
                    "last_updated": None,
                }

                core_understanding.append(core_item)
                print(f"✅ 返回 human block，内容前50字: {block.value[:50]}")

        print(f"🔍 最终返回 {len(core_understanding)} 个记忆项")
        return core_understanding

    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        return []


@app.get("/memory/credentials")
async def get_credentials_memory(user_id: Optional[str] = None):
    """Get credentials memory (knowledge vault with masked content)"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Get target user based on user_id parameter
        target_user = get_user_or_default(agent, user_id)

        client = agent.client
        knowledge_vault_manager = client.server.knowledge_vault_manager

        # Get knowledge vault items using correct method name
        vault_items = knowledge_vault_manager.list_knowledge(
            actor=target_user,
            agent_state=agent.agent_states.knowledge_vault_agent_state,
            limit=50,
            timezone_str=target_user.timezone,
        )

        # Transform to frontend format with masked content
        credentials = []
        for item in vault_items:
            credentials.append(
                {
                    "caption": item.caption,
                    "entry_type": item.entry_type,
                    "source": item.source,
                    "sensitivity": item.sensitivity,
                    "content": "••••••••••••"
                    if item.sensitivity == "high"
                    else item.secret_value,  # Always mask the actual content
                }
            )

        return credentials

    except Exception as e:
        print(f"Error retrieving credentials memory: {str(e)}")
        return []


@app.post("/conversation/clear", response_model=ClearConversationResponse)
async def clear_conversation_history():
    """Permanently clear all conversation history for the current agent (memories are preserved)"""
    try:
        if agent is None:
            raise HTTPException(status_code=400, detail="Agent not initialized")

        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)

        # Get current message count for this specific actor for reporting
        current_messages = agent.client.server.agent_manager.get_in_context_messages(
            agent_id=agent.agent_states.agent_state.id, actor=target_user
        )
        # Count messages belonging to this actor (excluding system messages)
        actor_messages_count = len(
            [
                msg
                for msg in current_messages
                if msg.role != "system" and msg.user_id == target_user.id
            ]
        )

        # Clear conversation history using the agent manager reset_messages method
        agent.client.server.agent_manager.reset_messages(
            agent_id=agent.agent_states.agent_state.id,
            actor=target_user,
            add_default_initial_messages=True,  # Keep system message and initial setup
        )

        return ClearConversationResponse(
            success=True,
            message=f"Successfully cleared conversation history for {target_user.name}. Messages from other users and system messages preserved.",
            messages_deleted=actor_messages_count,
        )

    except Exception as e:
        print(f"Error clearing conversation history: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Error clearing conversation: {str(e)}"
        )


@app.post("/export/memories", response_model=ExportMemoriesResponse)
async def export_memories(request: ExportMemoriesRequest):
    """Export memories to Excel file with separate sheets for each memory type"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Find the current active user
        users = agent.client.server.user_manager.list_users()
        active_user = next((user for user in users if user.status == "active"), None)
        target_user = active_user if active_user else (users[0] if users else None)
        result = agent.export_memories_to_excel(
            actor=target_user,
            file_path=request.file_path,
            memory_types=request.memory_types,
            include_embeddings=request.include_embeddings,
        )

        if result["success"]:
            return ExportMemoriesResponse(
                success=True,
                message=result["message"],
                exported_counts=result["exported_counts"],
                total_exported=result["total_exported"],
                file_path=result["file_path"],
            )
        else:
            raise HTTPException(status_code=500, detail=result["message"])

    except Exception as e:
        print(f"Error exporting memories: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Failed to export memories: {str(e)}"
        )


@app.post("/reflexion", response_model=ReflexionResponse)
async def trigger_reflexion(request: ReflexionRequest):
    """Trigger reflexion agent to reorganize memory - runs in separate thread to not block other requests"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        print("Starting reflexion process...")
        start_time = datetime.now()

        # Run reflexion in a separate thread to avoid blocking other requests
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            _run_reflexion_process,
            agent,
        )

        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        print(f"Reflexion process completed in {processing_time:.2f} seconds")

        return ReflexionResponse(
            success=result["success"],
            message=result["message"],
            processing_time=processing_time,
        )

    except Exception as e:
        print(f"Error in reflexion endpoint: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Reflexion process failed: {str(e)}"
        )


# MCP Marketplace endpoints
@app.get("/mcp/marketplace")
async def get_marketplace():
    """Get available MCP servers from marketplace"""
    marketplace = get_mcp_marketplace()
    servers = marketplace.get_all_servers()
    categories = marketplace.get_categories()

    # Check connection status
    mcp_manager = get_mcp_client_manager()
    connected_servers = mcp_manager.list_servers()

    # Debug logging
    logger.debug(
        f"MCP Marketplace: {len(connected_servers)} connected servers: {connected_servers}"
    )

    server_data = []
    for server in servers:
        server_dict = server.to_dict()
        server_dict["is_connected"] = server.id in connected_servers
        server_data.append(server_dict)

    return {"servers": server_data, "categories": categories}


@app.get("/mcp/status")
async def get_mcp_status():
    """Get current MCP connection status"""
    try:
        mcp_manager = get_mcp_client_manager()
        connected_servers = mcp_manager.list_servers()

        # Get detailed status for each connected server
        server_status = {}
        for server_name in connected_servers:
            try:
                # Try to get server info to verify it's actually working
                info = mcp_manager.get_server_info(server_name)
                server_status[server_name] = {
                    "connected": True,
                    "status": "active",
                    "info": info,
                }
            except Exception as e:
                server_status[server_name] = {
                    "connected": False,
                    "status": "error",
                    "error": str(e),
                }

        return {
            "connected_servers": connected_servers,
            "server_count": len(connected_servers),
            "server_status": server_status,
        }

    except Exception as e:
        logger.error(f"Error getting MCP status: {str(e)}")
        return {
            "connected_servers": [],
            "server_count": 0,
            "server_status": {},
            "error": str(e),
        }


@app.get("/mcp/marketplace/search")
async def search_mcp_marketplace(query: str = ""):
    """Search MCP marketplace"""
    try:
        marketplace = get_mcp_marketplace()

        if query.strip():
            results = marketplace.search(query)
        else:
            results = marketplace.get_all_servers()

        # Check connection status
        mcp_manager = get_mcp_client_manager()
        connected_servers = mcp_manager.list_servers()

        result_data = []
        for server in results:
            server_dict = server.to_dict()
            server_dict["is_connected"] = server.id in connected_servers
            result_data.append(server_dict)

        return {"results": result_data}

    except Exception as e:
        logger.error(f"Error searching MCP marketplace: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to search MCP marketplace: {str(e)}"
        )


@app.post("/mcp/marketplace/connect")
async def connect_mcp_server(request: dict):
    """Connect to an MCP server"""
    try:
        server_id = request.get("server_id")
        env_vars = request.get("env_vars", {})

        if not server_id:
            raise HTTPException(status_code=400, detail="server_id is required")

        marketplace = get_mcp_marketplace()
        server_listing = marketplace.get_server(server_id)

        if not server_listing:
            raise HTTPException(
                status_code=404, detail=f"Server {server_id} not found in marketplace"
            )

        mcp_manager = get_mcp_client_manager()

        # Special handling for Gmail - handle OAuth flow directly in backend
        if server_id == "gmail-native":
            client_id = env_vars.get("client_id")
            client_secret = env_vars.get("client_secret")

            if not client_id or not client_secret:
                raise HTTPException(
                    status_code=400,
                    detail="client_id and client_secret are required for Gmail integration",
                )

            # Handle Gmail OAuth and MCP connection directly
            success = await handle_gmail_connection(
                client_id, client_secret, server_listing.id
            )

        else:
            # Create stdio config for other servers
            config = StdioServerConfig(
                server_name=server_listing.id,
                command=server_listing.command,
                args=server_listing.args,
                env={**(server_listing.env or {}), **env_vars},
            )
            success = mcp_manager.add_server(config, env_vars)

        if success:
            # Register tools for this server
            mcp_tool_registry = get_mcp_tool_registry()
            # Get current user (using agent's user for now)
            if agent and agent.client.user:
                current_user = agent.client.user
                registered_tools = mcp_tool_registry.register_mcp_tools(
                    current_user, [server_listing.id]
                )
                tools_count = len(registered_tools)
            else:
                tools_count = 0

            # Add MCP tool to the current chat agent if available
            if agent and agent.client.user and hasattr(agent, "agent_states"):
                # Update the agent's MCP tools list
                agent.client.server.agent_manager.add_mcp_tool(
                    agent_id=agent.agent_states.agent_state.id,
                    mcp_tool_name=server_listing.id,
                    tool_ids=list(
                        set(
                            [tool.id for tool in registered_tools]
                            + [
                                tool.id
                                for tool in agent.client.server.agent_manager.get_agent_by_id(
                                    agent.agent_states.agent_state.id,
                                    actor=agent.client.user,
                                ).tools
                            ]
                        )
                    ),
                    actor=agent.client.user,
                )

                print(
                    f"✅ Added MCP tool '{server_listing.id}' to agent '{agent.agent_states.agent_state.name}'"
                )

            return {
                "success": True,
                "server_name": server_listing.name,
                "tools_count": tools_count,
                "message": f"Successfully connected to {server_listing.name}",
            }
        else:
            return {
                "success": False,
                "error": f"Failed to connect to {server_listing.name}",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting MCP server: {str(e)}")
        return {"success": False, "error": f"Connection failed: {str(e)}"}


@app.post("/mcp/marketplace/disconnect")
async def disconnect_mcp_server(request: dict):
    """Disconnect from an MCP server"""

    server_id = request.get("server_id")

    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")

    mcp_manager = get_mcp_client_manager()
    success = mcp_manager.remove_server(server_id)

    if success:
        # Unregister tools for this server and get the list of unregistered tool IDs
        mcp_tool_registry = get_mcp_tool_registry()
        if agent and agent.client.user:
            current_user = agent.client.user
            unregistered_tool_ids = mcp_tool_registry.unregister_mcp_tools(
                current_user, server_id
            )
            logger.info(
                f"Unregistered {len(unregistered_tool_ids)} tools for server {server_id}"
            )

            # Remove MCP tool from the current chat agent if available
            if hasattr(agent, "agent_states"):
                # Get current agent state
                current_agent = agent.client.server.agent_manager.get_agent_by_id(
                    agent.agent_states.agent_state.id, actor=agent.client.user
                )

                # Remove the specific MCP server from the mcp_tools list
                updated_mcp_tools = [
                    tool
                    for tool in (current_agent.mcp_tools or [])
                    if tool != server_id
                ]

                # Remove only the tools that belonged to this MCP server
                current_tool_ids = [tool.id for tool in current_agent.tools]
                updated_tool_ids = [
                    tool_id
                    for tool_id in current_tool_ids
                    if tool_id not in unregistered_tool_ids
                ]

                # Update the agent with the filtered lists
                agent.client.server.agent_manager.update_mcp_tools(
                    agent_id=agent.agent_states.agent_state.id,
                    mcp_tools=updated_mcp_tools,
                    tool_ids=updated_tool_ids,
                    actor=agent.client.user,
                )
                print(
                    f"✅ Removed MCP tool '{server_id}' and {len(unregistered_tool_ids)} associated tools from agent '{agent.agent_states.agent_state.name}'"
                )

        return {
            "success": True,
            "message": f"Successfully disconnected from {server_id}",
        }
    else:
        return {"success": False, "error": f"Failed to disconnect from {server_id}"}


def _run_reflexion_process(agent):
    """
    Run the reflexion process - this is the blocking function that runs in a separate thread.
    This function can be replaced with the actual reflexion agent logic.
    """
    try:
        # TODO: Replace this with actual reflexion agent logic
        # For now, this is a placeholder that simulates reflexion work

        agent.reflexion_on_memory()
        return {
            "success": True,
            "message": "Memory reorganization completed successfully. Reflexion agent has optimized memory structure and connections.",
        }

    except Exception as e:
        print(f"Error in reflexion process: {str(e)}")
        return {"success": False, "message": f"Reflexion process failed: {str(e)}"}


@app.post("/confirmation/respond")
async def respond_to_confirmation(request: ConfirmationRequest):
    """Handle user confirmation response"""
    confirmation_id = request.confirmation_id
    confirmed = request.confirmed

    # Find the confirmation queue for this ID
    confirmation_queue = confirmation_queues.get(confirmation_id)

    if confirmation_queue:
        # Send the confirmation result to the waiting thread
        confirmation_queue.put({"confirmed": confirmed})
        return {"success": True, "message": "Confirmation received"}
    else:
        return {"success": False, "message": "Confirmation ID not found or expired"}


@app.get("/users")
async def get_all_users():
    """Get all users in the system"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        users = agent.client.server.user_manager.list_users()
        return {"users": [user.model_dump() for user in users]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving users: {str(e)}")


class SwitchUserRequest(BaseModel):
    user_id: str


class SwitchUserResponse(BaseModel):
    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None


@app.post("/users/switch", response_model=SwitchUserResponse)
async def switch_user(request: SwitchUserRequest):
    """Switch the active user"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Use the existing switch_user_context function
        switch_user_context(agent, request.user_id)

        # Get the switched user details
        current_user = agent.client.user
        if current_user:
            return SwitchUserResponse(
                success=True,
                message=f"Successfully switched to user: {current_user.name}",
                user=current_user.model_dump(),
            )
        else:
            return SwitchUserResponse(
                success=False, message="Failed to switch user - user not found"
            )

    except Exception as e:
        return SwitchUserResponse(
            success=False, message=f"Error switching user: {str(e)}"
        )


class CreateUserRequest(BaseModel):
    name: str
    set_as_active: bool = True  # Whether to set this user as active when created


class CreateUserResponse(BaseModel):
    success: bool
    message: str
    user: Optional[Dict[str, Any]] = None


@app.post("/users/create", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    """Create a new user in the system"""
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # Use the AgentWrapper's create_user method
        result = agent.create_user(
            name=request.name, set_as_active=request.set_as_active
        )

        return CreateUserResponse(
            success=result["success"],
            message=result["message"],
            user=result["user"].model_dump(),
        )

    except Exception as e:
        return CreateUserResponse(
            success=False, message=f"Error creating user: {str(e)}"
        )


@app.post("/email/reply", response_model=EmailReplyResponse)
async def reply_to_email(request: EmailReplyRequest):
    """
    智能邮件回复接口 - 异步处理模式

    参数:
    - email_content: 需要回复的邮件内容（必需）
    - category_list: 邮件分类（必需）
    - email_account: 用户邮箱账户（必需）
    - email_basic_id: 邮件标识（必需）
    - callback_url: 回调地址（必需）

    返回:
    - task_id: 任务ID
    - status: 任务状态
    - message: 状态描述
    """
    request_start_time = time.time()
    
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")

    try:
        # 参数验证
        if not request.email_account.strip():
            raise HTTPException(status_code=400, detail="email_account不能为空")

        if not request.email_content.strip():
            raise HTTPException(status_code=400, detail="email_content不能为空")

        if not request.category_list.strip():
            raise HTTPException(status_code=400, detail="category_list不能为空")

        if not request.email_basic_id.strip():
            raise HTTPException(status_code=400, detail="email_basic_id不能为空")

        if not request.callback_url.strip():
            raise HTTPException(status_code=400, detail="callback_url不能为空")

        # 解析邮箱账户为用户ID（若不存在则创建）
        try:
            user_id = agent.client.server.user_manager.get_id_by_email(request.email_account)
        except Exception:
            user = agent.client.server.user_manager.get_or_create_user_by_email(request.email_account)
            user_id = user.id

        # 生成任务ID（使用MD5）
        task_id = generate_task_id(user_id, request.email_basic_id, request.email_content)

        # 检查任务是否已存在
        task_key = f"email_reply_task:{task_id}"
        existing_task = redis_client.hgetall(task_key)

        if existing_task:
            existing_status = existing_task.get("status", "unknown")

            # 如果任务状态是成功或执行中，返回现有任务信息
            if existing_status in ["completed", "processing"]:
                logger.info(f"任务 {task_id} 已存在且状态为 {existing_status}，返回现有任务信息")
                return EmailReplyResponse(
                    task_id=task_id,
                    status=existing_status,
                    message=f"任务已存在，状态: {existing_status}"
                )

            # 如果任务状态不是成功也不是执行中，删除现有任务并重新创建
            else:
                logger.info(f"任务 {task_id} 已存在但状态为 {existing_status}，删除并重新创建")
                redis_client.delete(task_key)

                # 如果任务在队列中，也需要尝试移除（虽然可能不在队列中）
                # 注意：Redis列表的移除操作比较复杂，这里我们让工作线程处理重复任务
                logger.info(f"已删除状态为 {existing_status} 的任务 {task_id}，将创建新任务")

        # 将任务信息存储到Redis
        task_data = {
            "task_id": task_id,
            "email_content": request.email_content,
            "category_list": request.category_list,
            "user_id": user_id,
            "email_basic_id": request.email_basic_id,
            "callback_url": request.callback_url,
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        redis_client.hset(task_key, mapping=task_data)
        # 设置过期时间为1小时
        redis_client.expire(task_key, 3600)

        # 将任务放入Redis队列
        queue_data = {
            "task_id": task_id,
            "email_content": request.email_content,
            "category_list": request.category_list,
            "user_id": user_id,
            "email_basic_id": request.email_basic_id,
            "callback_url": request.callback_url
        }
        redis_client.rpush(EMAIL_REPLY_QUEUE, json.dumps(queue_data))

        logger.info(f"邮件回复任务已排队: {task_id}")

        request_total_time = time.time() - request_start_time
        logger.info(f"[EMAIL_REPLY_API] 任务 {task_id} 接口处理完成，总耗时: {request_total_time:.3f}秒")

        return EmailReplyResponse(
            task_id=task_id,
            status="queued",
            message="任务已排队，将通过回调地址返回结果"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EMAIL_REPLY_API] 处理失败: {str(e)}")
        logger.error(f"[EMAIL_REPLY_API] 错误堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"邮件回复任务创建失败: {str(e)}")

        raise HTTPException(status_code=500, detail=f"邮件回复任务创建失败: {str(e)}")


@app.get("/email/reply/status/{task_id}")
async def get_email_reply_status(task_id: str):
    """
    查询邮件回复任务状态

    参数:
    - task_id: 任务ID

    返回:
    - 任务状态信息
    """
    try:
        task_key = f"email_reply_task:{task_id}"
        task_data = redis_client.hgetall(task_key)

        if not task_data:
            raise HTTPException(status_code=404, detail="任务不存在或已完成")

        return {
            "task_id": task_id,
            "status": task_data.get("status", "unknown"),
            "created_at": task_data.get("created_at"),
            "updated_at": task_data.get("updated_at"),
            "error": task_data.get("error"),
            "callback_error": task_data.get("callback_error")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询任务状态失败: {task_id}, 错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询任务状态失败: {str(e)}")


@app.post("/email/reply/queue/recover")
async def recover_email_queue():
    """手动触发邮件回复队列恢复"""
    try:
        recover_email_reply_tasks()
        return {"status": "success", "message": "队列恢复完成"}
    except Exception as e:
        logger.error(f"手动队列恢复失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"队列恢复失败: {str(e)}")


@app.post("/email/reply/queue/cleanup")
async def cleanup_email_queue():
    """手动触发过期任务清理"""
    try:
        cleanup_expired_email_tasks()
        return {"status": "success", "message": "过期任务清理完成"}
    except Exception as e:
        logger.error(f"手动任务清理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"任务清理失败: {str(e)}")


@app.get("/email/reply/queue/status")
async def get_email_queue_status():
    """获取邮件回复队列详细状态"""
    try:
        queue_length = redis_client.llen(EMAIL_REPLY_QUEUE)
        worker_count = len(worker_threads)

        # 获取所有任务详情
        task_keys = redis_client.keys("email_reply_task:*")
        tasks = []

        for task_key in task_keys[:50]:  # 限制返回最多50个任务
            task_data = redis_client.hgetall(task_key)
            if task_data:
                tasks.append({
                    "task_id": task_data.get("task_id"),
                    "status": task_data.get("status"),
                    "created_at": task_data.get("created_at"),
                    "updated_at": task_data.get("updated_at"),
                    "user_id": task_data.get("user_id"),
                    "email_basic_id": task_data.get("email_basic_id")
                })

        return {
            "queue_length": queue_length,
            "worker_threads": worker_count,
            "total_tasks": len(task_keys),
            "tasks": tasks
        }

    except Exception as e:
        logger.error(f"获取队列状态失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取队列状态失败: {str(e)}")


@app.post("/api/process_mysql_email", response_model=ProcessMysqlEmailResponse)
async def process_mysql_email(request: ProcessMysqlEmailRequest):
    """
    处理MySQL阿里云数据库的邮件数据

    该接口接收结构化的邮件数据，调用 Meta Memory Agent 进行分析，
    并返回触发的 memory agents 和处理时间。
    """
    try:
        # 检查agent是否已初始化
        if agent is None or not hasattr(agent, 'agent_states'):
            raise HTTPException(status_code=500, detail="Agent未初始化")

        # 验证参数
        if not request.email_data:
            raise HTTPException(status_code=400, detail="缺少required参数: email_data")

        email_data = request.email_data
        user_id = request.user_id

        # 验证邮件数据字段
        required_fields = ['id', 'subject', 'content_text', 'sent_date_time']
        for field in required_fields:
            if field not in email_data:
                raise HTTPException(
                    status_code=400,
                    detail=f"邮件数据缺少必需字段: {field}"
                )

        # 构建统一的数据格式
        email_id = str(email_data['id'])
        conversation_id = str(email_data.get('conversation_id', ''))
        user_email_account = email_data.get('user_email_account', '未知邮箱账户')

        # 加载提示词并构造消息
        email_analysis_prompt = gpt_system.get_system_text("base/meta_memory_agent")

        # 构建参与者信息
        participants_info = []
        if email_data.get('senders'):
            participants_info.append(f"发件人: {email_data['senders']}")
        if email_data.get('froms'):
            participants_info.append(f"来源: {email_data['froms']}")
        if email_data.get('recipients'):
            participants_info.append(f"收件人: {email_data['recipients']}")
        if email_data.get('cc_recipients'):
            participants_info.append(f"抄送: {email_data['cc_recipients']}")
        if email_data.get('bcc_recipients'):
            participants_info.append(f"密送: {email_data['bcc_recipients']}")
        if email_data.get('reply_to'):
            participants_info.append(f"回复地址: {email_data['reply_to']}")

        participants_text = '\n'.join(participants_info) if participants_info else '参与者信息不完整'

        # 获取邮件分类信息
        category_name = email_data.get('category_name', '未分类')
        source_category_text = f"\n- 📂 邮件分类: {category_name}" if category_name and category_name != '未分类' else ""

        email_content_message = f"""
邮件内容分析请求：

📧 邮件基本信息：
- 邮箱账户: {user_email_account}
- 主题: {email_data.get('subject', '无主题')}
- 时间: {email_data.get('sent_date_time', '未知时间')}
- 邮件类型: {email_data.get('mail_type', '未知')}{source_category_text}
- 是否有附件: {email_data.get('has_attachments', False)}

👥 参与者信息：
{participants_text}

📝 邮件正文：
{email_data.get('content_text', '无内容')}

🎯 请根据上述邮件内容，作为Meta Memory Manager进行分析并协调相应的记忆管理器。
{f'📌 注意：此邮件属于"{category_name}"分类，请在相关记忆中使用此分类作为 source_category 标签。' if category_name and category_name != '未分类' else ''}
"""

        # 构造完整的分析消息（提示词 + 邮件数据）
        full_analysis_message = f"{email_analysis_prompt}\n\n{email_content_message}"

        # 调用Meta Memory Agent处理
        agent_states = agent.agent_states
        if agent_states.meta_memory_agent_state is None:
            raise HTTPException(
                status_code=500,
                detail="Meta Memory Agent not initialized"
            )

        meta_agent_id = agent_states.meta_memory_agent_state.id
        start_time = datetime.now()
        meta_response = None

        try:
            loop = asyncio.get_event_loop()

            # 🎯 确定用于记忆存储的用户ID
            active_user_id = None
            if user_id:
                # 使用传入的user_id
                active_user_id = user_id
                logger.info(f"📋 使用指定用户进行记忆存储: {user_id}")
            else:
                # 回退到查询活跃用户
                try:
                    users = agent.client.server.user_manager.list_users()
                    active_user = next((user for user in users if user.status == 'active'), None)
                    if active_user:
                        active_user_id = active_user.id
                        logger.info(f"📋 使用活跃用户进行记忆存储: {active_user.name} (ID: {active_user_id})")
                    else:
                        logger.warning("⚠️ 未找到活跃用户，使用系统默认用户")
                except Exception as e:
                    logger.error(f"❌ 查询活跃用户失败，使用系统默认用户: {e}")

            # 创建消息队列以启用Memory Agent并发处理
            message_queue = MessageQueue()
            agent_client = agent.client

            meta_response = await loop.run_in_executor(
                None,
                lambda: agent_client.send_message(
                    agent_id=meta_agent_id,
                    message=full_analysis_message,
                    role='user',
                    message_queue=message_queue,  # 🔑 关键：启用并发处理
                    chaining=True,  # 启用链式调用
                    user_id=active_user_id  # 🎯 传递活跃用户ID
                )
            )

        except Exception as e:
            logger.error(f"❌ Meta Memory Agent调用失败: {e}")
            meta_response = None

        # 从Meta Memory Agent响应中提取信息
        triggered_count = 0
        triggered_memory_types = []

        if meta_response and hasattr(meta_response, 'messages') and meta_response.messages:
            # 从tool_call中提取memory_types
            for msg in meta_response.messages:
                if hasattr(msg, 'tool_call') and msg.tool_call and msg.tool_call.name == 'trigger_memory_update':
                    try:
                        args = json.loads(msg.tool_call.arguments)
                        if 'memory_types' in args:
                            triggered_memory_types = args['memory_types']
                            triggered_count = len(triggered_memory_types)
                            logger.info(
                                f"✅ 从tool_call提取成功: {triggered_count} agents, types: {triggered_memory_types}")
                            break
                    except Exception as e:
                        logger.error(f"❌ 解析tool_call失败: {e}")

        processing_time = (datetime.now() - start_time).total_seconds()

        return ProcessMysqlEmailResponse(
            status="success",
            message=f"MySQL邮件处理完成：{user_email_account}",
            email_id=email_id,
            conversation_id=conversation_id,
            user_email_account=user_email_account,
            agents_triggered=triggered_count,
            triggered_memory_types=triggered_memory_types,
            processing_time=f"{processing_time:.2f}s"
        )

    except HTTPException:
        raise
    except Exception as e:
        processing_time = (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0
        email_id_display = email_data.get('id', 'unknown') if 'email_data' in locals() else "unknown"

        logger.error(f"❌ MySQL邮件处理失败: {email_id_display} | {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=f"MySQL邮件处理失败: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=47283)

