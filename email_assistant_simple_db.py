#!/usr/bin/env python3
"""
邮件助手数据库初始化脚本
创建完整的向量化+回复功能支持：
- email_vectors: 向量化存储 + 智能体分析  
- email_replies: AI回复建议存储
- email_threads: 对话线程管理
"""

import os
import sys
from urllib.parse import urlparse
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# 加载.env文件中的环境变量
load_dotenv()

def parse_database_uri(uri):
    """解析数据库URI"""
    parsed = urlparse(uri)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password
    }

def create_simple_email_schema():
    """返回邮件助手完整数据库架构 - 向量化+回复功能"""
    return """
-- =====================================================
-- 邮件助手完整数据库架构
-- 包含向量化存储、回复建议、对话线程管理
-- =====================================================

-- 启用pgvector扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================
-- 1. 邮件向量化表 (核心表)
-- =====================================================
CREATE TABLE IF NOT EXISTS email_vectors (
    id SERIAL PRIMARY KEY,
    
    -- 关联原始邮件
    entry_id TEXT UNIQUE NOT NULL,              -- 原始邮件ID (用于关联)
    
    -- 向量嵌入 (核心!)
    content_embedding VECTOR(1536),             -- 邮件内容向量
    subject_embedding VECTOR(1536),             -- 主题向量
    
    -- 智能体分析结果
    agent_analysis JSONB,                       -- 完整的智能体分析结果
    
    -- 快速查询字段
    intent TEXT,                                -- 邮件意图
    urgency TEXT,                               -- 紧急程度
    sentiment TEXT,                             -- 情感色彩
    key_entities TEXT[],                        -- 关键实体
    
    -- 时间戳
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- 2. 邮件回复建议表
-- =====================================================
CREATE TABLE IF NOT EXISTS email_replies (
    id SERIAL PRIMARY KEY,
    email_vector_id INTEGER REFERENCES email_vectors(id) ON DELETE CASCADE,
    
    -- 回复建议
    suggested_reply TEXT NOT NULL,              -- 建议的回复内容
    reply_type TEXT,                            -- 回复类型 (approval/rejection/info等)
    confidence_score DECIMAL(3,2),              -- 置信度 (0.00-1.00)
    
    -- 元数据
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    generated_by TEXT DEFAULT 'mirix_agent',     -- 生成方式
    
    CONSTRAINT replies_confidence_check CHECK (confidence_score >= 0 AND confidence_score <= 1)
);

-- =====================================================
-- 3. 邮件对话线程表
-- =====================================================
CREATE TABLE IF NOT EXISTS email_threads (
    id SERIAL PRIMARY KEY,
    conversation_id TEXT UNIQUE NOT NULL,
    thread_subject TEXT,
    participant_emails TEXT[],                   -- 参与者邮箱列表
    first_email_time TIMESTAMP,
    last_email_time TIMESTAMP,
    email_count INTEGER DEFAULT 0,
    
    -- 线程摘要向量 (用于上下文理解)
    thread_summary_embedding VECTOR(1536),      -- 整个对话线程的摘要向量
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- 4. 性能优化索引
-- =====================================================

-- 向量搜索索引
CREATE INDEX IF NOT EXISTS idx_content_embedding 
ON email_vectors USING ivfflat (content_embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_subject_embedding 
ON email_vectors USING ivfflat (subject_embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_thread_summary_embedding
ON email_threads USING ivfflat (thread_summary_embedding vector_cosine_ops) WITH (lists = 50);

-- 基础查询索引
CREATE INDEX IF NOT EXISTS idx_entry_id ON email_vectors(entry_id);
CREATE INDEX IF NOT EXISTS idx_intent ON email_vectors(intent);
CREATE INDEX IF NOT EXISTS idx_urgency ON email_vectors(urgency);
CREATE INDEX IF NOT EXISTS idx_processed_at ON email_vectors(processed_at DESC);

-- 回复表索引
CREATE INDEX IF NOT EXISTS idx_email_vector_id ON email_replies(email_vector_id);
CREATE INDEX IF NOT EXISTS idx_confidence_score ON email_replies(confidence_score DESC);

-- 线程表索引
CREATE INDEX IF NOT EXISTS idx_conversation_id ON email_threads(conversation_id);
CREATE INDEX IF NOT EXISTS idx_last_email_time ON email_threads(last_email_time DESC);

-- JSON分析结果索引
CREATE INDEX IF NOT EXISTS idx_agent_analysis ON email_vectors USING GIN (agent_analysis);

-- =====================================================
-- 完成消息
-- =====================================================
DO $$
BEGIN
    RAISE NOTICE '✅ 邮件助手完整数据库创建完成!';
    RAISE NOTICE '📊 创建的表:';
    RAISE NOTICE '   - email_vectors (向量化存储 + 智能体分析)';
    RAISE NOTICE '   - email_replies (AI回复建议)';
    RAISE NOTICE '   - email_threads (对话线程管理)';
    RAISE NOTICE '🔗 完整的回复生成功能支持';
    RAISE NOTICE '⚡ 包含向量搜索和性能优化索引';
    RAISE NOTICE '🚀 支持智能回复生成!';
END;
$$;
"""

def initialize_simple_database():
    """初始化邮件助手表结构（不包含建库操作）"""
    
    print("🚀 邮件助手表结构初始化")
    print("📝 注意: 假设数据库已由运维创建，仅初始化表结构")
    print("=" * 50)
    
    # 从环境变量获取数据库URI
    db_uri = os.getenv('MIRIX_PG_URI')
    
    if not db_uri:
        print("❌ 未找到 MIRIX_PG_URI 环境变量")
        sys.exit(1)
    
    print(f"📍 数据库URI: {db_uri}")
    
    # 解析连接参数
    conn_params = parse_database_uri(db_uri)
    target_database = conn_params['database']
    print(f"📍 目标数据库: {target_database} @ {conn_params['host']}:{conn_params['port']}")
    
    try:
        # 直接连接到目标数据库（假设数据库已由运维创建）
        print(f"\n🔄 连接到目标数据库: {target_database}...")
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        print(f"✅ 成功连接到数据库: {target_database}")
        print("📝 注意: 假设数据库已由运维创建，只进行表结构初始化")
        
        # 执行简化架构SQL
        print("📝 创建简化的邮件助手表结构...")
        schema_sql = create_simple_email_schema()
        cursor.execute(schema_sql)
        
        # 验证创建结果
        print("\n🔍 验证表创建...")
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('email_vectors', 'email_replies', 'email_threads')
            ORDER BY table_name
        """)
        
        tables = cursor.fetchall()
        print(f"✅ 成功创建 {len(tables)} 个表:")
        for table in tables:
            print(f"   - {table[0]}")
        
        # 检查向量扩展
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        vector_ext = cursor.fetchone()
        print(f"✅ pgvector扩展: {'已安装' if vector_ext else '❌ 未安装'}")
        
        cursor.close()
        conn.close()
        
        print("\n" + "=" * 50)
        print("🎉 邮件助手完整数据库初始化完成!")
        print("💡 创建的内容:")
        print("   📊 email_vectors 表 - 向量化存储 + 智能体分析")
        print("   💬 email_replies 表 - AI回复建议存储")
        print("   🧵 email_threads 表 - 对话线程管理")
        print("   ⚡ 完整的向量搜索索引")
        print("   🔗 支持智能回复生成功能")
        print("📝 完整功能，支持邮件回复生成!")
        
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    initialize_simple_database()
