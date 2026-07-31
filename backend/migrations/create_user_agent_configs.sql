-- 用户级智能体配置表
-- 用于存储每个用户的个人配置，支持层级继承

CREATE TABLE IF NOT EXISTS user_agent_configs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id VARCHAR(64) NOT NULL COMMENT '用户 ID',
    workspace_id VARCHAR(64) NOT NULL COMMENT '工作区 ID',
    
    -- 以下字段均可为空，空值表示继承工作区配置
    max_retries INT DEFAULT NULL COMMENT '最大重试次数 (NULL=继承)',
    always_confirm TINYINT DEFAULT NULL COMMENT '始终确认计划 (0/1/NULL)',
    execution_mode VARCHAR(20) DEFAULT NULL COMMENT '执行模式 (auto|rag_only|sql_only|chart_only|office_only)',
    synthesizer_template VARCHAR(64) DEFAULT NULL COMMENT '回复风格模板 ID',
    synthesizer_custom_prompt TEXT DEFAULT NULL COMMENT '用户微调 Prompt',
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- 联合唯一约束：同一用户在同一工作区只能有一条配置
    UNIQUE KEY uk_user_workspace (user_id, workspace_id),
    -- 索引：按用户查询
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci 
COMMENT='用户级智能体配置表';
