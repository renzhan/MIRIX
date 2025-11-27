"""
图谱抽取器：使用LangChain LLMGraphTransformer从邮件中抽取知识图谱
"""

from typing import Any
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from langchain_community.graphs.graph_document import GraphDocument
from langchain_experimental.graph_transformers import LLMGraphTransformer

from email_graph_extraction.config import DEFAULT_OPENAI_MODEL, get_openai_api_key
from email_graph_extraction.graph_schema import (
    ALLOWED_NODES, 
    ALLOWED_RELATIONSHIPS,
    get_all_node_properties_for_llm,
    get_all_relationship_properties_for_llm
)
from email_graph_extraction.prompt_templates import ENTITY_EXTRACTION_GUIDELINES
from email_graph_extraction.neo4j_manager import Neo4jManager
from email_graph_extraction.entity_deduplicator import EntityDeduplicator


class GraphExtractor:
    """邮件图谱抽取器（带自动去重）"""
    
    def __init__(
        self,
        neo4j_manager: Neo4jManager,
        openai_api_key: str | None = None,
        model_name: str | None = None,
        enable_deduplication: bool = True,
        similarity_threshold: float = 0.85
    ):
        """
        初始化图谱抽取器
        
        Args:
            neo4j_manager: Neo4j管理器
            openai_api_key: OpenAI API Key
            model_name: 模型名称（默认gpt-5.1）
            enable_deduplication: 是否启用自动去重（默认True）
            similarity_threshold: 相似度阈值（默认0.85 = 85%）
        """
        self.neo4j_manager = neo4j_manager
        self.enable_deduplication = enable_deduplication
        self.similarity_threshold = similarity_threshold
        
        # 初始化LLM
        api_key = openai_api_key or get_openai_api_key()
        if not api_key:
            raise ValueError("OpenAI API Key未设置")
        
        self.llm = ChatOpenAI(
            model=model_name or DEFAULT_OPENAI_MODEL,
            api_key=api_key
        )
        
        # 初始化图谱转换器（使用最新版本的node_properties参数）
        relationship_names = sorted(set(rel[1] for rel in ALLOWED_RELATIONSHIPS))
        
        # 获取LLM应该抽取的属性列表
        node_properties = get_all_node_properties_for_llm()
        relationship_properties = get_all_relationship_properties_for_llm()
        
        print(f"[Config] LLM node properties to extract: {node_properties}")
        print(f"[Config] LLM relationship properties to extract: {relationship_properties}")
        
        self.transformer = LLMGraphTransformer(
            llm=self.llm,
            allowed_nodes=ALLOWED_NODES,
            allowed_relationships=relationship_names,
            strict_mode=True,
            # ✅ 关键：明确告诉LLM要抽取哪些属性
            node_properties=node_properties,  # ['email', 'title', 'department', 'role', 'company_type', ...]
            relationship_properties=relationship_properties,  # ['strength', 'description']
            # 添加额外说明
            additional_instructions=ENTITY_EXTRACTION_GUIDELINES
        )
        
        # 初始化去重器
        if self.enable_deduplication:
            self.deduplicator = EntityDeduplicator(neo4j_manager.driver)
    
    def extract_from_text(self, text: str, email_account: str) -> GraphDocument:
        """
        从文本抽取图谱
        
        Args:
            text: 邮件文本内容（Markdown格式）
            email_account: 用户邮箱账户
        
        Returns:
            GraphDocument对象
        """
        if not text or not text.strip():
            raise ValueError("文本内容不能为空")
        
        # 添加用户身份标识
        # 注意：ENTITY_EXTRACTION_GUIDELINES已通过additional_instructions传递给LLMGraphTransformer
        full_text = f"""My Email Account: {email_account}

Email Content:
{text}
"""
        
        # LLM抽取（会自动使用node_properties和relationship_properties）
        document = Document(page_content=full_text)
        graph_docs = self.transformer.convert_to_graph_documents([document])
        
        if not graph_docs:
            return GraphDocument(nodes=[], relationships=[], source=document)
        
        graph_doc = graph_docs[0]
        
        # 调试：打印LLM抽取的属性
        print(f"\n[DEBUG] LLM extraction results (first 3 nodes):")
        for node in graph_doc.nodes[:3]:  # 只打印前3个节点
            print(f"  Node: {node.id} ({node.type})")
            print(f"  Properties: {node.properties}")
        
        return graph_doc
    
    def store_graph(self, graph_doc: GraphDocument, user_id: str) -> dict[str, int]:
        """
        存储图谱到Neo4j
        
        Args:
            graph_doc: GraphDocument对象
            user_id: 用户ID
        
        Returns:
            存储统计
        """
        nodes_stored = 0
        relationships_stored = 0
        
        # 存储节点
        for node in graph_doc.nodes:
            try:
                self.neo4j_manager.create_or_update_node(
                    node_type=node.type,
                    name=node.id,
                    properties=node.properties or {},
                    user_id=user_id
                )
                nodes_stored += 1
            except Exception as e:
                print(f"存储节点失败 {node.id}: {e}")
        
        # 存储关系
        for rel in graph_doc.relationships:
            try:
                self.neo4j_manager.create_or_update_relationship(
                    source_type=rel.source.type,
                    source_name=rel.source.id,
                    relationship_type=rel.type,
                    target_type=rel.target.type,
                    target_name=rel.target.id,
                    properties=rel.properties or {},
                    user_id=user_id
                )
                relationships_stored += 1
            except Exception as e:
                print(f"存储关系失败 {rel.source.id}-[{rel.type}]->{rel.target.id}: {e}")
        
        return {
            'nodes_stored': nodes_stored,
            'relationships_stored': relationships_stored
        }
    
    def extract_and_store(
        self,
        email_content: str,
        user_id: str,
        email_account: str
    ) -> dict[str, Any]:
        """
        抽取并存储图谱（一站式方法，带自动去重）
        
        Args:
            email_content: 邮件Markdown文本
            user_id: 用户ID
            email_account: 用户邮箱账户
        
        Returns:
            完整结果字典（包含去重统计）
        """
        # 1. 抽取
        graph_doc = self.extract_from_text(email_content, email_account)
        
        # 2. 存储
        stats = self.store_graph(graph_doc, user_id)
        
        # 3. 自动去重（在存储后执行）
        dedup_stats = {}
        if self.enable_deduplication:
            dedup_stats = self.deduplicator.deduplicate_all(
                user_id=user_id,
                similarity_threshold=self.similarity_threshold
            )
        
        return {
            'graph_document': graph_doc,
            'nodes_extracted': len(graph_doc.nodes),
            'relationships_extracted': len(graph_doc.relationships),
            'nodes_stored': stats['nodes_stored'],
            'relationships_stored': stats['relationships_stored'],
            'deduplication': dedup_stats
        }
