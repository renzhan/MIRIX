"""
Email Graph Extraction - 企业邮件知识图谱抽取系统

支持从邮件中抽取Person、Company、Business、Department及其关系
"""

from email_graph_extraction.graph_extractor import GraphExtractor
from email_graph_extraction.neo4j_manager import Neo4jManager
from email_graph_extraction.html_to_md import convert_html_to_markdown
from email_graph_extraction.config import (
    NEO4J_URI,
    NEO4J_USERNAME,
    NEO4J_PASSWORD,
    OPENAI_API_KEY,
    DEFAULT_OPENAI_MODEL,
)

__version__ = "0.2.0"
__all__ = [
    "GraphExtractor",
    "Neo4jManager",
    "convert_html_to_markdown",
    "extract_and_store_graph",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "OPENAI_API_KEY",
    "DEFAULT_OPENAI_MODEL",
]


def extract_and_store_graph(
    email_content: str,
    user_id: str,
    email_account: str,
    neo4j_uri: str | None = None,
    neo4j_username: str | None = None,
    neo4j_password: str | None = None,
    openai_api_key: str | None = None,
    model_name: str | None = None,
    enable_deduplication: bool = True,
    similarity_threshold: float = 0.85
) -> dict:
    """
    抽取并存储邮件图谱（主函数，带自动去重）
    
    Args:
        email_content: 邮件Markdown文本内容
        user_id: 用户ID（必需，用于数据隔离）
        email_account: 用户邮箱账户（必需，用于识别self角色）
        neo4j_uri: Neo4j URI（可选）
        neo4j_username: Neo4j用户名（可选）
        neo4j_password: Neo4j密码（可选）
        openai_api_key: OpenAI API Key（可选）
        model_name: 模型名称（可选，默认gpt-5.1）
        enable_deduplication: 是否启用自动去重（默认True）
        similarity_threshold: 相似度阈值（默认0.85，即85%相似度）
    
    Returns:
        结果字典，包含：
        - nodes_extracted: 抽取的节点数
        - relationships_extracted: 抽取的关系数
        - nodes_stored: 存储的节点数
        - relationships_stored: 存储的关系数
        - deduplication: 去重统计信息
        - graph_document: 完整GraphDocument对象
    """
    # 使用配置或参数
    uri = neo4j_uri or NEO4J_URI
    username = neo4j_username or NEO4J_USERNAME
    password = neo4j_password or NEO4J_PASSWORD
    
    # 创建管理器
    neo4j_manager = Neo4jManager(uri, username, password)
    
    try:
        extractor = GraphExtractor(
            neo4j_manager=neo4j_manager,
            openai_api_key=openai_api_key,
            model_name=model_name,
            enable_deduplication=enable_deduplication,
            similarity_threshold=similarity_threshold
        )
        
        result = extractor.extract_and_store(
            email_content=email_content,
            user_id=user_id,
            email_account=email_account
        )
        
        return result
    
    finally:
        neo4j_manager.close()
