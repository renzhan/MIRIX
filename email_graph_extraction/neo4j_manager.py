"""
Neo4j数据库管理类
"""

from typing import Any, Optional
from datetime import datetime
from neo4j import GraphDatabase, Driver, Session


class Neo4jManager:
    """Neo4j数据库管理器"""
    
    def __init__(self, uri: str, username: str, password: str):
        """
        初始化Neo4j连接
        
        Args:
            uri: Neo4j连接URI
            username: 用户名
            password: 密码
        """
        self.uri = uri
        self.username = username
        self.password = password
        self.driver: Driver | None = None
        self._connect()
    
    def _connect(self):
        """建立数据库连接"""
        try:
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password)
            )
            # 验证连接
            self.driver.verify_connectivity()
        except Exception as e:
            raise ConnectionError(f"无法连接到Neo4j数据库: {str(e)}")
    
    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            self.driver = None
    
    def create_or_update_node(
        self,
        node_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None  # 必需参数，用于用户数据隔离
    ) -> dict[str, Any]:
        """
        创建或更新节点（使用MERGE操作）
        
        Args:
            node_type: 节点类型（Person/Company/Business）
            name: 节点名称
            properties: 节点属性
            user_id: 用户ID（用于隔离）
        
        Returns:
            创建的节点信息
        """
        if not self.driver:
            raise RuntimeError("数据库连接未建立")
        
        # user_id是必需的，用于数据隔离
        if not user_id:
            raise ValueError("user_id是必需的参数，用于确保用户数据隔离")
        
        properties = properties or {}
        properties['name'] = name
        properties['user_id'] = user_id  # 强制设置user_id
        properties['updated_at'] = datetime.now().isoformat()
        
        # 如果没有created_at，设置它
        if 'created_at' not in properties:
            properties['created_at'] = datetime.now().isoformat()
        
        with self.driver.session() as session:
            # 使用MERGE确保节点唯一性（基于name和user_id）
            # 注意：每个用户的同名节点是独立的（如用户A的"John"和用户B的"John"是不同的节点）
            query = f"""
            MERGE (n:{node_type} {{name: $name, user_id: $user_id}})
            ON CREATE SET n += $properties, n.created_at = $created_at
            ON MATCH SET n += $properties
            RETURN n
            """
            
            result = session.run(
                query,
                name=name,
                user_id=user_id,  # user_id已确保不为空
                properties=properties,
                created_at=properties['created_at']
            )
            
            record = result.single()
            if record:
                node = record['n']
                return dict(node)
            return {}
    
    def create_or_update_relationship(
        self,
        source_type: str,
        source_name: str,
        relationship_type: str,
        target_type: str,
        target_name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None  # 必需参数，用于用户数据隔离
    ) -> bool:
        """
        创建或更新关系（使用MERGE操作）
        
        Args:
            source_type: 源节点类型
            source_name: 源节点名称
            relationship_type: 关系类型
            target_type: 目标节点类型
            target_name: 目标节点名称
            properties: 关系属性
            user_id: 用户ID
        
        Returns:
            是否成功创建
        """
        if not self.driver:
            raise RuntimeError("数据库连接未建立")
        
        # user_id是必需的，用于数据隔离
        if not user_id:
            raise ValueError("user_id是必需的参数，用于确保用户数据隔离")
        
        properties = properties or {}
        properties['user_id'] = user_id  # 强制设置user_id
        properties['updated_at'] = datetime.now().isoformat()
        
        if 'created_at' not in properties:
            properties['created_at'] = datetime.now().isoformat()
        
        with self.driver.session() as session:
            # 先确保源节点和目标节点存在
            self.create_or_update_node(source_type, source_name, user_id=user_id)
            self.create_or_update_node(target_type, target_name, user_id=user_id)
            
            # 创建或更新关系
            query = f"""
            MATCH (a:{source_type} {{name: $source_name, user_id: $user_id}})
            MATCH (b:{target_type} {{name: $target_name, user_id: $user_id}})
            MERGE (a)-[r:{relationship_type}]->(b)
            ON CREATE SET r += $properties, r.created_at = $created_at
            ON MATCH SET r += $properties
            RETURN r
            """
            
            result = session.run(
                query,
                source_name=source_name,
                target_name=target_name,
                user_id=user_id,  # user_id已确保不为空
                properties=properties,
                created_at=properties['created_at']
            )
            
            return result.single() is not None
    
    def query_nodes(
        self,
        node_type: str | None = None,
        name_pattern: str | None = None,
        user_id: str | None = None,  # 必需参数，用于用户数据隔离
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        查询节点
        
        Args:
            node_type: 节点类型过滤
            name_pattern: 名称模式（模糊匹配）
            user_id: 用户ID（必需，用于数据隔离）
            limit: 返回数量限制
        
        Returns:
            节点列表（仅返回指定用户的数据）
        """
        # user_id是必需的，确保数据隔离
        if not user_id:
            raise ValueError("user_id是必需的参数，用于确保用户数据隔离")
        
        if not self.driver:
            raise RuntimeError("数据库连接未建立")
        
        with self.driver.session() as session:
            conditions = []
            params = {}
            
            # 强制使用user_id过滤
            conditions.append("n.user_id = $user_id")
            params['user_id'] = user_id
            
            if node_type:
                conditions.append(f"n:{node_type}")
            
            if name_pattern:
                conditions.append("n.name CONTAINS $name_pattern")
                params['name_pattern'] = name_pattern
            
            where_clause = " AND ".join(conditions) if conditions else ""
            query = f"""
            MATCH (n)
            {f"WHERE {where_clause}" if where_clause else ""}
            RETURN n
            LIMIT $limit
            """
            params['limit'] = limit
            
            result = session.run(query, **params)
            return [dict(record['n']) for record in result]
    
    def query_relationships(
        self,
        source_name: str | None = None,
        target_name: str | None = None,
        relationship_type: str | None = None,
        user_id: str | None = None,  # 必需参数，用于用户数据隔离
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        查询关系
        
        Args:
            source_name: 源节点名称
            target_name: 目标节点名称
            relationship_type: 关系类型
            user_id: 用户ID（必需，用于数据隔离）
            limit: 返回数量限制
        
        Returns:
            关系列表（仅返回指定用户的数据）
        """
        # user_id是必需的，确保数据隔离
        if not user_id:
            raise ValueError("user_id是必需的参数，用于确保用户数据隔离")
        
        if not self.driver:
            raise RuntimeError("数据库连接未建立")
        
        with self.driver.session() as session:
            conditions = []
            params = {}
            
            # 强制使用user_id过滤
            conditions.append("a.user_id = $user_id AND b.user_id = $user_id")
            params['user_id'] = user_id
            
            if source_name:
                conditions.append("a.name = $source_name")
                params['source_name'] = source_name
            
            if target_name:
                conditions.append("b.name = $target_name")
                params['target_name'] = target_name
            
            if relationship_type:
                conditions.append("TYPE(r) = $relationship_type")
                params['relationship_type'] = relationship_type
            
            where_clause = " AND ".join(conditions) if conditions else ""
            query = f"""
            MATCH (a)-[r]->(b)
            {f"WHERE {where_clause}" if where_clause else ""}
            RETURN a, r, b
            LIMIT $limit
            """
            params['limit'] = limit
            
            result = session.run(query, **params)
            relationships = []
            for record in result:
                relationships.append({
                    'source': dict(record['a']),
                    'relationship': dict(record['r']),
                    'target': dict(record['b']),
                })
            return relationships
    
    def get_subgraph(
        self,
        node_name: str,
        node_type: str | None = None,
        depth: int = 2,
        user_id: str | None = None  # 必需参数，用于用户数据隔离
    ) -> dict[str, Any]:
        """
        获取以某个节点为中心的子图谱
        
        Args:
            node_name: 中心节点名称
            node_type: 节点类型
            depth: 图谱深度（1=直接关系，2=二级关系）
            user_id: 用户ID（必需，用于数据隔离）
        
        Returns:
            包含节点和关系的子图谱（仅返回指定用户的数据）
        """
        # user_id是必需的，确保数据隔离
        if not user_id:
            raise ValueError("user_id是必需的参数，用于确保用户数据隔离")
        
        if not self.driver:
            raise RuntimeError("数据库连接未建立")
        
        with self.driver.session() as session:
            node_filter = f":{node_type}" if node_type else ""
            
            # 强制使用user_id过滤
            query = f"""
            MATCH path = (n{node_filter} {{name: $node_name, user_id: $user_id}})-[*1..{depth}]-(connected)
            WHERE n.name = $node_name AND n.user_id = $user_id AND connected.user_id = $user_id
            WITH n, relationships(path) as rels, nodes(path) as nodes
            RETURN DISTINCT n, rels, nodes
            LIMIT 100
            """
            
            params = {
                'node_name': node_name,
                'user_id': user_id
            }
            
            result = session.run(query, **params)
            
            nodes = set()
            relationships = []
            
            for record in result:
                nodes.add(record['n'])
                for node in record['nodes']:
                    nodes.add(node)
                for rel in record['rels']:
                    relationships.append(rel)
            
            return {
                'nodes': [dict(n) for n in nodes],
                'relationships': [dict(r) for r in relationships],
            }
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

