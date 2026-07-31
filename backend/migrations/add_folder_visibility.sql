-- 文件夹表添加可见性和部门字段
-- 用于支持文件夹的 scope 隔离功能

-- 1. 添加可见性字段 (public: 全局 | dept: 部门 | private: 私有)
ALTER TABLE folders ADD COLUMN visibility VARCHAR(20) DEFAULT 'public';

-- 2. 添加部门ID (关联 sys_departments.id)
ALTER TABLE folders ADD COLUMN dept_id INT NULL;

-- 3. 添加创建者ID
ALTER TABLE folders ADD COLUMN owner_id VARCHAR(36) NULL;

-- 4. 给已有文件夹设置默认值
UPDATE folders SET visibility = 'public' WHERE visibility IS NULL;
UPDATE folders SET owner_id = user_id WHERE owner_id IS NULL;
