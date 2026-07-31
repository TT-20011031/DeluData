-- Promote scanned physical tables and columns to trusted semantic objects.
-- Metrics and relationships remain suggested because they encode business meaning.

UPDATE semantic_tables
SET status = 'disabled',
    is_queryable = 0,
    updated_at = NOW()
WHERE status = 'suggested'
  AND (
      LOWER(physical_name) REGEXP '(^|_)(bak|backup|tmp|temp|test|copy|old)(_|[0-9]|$)'
      OR LOWER(physical_name) REGEXP '(_bak[0-9]*|_backup[0-9]*|_tmp[0-9]*|_temp[0-9]*)$'
      OR LOWER(physical_name) REGEXP '^(sys_|mysql_|information_schema|performance_schema)'
  );

UPDATE semantic_tables
SET status = 'confirmed',
    updated_at = NOW()
WHERE status = 'suggested';

UPDATE semantic_columns AS c
JOIN semantic_tables AS t ON t.id = c.table_id
SET c.status = 'confirmed',
    c.is_queryable = CASE
        WHEN t.status = 'disabled' OR t.is_queryable = 0 THEN 0
        ELSE c.is_queryable
    END,
    c.updated_at = NOW()
WHERE c.status = 'suggested';

UPDATE semantic_columns
SET is_sensitive = 1,
    updated_at = NOW()
WHERE LOWER(physical_name) REGEXP '(password|passwd|pwd|token|secret|credential|id_card|identity|phone|mobile|email|salary|bank|account|address)'
   OR physical_name REGEXP '(身份证|手机号|电话|邮箱|密码|工资|薪资|银行卡|地址)';
