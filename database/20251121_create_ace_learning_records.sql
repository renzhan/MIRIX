-- 创建 ACE 邮件学习记录表 (PostgreSQL版本)
-- 用于存储"邮件场景-工作流-策略"的对应关系，实现精准溯源和策略库构建

CREATE TABLE IF NOT EXISTS ace_email_learning_records (
    id BIGSERIAL PRIMARY KEY,
    
    -- 1. 原始邮件关联信息
    email_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(255),  -- 扩大长度以容纳较长的会话ID
    
    -- 2. 学习输入上下文 (场景特征)
    topic VARCHAR(512),
    workflow_data JSONB,  -- 使用 JSONB 性能更好
    ground_truth TEXT,
    
    -- 3. 学习产出 (策略与效果)
    learned_strategies JSONB,
    final_score FLOAT,
    
    -- 4. 审计时间
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 添加注释
COMMENT ON TABLE ace_email_learning_records IS 'ACE邮件学习产出记录表';
COMMENT ON COLUMN ace_email_learning_records.id IS '主键ID';
COMMENT ON COLUMN ace_email_learning_records.email_id IS '原始邮件ID (关联 email_basic.id)';
COMMENT ON COLUMN ace_email_learning_records.conversation_id IS '会话ID (用于聚合同一会话的学习记录)';
COMMENT ON COLUMN ace_email_learning_records.topic IS '邮件主题/业务意图 (LLM提取，一级检索标签)';
COMMENT ON COLUMN ace_email_learning_records.workflow_data IS '从API提取的完整工作流数据 (Context，二级核心特征)';
COMMENT ON COLUMN ace_email_learning_records.ground_truth IS '专家回复内容 (验证基准/SFT训练目标)';
COMMENT ON COLUMN ace_email_learning_records.learned_strategies IS '本轮学习产生的新策略列表 (Playbook Bullets JSON数组)';
COMMENT ON COLUMN ace_email_learning_records.final_score IS '学习后的最终评估得分 (0-10，用于质量过滤)';
COMMENT ON COLUMN ace_email_learning_records.created_at IS '创建时间';
COMMENT ON COLUMN ace_email_learning_records.updated_at IS '更新时间';

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_ace_learning_email_id ON ace_email_learning_records (email_id);
CREATE INDEX IF NOT EXISTS idx_ace_learning_conversation_id ON ace_email_learning_records (conversation_id);
CREATE INDEX IF NOT EXISTS idx_ace_learning_created_at ON ace_email_learning_records (created_at);

-- 创建更新时间触发器函数 (模拟 MySQL ON UPDATE CURRENT_TIMESTAMP)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 绑定触发器
DROP TRIGGER IF EXISTS update_ace_learning_modtime ON ace_email_learning_records;
CREATE TRIGGER update_ace_learning_modtime
    BEFORE UPDATE ON ace_email_learning_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
