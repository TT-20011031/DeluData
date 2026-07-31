-- ============================================
-- 多租户架构 - 数据库迁移脚本
-- 执行时间: 2026-01-30
-- ============================================

-- 1. 创建工作空间/租户表
CREATE TABLE IF NOT EXISTS sys_workspaces (
    id VARCHAR(36) PRIMARY KEY COMMENT '工作空间ID',
    name VARCHAR(128) NOT NULL COMMENT '企业名称',
    code VARCHAR(64) UNIQUE NOT NULL COMMENT '唯一标识码',
    owner_id VARCHAR(36) COMMENT '超管用户ID',
    plan VARCHAR(32) DEFAULT 'free' COMMENT '套餐类型',
    max_users INT DEFAULT 10 COMMENT '最大用户数',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    description TEXT COMMENT '描述信息',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_workspace_code (code),
    INDEX idx_workspace_active (is_active)
);

-- 2. 插入默认工作空间
INSERT IGNORE INTO sys_workspaces (id, name, code, plan, max_users, description)
VALUES ('default', '默认工作空间', 'default', 'enterprise', 9999, '系统默认工作空间');


-- 3. 添加 platform:admin 权限
INSERT IGNORE INTO sys_permissions (code, module, description)
VALUES ('platform:admin', 'PLATFORM', '平台超管权限');


-- 4. 业务表添加 workspace_id（如果不存在）
-- 注意: 部分表已有该字段，使用 ALTER IGNORE 或条件判断

-- agent_configs 表
SET @exist := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_configs' AND COLUMN_NAME = 'workspace_id');
SET @sql := IF(@exist = 0, 
    'ALTER TABLE agent_configs ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT ''default''', 
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- sql_examples 表
SET @exist := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sql_examples' AND COLUMN_NAME = 'workspace_id');
SET @sql := IF(@exist = 0, 
    'ALTER TABLE sql_examples ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT ''default''', 
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- user_prompt_templates 表
SET @exist := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_prompt_templates' AND COLUMN_NAME = 'workspace_id');
SET @sql := IF(@exist = 0, 
    'ALTER TABLE user_prompt_templates ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT ''default''', 
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- langgraph_checkpoints 表（之前已添加，这里确保索引）
-- 已在 session_isolation_migration.sql 中处理


-- 5. 将现有用户绑定到默认工作空间
UPDATE sys_users SET workspace_id = 'default' WHERE workspace_id IS NULL OR workspace_id = '';


-- 6. 验证迁移结果
SELECT 'sys_workspaces 表内容' AS info;
SELECT * FROM sys_workspaces;

SELECT '迁移完成' AS status;
