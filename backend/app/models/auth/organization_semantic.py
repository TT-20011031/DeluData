"""Versioned business context and organization semantic profiles."""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from app.core.db.database import Base


class WorkspaceBusinessContextModel(Base):
    __tablename__ = "workspace_business_contexts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, unique=True, index=True)
    content = Column(Text, nullable=False, default="")
    industry = Column(String(200), nullable=True)
    core_offerings_json = Column(JSON, nullable=True)
    business_objects_json = Column(JSON, nullable=True)
    business_processes_json = Column(JSON, nullable=True)
    customer_types_json = Column(JSON, nullable=True)
    operating_regions_json = Column(JSON, nullable=True)
    special_terms_json = Column(JSON, nullable=True)
    data_governance_constraints_json = Column(JSON, nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class OrganizationSemanticProfileModel(Base):
    """Mutable binding that points to immutable profile versions."""

    __tablename__ = "organization_semantic_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    org_unit_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="missing", index=True)
    revision = Column(Integer, nullable=False, default=0)
    active_version_id = Column(Integer, nullable=True)
    draft_version_id = Column(Integer, nullable=True)
    input_fingerprint = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "org_unit_id", name="uq_org_semantic_profile_org"),
    )


class OrganizationSemanticProfileVersionModel(Base):
    __tablename__ = "organization_semantic_profile_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    org_unit_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    content_json = Column(JSON, nullable=False, default=dict)
    evidence_json = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=True)
    input_fingerprint = Column(String(64), nullable=False)
    business_context_revision = Column(Integer, nullable=False)
    source = Column(String(20), nullable=False, default="ai")
    created_by = Column(String(64), nullable=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "org_unit_id", "version",
            name="uq_org_semantic_profile_version",
        ),
    )
