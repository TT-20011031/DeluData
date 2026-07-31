-- Keep existing column states aligned with disabled semantic tables.

UPDATE semantic_columns AS c
JOIN semantic_tables AS t ON t.id = c.table_id
SET c.status = 'disabled',
    c.is_queryable = 0,
    c.updated_at = NOW()
WHERE t.status = 'disabled'
   OR t.is_queryable = 0;
