-- 修复 user_db_configs 表缺少只读账号字段
-- 运行此脚本添加缺失的列

ALTER TABLE user_db_configs 
ADD COLUMN readonly_username VARCHAR(100) DEFAULT NULL 
AFTER encrypted_password;

ALTER TABLE user_db_configs 
ADD COLUMN readonly_encrypted_password VARCHAR(256) DEFAULT NULL 
AFTER readonly_username;

-- 验证
DESCRIBE user_db_configs;
