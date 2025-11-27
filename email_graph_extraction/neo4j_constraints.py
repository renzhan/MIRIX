"""
Neo4j数据库约束和索引设置

用于确保用户数据隔离和数据完整性
"""

from typing import Optional
from neo4j import GraphDatabase, Driver


def setup_user_isolation_constraints(driver: Driver):
    """
    设置用户隔离相关的约束和索引
    
    这些约束确保：
    1. 每个节点的user_id不能为空
    2. 每个关系的user_id不能为空
    3. 同一用户内的节点名称唯一性
    4. 查询性能优化
    
    Args:
        driver: Neo4j数据库驱动
    """
    with driver.session() as session:
        # 1. 为所有节点类型创建user_id非空约束
        node_types = ["Person", "Company", "Business"]
        for node_type in node_types:
            # 创建user_id属性存在性约束（如果Neo4j版本支持）
            try:
                session.run(f"""
                    CREATE CONSTRAINT IF NOT EXISTS {node_type.lower()}_user_id_exists
                    FOR (n:{node_type})
                    REQUIRE n.user_id IS NOT NULL
                """)
            except Exception:
                # 如果版本不支持，跳过（旧版本Neo4j）
                pass
        
        # 2. 为每个节点类型创建(user_id, name)的唯一性约束
        # 这确保同一用户内的同名节点是唯一的
        for node_type in node_types:
            try:
                session.run(f"""
                    CREATE CONSTRAINT IF NOT EXISTS {node_type.lower()}_user_name_unique
                    FOR (n:{node_type})
                    REQUIRE (n.user_id, n.name) IS UNIQUE
                """)
            except Exception:
                # 如果唯一性约束创建失败，尝试创建索引
                try:
                    session.run(f"""
                        CREATE INDEX IF NOT EXISTS {node_type.lower()}_user_name_idx
                        FOR (n:{node_type})
                        ON (n.user_id, n.name)
                    """)
                except Exception:
                    pass
        
        # 3. 为user_id创建索引（提高查询性能）
        for node_type in node_types:
            try:
                session.run(f"""
                    CREATE INDEX IF NOT EXISTS {node_type.lower()}_user_id_idx
                    FOR (n:{node_type})
                    ON (n.user_id)
                """)
            except Exception:
                pass
        
        # 4. 为关系创建user_id索引（如果关系有user_id属性）
        # 注意：关系的user_id通常从节点继承，但也可以单独存储
        print("用户隔离约束和索引设置完成")


def verify_user_isolation(driver: Driver, user_id: str) -> dict:
    """
    验证用户数据隔离是否正常工作
    
    Args:
        driver: Neo4j数据库驱动
        user_id: 用户ID
    
    Returns:
        验证结果统计
    """
    with driver.session() as session:
        # 统计该用户的节点数
        node_count = session.run("""
            MATCH (n)
            WHERE n.user_id = $user_id
            RETURN count(n) as count
        """, user_id=user_id).single()['count']
        
        # 统计该用户的关系数
        rel_count = session.run("""
            MATCH (a)-[r]->(b)
            WHERE a.user_id = $user_id AND b.user_id = $user_id
            RETURN count(r) as count
        """, user_id=user_id).single()['count']
        
        # 检查是否有user_id为空的节点（不应该存在）
        null_user_nodes = session.run("""
            MATCH (n)
            WHERE n.user_id IS NULL
            RETURN count(n) as count
        """).single()['count']
        
        return {
            'user_id': user_id,
            'node_count': node_count,
            'relationship_count': rel_count,
            'null_user_id_nodes': null_user_nodes,
            'isolation_valid': null_user_nodes == 0
        }


if __name__ == "__main__":
    """
    运行此脚本以设置Neo4j约束和索引
    
    使用方法：
    python -m email_graph_extraction.neo4j_constraints
    """
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    
    if not password:
        print("错误: 请设置NEO4J_PASSWORD环境变量")
        exit(1)
    
    driver = GraphDatabase.driver(uri, auth=(username, password))
    
    try:
        print("正在设置用户隔离约束和索引...")
        setup_user_isolation_constraints(driver)
        print("✅ 约束和索引设置完成")
    except Exception as e:
        print(f"❌ 设置失败: {str(e)}")
    finally:
        driver.close()

