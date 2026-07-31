-- 添加 always_confirm 字段到 agent_configs 表
ALTER TABLE agent_configs ADD COLUMN always_confirm INT DEFAULT 0 COMMENT '始终确认计划 (0=False, 1=True)' AFTER max_retries;
