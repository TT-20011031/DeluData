ALTER TABLE files
  ADD COLUMN document_type VARCHAR(50) NULL,
  ADD COLUMN business_domain VARCHAR(100) NULL,
  ADD COLUMN confidentiality_level VARCHAR(50) NULL,
  ADD COLUMN effective_from DATETIME NULL,
  ADD COLUMN effective_until DATETIME NULL,
  ADD COLUMN external_ref VARCHAR(128) NULL;

CREATE INDEX ix_files_workspace_metadata
ON files (workspace_id, dept_id, visibility, business_domain, document_type, confidentiality_level);
