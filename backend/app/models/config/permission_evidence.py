"""Auditable organization-to-semantic-asset evidence chain."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from app.core.db.database import Base


class SemanticAccessEvidenceSetModel(Base):
    __tablename__ = "semantic_access_evidence_sets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    model_version = Column(Integer, nullable=False, default=2)
    revision = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="generating", index=True)
    org_profile_fingerprint = Column(String(64), nullable=False)
    schema_fingerprint = Column(String(64), nullable=False)
    semantic_fingerprint = Column(String(64), nullable=False)
    review_progress_json = Column(JSON, nullable=False, default=dict)
    blockers_json = Column(JSON, nullable=False, default=list)
    generation_summary_json = Column(JSON, nullable=False, default=dict)
    compile_result_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    generated_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "datasource_id", "version",
            name="uq_semantic_access_evidence_set_version",
        ),
    )


class SemanticAccessEvidenceAssetModel(Base):
    __tablename__ = "semantic_access_evidence_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    evidence_set_id = Column(Integer, nullable=False, index=True)
    asset_type = Column(String(16), nullable=False)
    asset_id = Column(Integer, nullable=False)
    table_id = Column(Integer, nullable=False, index=True)
    baseline_access = Column(String(24), nullable=True)
    requires_individual_review = Column(Boolean, nullable=False, default=False)
    # Legacy v1 compatibility. Remove after the dual-read/write rollout window.
    access_class = Column(String(32), nullable=False, default="department_scoped")
    is_sensitive = Column(Boolean, nullable=False, default=False)
    inherits_table = Column(Boolean, nullable=False, default=False)
    review_status = Column(String(20), nullable=False, default="pending", index=True)
    reason = Column(Text, nullable=True)
    evidence_json = Column(JSON, nullable=False, default=list)
    override_reason = Column(Text, nullable=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "evidence_set_id", "asset_type", "asset_id",
            name="uq_semantic_access_evidence_asset",
        ),
    )


class SemanticAccessEvidenceRelationModel(Base):
    __tablename__ = "semantic_access_evidence_relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    evidence_set_id = Column(Integer, nullable=False, index=True)
    asset_type = Column(String(16), nullable=False)
    asset_id = Column(Integer, nullable=False)
    table_id = Column(Integer, nullable=False, index=True)
    org_unit_id = Column(Integer, nullable=False, index=True)
    business_role = Column(String(32), nullable=True)
    access_level = Column(String(16), nullable=True)
    field_decision = Column(String(16), nullable=True)
    # Legacy v1 compatibility. Remove after the dual-read/write rollout window.
    relation_role = Column(String(32), nullable=False, default="none")
    access_decision = Column(String(16), nullable=False, default="inherit")
    row_scope_json = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=False, default="")
    evidence_json = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=True)
    review_status = Column(String(20), nullable=False, default="pending", index=True)
    override_reason = Column(Text, nullable=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "evidence_set_id", "asset_type", "asset_id", "org_unit_id",
            name="uq_semantic_access_evidence_relation",
        ),
    )


class SemanticPermissionEvidenceRunModel(Base):
    __tablename__ = "semantic_permission_evidence_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=True, index=True)
    org_unit_id = Column(Integer, nullable=True, index=True)
    evidence_set_id = Column(Integer, nullable=True, index=True)
    job_kind = Column(String(24), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    stage = Column(String(32), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    input_fingerprint = Column(String(64), nullable=False)
    result_summary_json = Column(JSON, nullable=False, default=dict)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
