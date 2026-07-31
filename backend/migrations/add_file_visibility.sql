-- 知识库文件表添加可见性和部门字段
-- 用于支持概览统计和权限过滤功能

-- 1. 添加可见性字段 (public: 全局 | dept: 部门 | private: 私有)
ALTER TABLE files ADD COLUMN visibility VARCHAR(20) DEFAULT 'dept';

-- 2. 添加部门ID (关联 sys_departments.id)
ALTER TABLE files ADD COLUMN dept_id INT NULL;

-- 3. 添加上传者ID
ALTER TABLE files ADD COLUMN owner_id VARCHAR(36) NULL;

-- 4. 添加索引加速查询
CREATE INDEX idx_files_visibility ON files(visibility);
CREATE INDEX idx_files_dept_id ON files(dept_id);
CREATE INDEX idx_files_owner_id ON files(owner_id);

-- ================================================
-- 5. [重要] 给已有数据设置默认值
-- ================================================

-- 将所有旧文件设置为"全局共享"可见
UPDATE files SET visibility = 'public' WHERE visibility IS NULL OR visibility = '';

-- 将 owner_id 设置为 user_id（上传者就是创建者）
UPDATE files SET owner_id = user_id WHERE owner_id IS NULL;
