-- 系统回复风格模板表
-- 存储管理员可管理的系统预设模板

CREATE TABLE IF NOT EXISTS system_templates (
    id INT PRIMARY KEY AUTO_INCREMENT,
    template_id VARCHAR(64) NOT NULL COMMENT '模板唯一标识',
    workspace_id VARCHAR(64) NOT NULL COMMENT '工作区 ID',
    name VARCHAR(100) NOT NULL COMMENT '模板名称',
    description VARCHAR(500) DEFAULT NULL COMMENT '模板描述',
    prompt TEXT NOT NULL COMMENT '模板 Prompt 内容',
    is_default TINYINT DEFAULT 0 COMMENT '是否为默认模板',
    sort_order INT DEFAULT 0 COMMENT '排序权重',
    created_by VARCHAR(64) DEFAULT NULL COMMENT '创建者 ID',
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uq_system_templates_workspace_template (workspace_id, template_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci 
COMMENT='系统回复风格模板表';
