-- ============================================
-- 批量操作与分组功能 数据库迁移脚本
-- 执行前请先备份数据库
-- ============================================

-- 1. 创建 SQL 示例分组表
CREATE TABLE IF NOT EXISTS sql_example_groups (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL COMMENT '分组名称',
    description VARCHAR(500) COMMENT '分组描述',
    color VARCHAR(20) DEFAULT '#3B82F6' COMMENT '分组颜色（UI显示用）',
    workspace_id VARCHAR(64) NOT NULL COMMENT '工作空间ID',
    created_by VARCHAR(64) NOT NULL COMMENT '创建者ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SQL示例分组表';


-- 2. 为 SQL 示例表添加 group_id 和 vector_sync_status 字段
-- 检查字段是否已存在（MySQL 不支持 IF NOT EXISTS 语法，需手动检查）
-- 如果字段已存在，此语句会报错，可忽略
ALTER TABLE sql_examples
ADD COLUMN group_id INT COMMENT '所属分组ID',
ADD COLUMN vector_sync_status VARCHAR(20) DEFAULT 'synced' COMMENT '向量库同步状态',
ADD INDEX idx_group (group_id),
ADD INDEX idx_sync_status (vector_sync_status);


-- 3. (可选) 创建知识库分组表
CREATE TABLE IF NOT EXISTS knowledge_groups (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '分组名称',
    description VARCHAR(500) COMMENT '分组描述',
    color VARCHAR(20) DEFAULT '#10B981' COMMENT '分组颜色',
    user_id VARCHAR(64) NOT NULL COMMENT '所属用户',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库分组表';


-- 4. (可选) 为知识库文件表添加 group_id 字段
-- ALTER TABLE files
-- ADD COLUMN group_id VARCHAR(36) COMMENT '所属分组ID',
-- ADD INDEX idx_group (group_id);


-- ============================================
-- 验证脚本（执行后检查输出）
-- ============================================

-- 检查 sql_example_groups 表是否创建成功
-- SELECT COUNT(*) AS group_table_exists FROM information_schema.tables 
-- WHERE table_schema = DATABASE() AND table_name = 'sql_example_groups';

-- 检查 sql_examples 表是否有 group_id 字段
-- SELECT COUNT(*) AS group_id_exists FROM information_schema.columns 
-- WHERE table_schema = DATABASE() AND table_name = 'sql_examples' AND column_name = 'group_id';
