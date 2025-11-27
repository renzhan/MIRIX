-- 迁移脚本：将 workflow_data 重命名为 mirix_data
-- 目的：使字段名称更通用，支持存储任意 agent 接口返回的数据

-- 1. 重命名列
ALTER TABLE ace_email_learning_records 
RENAME COLUMN workflow_data TO mirix_data;

-- 2. 更新列注释
COMMENT ON COLUMN ace_email_learning_records.mirix_data IS '从 Agent 接口提取的数据 (Context，支持 workflow/memory 等任意 agent 返回的 JSON 数据)';

