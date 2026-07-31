-- ============================================
-- 会话隔离与数据库连接共享 - 数据库迁移脚本
-- 执行时间: 2026-01-30
-- ============================================

-- 1. langgraph_checkpoints 表: 添加 user_id 列
ALTER TABLE langgraph_checkpoints 
ADD COLUMN user_id VARCHAR(36) NULL AFTER checkpoint_id;

-- 添加索引以优化按用户查询
CREATE INDEX idx_langgraph_user_id ON langgraph_checkpoints(user_id);


-- 2. user_db_configs 表: 添加 workspace_id 和 configured_by 列
ALTER TABLE user_db_configs 
ADD COLUMN workspace_id VARCHAR(128) NULL AFTER user_id,
ADD COLUMN configured_by VARCHAR(64) NULL AFTER workspace_id;

-- 迁移数据: 将现有 user_id 复制到 workspace_id（兼容旧数据）
UPDATE user_db_configs SET workspace_id = user_id WHERE workspace_id IS NULL;

-- 添加索引
CREATE INDEX idx_workspace_id ON user_db_configs(workspace_id);


-- 验证迁移结果
SELECT 'langgraph_checkpoints 结构' AS info;
DESCRIBE langgraph_checkpoints;

SELECT 'user_db_configs 结构' AS info;
DESCRIBE user_db_configs;
