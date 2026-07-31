"""Semantic model storage for the first-stage NL2SQL refactor."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.sql.elements import quoted_name

from app.core.db.database import Base, get_async_db_manager

SemanticStatus = Literal["suggested", "confirmed", "disabled"]
SemanticSyncState = Literal["current", "stale", "orphaned"]
SemanticRuntimeMode = Literal["disabled", "shadow", "trusted"]


class SemanticDatasourceModel(Base):
    """Workspace database boundary and semantic SQL rollout switches."""

    __tablename__ = "semantic_datasources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=3306)
    database = Column(quoted_name("database", True), String(128), nullable=False)
    dialect = Column(String(32), nullable=False, default="mysql")
    is_active = Column(Boolean, nullable=False, default=True)
    semantic_sql_enabled = Column(Boolean, nullable=False, default=False)
    semantic_sql_fallback_enabled = Column(Boolean, nullable=False, default=True)
    runtime_mode = Column(String(20), nullable=False, default="disabled")
    access_bootstrap_required = Column(Boolean, nullable=False, default=True)
    active_evidence_set_id = Column(Integer, nullable=True, index=True)
    schema_fingerprint = Column(String(64), nullable=True)
    last_applied_scan_id = Column(Integer, nullable=True, index=True)
    last_scan_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "host",
            "port",
            "database",
            name="uq_semantic_datasource_endpoint",
        ),
    )


class SemanticTableModel(Base):
    """Business-facing table mapping."""

    __tablename__ = "semantic_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    physical_name = Column(String(128), nullable=False, index=True)
    business_name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    physical_comment = Column(Text, nullable=True)
    synonyms = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="confirmed")
    is_queryable = Column(Boolean, nullable=False, default=True)
    is_sensitive = Column(Boolean, nullable=False, default=False)
    sync_state = Column(String(20), nullable=False, default="current")
    origin_source = Column(String(32), nullable=False, default="legacy_unknown")
    management_mode = Column(String(20), nullable=False, default="human")
    confidence = Column(Float, nullable=True)
    evidence_json = Column(JSON, nullable=False, default=dict)
    stale_reason_json = Column(JSON, nullable=False, default=dict)
    schema_fingerprint = Column(String(64), nullable=True)
    last_seen_scan_id = Column(Integer, nullable=True)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    business_semantics_status = Column(String(20), nullable=False, default="pending", index=True)
    business_semantics_revision = Column(Integer, nullable=False, default=0)
    business_semantics_reviewed_by = Column(String(64), nullable=True)
    business_semantics_reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "datasource_id",
            "physical_name",
            name="uq_semantic_table_physical",
        ),
    )


class SemanticColumnModel(Base):
    """Business-facing field mapping."""

    __tablename__ = "semantic_columns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    table_id = Column(Integer, nullable=False, index=True)
    physical_table = Column(String(128), nullable=False)
    physical_name = Column(String(128), nullable=False, index=True)
    data_type = Column(String(128), nullable=False, default="")
    business_name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    physical_comment = Column(Text, nullable=True)
    synonyms = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="confirmed")
    is_queryable = Column(Boolean, nullable=False, default=True)
    is_sensitive = Column(Boolean, nullable=False, default=False)
    is_primary_key = Column(Boolean, nullable=False, default=False)
    is_indexed = Column(Boolean, nullable=False, default=False)
    ordinal_position = Column(Integer, nullable=False, default=0)
    sync_state = Column(String(20), nullable=False, default="current")
    origin_source = Column(String(32), nullable=False, default="legacy_unknown")
    management_mode = Column(String(20), nullable=False, default="human")
    confidence = Column(Float, nullable=True)
    evidence_json = Column(JSON, nullable=False, default=dict)
    stale_reason_json = Column(JSON, nullable=False, default=dict)
    schema_fingerprint = Column(String(64), nullable=True)
    last_seen_scan_id = Column(Integer, nullable=True)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    business_semantics_status = Column(String(20), nullable=False, default="pending", index=True)
    business_semantics_revision = Column(Integer, nullable=False, default=0)
    business_semantics_reviewed_by = Column(String(64), nullable=True)
    business_semantics_reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "datasource_id",
            "table_id",
            "physical_name",
            name="uq_semantic_column_physical",
        ),
    )


class SemanticRowPermissionModel(Base):
    """System-managed row-level permission rule for semantic tables."""

    __tablename__ = "semantic_row_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    table_id = Column(Integer, nullable=False, index=True)
    subject_type = Column(String(16), nullable=False, index=True)
    subject_id = Column(String(64), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    condition_json = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SemanticAccessPolicyModel(Base):
    """Versioned structured semantic access profile."""

    __tablename__ = "semantic_access_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    source_type = Column(String(32), nullable=False, default="manual")
    source_text = Column(Text, nullable=True)
    source_ref = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="draft", index=True)
    subject_json = Column(JSON, nullable=False, default=dict)
    asset_selector_json = Column(JSON, nullable=False, default=dict)
    constraint_json = Column(JSON, nullable=False, default=list)
    compile_summary_json = Column(JSON, nullable=False, default=dict)
    validation_json = Column(JSON, nullable=False, default=dict)
    model_version = Column(Integer, nullable=False, default=2)
    policy_version = Column(Integer, nullable=False, default=1)
    schema_fingerprint = Column(String(64), nullable=True)
    created_by = Column(String(64), nullable=True)
    activated_by = Column(String(64), nullable=True)
    activated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "datasource_id",
            "source_ref",
            name="uq_semantic_access_policy_source",
        ),
        Index(
            "ix_semantic_access_policy_runtime",
            "workspace_id",
            "datasource_id",
            "status",
        ),
        Index("ix_semantic_access_policy_model_version", "model_version"),
    )


class SemanticAccessPolicyEffectModel(Base):
    """Compiled deterministic effect used by semantic query enforcement."""

    __tablename__ = "semantic_access_policy_effects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    policy_id = Column(Integer, nullable=False, index=True)
    effect_key = Column(String(128), nullable=False)
    subject_type = Column(String(16), nullable=False, index=True)
    subject_id = Column(String(64), nullable=False, default="*", index=True)
    asset_type = Column(String(16), nullable=False, index=True)
    asset_id = Column(Integer, nullable=False, index=True)
    effect_type = Column(String(32), nullable=False, index=True)
    condition_json = Column(JSON, nullable=False, default=dict)
    compiled_sql = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(Boolean, nullable=False, default=True)
    policy_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("policy_id", "effect_key", name="uq_semantic_access_policy_effect"),
        Index(
            "ix_semantic_access_effect_runtime",
            "workspace_id",
            "datasource_id",
            "subject_type",
            "subject_id",
            "enabled",
        ),
        Index(
            "ix_semantic_access_effect_asset",
            "workspace_id",
            "datasource_id",
            "asset_type",
            "asset_id",
            "effect_type",
        ),
    )


class SemanticPolicyBindingModel(Base):
    """Stable target binding with an atomic pointer to the active version."""

    __tablename__ = "semantic_policy_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    target_type = Column(String(24), nullable=False, index=True)
    target_id = Column(String(64), nullable=False, default="*")
    include_descendants = Column(Boolean, nullable=False, default=False, server_default="0")
    active_version_id = Column(Integer, nullable=True, index=True)
    revision = Column(Integer, nullable=False, default=0)
    status = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "datasource_id", "target_type", "target_id",
            name="uq_semantic_policy_binding_target",
        ),
        Index("ix_semantic_policy_binding_runtime", "workspace_id", "datasource_id", "status"),
    )


class SemanticPolicyVersionModel(Base):
    """Immutable semantic access definition and validation snapshot."""

    __tablename__ = "semantic_policy_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    binding_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    definition_json = Column(JSON, nullable=False, default=dict)
    source_text = Column(Text, nullable=True)
    source_type = Column(String(32), nullable=False, default="manual", server_default="manual")
    source_ref = Column(String(128), nullable=True, index=True)
    validation_json = Column(JSON, nullable=False, default=dict)
    compile_summary_json = Column(JSON, nullable=False, default=dict)
    schema_fingerprint = Column(String(64), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("binding_id", "version", name="uq_semantic_policy_binding_version"),
    )


class SemanticPolicyVersionEffectModel(Base):
    """Compiled effect owned by an immutable semantic policy version."""

    __tablename__ = "semantic_policy_version_effects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    binding_id = Column(Integer, nullable=False, index=True)
    version_id = Column(Integer, nullable=False, index=True)
    effect_key = Column(String(128), nullable=False)
    asset_type = Column(String(16), nullable=False, index=True)
    asset_id = Column(Integer, nullable=False, index=True)
    effect_type = Column(String(32), nullable=False, index=True)
    condition_json = Column(JSON, nullable=False, default=dict)
    compiled_sql = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("version_id", "effect_key", name="uq_semantic_policy_version_effect"),
        Index("ix_semantic_policy_version_effect_asset", "workspace_id", "datasource_id", "asset_type", "asset_id"),
    )


class SemanticOwnershipMappingModel(Base):
    """Maps semantic tables to organization and user ownership columns."""

    __tablename__ = "semantic_ownership_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    table_id = Column(Integer, nullable=False, index=True)
    org_column_id = Column(Integer, nullable=True)
    org_value_kind = Column(String(16), nullable=False, default="id")
    user_column_id = Column(Integer, nullable=True)
    user_value_kind = Column(String(16), nullable=False, default="id")
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "datasource_id", "table_id", name="uq_semantic_ownership_mapping"),
    )


class SemanticAccessBootstrapRunModel(Base):
    """Persistent AI-assisted first-time semantic access configuration run."""

    __tablename__ = "semantic_access_bootstrap_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    evidence_set_id = Column(Integer, nullable=True, index=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    stage = Column(String(32), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=0)
    schema_fingerprint = Column(String(64), nullable=True)
    organization_fingerprint = Column(String(64), nullable=False)
    business_context = Column(Text, nullable=True)
    input_snapshot_json = Column(JSON, nullable=False, default=dict)
    summary_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    triggered_by = Column(String(64), nullable=True)
    applied_by = Column(String(64), nullable=True)
    worker_id = Column(String(128), nullable=True)
    run_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=2)
    cancel_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now(), index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "ix_semantic_access_bootstrap_run_claim",
            "status",
            "lease_expires_at",
            "created_at",
        ),
    )


class SemanticAccessBootstrapTargetModel(Base):
    """Reviewable target policy generated by an access bootstrap run."""

    __tablename__ = "semantic_access_bootstrap_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    target_type = Column(String(24), nullable=False, index=True)
    target_id = Column(String(64), nullable=False)
    target_label = Column(String(128), nullable=False)
    include_descendants = Column(Boolean, nullable=False, default=False)
    base_binding_id = Column(Integer, nullable=True)
    base_revision = Column(Integer, nullable=False, default=0)
    included = Column(Boolean, nullable=False, default=True)
    status = Column(String(24), nullable=False, default="proposed", index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    definition_json = Column(JSON, nullable=False, default=dict)
    candidates_json = Column(JSON, nullable=False, default=list)
    explanation_json = Column(JSON, nullable=False, default=list)
    validation_json = Column(JSON, nullable=False, default=dict)
    edited_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "target_type", "target_id", name="uq_semantic_access_bootstrap_target"),
    )


class SemanticAccessBootstrapMappingModel(Base):
    """Reviewable semantic ownership mapping proposed by a bootstrap run."""

    __tablename__ = "semantic_access_bootstrap_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    table_id = Column(Integer, nullable=False, index=True)
    table_label = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="proposed", index=True)
    accepted = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=False, default=0.0)
    base_mapping_json = Column(JSON, nullable=False, default=dict)
    proposed_mapping_json = Column(JSON, nullable=False, default=dict)
    evidence_json = Column(JSON, nullable=False, default=list)
    validation_json = Column(JSON, nullable=False, default=dict)
    edited_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "table_id", name="uq_semantic_access_bootstrap_mapping"),
    )


class SemanticAssetTagModel(Base):
    """Reusable classification tag for semantic tables, columns and metrics."""

    __tablename__ = "semantic_asset_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    asset_type = Column(String(16), nullable=False, index=True)
    asset_id = Column(Integer, nullable=False, index=True)
    tag_type = Column(String(32), nullable=False, index=True)
    tag_value = Column(String(128), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="manual")
    confidence = Column(Float, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "datasource_id",
            "asset_type",
            "asset_id",
            "tag_type",
            "tag_value",
            name="uq_semantic_asset_tag",
        ),
    )


class SemanticMetricModel(Base):
    """Governed business metric definition."""

    __tablename__ = "semantic_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    business_name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    formula = Column(Text, nullable=False)
    aggregation = Column(String(32), nullable=True)
    table_id = Column(Integer, nullable=False, index=True)
    column_id = Column(Integer, nullable=True, index=True)
    time_column_id = Column(Integer, nullable=True, index=True)
    default_grain = Column(String(32), nullable=True)
    synonyms = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="suggested")
    is_queryable = Column(Boolean, nullable=False, default=True)
    is_sensitive = Column(Boolean, nullable=False, default=False)
    sync_state = Column(String(20), nullable=False, default="current")
    origin_source = Column(String(32), nullable=False, default="legacy_unknown")
    management_mode = Column(String(20), nullable=False, default="human")
    confidence = Column(Float, nullable=True)
    evidence_json = Column(JSON, nullable=False, default=dict)
    stale_reason_json = Column(JSON, nullable=False, default=dict)
    schema_fingerprint = Column(String(64), nullable=True)
    last_seen_scan_id = Column(Integer, nullable=True)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "datasource_id",
            "name",
            name="uq_semantic_metric_name",
        ),
    )


class SemanticRelationshipModel(Base):
    """Confirmed join edge between two semantic tables."""

    __tablename__ = "semantic_relationships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    left_table_id = Column(Integer, nullable=False, index=True)
    right_table_id = Column(Integer, nullable=False, index=True)
    left_column_id = Column(Integer, nullable=False)
    right_column_id = Column(Integer, nullable=False)
    relationship_type = Column(String(32), nullable=False, default="many_to_one")
    confidence = Column(Float, nullable=False, default=0.5)
    status = Column(String(20), nullable=False, default="suggested")
    is_queryable = Column(Boolean, nullable=False, default=True)
    description = Column(Text, nullable=True)
    sync_state = Column(String(20), nullable=False, default="current")
    origin_source = Column(String(32), nullable=False, default="legacy_unknown")
    management_mode = Column(String(20), nullable=False, default="human")
    evidence_json = Column(JSON, nullable=False, default=dict)
    stale_reason_json = Column(JSON, nullable=False, default=dict)
    schema_fingerprint = Column(String(64), nullable=True)
    last_seen_scan_id = Column(Integer, nullable=True)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SemanticQueryRunModel(Base):
    """Audit record for semantic SQL rollout and old/new comparison."""

    __tablename__ = "semantic_query_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    session_id = Column(String(128), nullable=True, index=True)
    question = Column(Text, nullable=False)
    semantic_enabled = Column(Boolean, nullable=False, default=False)
    fallback_used = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="unknown")
    error_type = Column(String(64), nullable=True)
    intent_json = Column(JSON, nullable=True)
    plan_json = Column(JSON, nullable=True)
    sql = Column(quoted_name("sql", True), Text, nullable=True)
    legacy_sql = Column(Text, nullable=True)
    referenced_tables = Column(JSON, nullable=False, default=list)
    row_count = Column(Integer, nullable=False, default=0)
    execution_time_ms = Column(Integer, nullable=False, default=0)
    runtime_mode = Column(String(20), nullable=False, default="disabled")
    correlation_id = Column(String(64), nullable=True, index=True)
    semantic_result_json = Column(JSON, nullable=True)
    legacy_result_json = Column(JSON, nullable=True)
    comparison_json = Column(JSON, nullable=True)
    returned_chain = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class SemanticScanRunModel(Base):
    """Immutable schema preview and atomic-apply record."""

    __tablename__ = "semantic_scan_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    created_by = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="previewed", index=True)
    base_fingerprint = Column(String(64), nullable=True)
    target_fingerprint = Column(String(64), nullable=False)
    snapshot_json = Column(JSON, nullable=False, default=dict)
    diff_json = Column(JSON, nullable=False, default=list)
    impact_json = Column(JSON, nullable=False, default=list)
    summary_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    expires_at = Column(DateTime, nullable=False)
    applied_at = Column(DateTime, nullable=True)


class SemanticGovernanceEventModel(Base):
    """Workspace-scoped semantic governance audit event."""

    __tablename__ = "semantic_governance_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=True, index=True)
    actor_id = Column(String(64), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    object_type = Column(String(32), nullable=True)
    object_id = Column(Integer, nullable=True)
    scan_id = Column(Integer, nullable=True, index=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.now, index=True)


class SemanticGovernancePolicyModel(Base):
    """Per-datasource evidence collection and automation guardrails."""

    __tablename__ = "semantic_governance_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    observe_only = Column(Boolean, nullable=False, default=True)
    run_after_scan = Column(Boolean, nullable=False, default=False)
    exact_row_threshold = Column(Integer, nullable=False, default=50000)
    sample_row_limit = Column(Integer, nullable=False, default=1000)
    table_timeout_sec = Column(Integer, nullable=False, default=3)
    max_tables_per_run = Column(Integer, nullable=False, default=5)
    max_run_seconds = Column(Integer, nullable=False, default=90)
    review_threshold = Column(Float, nullable=False, default=0.65)
    high_confidence_threshold = Column(Float, nullable=False, default=0.90)
    auto_apply_threshold = Column(Float, nullable=False, default=0.98)
    min_auto_evidence_sources = Column(Integer, nullable=False, default=2)
    auto_action_types = Column(JSON, nullable=False, default=list)
    policy_version = Column(String(32), nullable=False, default="evidence-v1")
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("workspace_id", "datasource_id", name="uq_semantic_governance_policy"),
    )


class SemanticGovernanceRunModel(Base):
    """Persistent evidence-driven governance run."""

    __tablename__ = "semantic_governance_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False, default="manual")
    triggered_by = Column(String(64), nullable=True)
    scan_id = Column(Integer, nullable=True, index=True)
    schema_fingerprint = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    stage = Column(String(32), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    observe_only = Column(Boolean, nullable=False, default=True)
    summary_json = Column(JSON, nullable=False, default=dict)
    budget_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    worker_id = Column(String(128), nullable=True)
    run_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=2)
    cancel_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class SemanticColumnProfileModel(Base):
    """Aggregate-only column profile. Raw values are never persisted."""

    __tablename__ = "semantic_column_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    table_id = Column(Integer, nullable=False, index=True)
    column_id = Column(Integer, nullable=False, index=True)
    schema_fingerprint = Column(String(64), nullable=True)
    profile_status = Column(String(20), nullable=False, default="complete")
    sample_method = Column(String(20), nullable=False, default="exact")
    estimated_rows = Column(Integer, nullable=True)
    sampled_rows = Column(Integer, nullable=False, default=0)
    non_null_count = Column(Integer, nullable=False, default=0)
    null_ratio = Column(Float, nullable=True)
    distinct_count = Column(Integer, nullable=True)
    distinct_ratio = Column(Float, nullable=True)
    range_json = Column(JSON, nullable=False, default=dict)
    avg_length = Column(Float, nullable=True)
    pattern_ratios_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    profiled_at = Column(DateTime, default=datetime.now, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("run_id", "column_id", name="uq_semantic_column_profile_run"),
    )


class SemanticEvidenceFactModel(Base):
    """Normalized supporting or conflicting evidence for a semantic claim."""

    __tablename__ = "semantic_evidence_facts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    subject_type = Column(String(32), nullable=False, index=True)
    subject_id = Column(Integer, nullable=True, index=True)
    claim_type = Column(String(64), nullable=False, index=True)
    claim_key = Column(String(255), nullable=False)
    value_json = Column(JSON, nullable=False, default=dict)
    source_type = Column(String(32), nullable=False, index=True)
    source_ref = Column(String(255), nullable=True)
    direction = Column(String(16), nullable=False, default="support")
    reliability = Column(Float, nullable=False, default=0.5)
    strength = Column(Float, nullable=False, default=1.0)
    fact_fingerprint = Column(String(64), nullable=False)
    schema_fingerprint = Column(String(64), nullable=True)
    observed_at = Column(DateTime, default=datetime.now, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "datasource_id", "fact_fingerprint", name="uq_semantic_evidence_fact"),
    )


class SemanticGovernanceCandidateModel(Base):
    """Explainable governance proposal and its complete decision lifecycle."""

    __tablename__ = "semantic_governance_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    target_type = Column(String(32), nullable=False, index=True)
    target_id = Column(Integer, nullable=True, index=True)
    candidate_type = Column(String(64), nullable=False, index=True)
    candidate_key = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    before_json = Column(JSON, nullable=False, default=dict)
    proposed_patch_json = Column(JSON, nullable=False, default=dict)
    applied_patch_json = Column(JSON, nullable=False, default=dict)
    supporting_evidence_json = Column(JSON, nullable=False, default=list)
    conflicting_evidence_json = Column(JSON, nullable=False, default=list)
    evidence_fact_ids = Column(JSON, nullable=False, default=list)
    source_types = Column(JSON, nullable=False, default=list)
    score = Column(Float, nullable=False, default=0.0)
    score_version = Column(String(32), nullable=False, default="evidence-v1")
    risk_level = Column(String(20), nullable=False, default="medium", index=True)
    status = Column(String(24), nullable=False, default="proposed", index=True)
    auto_eligible = Column(Boolean, nullable=False, default=False)
    deterministic_check_passed = Column(Boolean, nullable=False, default=False)
    policy_version = Column(String(32), nullable=False, default="evidence-v1")
    target_updated_at = Column(DateTime, nullable=True)
    decided_by = Column(String(64), nullable=True)
    decision_reason = Column(Text, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("workspace_id", "datasource_id", "candidate_key", name="uq_semantic_governance_candidate"),
    )


class SemanticEvaluationRunModel(Base):
    """Regression evaluation result for the semantic NL2SQL rollout."""

    __tablename__ = "semantic_evaluation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    case_id = Column(String(32), nullable=False, index=True)
    question = Column(Text, nullable=False)
    test_dimension = Column(String(255), nullable=False, default="")
    expected_focus = Column(JSON, nullable=False, default=list)
    expected_limit = Column(Integer, nullable=True)
    semantic_result = Column(JSON, nullable=False, default=dict)
    legacy_result = Column(JSON, nullable=False, default=dict)
    verdict = Column(String(32), nullable=False, default="unknown")
    diagnostics = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.now, index=True)


class SemanticBusinessSuggestionModel(Base):
    """Pending business semantics generated from physical metadata."""

    __tablename__ = "semantic_business_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    datasource_id = Column(Integer, nullable=False, index=True)
    object_type = Column(String(20), nullable=False)
    object_id = Column(Integer, nullable=False)
    physical_name = Column(String(128), nullable=False)
    suggested_business_name = Column(String(128), nullable=False)
    suggested_description = Column(Text, nullable=True)
    suggested_synonyms = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.5)
    source = Column(String(32), nullable=False, default="rule")
    status = Column(String(20), nullable=False, default="pending")
    evidence_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    accepted_at = Column(DateTime, nullable=True)

class SemanticDatasource(BaseModel):
    id: int
    workspace_id: str
    name: str
    host: str
    port: int
    database: str
    dialect: str = "mysql"
    is_active: bool = True
    semantic_sql_enabled: bool = False
    semantic_sql_fallback_enabled: bool = True
    runtime_mode: SemanticRuntimeMode = "disabled"
    access_bootstrap_required: bool = True
    active_evidence_set_id: Optional[int] = None
    schema_fingerprint: Optional[str] = None
    last_applied_scan_id: Optional[int] = None
    last_scan_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticDatasourceModel) -> "SemanticDatasource":
        return cls.model_validate(model, from_attributes=True)


class SemanticTable(BaseModel):
    id: int
    workspace_id: str
    datasource_id: int
    physical_name: str
    business_name: str
    description: Optional[str] = None
    physical_comment: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    status: SemanticStatus = "suggested"
    is_queryable: bool = True
    is_sensitive: bool = False
    sync_state: SemanticSyncState = "current"
    origin_source: str = "legacy_unknown"
    management_mode: str = "human"
    confidence: Optional[float] = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    stale_reason_json: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    last_seen_scan_id: Optional[int] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    business_semantics_status: str = "pending"
    business_semantics_revision: int = 0
    business_semantics_reviewed_by: Optional[str] = None
    business_semantics_reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticTableModel) -> "SemanticTable":
        return cls.model_validate(model, from_attributes=True)


class SemanticColumn(BaseModel):
    id: int
    workspace_id: str
    datasource_id: int
    table_id: int
    physical_table: str
    physical_name: str
    data_type: str = ""
    business_name: str
    description: Optional[str] = None
    physical_comment: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    status: SemanticStatus = "suggested"
    is_queryable: bool = True
    is_sensitive: bool = False
    is_primary_key: bool = False
    is_indexed: bool = False
    ordinal_position: int = 0
    sync_state: SemanticSyncState = "current"
    origin_source: str = "legacy_unknown"
    management_mode: str = "human"
    confidence: Optional[float] = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    stale_reason_json: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    last_seen_scan_id: Optional[int] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    business_semantics_status: str = "pending"
    business_semantics_revision: int = 0
    business_semantics_reviewed_by: Optional[str] = None
    business_semantics_reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticColumnModel) -> "SemanticColumn":
        return cls.model_validate(model, from_attributes=True)


class SemanticMetric(BaseModel):
    id: int
    workspace_id: str
    datasource_id: int
    name: str
    business_name: str
    description: Optional[str] = None
    formula: str
    aggregation: Optional[str] = None
    table_id: int
    column_id: Optional[int] = None
    time_column_id: Optional[int] = None
    default_grain: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    status: SemanticStatus = "suggested"
    is_queryable: bool = True
    is_sensitive: bool = False
    sync_state: SemanticSyncState = "current"
    origin_source: str = "legacy_unknown"
    management_mode: str = "human"
    confidence: Optional[float] = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    stale_reason_json: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    last_seen_scan_id: Optional[int] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticMetricModel) -> "SemanticMetric":
        return cls.model_validate(model, from_attributes=True)


class SemanticRelationship(BaseModel):
    id: int
    workspace_id: str
    datasource_id: int
    left_table_id: int
    right_table_id: int
    left_column_id: int
    right_column_id: int
    relationship_type: str = "many_to_one"
    confidence: float = 0.5
    status: SemanticStatus = "suggested"
    is_queryable: bool = True
    description: Optional[str] = None
    sync_state: SemanticSyncState = "current"
    origin_source: str = "legacy_unknown"
    management_mode: str = "human"
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    stale_reason_json: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    last_seen_scan_id: Optional[int] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticRelationshipModel) -> "SemanticRelationship":
        return cls.model_validate(model, from_attributes=True)


class SemanticQueryRun(BaseModel):
    id: int
    workspace_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    question: str
    semantic_enabled: bool = False
    fallback_used: bool = False
    status: str = "unknown"
    error_type: Optional[str] = None
    intent_json: Optional[dict[str, Any]] = None
    plan_json: Optional[dict[str, Any]] = None
    sql: Optional[str] = None
    legacy_sql: Optional[str] = None
    referenced_tables: list[str] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    runtime_mode: str = "disabled"
    correlation_id: Optional[str] = None
    semantic_result_json: Optional[dict[str, Any]] = None
    legacy_result_json: Optional[dict[str, Any]] = None
    comparison_json: Optional[dict[str, Any]] = None
    returned_chain: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticQueryRunModel) -> "SemanticQueryRun":
        return cls.model_validate(model, from_attributes=True)


class SemanticEvaluationRun(BaseModel):
    id: int
    workspace_id: str
    user_id: Optional[str] = None
    case_id: str
    question: str
    test_dimension: str = ""
    expected_focus: list[str] = Field(default_factory=list)
    expected_limit: Optional[int] = None
    semantic_result: dict[str, Any] = Field(default_factory=dict)
    legacy_result: dict[str, Any] = Field(default_factory=dict)
    verdict: str = "unknown"
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, model: SemanticEvaluationRunModel) -> "SemanticEvaluationRun":
        return cls.model_validate(model, from_attributes=True)


class SemanticBusinessSuggestion(BaseModel):
    id: int
    workspace_id: str
    datasource_id: int
    object_type: str
    object_id: int
    physical_name: str
    suggested_business_name: str
    suggested_description: Optional[str] = None
    suggested_synonyms: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    source: str = "rule"
    status: str = "pending"
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    needs_manual_input: bool = False
    reason: Optional[str] = None
    profile_summary: dict[str, Any] = Field(default_factory=dict)
    target_context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    accepted_at: Optional[datetime] = None

    @classmethod
    def from_orm(cls, model: SemanticBusinessSuggestionModel) -> "SemanticBusinessSuggestion":
        evidence = dict(model.evidence_json or {})
        payload = {
            "id": model.id,
            "workspace_id": model.workspace_id,
            "datasource_id": model.datasource_id,
            "object_type": model.object_type,
            "object_id": model.object_id,
            "physical_name": model.physical_name,
            "suggested_business_name": model.suggested_business_name,
            "suggested_description": model.suggested_description,
            "suggested_synonyms": model.suggested_synonyms or [],
            "confidence": model.confidence,
            "source": model.source,
            "status": model.status,
            "evidence_json": evidence,
            "needs_manual_input": bool(evidence.get("needs_manual_input")),
            "reason": evidence.get("reason"),
            "profile_summary": evidence.get("profile_summary") or {},
            "target_context": evidence.get("target_context") or {},
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "accepted_at": model.accepted_at,
        }
        return cls(**payload)


async def get_active_semantic_datasource_async(
    workspace_id: str,
) -> Optional[SemanticDatasource]:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SemanticDatasourceModel)
            .where(
                SemanticDatasourceModel.workspace_id == workspace_id,
                SemanticDatasourceModel.is_active == True,  # noqa: E712
            )
            .order_by(SemanticDatasourceModel.updated_at.desc())
        )
        model = result.scalars().first()
        return SemanticDatasource.from_orm(model) if model else None
