-- 迁移: 添加 execution_mode 列到 agent_configs 表
-- 用于支持直连执行模式功能

ALTER TABLE agent_configs 
ADD COLUMN execution_mode VARCHAR(20) NOT NULL DEFAULT 'auto'
AFTER always_confirm;

-- 验证（可选）
-- SELECT * FROM agent_configs;
