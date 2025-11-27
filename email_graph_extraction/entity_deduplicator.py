"""
实体去重器：语义层面自动合并重复实体
利用APOC实现高性能去重，在抽取后自动执行
"""

from typing import Any
from neo4j import Driver
from difflib import SequenceMatcher
import re


class EntityDeduplicator:
    """实体去重器 - 基于规则和语义相似度"""
    
    def __init__(self, driver: Driver):
        """
        初始化去重器
        
        Args:
            driver: Neo4j驱动
        """
        self.driver = driver
    
    def normalize_name(self, name: str) -> str:
        """
        标准化名称（用于相似度比较）
        
        规则：
        - 转小写
        - 移除域名后缀（.com, .net, .cn等）
        - 移除公司后缀（Inc, Ltd, Co, LLC等）
        - 移除特殊字符和多余空格
        
        Args:
            name: 原始名称
        
        Returns:
            标准化后的名称
        """
        if not name:
            return ""
        
        name = name.lower().strip()
        
        # 移除域名后缀
        name = re.sub(r'\.(com|net|org|cn|io|ai|co|uk|us|ca|de|jp)$', '', name)
        
        # 移除公司后缀
        company_suffixes = [
            r'\s+(inc\.?|incorporated)$',
            r'\s+(ltd\.?|limited)$',
            r'\s+(llc)$',
            r'\s+(corp\.?|corporation)$',
            r'\s+(co\.?)$',
            r'\s+(company)$',
            r',?\s+(inc\.?|ltd\.?|llc|corp\.?)$',
        ]
        for suffix in company_suffixes:
            name = re.sub(suffix, '', name, flags=re.IGNORECASE)
        
        # 移除特殊字符（保留字母、数字、空格）
        name = re.sub(r'[^\w\s]', '', name)
        
        # 移除多余空格
        name = re.sub(r'\s+', ' ', name).strip()
        
        return name
    
    def calculate_similarity(self, name1: str, name2: str) -> float:
        """
        计算两个名称的相似度分数
        
        Args:
            name1: 名称1
            name2: 名称2
        
        Returns:
            相似度分数（0.0-1.0）
        """
        # 标准化
        norm1 = self.normalize_name(name1)
        norm2 = self.normalize_name(name2)
        
        if not norm1 or not norm2:
            return 0.0
        
        # 完全匹配
        if norm1 == norm2:
            return 1.0
        
        # 包含关系（如 "item" 和 "item.com"）
        if norm1 in norm2 or norm2 in norm1:
            shorter = min(len(norm1), len(norm2))
            longer = max(len(norm1), len(norm2))
            # 长度差异惩罚
            return max(0.85, shorter / longer)
        
        # 序列相似度（基于编辑距离）
        return SequenceMatcher(None, norm1, norm2).ratio()
    
    def find_and_merge_duplicates(
        self,
        node_type: str,
        user_id: str,
        similarity_threshold: float = 0.85
    ) -> dict[str, int]:
        """
        查找并自动合并重复节点（核心方法）
        
        Args:
            node_type: 节点类型（Company/Person/Business/Department）
            user_id: 用户ID
            similarity_threshold: 相似度阈值（0.85 = 85%）
        
        Returns:
            统计信息
        """
        if not self.driver:
            raise RuntimeError("驱动未初始化")
        
        duplicates_found = 0
        nodes_merged = 0
        
        with self.driver.session() as session:
            # 1. 获取所有同类型节点
            query = f"""
            MATCH (n:{node_type} {{user_id: $user_id}})
            RETURN id(n) as node_id, n.name as name
            ORDER BY n.created_at ASC
            """
            result = session.run(query, user_id=user_id)
            nodes = [dict(record) for record in result]
            
            # 2. 找出重复节点对
            merged_ids = set()  # 跟踪已合并的节点
            
            for i, node1 in enumerate(nodes):
                # 跳过已被合并的节点
                if node1['node_id'] in merged_ids:
                    continue
                
                for node2 in nodes[i+1:]:
                    # 跳过已被合并的节点
                    if node2['node_id'] in merged_ids:
                        continue
                    
                    # 计算相似度
                    similarity = self.calculate_similarity(
                        node1['name'],
                        node2['name']
                    )
                    
                    if similarity >= similarity_threshold:
                        duplicates_found += 1
                        
                        # 3. 自动合并（保留较早创建的节点）
                        success = self._merge_nodes_with_apoc(
                            keep_id=node1['node_id'],    # 保留node1（更早）
                            delete_id=node2['node_id']   # 删除node2
                        )
                        
                        if success:
                            nodes_merged += 1
                            merged_ids.add(node2['node_id'])
                            print(f"✅ 合并 {node_type}: '{node1['name']}' ← '{node2['name']}' (相似度: {similarity:.2f})")
        
        return {
            'duplicates_found': duplicates_found,
            'nodes_merged': nodes_merged
        }
    
    def _merge_nodes_with_apoc(self, keep_id: int, delete_id: int) -> bool:
        """
        使用APOC合并两个节点
        
        流程：
        1. 转移所有关系到保留节点
        2. 合并属性
        3. 删除冗余节点
        
        Args:
            keep_id: 保留的节点ID
            delete_id: 要删除的节点ID
        
        Returns:
            是否成功
        """
        if not self.driver:
            raise RuntimeError("驱动未初始化")
        
        with self.driver.session() as session:
            try:
                # 使用APOC批量转移关系并合并节点
                query = """
                MATCH (keep) WHERE id(keep) = $keep_id
                MATCH (delete) WHERE id(delete) = $delete_id
                
                // 转移所有传入关系
                WITH keep, delete
                OPTIONAL MATCH (other)-[r_in]->(delete)
                WHERE other <> keep
                FOREACH (_ IN CASE WHEN r_in IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (other)-[new_r:PLACEHOLDER]->(keep)
                    SET new_r = r_in
                    DELETE r_in
                )
                
                // 转移所有传出关系
                WITH keep, delete
                OPTIONAL MATCH (delete)-[r_out]->(other)
                WHERE other <> keep
                FOREACH (_ IN CASE WHEN r_out IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (keep)-[new_r:PLACEHOLDER]->(other)
                    SET new_r = r_out
                    DELETE r_out
                )
                
                // 合并属性（保留keep的属性，补充delete独有的）
                WITH keep, delete
                SET keep += delete
                
                // 删除冗余节点
                DETACH DELETE delete
                
                RETURN count(keep) as merged
                """
                
                result = session.run(
                    query,
                    keep_id=keep_id,
                    delete_id=delete_id
                )
                
                record = result.single()
                return record and record['merged'] > 0
                
            except Exception as e:
                print(f"⚠️  合并节点失败 (keep={keep_id}, delete={delete_id}): {e}")
                return False
    
    def deduplicate_all(
        self,
        user_id: str,
        similarity_threshold: float = 0.85
    ) -> dict[str, Any]:
        """
        对所有节点类型执行去重（自动调用）
        
        Args:
            user_id: 用户ID
            similarity_threshold: 相似度阈值（默认0.85 = 85%）
        
        Returns:
            完整统计信息
        """
        from email_graph_extraction.graph_schema import ALLOWED_NODES
        
        stats = {
            'total_duplicates_found': 0,
            'total_nodes_merged': 0,
            'by_type': {}
        }
        
        # 对每种节点类型执行去重
        for node_type in ALLOWED_NODES:
            type_stats = self.find_and_merge_duplicates(
                node_type=node_type,
                user_id=user_id,
                similarity_threshold=similarity_threshold
            )
            
            stats['by_type'][node_type] = type_stats
            stats['total_duplicates_found'] += type_stats['duplicates_found']
            stats['total_nodes_merged'] += type_stats['nodes_merged']
        
        # 打印总结
        if stats['total_nodes_merged'] > 0:
            print(f"\n🎯 去重完成: 发现 {stats['total_duplicates_found']} 对重复，合并 {stats['total_nodes_merged']} 个节点")
        
        return stats

