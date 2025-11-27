"""
配置文件：管理Neo4j连接和OpenAI API Key配置
"""

import os
from typing import Optional
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 尝试从项目配置导入（如果可用）
try:
    from mirix.settings import model_settings
    _has_mirix_settings = True
except ImportError:
    _has_mirix_settings = False


def get_openai_api_key() -> Optional[str]:
    """
    获取OpenAI API Key
    优先级：环境变量 > mirix配置 > None
    """
    # 1. 优先从环境变量获取
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        return api_key
    
    # 2. 从mirix配置获取
    if _has_mirix_settings and model_settings.openai_api_key:
        return model_settings.openai_api_key
    
    return None


def get_neo4j_uri() -> str:
    """获取Neo4j连接URI"""
    return os.getenv("NEO4J_URI", "bolt://localhost:7687")


def get_neo4j_username() -> str:
    """获取Neo4j用户名"""
    return os.getenv("NEO4J_USERNAME", "neo4j")


def get_neo4j_password() -> str:
    """获取Neo4j密码"""
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise ValueError(
            "NEO4J_PASSWORD环境变量未设置。请在.env文件中设置NEO4J_PASSWORD"
        )
    return password


# 配置常量
OPENAI_API_KEY = get_openai_api_key()
NEO4J_URI = get_neo4j_uri()
NEO4J_USERNAME = get_neo4j_username()
NEO4J_PASSWORD = get_neo4j_password()

# 默认使用GPT-5.1模型（2025年11月最新）
DEFAULT_OPENAI_MODEL = "gpt-5.1"

