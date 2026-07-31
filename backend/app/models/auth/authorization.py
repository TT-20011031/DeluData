"""ERP-style organization, assignment and scoped role authorization models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.db.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class PositionModel(Base):
    __tablename__ = "sys_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    org_unit_id = Column(Integer, ForeignKey("sys_departments.id"), nullable=False, index=True)
    name = Column(String(96), nullable=False)
    code = Column(String(64), nullable=True)
    status = Column(Boolean, nullable=False, default=True)
    legacy_generated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_now, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "org_unit_id", "name", name="uq_position_org_name"),
        Index("ix_position_workspace_status", "workspace_id", "status"),
    )


class AssignmentModel(Base):
    __tablename__ = "sys_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("sys_users.id", ondelete="CASCADE"), nullable=False, index=True)
    position_id = Column(Integer, ForeignKey("sys_positions.id"), nullable=False, index=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    starts_at = Column(DateTime, nullable=False, default=_now)
    ends_at = Column(DateTime, nullable=True)
    status = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_now, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now())

    __table_args__ = (
        Index("ix_assignment_effective", "workspace_id", "user_id", "status", "starts_at", "ends_at"),
    )


class RoleBindingModel(Base):
    __tablename__ = "sys_role_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    position_id = Column(Integer, ForeignKey("sys_positions.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("sys_roles.id"), nullable=False, index=True)
    scope_type = Column(String(24), nullable=False, default="self")
    scope_org_unit_id = Column(Integer, ForeignKey("sys_departments.id"), nullable=True)
    custom_org_unit_ids = Column(JSON, nullable=False, default=list)
    starts_at = Column(DateTime, nullable=False, default=_now)
    ends_at = Column(DateTime, nullable=True)
    status = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now())

    __table_args__ = (
        Index("ix_role_binding_effective", "workspace_id", "status", "starts_at", "ends_at"),
    )


class AuthorizationExceptionModel(Base):
    __tablename__ = "sys_authorization_exceptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("sys_users.id", ondelete="CASCADE"), nullable=False, index=True)
    effect_type = Column(String(8), nullable=False)
    capability_codes = Column(JSON, nullable=False, default=list)
    scope_type = Column(String(24), nullable=False, default="self")
    scope_org_unit_ids = Column(JSON, nullable=False, default=list)
    reason = Column(Text, nullable=False)
    owner_id = Column(String(36), nullable=False)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    status = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(36), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_now, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now())

    __table_args__ = (
        Index("ix_auth_exception_effective", "workspace_id", "user_id", "status", "starts_at", "ends_at"),
    )


class AuthorizationAuditEventModel(Base):
    __tablename__ = "sys_authorization_audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    actor_id = Column(String(36), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(64), nullable=True)
    before_json = Column(JSON, nullable=False, default=dict)
    after_json = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=True)
    request_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now, server_default=func.now(), index=True)


class AuthorizationRevisionModel(Base):
    __tablename__ = "sys_authorization_revisions"

    workspace_id = Column(String(36), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now())
