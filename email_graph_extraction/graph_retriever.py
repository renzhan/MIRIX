"""
基于LLM的图谱检索器：自然语言 -> Cypher -> 结果

使用 LangChain 的 GraphCypherQAChain 实现 Text-to-Cypher 查询
"""

from typing import Any
from langchain_neo4j import Neo4jGraph
from langchain_neo4j import GraphCypherQAChain
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_neo4j.chains.graph_qa.cypher import CYPHER_GENERATION_PROMPT
from email_graph_extraction.config import (
    get_neo4j_uri,
    get_neo4j_username,
    get_neo4j_password,
    get_openai_api_key,
    DEFAULT_OPENAI_MODEL
)

class GraphRetriever:
    """基于LLM的图谱检索器"""
    
    def __init__(
        self,
        user_id: str,
        neo4j_uri: str | None = None,
        neo4j_username: str | None = None,
        neo4j_password: str | None = None,
        openai_api_key: str | None = None,
        model_name: str | None = None,
        **kwargs: Any
    ):
        """
        初始化图谱检索器
        
        Args:
            user_id: 用户ID（必需，用于数据隔离）
            neo4j_uri: Neo4j URI
            neo4j_username: Neo4j 用户名
            neo4j_password: Neo4j 密码
            openai_api_key: OpenAI API Key
            model_name: LLM 模型名称
            **kwargs: 传递给 ChatOpenAI 的其他参数
        """
        if not user_id:
            raise ValueError("user_id是必需的参数")
        
        self.user_id = user_id
        
        # 获取配置
        uri = neo4j_uri or get_neo4j_uri()
        username = neo4j_username or get_neo4j_username()
        password = neo4j_password or get_neo4j_password()
        api_key = openai_api_key or get_openai_api_key()
        
        if not uri or not username or not password:
            raise ValueError("Neo4j 配置未设置")
        if not api_key:
            raise ValueError("OpenAI API Key 未设置")
        
        # 初始化 Neo4j Graph
        self.graph = Neo4jGraph(
            url=uri,
            username=username,
            password=password
        )
        
        # 初始化 LLM
        model = model_name or DEFAULT_OPENAI_MODEL
        self.llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            **kwargs
        )
        
        # 基于 LangChain 默认 Cypher Prompt，追加 user_id 过滤规则
        # 获取默认 Prompt 的模板
        default_template = CYPHER_GENERATION_PROMPT.template
        
        # 追加 user_id 过滤规则（在默认模板基础上添加）
        user_id_filter_rules = f"""

## 数据隔离规则（必须遵守）
1. 当前用户ID: {self.user_id}
2. **所有节点**必须包含过滤条件: `WHERE n.user_id = "{self.user_id}"`
3. 如果查询涉及多个节点，**每个节点**都必须加 user_id 过滤
4. 关系查询时，源节点和目标节点都必须过滤 user_id

## 查询优化建议
1. 名称模糊匹配: 使用 `toLower(n.name) CONTAINS toLower("关键词")`
2. 返回结果限制: 使用 LIMIT 避免过多结果（建议10-50）
3. 属性选择: 优先返回业务属性（name, email, role, title等），避免系统字段（source_email_id, created_at）

"""
        
        # 组合成新的 Prompt
        cypher_prompt = PromptTemplate(
            input_variables=CYPHER_GENERATION_PROMPT.input_variables,
            template=default_template + user_id_filter_rules
        )
        
        # 初始化 GraphCypherQAChain
        # 注意：allow_dangerous_requests=True 表示允许LLM生成并执行Cypher查询
        # 安全措施：
        # 1. 已通过user_id实现数据隔离
        # 2. 数据库连接应配置为只读权限（在生产环境）
        # 3. Prompt中已强制要求包含user_id过滤
        self.chain = GraphCypherQAChain.from_llm(
            llm=self.llm,
            graph=self.graph,
            verbose=True,
            return_intermediate_steps=True,
            cypher_prompt=cypher_prompt,
            allow_dangerous_requests=True  # 明确确认理解风险并允许执行
        )

    
    def query(self, question: str) -> dict[str, Any]:
        """
        使用自然语言查询图谱
        
        Args:
            question: 自然语言问题
        
        Returns:
            包含结果和中间步骤的字典
        """
        try:
            result = self.chain.invoke({"query": question})
            
            return {
                "question": question,
                "cypher_query": result.get("intermediate_steps", [{}])[0].get("query", ""),
                "result": result.get("result", ""),
                "context": result.get("intermediate_steps", [])
            }
        except Exception as e:
            return {
                "question": question,
                "error": str(e),
                "result": f"查询失败: {str(e)}"
            }







