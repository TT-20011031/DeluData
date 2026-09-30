"""Versioned access evidence generation, review and deterministic publishing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.db.read_only_executor import ReadOnlyExecutor
from app.core.llm.async_llm import get_async_llm
from app.models.auth.authorization import AssignmentModel, PositionModel
from app.models.auth.organization import DepartmentModel
from app.models.auth.organization_semantic import (
    OrganizationSemanticProfileModel,
    OrganizationSemanticProfileVersionModel,
    WorkspaceBusinessContextModel,
)
from app.models.config.permission_evidence import (
    SemanticAccessEvidenceAssetModel,
    SemanticAccessEvidenceRelationModel,
    SemanticAccessEvidenceSetModel,
    SemanticPermissionEvidenceRunModel,
)
from app.models.config.db_config import get_workspace_db_config_async
from app.models.config.semantic import (
    SemanticAccessBootstrapRunModel,
    SemanticAccessBootstrapTargetModel,
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticOwnershipMappingModel,
    SemanticPolicyBindingModel,
    SemanticPolicyVersionModel,
    SemanticTableModel,
)
from app.services.authorization_service import (
    build_effective_access_context,
    bump_authorization_revision,
    record_authorization_audit,
)
from app.services.organization_semantic_service import (
    business_context_snapshot,
    restrict_profile_collaborations,
    stable_fingerprint,
)
from app.services.semantic_policy_binding_service import (
    SemanticBindingError,
    _context_allows_scope,
    persist_target_policy_version_in_session,
)


BUSINESS_ROLES = {"owner", "producer", "required_consumer", "conditional_consumer", "none"}
# Row-level evidence generation is temporarily disabled. Keep the broader
# vocabulary for reviewed/manual relations, but constrain AI drafts to roles
# that can be compiled using table and field decisions alone.
GENERATED_BUSINESS_ROLES = {"owner", "producer", "required_consumer", "none"}
BASELINE_ACCESS = {"workspace_visible", "controlled"}
ACCESS_LEVELS = {"hidden", "visible", "partial"}
FIELD_DECISIONS = {"visible", "hidden"}
# Legacy v1 vocabulary retained only during the dual-read/write rollout.
ACCESS_CLASSES = {"workspace_public", "department_scoped", "restricted"}
REVIEWED = {"auto_safe", "accepted", "modified", "rejected"}
MANUALLY_APPROVED = {"accepted", "modified"}
ROW_SCOPE_TYPES = {
    "all", "self", "target_org", "target_org_tree", "primary_assignment",
    "all_assignments", "custom_org", "custom",
}

logger = logging.getLogger(__name__)
ROW_OWNERSHIP_VERSION = 2
ROW_OWNERSHIP_STRONG_ENGLISH = {
    "department", "dept", "organization", "org", "orgunit",
}
ROW_OWNERSHIP_EXPANDED_ENGLISH = {
    "division", "businessunit", "bu", "branch", "office", "center",
    "team", "group", "unit", "store", "outlet", "site", "section",
    "workshop",
}
ROW_OWNERSHIP_MODIFIER_ENGLISH = {
    "belong", "belongs", "belonging", "owner", "owning", "responsible",
    "accountable", "managed", "assigned",
}
ROW_OWNERSHIP_IDENTIFIER_ENGLISH = {
    "id", "code", "no", "key", "name", "identifier",
}
ROW_OWNERSHIP_STRONG_CHINESE = ("部门", "组织", "机构")
ROW_OWNERSHIP_EXPANDED_CHINESE = (
    "事业部", "分部", "分支", "办事处", "中心", "团队", "班组", "门店",
    "网点", "科室", "处室", "车间", "项目部",
)
ROW_OWNERSHIP_MODIFIER_CHINESE = (
    "所属", "归属", "责任", "负责", "管理", "管辖", "承办",
)
ROW_OWNERSHIP_IDENTIFIER_CHINESE = ("编号", "编码", "标识", "名称")
ROW_OWNERSHIP_HARD_EXCLUDE_ENGLISH = {
    "createdby", "creator", "updatedby", "updater", "owneruser", "userid",
    "user", "account", "employee", "person", "manager", "staff", "member",
    "operator", "assignee", "username", "customer", "client", "supplier",
    "vendor", "company", "legalentity", "region", "area", "country", "city",
    "warehouse", "product", "channel", "measurement", "measure", "uom",
    "location", "address",
}
ROW_OWNERSHIP_HARD_EXCLUDE_CHINESE = (
    "创建人", "更新人", "负责人", "责任人", "经办人", "管理员", "人员", "用户",
    "员工", "客户", "供应商",
    "公司", "法人", "区域", "国家", "城市", "仓库", "产品", "渠道", "计量",
    "地点", "地址",
)


def _quote_identifier(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


def _ownership_source_type(data_type: Any) -> str | None:
    value = str(data_type or "").lower()
    if any(item in value for item in ("int", "serial")):
        return "integer"
    if any(item in value for item in ("char", "text", "string", "enum", "set")):
        return "string"
    return None


def _ownership_english_tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    tokens = re.findall(r"[a-zA-Z0-9]+", text.casefold())
    result = set(tokens)
    result.update(
        "".join(tokens[index:index + 2])
        for index in range(max(len(tokens) - 1, 0))
    )
    return result


def _ownership_text_values(column: Any, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for name in names:
        value = getattr(column, name, None)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item or "") for item in value)
        else:
            values.append(str(value or ""))
    return values


def _ownership_candidate_metadata(column: Any) -> dict[str, Any] | None:
    """Score direct organization ownership metadata without inspecting row values."""
    if not _ownership_source_type(getattr(column, "data_type", None)):
        return None

    structural_values = _ownership_text_values(
        column, ("physical_name", "business_name", "synonyms"),
    )
    descriptive_values = _ownership_text_values(
        column, ("description", "physical_comment"),
    )
    all_values = structural_values + descriptive_values
    structural_text = " ".join(structural_values)
    descriptive_text = " ".join(descriptive_values)
    all_text = " ".join(all_values)
    structural_tokens = _ownership_english_tokens(structural_text)
    descriptive_tokens = _ownership_english_tokens(descriptive_text)
    all_tokens = structural_tokens | descriptive_tokens
    compact_tokens = {token.replace("_", "") for token in all_tokens}

    hard_excludes = sorted(
        token for token in ROW_OWNERSHIP_HARD_EXCLUDE_ENGLISH
        if token in compact_tokens
    )
    hard_excludes.extend(
        value for value in ROW_OWNERSHIP_HARD_EXCLUDE_CHINESE if value in all_text
    )
    if hard_excludes:
        return None

    strong_structural = sorted(
        ROW_OWNERSHIP_STRONG_ENGLISH & structural_tokens
    ) + [value for value in ROW_OWNERSHIP_STRONG_CHINESE if value in structural_text]
    expanded_structural = sorted(
        ROW_OWNERSHIP_EXPANDED_ENGLISH & structural_tokens
    ) + [value for value in ROW_OWNERSHIP_EXPANDED_CHINESE if value in structural_text]
    strong_descriptive = sorted(
        ROW_OWNERSHIP_STRONG_ENGLISH & descriptive_tokens
    ) + [value for value in ROW_OWNERSHIP_STRONG_CHINESE if value in descriptive_text]
    expanded_descriptive = sorted(
        ROW_OWNERSHIP_EXPANDED_ENGLISH & descriptive_tokens
    ) + [value for value in ROW_OWNERSHIP_EXPANDED_CHINESE if value in descriptive_text]
    modifiers = sorted(
        ROW_OWNERSHIP_MODIFIER_ENGLISH & all_tokens
    ) + [value for value in ROW_OWNERSHIP_MODIFIER_CHINESE if value in all_text]
    structural_modifiers = sorted(
        ROW_OWNERSHIP_MODIFIER_ENGLISH & structural_tokens
    ) + [value for value in ROW_OWNERSHIP_MODIFIER_CHINESE if value in structural_text]
    identifiers = sorted(
        ROW_OWNERSHIP_IDENTIFIER_ENGLISH & structural_tokens
    ) + [value for value in ROW_OWNERSHIP_IDENTIFIER_CHINESE if value in structural_text]

    score = 0
    signals: list[str] = []
    if strong_structural or strong_descriptive:
        score += 4
        signals.append("strong_unit:" + str((strong_structural or strong_descriptive)[0]))
    elif expanded_structural or expanded_descriptive:
        score += 3
        signals.append("expanded_unit:" + str((expanded_structural or expanded_descriptive)[0]))
    if modifiers:
        score += 2
        signals.append("ownership_modifier:" + str(modifiers[0]))
    if identifiers:
        score += 1
        signals.append("identifier:" + str(identifiers[0]))

    if strong_structural:
        tier = "strong"
    elif expanded_structural and (modifiers or identifiers):
        tier = "expanded"
    elif (
        (strong_descriptive or expanded_descriptive)
        and structural_modifiers
    ):
        # Description/comment evidence needs an independent structural signal.
        tier = "expanded"
    else:
        return None
    if score < 4:
        return None
    return {
        "candidate_tier": tier,
        "metadata_score": score,
        "matched_signals": signals,
    }


def _ownership_candidate_thresholds(policy: Any, tier: str) -> tuple[float, float]:
    if tier == "expanded":
        return (
            float(getattr(policy, "expanded_candidate_confidence", 0.95)),
            float(getattr(policy, "expanded_candidate_margin", 0.20)),
        )
    return float(policy.candidate_confidence), float(policy.candidate_margin)


def _ownership_value_confidence(policy: Any, tier: str) -> float:
    if tier == "expanded":
        return float(getattr(policy, "expanded_value_confidence", 0.95))
    return float(policy.value_confidence)


def _normalized_org_value(value: Any) -> str:
    return re.sub(r"[\s_\-（）()]+", "", str(value or "")).casefold()


def _typed_value_key(source_type: str, value: Any) -> tuple[str, str]:
    return source_type, str(value)


class PermissionEvidenceError(Exception):
    def __init__(
        self, message: str, *, code: str = "permission_evidence_error",
        status_code: int = 422, details: Any = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details


def _required_confidence(value: Any, label: str) -> float:
    """Require model confidence instead of silently turning omissions into 0%."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是 0 到 1 的数字")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise ValueError(f"{label}必须在 0 到 1 之间")
    return confidence


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def baseline_access_from_legacy(access_class: str | None) -> str:
    return "workspace_visible" if access_class == "workspace_public" else "controlled"


def legacy_access_class(
    baseline_access: str, requires_individual_review: bool,
) -> str:
    if baseline_access == "workspace_visible":
        return "workspace_public"
    return "restricted" if requires_individual_review else "department_scoped"


def access_level_from_legacy(
    business_role: str | None, row_scope: dict[str, Any] | None,
) -> str:
    if business_role == "none" or not business_role:
        return "hidden"
    return "visible" if (row_scope or {}).get("type", "all") == "all" else "partial"


def requires_manual_review(*, grant_kind: str, decision: str) -> bool:
    return (
        grant_kind == "public" and decision == "workspace_visible"
        or grant_kind == "table" and decision in {"visible", "partial"}
        or grant_kind == "field" and decision == "visible"
    )


def grant_is_compilable(review_status: str | None) -> bool:
    return review_status in MANUALLY_APPROVED


def sensitive_field_review_requires_reason(
    *, action: str, field_decision: str, has_reason: bool,
) -> bool:
    """Accepting the generated suggestion is explicit review, not an override."""
    return (
        field_decision == "visible"
        and action != "accepted"
        and not has_reason
    )


def generated_grant_requires_override_reason(
    *,
    expands: bool,
    sensitive_grant: bool,
    review_status: str,
    has_reason: bool,
) -> bool:
    """Generated risk suggestions can be accepted unchanged without a reason."""
    return (
        not has_reason
        and (
            expands
            or sensitive_grant and review_status != "accepted"
        )
    )


def filter_enabled_evidence_items(
    rows: list[Any],
    enabled_table_ids: set[int],
    enabled_column_ids: set[int],
) -> list[Any]:
    """Exclude disabled/stale evidence from review, progress, and publication."""
    return [
        row for row in rows
        if (
            int(row.table_id) in enabled_table_ids
            and (
                row.asset_type == "table"
                or int(row.asset_id) in enabled_column_ids
            )
        )
    ]


def resolve_matrix_decision(
    matched: list[tuple[str, str, dict[str, Any]]],
) -> tuple[str, str, str | None, str]:
    """Resolve one organization/table cell using deny-first policy semantics."""
    hidden = [
        item for item in matched if item[2].get("decision") == "hidden"
    ]
    visible = [
        item for item in matched
        if item[2].get("decision", "visible") == "visible"
    ]
    if hidden:
        source, source_target_id, _rule = hidden[-1]
        return "hidden", source, source_target_id, "explicit_hidden"
    if visible:
        unrestricted = [
            item for item in visible
            if (item[2].get("row_scope") or {}).get("type", "all") == "all"
        ]
        source, source_target_id, _rule = (
            unrestricted[-1] if unrestricted else visible[-1]
        )
        return (
            "visible" if unrestricted else "partial",
            source,
            source_target_id,
            "grant",
        )
    return "hidden", "default_deny", None, "default_deny"


class PermissionEvidenceService:
    def __init__(self) -> None:
        self.llm = get_async_llm()
        self.settings = get_settings()

    @staticmethod
    def _baseline_access(asset: SemanticAccessEvidenceAssetModel) -> str:
        value = getattr(asset, "baseline_access", None)
        return value if value in BASELINE_ACCESS else baseline_access_from_legacy(
            getattr(asset, "access_class", None),
        )

    @staticmethod
    def _requires_individual_review(asset: SemanticAccessEvidenceAssetModel) -> bool:
        return bool(
            getattr(asset, "requires_individual_review", False)
            or asset.is_sensitive
            or getattr(asset, "access_class", None) == "restricted"
        )

    @staticmethod
    def _business_role(relation: SemanticAccessEvidenceRelationModel) -> str:
        value = getattr(relation, "business_role", None)
        return value if value in BUSINESS_ROLES else relation.relation_role

    @classmethod
    def _access_level(cls, relation: SemanticAccessEvidenceRelationModel) -> str:
        value = getattr(relation, "access_level", None)
        return value if value in ACCESS_LEVELS else access_level_from_legacy(
            cls._business_role(relation), relation.row_scope_json,
        )

    @staticmethod
    def _field_decision(relation: SemanticAccessEvidenceRelationModel) -> str:
        value = getattr(relation, "field_decision", None)
        if value in FIELD_DECISIONS:
            return value
        return relation.access_decision if relation.access_decision in FIELD_DECISIONS else "hidden"

    @classmethod
    def _table_relation_is_confirmable(
        cls,
        mode: str,
        asset: SemanticAccessEvidenceAssetModel | None,
        relation: SemanticAccessEvidenceRelationModel,
    ) -> bool:
        if mode in {"table", "target"}:
            return True
        return bool(
            not (asset and cls._requires_individual_review(asset))
            and cls._access_level(relation) == "visible"
            and (relation.row_scope_json or {}).get("type", "all") == "all"
        )

    @classmethod
    def _field_relation_is_confirmable(
        cls,
        mode: str,
        parent_asset: SemanticAccessEvidenceAssetModel | None,
        *,
        sensitive: bool,
    ) -> bool:
        if mode in {"table", "target"}:
            return True
        return bool(
            not sensitive
            and not (
                parent_asset and cls._requires_individual_review(parent_asset)
            )
        )

    async def readiness(self, workspace_id: str, datasource_id: int) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            return await self._readiness_in_session(session, workspace_id, datasource_id)

    async def current(self, workspace_id: str, datasource_id: int) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            await self._datasource(session, workspace_id, datasource_id)
            row = (await session.execute(select(SemanticAccessEvidenceSetModel).where(
                SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                SemanticAccessEvidenceSetModel.datasource_id == datasource_id,
            ).order_by(SemanticAccessEvidenceSetModel.version.desc()))).scalars().first()
            if not row:
                return {"set": None, "readiness": await self._readiness_in_session(
                    session, workspace_id, datasource_id,
                )}
            return await self._set_payload(session, row, include_items=True)

    async def generate(
        self, workspace_id: str, datasource_id: int, actor_id: str,
    ) -> dict[str, Any]:
        """Idempotently enqueue first-permission evidence generation."""
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._datasource(session, workspace_id, datasource_id)
            # Serialize concurrent generate requests for one datasource so the
            # open-run lookup and insert remain idempotent.
            await session.execute(select(SemanticDatasourceModel.id).where(
                SemanticDatasourceModel.id == datasource_id,
                SemanticDatasourceModel.workspace_id == workspace_id,
            ).with_for_update())
            readiness = await self._readiness_in_session(
                session, workspace_id, datasource_id,
            )
            if not readiness["ready"]:
                raise PermissionEvidenceError(
                    "首次权限生成条件未满足",
                    code="readiness_blocked",
                    details={"blockers": readiness["blockers"]},
                )
            run = await self._enqueue_in_session(
                session, workspace_id, datasource_id, actor_id, force=False,
            )
            return self._run_payload(run)

    async def get_run(self, workspace_id: str, run_id: int) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            row = await session.get(SemanticPermissionEvidenceRunModel, run_id)
            if (
                not row
                or row.workspace_id != workspace_id
                or row.job_kind != "access_evidence"
            ):
                raise PermissionEvidenceError(
                    "权限生成任务不存在",
                    code="evidence_run_not_found",
                    status_code=404,
                )
            return self._run_payload(row)

    async def access_matrix(
        self, workspace_id: str, datasource_id: int, actor_id: str,
    ) -> dict[str, Any]:
        """Return enabled-table organization permissions without user-level overlays."""
        db = get_async_db_manager()
        async with db.get_session() as session:
            await self._datasource(session, workspace_id, datasource_id)
            context = await build_effective_access_context(
                session, workspace_id, actor_id,
            )
            departments = list((await session.execute(select(DepartmentModel).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.status == True,  # noqa: E712
            ).order_by(DepartmentModel.order_num, DepartmentModel.id))).scalars())
            departments = [
                row for row in departments
                if _context_allows_scope(
                    context, "semantic_access:view", [int(row.id)],
                )
            ]
            tables = list((await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource_id,
                SemanticTableModel.status == "confirmed",
                SemanticTableModel.sync_state == "current",
                SemanticTableModel.is_queryable == True,  # noqa: E712
            ).order_by(SemanticTableModel.id))).scalars())
            bindings = list((await session.execute(select(
                SemanticPolicyBindingModel,
            ).where(
                SemanticPolicyBindingModel.workspace_id == workspace_id,
                SemanticPolicyBindingModel.datasource_id == datasource_id,
                SemanticPolicyBindingModel.status == True,  # noqa: E712
                SemanticPolicyBindingModel.target_type.in_(("baseline", "org_unit")),
                SemanticPolicyBindingModel.active_version_id.is_not(None),
            ))).scalars())
            version_ids = [
                int(row.active_version_id) for row in bindings if row.active_version_id
            ]
            versions = {
                int(row.id): row for row in (await session.execute(select(
                    SemanticPolicyVersionModel,
                ).where(
                    SemanticPolicyVersionModel.id.in_(version_ids or [-1]),
                ))).scalars()
            }
            rules_by_binding: dict[int, dict[int, dict[str, Any]]] = {}
            for binding in bindings:
                version = versions.get(int(binding.active_version_id or 0))
                rules_by_binding[int(binding.id)] = {
                    int(item["table_id"]): item
                    for item in (version.definition_json if version else {}).get("tables") or []
                    if item.get("table_id")
                }

            department_by_id = {int(row.id): row for row in departments}
            all_parent_by_id = dict((await session.execute(select(
                DepartmentModel.id, DepartmentModel.parent_id,
            ).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.status == True,  # noqa: E712
            ))).all())

            def ancestor_ids(org_id: int) -> list[int]:
                chain = [org_id]
                seen = {org_id}
                current = all_parent_by_id.get(org_id)
                while current is not None and int(current) not in seen:
                    current_id = int(current)
                    seen.add(current_id)
                    chain.append(current_id)
                    current = all_parent_by_id.get(current_id)
                return list(reversed(chain))

            baseline_bindings = [
                row for row in bindings if row.target_type == "baseline"
            ]
            org_bindings = {
                int(row.target_id): row
                for row in bindings
                if row.target_type == "org_unit"
            }

            evidence_set = (await session.execute(select(
                SemanticAccessEvidenceSetModel,
            ).where(
                SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                SemanticAccessEvidenceSetModel.datasource_id == datasource_id,
                SemanticAccessEvidenceSetModel.status.in_(("review_ready", "publish_failed")),
            ).order_by(SemanticAccessEvidenceSetModel.version.desc()))).scalars().first()
            pending_assets: dict[int, SemanticAccessEvidenceAssetModel] = {}
            pending_relations: dict[tuple[int, int], SemanticAccessEvidenceRelationModel] = {}
            if evidence_set:
                pending_assets = {
                    int(row.table_id): row for row in (await session.execute(select(
                        SemanticAccessEvidenceAssetModel,
                    ).where(
                        SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
                        SemanticAccessEvidenceAssetModel.asset_type == "table",
                    ))).scalars()
                }
                pending_relations = {
                    (int(row.org_unit_id), int(row.table_id)): row
                    for row in (await session.execute(select(
                        SemanticAccessEvidenceRelationModel,
                    ).where(
                        SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
                        SemanticAccessEvidenceRelationModel.asset_type == "table",
                    ))).scalars()
                }

            draft_targets: list[Any] = []
            if evidence_set:
                draft_run = (await session.execute(select(
                    SemanticAccessBootstrapRunModel,
                ).where(
                    SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                    SemanticAccessBootstrapRunModel.evidence_set_id == evidence_set.id,
                    SemanticAccessBootstrapRunModel.status == "review_ready",
                ).order_by(
                    SemanticAccessBootstrapRunModel.created_at.desc(),
                ))).scalars().first()
                if draft_run:
                    draft_targets = list((await session.execute(select(
                        SemanticAccessBootstrapTargetModel,
                    ).where(
                        SemanticAccessBootstrapTargetModel.run_id == draft_run.id,
                        SemanticAccessBootstrapTargetModel.included == True,  # noqa: E712
                    ))).scalars())
            draft_baselines = [
                row for row in draft_targets if row.target_type == "baseline"
            ]
            draft_orgs = {
                int(row.target_id): row
                for row in draft_targets
                if row.target_type == "org_unit"
            }

            cells = []
            for department in departments:
                chain = ancestor_ids(int(department.id))
                top_org_id = chain[0] if chain else int(department.id)
                applicable: list[tuple[str, str, dict[int, dict[str, Any]]]] = []
                for binding in baseline_bindings:
                    applicable.append((
                        "baseline", binding.target_id,
                        rules_by_binding.get(int(binding.id), {}),
                    ))
                for org_id in chain:
                    binding = org_bindings.get(org_id)
                    if not binding:
                        continue
                    applicable.append((
                        "direct" if org_id == int(department.id) else "inherited",
                        str(org_id),
                        rules_by_binding.get(int(binding.id), {}),
                    ))

                for table in tables:
                    org_matched = [
                        (source, source_target_id, rules[int(table.id)])
                        for source, source_target_id, rules in reversed(applicable)
                        if source != "baseline" and int(table.id) in rules
                    ]
                    baseline_matched = [
                        (source, source_target_id, rules[int(table.id)])
                        for source, source_target_id, rules in applicable
                        if source == "baseline" and int(table.id) in rules
                    ]
                    # Department permissions are sparse overrides. The nearest
                    # configured department wins; baseline is consulted only
                    # when no department in the chain configures this table.
                    matched = org_matched[:1] or baseline_matched
                    decision, source, source_target_id, reason = (
                        resolve_matrix_decision(matched)
                    )

                    pending_review = False
                    confidence = None
                    pending_decision = None
                    pending_source = None
                    draft_applicable: list[tuple[str, str, dict[str, Any]]] = []
                    for target in draft_baselines:
                        rule = next((
                            item
                            for item in (target.definition_json or {}).get("tables") or []
                            if int(item.get("table_id") or 0) == int(table.id)
                        ), None)
                        if rule:
                            draft_applicable.append(("baseline", target.target_id, rule))
                    for org_id in chain:
                        target = draft_orgs.get(org_id)
                        if not target:
                            continue
                        rule = next((
                            item
                            for item in (target.definition_json or {}).get("tables") or []
                            if int(item.get("table_id") or 0) == int(table.id)
                        ), None)
                        if rule:
                            draft_applicable.append((
                                "direct" if org_id == int(department.id) else "inherited",
                                str(org_id),
                                rule,
                            ))
                    draft_org_matched = [
                        item for item in reversed(draft_applicable)
                        if item[0] != "baseline"
                    ]
                    draft_baseline_matched = [
                        item for item in draft_applicable if item[0] == "baseline"
                    ]
                    draft_applicable = draft_org_matched[:1] or draft_baseline_matched
                    if draft_applicable:
                        (
                            pending_decision,
                            pending_source,
                            pending_source_target_id,
                            _pending_reason,
                        ) = resolve_matrix_decision(draft_applicable)
                        pending_review = True
                        source_target = next((
                            row for row in draft_targets
                            if row.target_type == (
                                "baseline" if pending_source == "baseline" else "org_unit"
                            )
                            and row.target_id == pending_source_target_id
                        ), None)
                        candidate = next((
                            item
                            for item in (source_target.candidates_json if source_target else []) or []
                            if int(item.get("table_id") or 0) == int(table.id)
                        ), None)
                        confidence = (
                            candidate.get("confidence") if candidate else None
                        )
                    else:
                        if (
                            pending_relations.get((top_org_id, int(table.id)))
                            and self._access_level(
                                pending_relations[(top_org_id, int(table.id))]
                            ) in {"visible", "partial"}
                            and pending_relations[
                                (top_org_id, int(table.id))
                            ].review_status not in MANUALLY_APPROVED
                        ):
                            pending_relation = pending_relations[
                                (top_org_id, int(table.id))
                            ]
                            pending_review = True
                            confidence = pending_relation.confidence
                            pending_decision = self._access_level(pending_relation)
                        else:
                            pending_asset = pending_assets.get(int(table.id))
                            if (
                                pending_asset
                                and self._baseline_access(pending_asset) == "workspace_visible"
                                and pending_asset.review_status not in MANUALLY_APPROVED
                            ):
                                pending_review = True
                                pending_decision = "visible"
                                pending_source = "baseline"

                    cells.append({
                        "department_id": int(department.id),
                        "table_id": int(table.id),
                        "decision": decision,
                        "reason": reason,
                        "source": source,
                        "source_target_id": source_target_id,
                        "pending_review": pending_review,
                        "pending_decision": pending_decision,
                        "pending_source": pending_source,
                        "confidence": confidence,
                    })

            return {
                "datasource_id": datasource_id,
                "evidence_set_id": evidence_set.id if evidence_set else None,
                "tables": [{
                    "id": int(row.id),
                    "business_name": row.business_name,
                    "physical_name": row.physical_name,
                } for row in tables],
                "departments": [{
                    "id": int(row.id),
                    "name": row.name,
                    "parent_id": (
                        int(row.parent_id) if row.parent_id is not None else None
                    ),
                    "level": len(ancestor_ids(int(row.id))) - 1,
                    "path": " / ".join(
                        department_by_id[item].name
                        for item in ancestor_ids(int(row.id))
                        if item in department_by_id
                    ) or row.name,
                } for row in departments],
                "cells": cells,
            }

    async def get_set(self, workspace_id: str, set_id: int) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            row = await self._set(session, workspace_id, set_id)
            return await self._set_payload(session, row, include_items=True)

    async def compile_preview(self, workspace_id: str, set_id: int) -> dict[str, Any]:
        """Read-only deterministic preview; never persists a policy version."""
        db = get_async_db_manager()
        async with db.get_session() as session:
            evidence_set = await self._set(session, workspace_id, set_id)
            blockers = await self._validation_blockers(session, evidence_set)
            definitions = await self._compile_definitions(session, evidence_set)
            representatives = await self._representative_accounts(session, workspace_id)
            baseline = next(
                (item for item in definitions if item["target_type"] == "baseline"), None,
            )
            simulations = []
            for representative in representatives:
                department = next((
                    item for item in definitions
                    if item["target_type"] == "org_unit"
                    and item["target_id"] == str(representative["org_unit_id"])
                ), None)
                table_ids = sorted({
                    int(rule["table_id"])
                    for target in (baseline, department) if target
                    for rule in target["definition"].get("tables") or []
                })
                simulations.append({
                    **representative,
                    "visible_table_ids": table_ids,
                    "visible_table_count": len(table_ids),
                })
            return {
                "evidence_set_id": evidence_set.id,
                "evidence_version": evidence_set.version,
                "fingerprint": stable_fingerprint(definitions),
                "blockers": blockers,
                "targets": definitions,
                "representative_accounts": representatives,
                "simulations": simulations,
            }

    async def history(self, workspace_id: str, datasource_id: int, limit: int) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            rows = list((await session.execute(select(SemanticAccessEvidenceSetModel).where(
                SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                SemanticAccessEvidenceSetModel.datasource_id == datasource_id,
            ).order_by(SemanticAccessEvidenceSetModel.version.desc()).limit(limit))).scalars())
            return {"items": [self._set_summary(row) for row in rows]}

    async def retry(self, workspace_id: str, set_id: int, actor_id: str) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            row = await self._set(session, workspace_id, set_id)
            run = await self._enqueue_in_session(
                session, workspace_id, row.datasource_id, actor_id, force=True,
            )
            return {"run_id": run.id, "status": run.status}

    async def confirm_target(
        self,
        workspace_id: str,
        set_id: int,
        actor_id: str,
        target_type: str,
        target_id: str,
        mode: str,
        expected_revision: int,
        table_id: int | None = None,
    ) -> dict[str, Any]:
        """Confirm one table bundle, ordinary grants, or every grant for a target."""
        if target_type not in {"baseline", "org_unit"}:
            raise PermissionEvidenceError("审核目标无效", code="invalid_target")
        if mode not in {"table", "ordinary_target", "target"}:
            raise PermissionEvidenceError("审核模式无效", code="invalid_review_mode")
        if mode == "table" and not table_id:
            raise PermissionEvidenceError("按表确认必须选择表", code="table_required")
        if target_type == "baseline" and target_id != "*":
            raise PermissionEvidenceError("全员基线目标无效", code="invalid_target")
        try:
            org_unit_id = None if target_type == "baseline" else int(target_id)
        except (TypeError, ValueError) as exc:
            raise PermissionEvidenceError("部门目标无效", code="invalid_target") from exc

        db = get_async_db_manager()
        async with db.session_scope() as session:
            evidence_set = await self._set_for_review(
                session, workspace_id, set_id, expected_revision,
            )
            enabled_table_ids, enabled_column_ids = await self._enabled_asset_ids(
                session, workspace_id, evidence_set.datasource_id,
            )
            if org_unit_id is not None:
                exists = (await session.execute(select(DepartmentModel.id).where(
                    DepartmentModel.workspace_id == workspace_id,
                    DepartmentModel.id == org_unit_id,
                    DepartmentModel.status == True,  # noqa: E712
                ))).scalar_one_or_none()
                if exists is None:
                    raise PermissionEvidenceError(
                        "部门不存在或已停用", code="target_not_found", status_code=404,
                    )

            assets = list((await session.execute(
                select(SemanticAccessEvidenceAssetModel).where(
                    SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                    SemanticAccessEvidenceAssetModel.asset_type == "table",
                    SemanticAccessEvidenceAssetModel.table_id.in_(
                        enabled_table_ids or {-1},
                    ),
                )
            )).scalars())
            asset_by_table = {int(row.table_id): row for row in assets}
            relations = list((await session.execute(
                select(SemanticAccessEvidenceRelationModel).where(
                    SemanticAccessEvidenceRelationModel.evidence_set_id == set_id,
                    *(
                        [SemanticAccessEvidenceRelationModel.org_unit_id == org_unit_id]
                        if org_unit_id is not None else []
                    ),
                    SemanticAccessEvidenceRelationModel.table_id.in_(
                        enabled_table_ids or {-1},
                    ),
                )
            )).scalars())
            relations = filter_enabled_evidence_items(
                relations, enabled_table_ids, enabled_column_ids,
            )
            column_ids = [
                int(row.asset_id) for row in relations if row.asset_type == "column"
            ]
            sensitive_columns = set((await session.execute(
                select(SemanticColumnModel.id).where(
                    SemanticColumnModel.workspace_id == workspace_id,
                    SemanticColumnModel.id.in_(column_ids or [-1]),
                    SemanticColumnModel.is_sensitive == True,  # noqa: E712
                )
            )).scalars())

            reviewed_assets = 0
            reviewed_relations = 0
            now = datetime.now()

            if target_type == "baseline":
                for asset in assets:
                    if table_id and int(asset.table_id) != int(table_id):
                        continue
                    if self._baseline_access(asset) != "workspace_visible":
                        continue
                    if asset.review_status in MANUALLY_APPROVED:
                        continue
                    if mode == "ordinary_target" and self._requires_individual_review(asset):
                        continue
                    asset.review_status = "accepted"
                    asset.reviewed_by, asset.reviewed_at = actor_id, now
                    reviewed_assets += 1
            else:
                selected_table_ids: set[int] = set()
                for relation in relations:
                    if relation.asset_type != "table":
                        continue
                    if table_id and int(relation.table_id) != int(table_id):
                        continue
                    if self._access_level(relation) not in {"visible", "partial"}:
                        continue
                    if relation.review_status in MANUALLY_APPROVED:
                        selected_table_ids.add(int(relation.table_id))
                        continue
                    asset = asset_by_table.get(int(relation.table_id))
                    if not self._table_relation_is_confirmable(
                        mode, asset, relation,
                    ):
                        continue
                    relation.review_status = "accepted"
                    relation.reviewed_by, relation.reviewed_at = actor_id, now
                    reviewed_relations += 1
                    selected_table_ids.add(int(relation.table_id))

                for relation in relations:
                    if relation.asset_type != "column":
                        continue
                    if int(relation.table_id) not in selected_table_ids:
                        continue
                    if self._field_decision(relation) != "visible":
                        continue
                    if relation.review_status in MANUALLY_APPROVED:
                        continue
                    parent_asset = asset_by_table.get(int(relation.table_id))
                    if not self._field_relation_is_confirmable(
                        mode,
                        parent_asset,
                        sensitive=int(relation.asset_id) in sensitive_columns,
                    ):
                        continue
                    relation.review_status = "accepted"
                    relation.reviewed_by, relation.reviewed_at = actor_id, now
                    reviewed_relations += 1

            if not reviewed_assets and not reviewed_relations:
                raise PermissionEvidenceError(
                    "当前范围没有可确认的授权配置",
                    code="nothing_to_confirm",
                    status_code=409,
                )
            evidence_set.revision += 1
            await self._refresh_progress(session, evidence_set)
            payload = await self._set_payload(session, evidence_set, include_items=True)
            payload["confirmation"] = {
                "assets": reviewed_assets,
                "relations": reviewed_relations,
            }
            return payload

    async def enqueue_if_ready_in_session(
        self, session, workspace_id: str, datasource_id: int, actor_id: str,
    ) -> bool:
        if not self.settings.semantic_access_evidence.auto_regenerate_on_semantic_change:
            logger.info(
                "semantic access evidence auto-regeneration disabled: workspace=%s datasource=%s",
                workspace_id,
                datasource_id,
            )
            return False
        current_sets = list((await session.execute(select(SemanticAccessEvidenceSetModel).where(
            SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
            SemanticAccessEvidenceSetModel.datasource_id == datasource_id,
            SemanticAccessEvidenceSetModel.status == "published",
        ))).scalars())
        for evidence_set in current_sets:
            evidence_set.status = "stale"
        readiness = await self._readiness_in_session(session, workspace_id, datasource_id)
        if not readiness["ready"]:
            return False
        await self._enqueue_in_session(
            session, workspace_id, datasource_id, actor_id, force=False,
        )
        return True

    async def patch_relation(
        self, workspace_id: str, set_id: int, relation_id: int, actor_id: str,
        payload: dict[str, Any], expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            evidence_set = await self._set_for_review(session, workspace_id, set_id, expected_revision)
            relation = await session.get(SemanticAccessEvidenceRelationModel, relation_id)
            if not relation or relation.evidence_set_id != set_id or relation.workspace_id != workspace_id:
                raise PermissionEvidenceError("访问关系不存在", code="relation_not_found", status_code=404)
            old_level = self._access_level(relation)
            old_field_decision = self._field_decision(relation)
            business_role = self._business_role(relation)
            legacy_role = payload.get("relation_role")
            if legacy_role is not None:
                if legacy_role not in BUSINESS_ROLES:
                    raise PermissionEvidenceError("无效的业务角色", code="invalid_business_role")
                business_role = legacy_role
            row_scope = payload.get("row_scope", relation.row_scope_json or {"type": "all"})
            self._validate_row_scope(row_scope)
            access_level = payload.get("access_level")
            field_decision = payload.get("field_decision", payload.get("access_decision"))
            if relation.asset_type == "table":
                if access_level is None:
                    access_level = (
                        access_level_from_legacy(business_role, row_scope)
                        if legacy_role is not None else old_level
                    )
                if access_level not in ACCESS_LEVELS:
                    raise PermissionEvidenceError("无效的表访问级别", code="invalid_access_level")
                if access_level == "hidden":
                    row_scope = {"type": "all"}
                elif access_level == "visible" and row_scope.get("type", "all") != "all":
                    raise PermissionEvidenceError(
                        "可见必须使用全部行范围", code="visible_scope_must_be_all",
                    )
                elif access_level == "partial" and row_scope.get("type", "all") == "all":
                    raise PermissionEvidenceError(
                        "部分可见必须选择受限行范围", code="partial_scope_required",
                    )
                field_decision = old_field_decision
            else:
                access_level = None
                row_scope = {"type": "all"}
                field_decision = field_decision or old_field_decision
                if field_decision not in FIELD_DECISIONS:
                    raise PermissionEvidenceError("无效的字段决定", code="invalid_field_decision")
            review_status = payload.get("review_status", "modified")
            if review_status not in {"accepted", "modified", "rejected"}:
                raise PermissionEvidenceError("审核状态无效", code="invalid_review_status")
            if review_status == "rejected":
                if relation.asset_type == "table":
                    access_level, row_scope = "hidden", {"type": "all"}
                else:
                    field_decision = "hidden"
            override_reason = str(payload.get("override_reason") or "").strip()
            expands = (
                old_level == "hidden" and access_level in {"visible", "partial"}
                or old_level == "partial" and access_level == "visible"
                or (
                    old_level == "partial"
                    and access_level == "partial"
                    and self._scope_rank(row_scope) > self._scope_rank(relation.row_scope_json or {})
                )
                or old_field_decision == "hidden" and field_decision == "visible"
            )
            table_asset = (await session.execute(select(SemanticAccessEvidenceAssetModel).where(
                SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                SemanticAccessEvidenceAssetModel.asset_type == "table",
                SemanticAccessEvidenceAssetModel.table_id == relation.table_id,
            ))).scalar_one_or_none()
            column_sensitive = False
            if relation.asset_type == "column":
                column_sensitive = bool((await session.execute(select(
                    SemanticColumnModel.is_sensitive,
                ).where(
                    SemanticColumnModel.workspace_id == workspace_id,
                    SemanticColumnModel.id == relation.asset_id,
                ))).scalar_one_or_none())
            sensitive_grant = (
                relation.asset_type == "table"
                and access_level in {"visible", "partial"}
                and bool(table_asset and table_asset.is_sensitive)
                or relation.asset_type == "column"
                and field_decision == "visible"
                and column_sensitive
            )
            if generated_grant_requires_override_reason(
                expands=expands,
                sensitive_grant=sensitive_grant,
                review_status=review_status,
                has_reason=bool(override_reason),
            ):
                raise PermissionEvidenceError(
                    "扩大访问、放开敏感字段或放宽行范围必须填写理由",
                    code="override_reason_required",
                )
            relation.business_role = business_role
            relation.relation_role = business_role
            relation.access_level = access_level
            relation.field_decision = field_decision
            relation.access_decision = field_decision if relation.asset_type == "column" else "inherit"
            relation.row_scope_json = row_scope
            relation.reason = str(payload.get("reason", relation.reason) or "")
            relation.review_status = review_status
            relation.override_reason = override_reason or relation.override_reason
            relation.reviewed_by = actor_id
            relation.reviewed_at = datetime.now()
            if relation.asset_type == "column" and field_decision == "visible":
                legacy_field_asset = (await session.execute(select(
                    SemanticAccessEvidenceAssetModel,
                ).where(
                    SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                    SemanticAccessEvidenceAssetModel.asset_type == "column",
                    SemanticAccessEvidenceAssetModel.asset_id == relation.asset_id,
                ))).scalar_one_or_none()
                if legacy_field_asset and legacy_field_asset.review_status == "rejected":
                    legacy_field_asset.review_status = "modified"
                    legacy_field_asset.override_reason = override_reason or legacy_field_asset.override_reason
                    legacy_field_asset.reviewed_by = actor_id
                    legacy_field_asset.reviewed_at = datetime.now()
            evidence_set.revision += 1
            await self._refresh_progress(session, evidence_set)
            return await self._set_payload(session, evidence_set, include_items=True)

    async def review_decisions(
        self, workspace_id: str, set_id: int, actor_id: str,
        decisions: list[dict[str, Any]], expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            evidence_set = await self._set_for_review(session, workspace_id, set_id, expected_revision)
            asset_ids = [int(item["asset_id"]) for item in decisions if item.get("kind") == "asset"]
            relation_ids = [int(item["relation_id"]) for item in decisions if item.get("kind") == "relation"]
            assets = {
                int(row.id): row for row in (await session.execute(
                    select(SemanticAccessEvidenceAssetModel).where(
                        SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                        SemanticAccessEvidenceAssetModel.id.in_(asset_ids or [-1]),
                    )
                )).scalars()
            }
            relations = {
                int(row.id): row for row in (await session.execute(
                    select(SemanticAccessEvidenceRelationModel).where(
                        SemanticAccessEvidenceRelationModel.evidence_set_id == set_id,
                        SemanticAccessEvidenceRelationModel.id.in_(relation_ids or [-1]),
                    )
                )).scalars()
            }
            for item in decisions:
                action = item.get("action")
                if action not in {"accepted", "modified", "rejected"}:
                    raise PermissionEvidenceError("审核动作无效", code="invalid_review_action")
                reason = str(item.get("reason") or "").strip()
                if item.get("kind") == "asset":
                    row = assets.get(int(item["asset_id"]))
                    if not row:
                        raise PermissionEvidenceError("资产不存在", code="asset_not_found", status_code=404)
                    if row.asset_type != "table":
                        # Compatibility no-op during the dual-protocol window:
                        # V2 does not create or validate column assets, while an
                        # older frontend may still submit their historic review.
                        row.review_status = action
                        row.override_reason = reason or row.override_reason
                        row.reviewed_by, row.reviewed_at = actor_id, datetime.now()
                        continue
                    baseline_access = item.get("baseline_access")
                    requires_individual_review = item.get("requires_individual_review")
                    legacy_class = item.get("access_class")
                    if baseline_access is None and legacy_class is not None:
                        baseline_access = baseline_access_from_legacy(legacy_class)
                    if requires_individual_review is None and legacy_class is not None:
                        requires_individual_review = legacy_class == "restricted"
                    baseline_access = baseline_access or self._baseline_access(row)
                    if baseline_access not in BASELINE_ACCESS:
                        raise PermissionEvidenceError("基线访问无效", code="invalid_baseline_access")
                    requires_individual_review = (
                        self._requires_individual_review(row)
                        if requires_individual_review is None
                        else bool(requires_individual_review)
                    )
                    if row.is_sensitive:
                        requires_individual_review = True
                    if len(decisions) > 1 and (
                        baseline_access == "workspace_visible"
                        or requires_individual_review
                    ):
                        raise PermissionEvidenceError(
                            "全员访问和高风险资产不能批量审核",
                            code="bulk_review_forbidden",
                        )
                    expands = (
                        self._baseline_access(row) != "workspace_visible"
                        and baseline_access == "workspace_visible"
                        or self._requires_individual_review(row)
                        and not requires_individual_review
                    )
                    if expands and not reason:
                        raise PermissionEvidenceError(
                            "开放全员访问或取消高风险标记必须填写理由",
                            code="override_reason_required",
                        )
                    if row.is_sensitive and baseline_access == "workspace_visible":
                        raise PermissionEvidenceError(
                            "敏感表不能设为全员可问",
                            code="sensitive_table_cannot_be_public",
                        )
                    if action == "rejected":
                        baseline_access = "controlled"
                    row.baseline_access = baseline_access
                    row.requires_individual_review = requires_individual_review
                    row.access_class = legacy_access_class(
                        baseline_access, requires_individual_review,
                    )
                    row.review_status = action
                    row.override_reason = reason or row.override_reason
                    row.reviewed_by, row.reviewed_at = actor_id, datetime.now()
                else:
                    row = relations.get(int(item["relation_id"]))
                    if not row:
                        raise PermissionEvidenceError("关系不存在", code="relation_not_found", status_code=404)
                    table_asset = (await session.execute(select(SemanticAccessEvidenceAssetModel).where(
                        SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                        SemanticAccessEvidenceAssetModel.asset_type == "table",
                        SemanticAccessEvidenceAssetModel.table_id == row.table_id,
                    ))).scalar_one_or_none()
                    level = self._access_level(row)
                    field_decision = self._field_decision(row)
                    column_sensitive = False
                    if row.asset_type == "column":
                        column_sensitive = bool((await session.execute(select(
                            SemanticColumnModel.is_sensitive,
                        ).where(
                            SemanticColumnModel.workspace_id == workspace_id,
                            SemanticColumnModel.id == row.asset_id,
                        ))).scalar_one_or_none())
                    # High-risk table grants and field grants remain individually reviewable.
                    if len(decisions) > 1 and (
                        row.asset_type != "table"
                        or level != "visible"
                        or (row.row_scope_json or {}).get("type", "all") != "all"
                        or bool(table_asset and self._requires_individual_review(table_asset))
                    ):
                        raise PermissionEvidenceError(
                            "只有普通表的全部行授权可以批量审核", code="bulk_review_forbidden",
                        )
                    if (
                        row.asset_type == "column"
                        and column_sensitive
                        and sensitive_field_review_requires_reason(
                            action=action,
                            field_decision=field_decision,
                            has_reason=bool(reason or row.override_reason),
                        )
                    ):
                        raise PermissionEvidenceError(
                            "放开敏感字段必须填写理由",
                            code="override_reason_required",
                        )
                    if action == "rejected":
                        if row.asset_type == "table":
                            row.access_level = "hidden"
                            row.row_scope_json = {"type": "all"}
                        else:
                            row.field_decision = "hidden"
                            row.access_decision = "hidden"
                    row.business_role = self._business_role(row)
                    row.relation_role = row.business_role
                    if row.asset_type == "table":
                        row.access_level = row.access_level or level
                        row.access_decision = "inherit"
                    else:
                        row.field_decision = row.field_decision or field_decision
                        row.access_decision = row.field_decision
                    row.review_status = action
                    row.override_reason = reason or row.override_reason
                    row.reviewed_by, row.reviewed_at = actor_id, datetime.now()
                    if row.asset_type == "column" and row.field_decision == "visible":
                        legacy_field_asset = (await session.execute(select(
                            SemanticAccessEvidenceAssetModel,
                        ).where(
                            SemanticAccessEvidenceAssetModel.evidence_set_id == set_id,
                            SemanticAccessEvidenceAssetModel.asset_type == "column",
                            SemanticAccessEvidenceAssetModel.asset_id == row.asset_id,
                        ))).scalar_one_or_none()
                        if legacy_field_asset and legacy_field_asset.review_status == "rejected":
                            legacy_field_asset.review_status = "modified"
                            legacy_field_asset.override_reason = reason or legacy_field_asset.override_reason
                            legacy_field_asset.reviewed_by = actor_id
                            legacy_field_asset.reviewed_at = datetime.now()
            evidence_set.revision += 1
            await self._refresh_progress(session, evidence_set)
            return await self._set_payload(session, evidence_set, include_items=True)

    async def confirm(
        self, workspace_id: str, set_id: int, actor_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        # Compatibility path: once an evidence set has been projected into the
        # direct editor, publishing must use the edited draft rather than
        # recompiling the original evidence and losing administrator changes.
        async with db.get_session() as session:
            evidence_set = await self._set(session, workspace_id, set_id)
            if int(evidence_set.revision or 0) != int(expected_revision):
                raise PermissionEvidenceError(
                    "访问依据集已被其他管理员修改，请刷新后重试",
                    code="evidence_revision_conflict",
                    status_code=409,
                    details={"current_revision": int(evidence_set.revision or 0)},
                )
            draft_run = (await session.execute(select(
                SemanticAccessBootstrapRunModel,
            ).where(
                SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                SemanticAccessBootstrapRunModel.evidence_set_id == set_id,
                SemanticAccessBootstrapRunModel.status == "review_ready",
            ).order_by(SemanticAccessBootstrapRunModel.created_at.desc()))).scalars().first()
            draft_run_id = int(draft_run.id) if draft_run else None
            draft_revision = int(draft_run.revision or 0) if draft_run else None
        if draft_run_id is not None and draft_revision is not None:
            from app.services.semantic_access_bootstrap_service import (
                get_semantic_access_bootstrap_service,
            )
            await get_semantic_access_bootstrap_service().apply_run(
                workspace_id,
                draft_run_id,
                actor_id,
                draft_revision,
                confirm_warnings=True,
            )
            return await self.get_set(workspace_id, set_id)
        try:
            async with db.session_scope() as session:
                evidence_set = await self._set_for_review(
                    session, workspace_id, set_id, expected_revision,
                )
                readiness = await self._readiness_in_session(
                    session, workspace_id, evidence_set.datasource_id,
                )
                if not readiness["ready"]:
                    raise PermissionEvidenceError(
                        "上游事实已变化，请重新生成依据集", code="upstream_not_ready",
                        status_code=409, details=readiness["blockers"],
                    )
                blockers = await self._validation_blockers(session, evidence_set)
                if blockers:
                    evidence_set.blockers_json = blockers
                    raise PermissionEvidenceError(
                        "依据集仍有阻断项", code="evidence_validation_blocked",
                        details=blockers,
                    )
                definitions = await self._compile_definitions(session, evidence_set)
                compile_fingerprint = stable_fingerprint(definitions)
                results = []
                current_target_keys = {
                    (target["target_type"], target["target_id"]) for target in definitions
                }
                obsolete_bindings = list((await session.execute(
                    select(SemanticPolicyBindingModel).where(
                        SemanticPolicyBindingModel.workspace_id == workspace_id,
                        SemanticPolicyBindingModel.datasource_id == evidence_set.datasource_id,
                        SemanticPolicyBindingModel.target_type.in_(("baseline", "org_unit")),
                        SemanticPolicyBindingModel.status == True,  # noqa: E712
                    ).with_for_update()
                )).scalars())
                for obsolete in obsolete_bindings:
                    if (obsolete.target_type, obsolete.target_id) not in current_target_keys:
                        obsolete.status = False
                for target in definitions:
                    binding = (await session.execute(select(SemanticPolicyBindingModel).where(
                        SemanticPolicyBindingModel.workspace_id == workspace_id,
                        SemanticPolicyBindingModel.datasource_id == evidence_set.datasource_id,
                        SemanticPolicyBindingModel.target_type == target["target_type"],
                        SemanticPolicyBindingModel.target_id == target["target_id"],
                    ).with_for_update())).scalar_one_or_none()
                    if binding is None:
                        binding = SemanticPolicyBindingModel(
                            workspace_id=workspace_id,
                            datasource_id=evidence_set.datasource_id,
                            target_type=target["target_type"],
                            target_id=target["target_id"],
                            include_descendants=target["target_type"] == "org_unit",
                            created_by=actor_id,
                        )
                        session.add(binding)
                        await session.flush()
                    binding.status = True
                    binding.include_descendants = target["target_type"] == "org_unit"
                    version, compilation, warnings = await persist_target_policy_version_in_session(
                        session, binding, target["definition"], actor_id,
                        source_text=f"访问依据集 v{evidence_set.version}",
                        source_type="evidence_bootstrap",
                        source_ref=f"evidence_set:{evidence_set.id}",
                        confirm_warnings=True,
                    )
                    results.append({
                        "target_type": target["target_type"], "target_id": target["target_id"],
                        "version_id": version.id, "summary": compilation["summary"],
                        "warnings": warnings,
                    })
                datasource = await self._datasource(
                    session, workspace_id, evidence_set.datasource_id,
                )
                datasource.access_bootstrap_required = False
                datasource.active_evidence_set_id = evidence_set.id
                evidence_set.status = "published"
                evidence_set.confirmed_by = actor_id
                evidence_set.confirmed_at = datetime.now()
                evidence_set.published_at = datetime.now()
                evidence_set.compile_result_json = {
                    "fingerprint": compile_fingerprint,
                    "targets": results,
                }
                evidence_set.revision += 1
                await bump_authorization_revision(session, workspace_id)
                await record_authorization_audit(
                    session, workspace_id=workspace_id, actor_id=actor_id,
                    action="semantic_access.evidence.publish",
                    target_type="semantic_access_evidence_set", target_id=evidence_set.id,
                    before={"status": "review_ready"},
                    after={
                        "status": "published", "version": evidence_set.version,
                        "compile_fingerprint": compile_fingerprint,
                    },
                    reason=f"evidence_set:{evidence_set.id}",
                )
                return await self._set_payload(session, evidence_set, include_items=True)
        except SemanticBindingError as exc:
            await self._mark_publish_failed(workspace_id, set_id, str(exc), exc.details)
            raise PermissionEvidenceError(
                str(exc), code=exc.code, status_code=exc.status_code, details=exc.details,
            ) from exc
        except PermissionEvidenceError:
            raise
        except Exception as exc:
            await self._mark_publish_failed(workspace_id, set_id, str(exc), [])
            raise PermissionEvidenceError(
                "自动编译或发布失败，当前有效权限未改变",
                code="evidence_publish_failed",
                status_code=500,
            ) from exc

    async def process_run(self, run_id: int) -> None:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
            if not run or run.job_kind != "access_evidence":
                return
            readiness = await self._readiness_in_session(
                session, run.workspace_id, int(run.datasource_id),
            )
            if not readiness["ready"]:
                run.status, run.stage = "blocked", "waiting_for_readiness"
                run.result_summary_json = {"blockers": readiness["blockers"]}
                run.finished_at = datetime.now()
                return
            snapshot = await self._generation_snapshot(
                session, run.workspace_id, int(run.datasource_id),
            )
            run.input_fingerprint = stable_fingerprint(snapshot["fingerprints"])
            run.status, run.stage, run.progress = "running", "generating", 20
            run.started_at = datetime.now()
            input_fingerprint = run.input_fingerprint
        try:
            validated = await self._generate_evidence_drafts(snapshot)
        except Exception as exc:
            async with db.session_scope() as session:
                run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
                if run:
                    run.status, run.stage, run.error_message = "failed", "failed", str(exc)[:3000]
                    run.finished_at = datetime.now()
                    version = int((await session.execute(select(func.max(
                        SemanticAccessEvidenceSetModel.version
                    )).where(
                        SemanticAccessEvidenceSetModel.workspace_id == run.workspace_id,
                        SemanticAccessEvidenceSetModel.datasource_id == run.datasource_id,
                    ))).scalar() or 0) + 1
                    failed_set = SemanticAccessEvidenceSetModel(
                        workspace_id=run.workspace_id,
                        datasource_id=run.datasource_id,
                        version=version,
                        status="failed",
                        org_profile_fingerprint=snapshot["fingerprints"]["org_profiles"],
                        schema_fingerprint=snapshot["fingerprints"]["schema"],
                        semantic_fingerprint=snapshot["fingerprints"]["semantics"],
                        error_message=str(exc)[:3000],
                    )
                    session.add(failed_set)
                    await session.flush()
                    run.evidence_set_id = failed_set.id
            return
        try:
            ownership_summary = await self._generate_ownership_candidates(snapshot)
        except Exception as exc:  # Row inference must never discard table/field evidence.
            logger.warning("row ownership candidate generation failed: %s", exc, exc_info=True)
            ownership_summary = {
                "version": ROW_OWNERSHIP_VERSION,
                "candidates": [],
                "skipped": [{
                    "reason_code": "row_ownership_generation_failed",
                    "message": str(exc)[:1000],
                }],
            }
        async with db.session_scope() as session:
            run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
            current = await self._generation_snapshot(
                session, run.workspace_id, int(run.datasource_id),
            )
            if stable_fingerprint(current["fingerprints"]) != input_fingerprint:
                run.status, run.stage, run.finished_at = "discarded", "input_changed", datetime.now()
                await self._enqueue_in_session(
                    session, run.workspace_id, int(run.datasource_id),
                    run.created_by or "system", force=True,
                )
                return
            previous = (await session.execute(select(func.max(
                SemanticAccessEvidenceSetModel.version
            )).where(
                SemanticAccessEvidenceSetModel.workspace_id == run.workspace_id,
                SemanticAccessEvidenceSetModel.datasource_id == run.datasource_id,
            ))).scalar() or 0
            evidence_set = SemanticAccessEvidenceSetModel(
                workspace_id=run.workspace_id, datasource_id=run.datasource_id,
                version=int(previous) + 1, model_version=3, status="review_ready",
                org_profile_fingerprint=current["fingerprints"]["org_profiles"],
                schema_fingerprint=current["fingerprints"]["schema"],
                semantic_fingerprint=current["fingerprints"]["semantics"],
                generation_summary_json={"row_ownership_v2": ownership_summary},
                generated_at=datetime.now(),
            )
            session.add(evidence_set)
            await session.flush()
            table_map = {int(row.id): row for row in current["tables"]}
            column_map = {int(row.id): row for row in current["columns"]}
            for item in validated:
                table = table_map[item["table_id"]]
                session.add(SemanticAccessEvidenceAssetModel(
                    workspace_id=run.workspace_id, evidence_set_id=evidence_set.id,
                    asset_type="table", asset_id=table.id, table_id=table.id,
                    baseline_access=item["baseline_access"],
                    requires_individual_review=bool(
                        item["requires_individual_review"] or table.is_sensitive
                    ),
                    access_class=legacy_access_class(
                        item["baseline_access"],
                        bool(item["requires_individual_review"] or table.is_sensitive),
                    ),
                    is_sensitive=bool(table.is_sensitive),
                    review_status=(
                        "pending"
                        if item["baseline_access"] == "workspace_visible"
                        else "auto_safe"
                    ),
                    reason=item["reason"], evidence_json=["confirmed_semantics", "confirmed_org_profiles"],
                ))
                for relation in item["department_relations"]:
                    session.add(SemanticAccessEvidenceRelationModel(
                        workspace_id=run.workspace_id, evidence_set_id=evidence_set.id,
                        asset_type="table", asset_id=table.id, table_id=table.id,
                        org_unit_id=relation["org_unit_id"],
                        business_role=relation["business_role"],
                        access_level=relation["access_level"],
                        relation_role=relation["business_role"],
                        row_scope_json=relation["row_scope"],
                        review_status=(
                            "auto_safe"
                            if relation["access_level"] == "hidden" else "pending"
                        ),
                        reason=relation["reason"], confidence=relation["confidence"],
                    ))
                for exception in item["field_exceptions"]:
                    column = column_map[exception["column_id"]]
                    session.add(SemanticAccessEvidenceRelationModel(
                        workspace_id=run.workspace_id, evidence_set_id=evidence_set.id,
                        asset_type="column", asset_id=column.id, table_id=table.id,
                        org_unit_id=exception["org_unit_id"],
                        business_role=exception["business_role"],
                        field_decision=exception["field_decision"],
                        relation_role=exception["business_role"],
                        access_decision=exception["field_decision"],
                        row_scope_json={"type": "all"}, reason=exception["reason"],
                        review_status=(
                            "auto_safe"
                            if exception["field_decision"] == "hidden" else "pending"
                        ),
                        confidence=exception["confidence"],
                    ))
            await session.flush()
            await self._refresh_progress(session, evidence_set)
            run.evidence_set_id = evidence_set.id
            run.status, run.stage, run.progress = "completed", "review_ready", 100
            run.finished_at = datetime.now()
            run.result_summary_json = {
                "evidence_set_id": evidence_set.id,
                "version": evidence_set.version,
                "row_ownership_candidate_count": len(ownership_summary.get("candidates") or []),
            }

    async def _generate_evidence_drafts(self, snapshot: dict[str, Any]):
        """Generate small strict-ID batches and retry only the invalid batch."""
        all_results = []
        table_rows = list(snapshot["tables"])
        for offset in range(0, len(table_rows), 8):
            batch_tables = table_rows[offset:offset + 8]
            table_ids = {int(row.id) for row in batch_tables}
            batch = {
                **snapshot,
                "tables": batch_tables,
                "columns": [
                    row for row in snapshot["columns"] if int(row.table_id) in table_ids
                ],
                "payload": {
                    **snapshot["payload"],
                    "tables": [
                        row for row in snapshot["payload"]["tables"]
                        if int(row["table_id"]) in table_ids
                    ],
                },
            }
            last_error = ""
            for attempt in range(3):
                messages = [
                    {"role": "system", "content": (
                        "根据已确认的部门职责画像和表字段业务语义生成访问依据草案，不生成最终权限。"
                        "输出 JSON: {tables:[{table_id,baseline_access,"
                        "requires_individual_review,reason,department_relations:["
                        "{org_unit_id,business_role,reason,confidence}],field_exceptions:["
                        "{column_id,org_unit_id,field_decision,business_role,reason,confidence}]}]}。"
                        "当前仅生成表级和字段级访问依据，暂不生成行级范围，不要输出 row_scope。"
                        "部门关系 business_role 只能是 owner/producer/required_consumer/none，"
                        "不要使用 conditional_consumer；"
                        "字段例外 field_decision 只能是 visible/hidden，字段例外 business_role "
                        "也只能是 owner/producer/required_consumer/none；"
                        "baseline_access 只能是 workspace_visible/controlled；"
                        "requires_individual_review 只能是 true/false；"
                        "每张表必须覆盖所有部门。敏感字段、自由文本或部门差异字段才生成字段例外。"
                        "不得使用原始样例值、账号身份或现有权限。部门画像中的 assumptions 和 "
                        "missing_information 只能作为风险提示，不能单独作为扩大访问、标记公共数据、"
                        "放开敏感字段或放宽行范围的依据；证据不足时使用 none、controlled，"
                        "并将 requires_individual_review 设为 true。"
                    )},
                    {"role": "user", "content": json.dumps(batch["payload"], ensure_ascii=False)},
                ]
                if last_error:
                    messages.append({
                        "role": "user",
                        "content": f"上次输出未通过严格 ID/结构校验：{last_error[:800]}。请完整修正本批次。",
                    })
                try:
                    result = await self.llm.generate_json(
                        messages,
                        model=getattr(self.settings.llm, "fast_model", self.settings.llm.model),
                        temperature=0,
                        max_tokens=10000,
                    )
                    all_results.extend(self._validate_generation(result, batch))
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            else:
                raise ValueError(f"访问依据批次生成失败: {last_error}")
        return all_results

    async def _generate_ownership_candidates(
        self, snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Rank direct ownership metadata, then validate the selected value domain."""
        policy = self.settings.semantic_row_ownership
        if not policy.enabled:
            return {
                "version": ROW_OWNERSHIP_VERSION,
                "candidates": [],
                "skipped": [{"reason_code": "disabled"}],
            }

        columns_by_table: dict[int, list[tuple[Any, dict[str, Any]]]] = {}
        for column in snapshot["columns"]:
            metadata = _ownership_candidate_metadata(column)
            if metadata is None:
                continue
            columns_by_table.setdefault(int(column.table_id), []).append((column, metadata))

        candidate_input = []
        skipped: list[dict[str, Any]] = []
        metadata_by_column_id: dict[int, dict[str, Any]] = {}
        candidate_limit = max(1, min(int(getattr(policy, "candidate_limit", 8)), 30))
        for table in snapshot["tables"]:
            ranked = sorted(
                columns_by_table.get(int(table.id), []),
                key=lambda item: (
                    -int(item[1]["metadata_score"]),
                    int(getattr(item[0], "ordinal_position", 0) or 0),
                    int(item[0].id),
                ),
            )
            eligible = ranked[:candidate_limit]
            for column, metadata in ranked[candidate_limit:]:
                skipped.append({
                    "table_id": int(table.id),
                    "column_id": int(column.id),
                    "reason_code": "candidate_shortlist_limit",
                    **metadata,
                })
            if not eligible:
                skipped.append({
                    "table_id": int(table.id),
                    "reason_code": "no_scored_candidate",
                })
                continue
            for column, metadata in eligible:
                metadata_by_column_id[int(column.id)] = metadata
            candidate_input.append({
                "table_id": int(table.id),
                "physical_name": table.physical_name,
                "business_name": table.business_name,
                "description": table.description or "",
                "columns": [{
                    "column_id": int(column.id),
                    "physical_name": column.physical_name,
                    "business_name": column.business_name,
                    "description": column.description or "",
                    "physical_comment": getattr(column, "physical_comment", None) or "",
                    "synonyms": list(getattr(column, "synonyms", None) or []),
                    "data_type": column.data_type,
                    **metadata,
                } for column, metadata in eligible],
            })
        if not candidate_input:
            return {
                "version": ROW_OWNERSHIP_VERSION,
                "candidates": [],
                "skipped": skipped,
            }

        selected: list[dict[str, Any]] = []
        column_by_id = {int(row.id): row for row in snapshot["columns"]}
        table_by_id = {int(row.id): row for row in snapshot["tables"]}
        for offset in range(0, len(candidate_input), 20):
            batch = candidate_input[offset: offset + 20]
            result = await self.llm.generate_json(
                [{
                    "role": "system",
                    "content": (
                        "识别表中直接表示数据所属部门或组织的字段。只输出给定 ID。"
                        "字段的候选层级和命中词只用于召回，不能单独证明它是组织归属字段。"
                        "门店、中心、团队、分支等字段只有在确实对应 DeluData 内部部门层级时才可选择。"
                        "不要选择人员、创建人、客户、供应商、公司主体、区域、地点、仓库、产品或渠道字段。"
                        "不推断关联表、多字段或自定义条件。"
                        "每张表返回最多两个候选，按置信度降序。"
                        "输出 JSON: {tables:[{table_id,candidates:[{column_id,confidence,reason}]}]}。"
                    ),
                }, {
                    "role": "user",
                    "content": json.dumps({"tables": batch}, ensure_ascii=False),
                }],
                model=getattr(self.settings.llm, "fast_model", self.settings.llm.model),
                temperature=0,
                max_tokens=6000,
            )
            rows = result.get("tables") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise ValueError("行归属候选输出缺少 tables")
            returned_tables: set[int] = set()
            batch_by_id = {int(item["table_id"]): item for item in batch}
            for item in rows:
                table_id = int(item.get("table_id") or 0)
                if table_id not in batch_by_id or table_id in returned_tables:
                    raise ValueError("行归属候选包含非法或重复表 ID")
                returned_tables.add(table_id)
                allowed_columns = {
                    int(column["column_id"])
                    for column in batch_by_id[table_id]["columns"]
                }
                candidates = item.get("candidates") or []
                if not isinstance(candidates, list):
                    raise ValueError("行归属候选格式无效")
                normalized = []
                seen_columns: set[int] = set()
                for candidate in candidates[:2]:
                    column_id = int(candidate.get("column_id") or 0)
                    if column_id not in allowed_columns or column_id in seen_columns:
                        raise ValueError("行归属候选包含非法或重复字段 ID")
                    seen_columns.add(column_id)
                    normalized.append({
                        "column_id": column_id,
                        "confidence": _required_confidence(
                            candidate.get("confidence"), "行归属字段置信度",
                        ),
                        "reason": str(candidate.get("reason") or "")[:500],
                        **metadata_by_column_id[column_id],
                    })
                normalized.sort(key=lambda row: row["confidence"], reverse=True)
                if not normalized:
                    skipped.append({"table_id": table_id, "reason_code": "ai_no_candidate"})
                    continue
                top = normalized[0]
                margin = top["confidence"] - (
                    normalized[1]["confidence"] if len(normalized) > 1 else 0.0
                )
                confidence_threshold, margin_threshold = _ownership_candidate_thresholds(
                    policy, top["candidate_tier"],
                )
                if top["confidence"] < confidence_threshold or margin < margin_threshold:
                    skipped.append({
                        "table_id": table_id,
                        "column_id": top["column_id"],
                        "reason_code": "candidate_ambiguous",
                        "confidence": top["confidence"],
                        "confidence_threshold": confidence_threshold,
                        "margin": margin,
                        "margin_threshold": margin_threshold,
                        "candidate_tier": top["candidate_tier"],
                        "metadata_score": top["metadata_score"],
                        "matched_signals": top["matched_signals"],
                    })
                    continue
                selected.append({
                    "table": table_by_id[table_id],
                    "column": column_by_id[top["column_id"]],
                    "confidence": top["confidence"],
                    "reason": top["reason"],
                    "candidate_tier": top["candidate_tier"],
                    "metadata_score": top["metadata_score"],
                    "matched_signals": top["matched_signals"],
                })
            for table_id in set(batch_by_id) - returned_tables:
                skipped.append({"table_id": table_id, "reason_code": "ai_no_candidate"})

        config = await get_workspace_db_config_async(snapshot["workspace_id"])
        if not config:
            skipped.extend({
                "table_id": int(item["table"].id),
                "reason_code": "database_connection_missing",
            } for item in selected)
            return {
                "version": ROW_OWNERSHIP_VERSION,
                "candidates": [],
                "skipped": skipped,
            }
        connection_url = (
            config.get_readonly_connection_url()
            if config.has_readonly_config() else config.get_connection_url()
        )
        executor = ReadOnlyExecutor(
            f"row-ownership-{snapshot['workspace_id']}-{snapshot['datasource_id']}",
            connection_url,
            connect_timeout_sec=policy.probe_timeout_sec,
        )
        candidates: list[dict[str, Any]] = []
        for item in selected:
            profile = await asyncio.to_thread(
                self._profile_ownership_values_sync,
                executor,
                item["table"],
                item["column"],
                policy.max_distinct_values,
                policy.probe_timeout_sec,
            )
            if profile.get("status") != "complete" or not profile.get("source_values"):
                skipped.append({
                    "table_id": int(item["table"].id),
                    "column_id": int(item["column"].id),
                    "reason_code": profile.get("reason_code") or "value_profile_failed",
                    "candidate_tier": item["candidate_tier"],
                    "metadata_score": item["metadata_score"],
                    "matched_signals": item["matched_signals"],
                })
                continue
            mapped = await self._map_ownership_values(
                item,
                profile,
                snapshot["organization_directory"],
            )
            candidates.append(mapped)
        return {
            "version": ROW_OWNERSHIP_VERSION,
            "candidates": candidates,
            "skipped": skipped,
            "candidate_confidence_threshold": policy.candidate_confidence,
            "expanded_candidate_confidence_threshold": float(
                getattr(policy, "expanded_candidate_confidence", 0.95)
            ),
            "expanded_candidate_margin_threshold": float(
                getattr(policy, "expanded_candidate_margin", 0.20)
            ),
            "value_confidence_threshold": policy.value_confidence,
            "expanded_value_confidence_threshold": float(
                getattr(policy, "expanded_value_confidence", 0.95)
            ),
            "candidate_limit": candidate_limit,
            "max_distinct_values": policy.max_distinct_values,
        }

    @staticmethod
    def _profile_ownership_values_sync(
        executor: ReadOnlyExecutor,
        table: Any,
        column: Any,
        max_distinct_values: int,
        timeout_sec: int,
    ) -> dict[str, Any]:
        table_name = _quote_identifier(table.physical_name)
        column_name = _quote_identifier(column.physical_name)
        source_type = _ownership_source_type(column.data_type)
        empty_expression = (
            f"SUM(CASE WHEN {column_name} = '' THEN 1 ELSE 0 END)"
            if source_type == "string" else "0"
        )
        aggregate = executor.execute_query(
            "SELECT COUNT(*) AS total_rows, "
            f"SUM(CASE WHEN {column_name} IS NULL THEN 1 ELSE 0 END) AS null_rows, "
            f"{empty_expression} AS empty_rows FROM {table_name}",
            timeout_sec=timeout_sec,
            max_rows=2,
        )
        if aggregate.error or not aggregate.rows:
            return {"status": "failed", "reason_code": "value_profile_failed"}
        summary = dict(zip(aggregate.columns, aggregate.rows[0]))
        total_rows = int(summary.get("total_rows") or 0)
        if total_rows <= 0:
            return {"status": "skipped", "reason_code": "empty_table"}
        where = f"{column_name} IS NOT NULL"
        if source_type == "string":
            where += f" AND {column_name} <> ''"
        distinct_result = executor.execute_query(
            f"SELECT {column_name} AS source_value, COUNT(*) AS row_count "
            f"FROM {table_name} WHERE {where} GROUP BY {column_name} "
            f"ORDER BY source_value "
            f"LIMIT {int(max_distinct_values) + 1}",
            timeout_sec=timeout_sec,
            max_rows=int(max_distinct_values) + 1,
        )
        if distinct_result.error:
            return {"status": "failed", "reason_code": "value_profile_failed"}
        if len(distinct_result.rows) > max_distinct_values:
            return {"status": "skipped", "reason_code": "distinct_value_limit_exceeded"}
        source_values = []
        for row in distinct_result.rows:
            value, count = row[0], int(row[1] or 0)
            if source_type == "integer":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return {"status": "failed", "reason_code": "source_value_type_invalid"}
            else:
                value = str(value)
            source_values.append({
                "source_type": source_type,
                "source_value": value,
                "row_count": count,
            })
        return {
            "status": "complete",
            "total_rows": total_rows,
            "null_rows": int(summary.get("null_rows") or 0),
            "empty_rows": int(summary.get("empty_rows") or 0),
            "source_values": source_values,
            "source_domain_fingerprint": stable_fingerprint([
                (row["source_type"], row["source_value"])
                for row in source_values
            ]),
        }

    async def _map_ownership_values(
        self,
        candidate: dict[str, Any],
        profile: dict[str, Any],
        organizations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        policy = self.settings.semantic_row_ownership
        value_confidence = _ownership_value_confidence(
            policy, candidate.get("candidate_tier", "strong"),
        )
        org_by_id = {int(row["org_unit_id"]): row for row in organizations}
        code_index: dict[str, list[int]] = {}
        normalized_index: dict[str, list[int]] = {}
        for row in organizations:
            org_id = int(row["org_unit_id"])
            if row.get("code"):
                code_index.setdefault(str(row["code"]), []).append(org_id)
            for value in (row.get("code"), row.get("name"), row.get("full_path")):
                if value:
                    normalized_index.setdefault(_normalized_org_value(value), []).append(org_id)

        resolved: dict[tuple[str, str], dict[str, Any]] = {}
        direct_modes: set[str] = set()
        unresolved: list[dict[str, Any]] = []
        for source in profile["source_values"]:
            source_type = source["source_type"]
            source_value = source["source_value"]
            org_id = None
            match_method = None
            if source_type == "integer" and int(source_value) in org_by_id:
                org_id, match_method = int(source_value), "exact_id"
                direct_modes.add("id")
            elif source_type == "string" and len(code_index.get(str(source_value), [])) == 1:
                org_id, match_method = code_index[str(source_value)][0], "exact_code"
                direct_modes.add("code")
            else:
                matches = list(dict.fromkeys(
                    normalized_index.get(_normalized_org_value(source_value), [])
                ))
                if len(matches) == 1:
                    org_id, match_method = matches[0], "exact_name_or_path"
                    direct_modes.add("external")
            if org_id is None:
                unresolved.append(source)
                continue
            resolved[_typed_value_key(source_type, source_value)] = {
                **source,
                "target_kind": "org_unit",
                "org_unit_id": org_id,
                "confidence": 1.0,
                "match_method": match_method,
                "reason": "与现有部门标识唯一匹配",
            }

        if unresolved:
            result = await self.llm.generate_json(
                [{
                    "role": "system",
                    "content": (
                        "把外部组织值映射到给定的 DeluData 部门。只能选择给定 org_unit_id。"
                        "无法唯一判断时返回 target_kind=unresolved；不得把非空值判断为无归属。"
                        "输出 JSON: {mappings:[{source_type,source_value,target_kind,org_unit_id,confidence,reason}]}。"
                    ),
                }, {
                    "role": "user",
                    "content": json.dumps({
                        "source_values": unresolved,
                        "departments": organizations,
                    }, ensure_ascii=False),
                }],
                model=getattr(self.settings.llm, "fast_model", self.settings.llm.model),
                temperature=0,
                max_tokens=10000,
            )
            ai_rows = result.get("mappings") if isinstance(result, dict) else None
            if not isinstance(ai_rows, list):
                ai_rows = []
            unresolved_keys = {
                _typed_value_key(row["source_type"], row["source_value"]): row
                for row in unresolved
            }
            for row in ai_rows:
                source_type = str(row.get("source_type") or "")
                source_value = row.get("source_value")
                key = _typed_value_key(source_type, source_value)
                source = unresolved_keys.get(key)
                org_id = int(row.get("org_unit_id") or 0)
                confidence = _required_confidence(
                    row.get("confidence"), "外部组织值映射置信度",
                )
                if (
                    source
                    and row.get("target_kind") == "org_unit"
                    and org_id in org_by_id
                    and confidence >= value_confidence
                ):
                    direct_modes.add("external")
                    resolved[key] = {
                        **source,
                        "target_kind": "org_unit",
                        "org_unit_id": org_id,
                        "confidence": confidence,
                        "match_method": "ai",
                        "reason": str(row.get("reason") or "")[:500],
                    }

        unresolved_values = [
            row for row in profile["source_values"]
            if _typed_value_key(row["source_type"], row["source_value"]) not in resolved
        ]
        all_direct_id = not unresolved_values and direct_modes == {"id"} and len(resolved) == len(profile["source_values"])
        all_direct_code = not unresolved_values and direct_modes == {"code"} and len(resolved) == len(profile["source_values"])
        org_value_kind = "id" if all_direct_id else "code" if all_direct_code else "external"
        bindings = [] if org_value_kind in {"id", "code"} else list(resolved.values())
        table, column = candidate["table"], candidate["column"]
        blockers = []
        if unresolved_values:
            blockers.append({
                "code": "ownership_values_unresolved",
                "message": f"{len(unresolved_values)} 个非空归属值尚未映射",
            })
        return {
            "table_id": int(table.id),
            "table_label": table.business_name,
            "confidence": float(candidate["confidence"]),
            "reason": candidate["reason"],
            "candidate_tier": candidate.get("candidate_tier", "strong"),
            "metadata_score": int(candidate.get("metadata_score") or 0),
            "matched_signals": list(candidate.get("matched_signals") or []),
            "proposed_mapping": {
                "org_column_id": int(column.id),
                "org_value_kind": org_value_kind,
                "org_value_mapping": {
                    "version": ROW_OWNERSHIP_VERSION,
                    "bindings": bindings,
                    "source_domain_fingerprint": profile["source_domain_fingerprint"],
                },
                "user_column_id": None,
                "user_value_kind": "id",
            },
            "evidence": [
                "metadata_scoring_v2",
                f"candidate_tier:{candidate.get('candidate_tier', 'strong')}",
                *list(candidate.get("matched_signals") or []),
                "bounded_distinct_value_profile",
            ],
            "validation": {
                "blockers": blockers,
                "warnings": [],
                "total_rows": profile["total_rows"],
                "null_rows": profile["null_rows"],
                "empty_rows": profile["empty_rows"],
                "source_values": profile["source_values"],
                "unresolved_values": unresolved_values,
                "value_confidence_threshold": value_confidence,
                "source_domain_fingerprint": profile["source_domain_fingerprint"],
            },
        }

    async def _enqueue_in_session(
        self, session, workspace_id: str, datasource_id: int, actor_id: str, *, force: bool,
    ):
        await self._datasource(session, workspace_id, datasource_id)
        open_run = (await session.execute(select(SemanticPermissionEvidenceRunModel).where(
            SemanticPermissionEvidenceRunModel.workspace_id == workspace_id,
            SemanticPermissionEvidenceRunModel.datasource_id == datasource_id,
            SemanticPermissionEvidenceRunModel.job_kind == "access_evidence",
            SemanticPermissionEvidenceRunModel.status.in_(("queued", "running")),
        ).order_by(SemanticPermissionEvidenceRunModel.id.desc()))).scalars().first()
        if open_run and not force:
            return open_run
        if open_run:
            open_run.status, open_run.stage, open_run.finished_at = "discarded", "superseded", datetime.now()
        run = SemanticPermissionEvidenceRunModel(
            workspace_id=workspace_id, datasource_id=datasource_id,
            job_kind="access_evidence",
            input_fingerprint=stable_fingerprint({"workspace": workspace_id, "datasource": datasource_id}),
            created_by=actor_id,
        )
        session.add(run)
        await session.flush()
        return run

    async def _readiness_in_session(self, session, workspace_id: str, datasource_id: int):
        datasource = await self._datasource(session, workspace_id, datasource_id)
        context = (await session.execute(select(WorkspaceBusinessContextModel).where(
            WorkspaceBusinessContextModel.workspace_id == workspace_id,
        ))).scalar_one_or_none()
        departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.parent_id.is_(None),
            DepartmentModel.status == True,  # noqa: E712
        ))).scalars())
        profiles = {
            int(row.org_unit_id): row for row in (await session.execute(
                select(OrganizationSemanticProfileModel).where(
                    OrganizationSemanticProfileModel.workspace_id == workspace_id,
                )
            )).scalars()
        }
        missing_profiles = [
            {"id": int(row.id), "name": row.name, "status": (
                profiles[int(row.id)].status if int(row.id) in profiles else "missing"
            )}
            for row in departments
            if int(row.id) not in profiles or profiles[int(row.id)].status != "confirmed"
        ]
        tables = list((await session.execute(select(SemanticTableModel).where(
            SemanticTableModel.workspace_id == workspace_id,
            SemanticTableModel.datasource_id == datasource_id,
            SemanticTableModel.status == "confirmed",
            SemanticTableModel.sync_state == "current",
            SemanticTableModel.is_queryable == True,  # noqa: E712
        ))).scalars())
        table_ids = [int(row.id) for row in tables]
        columns = list((await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
            SemanticColumnModel.table_id.in_(table_ids or [-1]),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ))).scalars())
        unconfirmed_tables = [
            {"id": int(row.id), "name": row.business_name}
            for row in tables if row.business_semantics_status != "confirmed"
        ]
        unconfirmed_columns = [
            {"id": int(row.id), "table_id": int(row.table_id), "name": row.business_name}
            for row in columns if row.business_semantics_status != "confirmed"
        ]
        blockers = []
        if missing_profiles:
            blockers.append({"code": "org_profiles_incomplete", "items": missing_profiles})
        if not tables:
            blockers.append({"code": "queryable_tables_missing", "message": "没有当前可问数表"})
        if unconfirmed_tables:
            blockers.append({"code": "table_semantics_unconfirmed", "items": unconfirmed_tables})
        if unconfirmed_columns:
            blockers.append({"code": "column_semantics_unconfirmed", "items": unconfirmed_columns})
        return {
            "ready": not blockers, "datasource_id": datasource.id,
            "access_bootstrap_required": bool(
                getattr(datasource, "access_bootstrap_required", False)
            ),
            "business_context": {
                "exists": bool(business_context_snapshot(context)["quality"]["has_any"]),
                "revision": context.revision if context else 0,
                "quality": business_context_snapshot(context)["quality"],
            },
            "department_profiles": {
                "total": len(departments), "confirmed": len(departments) - len(missing_profiles),
                "missing": missing_profiles,
            },
            "business_semantics": {
                "tables_total": len(tables), "tables_unconfirmed": unconfirmed_tables,
                "columns_total": len(columns), "columns_unconfirmed": unconfirmed_columns,
            },
            "blockers": blockers,
        }

    async def _generation_snapshot(self, session, workspace_id: str, datasource_id: int):
        readiness = await self._readiness_in_session(session, workspace_id, datasource_id)
        if not readiness["ready"]:
            raise PermissionEvidenceError("访问依据生成条件未满足", code="readiness_blocked")
        context = (await session.execute(select(WorkspaceBusinessContextModel).where(
            WorkspaceBusinessContextModel.workspace_id == workspace_id,
        ))).scalar_one_or_none()
        departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.parent_id.is_(None),
            DepartmentModel.status == True,  # noqa: E712
        ).order_by(DepartmentModel.id))).scalars())
        all_departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.status == True,  # noqa: E712
        ).order_by(DepartmentModel.id))).scalars())
        bindings = list((await session.execute(select(OrganizationSemanticProfileModel).where(
            OrganizationSemanticProfileModel.workspace_id == workspace_id,
            OrganizationSemanticProfileModel.org_unit_id.in_([row.id for row in departments]),
        ))).scalars())
        versions = {
            int(row.id): row for row in (await session.execute(
                select(OrganizationSemanticProfileVersionModel).where(
                    OrganizationSemanticProfileVersionModel.id.in_(
                        [row.active_version_id for row in bindings]
                    )
                )
            )).scalars()
        }
        profile_by_org = {
            int(row.org_unit_id): versions[int(row.active_version_id)] for row in bindings
        }
        department_names = {row.name for row in departments}
        datasource = await self._datasource(session, workspace_id, datasource_id)
        tables = list((await session.execute(select(SemanticTableModel).where(
            SemanticTableModel.workspace_id == workspace_id,
            SemanticTableModel.datasource_id == datasource_id,
            SemanticTableModel.status == "confirmed",
            SemanticTableModel.sync_state == "current",
            SemanticTableModel.is_queryable == True,  # noqa: E712
        ).order_by(SemanticTableModel.id))).scalars())
        columns = list((await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
            SemanticColumnModel.table_id.in_([row.id for row in tables] or [-1]),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ).order_by(SemanticColumnModel.table_id, SemanticColumnModel.ordinal_position))).scalars())
        org_payload = [{
            "org_unit_id": int(row.id), "name": row.name,
            "profile": restrict_profile_collaborations(
                profile_by_org[int(row.id)].content_json or {},
                department_names - {row.name},
            ),
            "profile_version": profile_by_org[int(row.id)].version,
        } for row in departments]
        table_payload = [{
            "table_id": int(row.id), "physical_name": row.physical_name,
            "business_name": row.business_name,
            "description": row.description or "", "is_sensitive": bool(row.is_sensitive),
            "columns": [{
                "column_id": int(column.id), "physical_name": column.physical_name,
                "business_name": column.business_name,
                "description": column.description or "", "data_type": column.data_type,
                "is_sensitive": bool(column.is_sensitive),
            } for column in columns if int(column.table_id) == int(row.id)],
        } for row in tables]
        org_fp = stable_fingerprint([
            (item["org_unit_id"], item["profile_version"], item["profile"]) for item in org_payload
        ])
        semantic_fp = stable_fingerprint([
            (
                row.id, row.business_semantics_revision, row.business_name, row.description,
                [(col.id, col.business_semantics_revision, col.business_name, col.description)
                 for col in columns if col.table_id == row.id],
            ) for row in tables
        ])
        department_by_id = {int(row.id): row for row in all_departments}

        def department_path(row: Any) -> str:
            names, seen = [], set()
            current = row
            while current is not None and int(current.id) not in seen:
                seen.add(int(current.id))
                names.append(str(current.name))
                current = department_by_id.get(int(current.parent_id)) if current.parent_id else None
            return " / ".join(reversed(names))

        organization_directory = [{
            "org_unit_id": int(row.id),
            "name": row.name,
            "code": row.code,
            "parent_id": int(row.parent_id) if row.parent_id is not None else None,
            "full_path": department_path(row),
        } for row in all_departments]
        return {
            "payload": {
                "business_context": business_context_snapshot(context),
                "departments": org_payload,
                "tables": table_payload,
            },
            "fingerprints": {
                "org_profiles": org_fp,
                "schema": datasource.schema_fingerprint or "",
                "semantics": semantic_fp,
            },
            "workspace_id": workspace_id,
            "datasource_id": datasource_id,
            "organization_directory": organization_directory,
            "departments": departments, "tables": tables, "columns": columns,
        }

    def _validate_generation(self, result: Any, snapshot: dict[str, Any]):
        if not isinstance(result, dict) or not isinstance(result.get("tables"), list):
            raise ValueError("模型输出缺少 tables")
        table_by_id = {int(row.id): row for row in snapshot["tables"]}
        table_ids = set(table_by_id)
        column_by_table = {}
        for row in snapshot["columns"]:
            column_by_table.setdefault(int(row.table_id), set()).add(int(row.id))
        org_ids = {int(row.id) for row in snapshot["departments"]}
        output = []
        seen_tables = set()
        for item in result["tables"]:
            table_id = int(item.get("table_id"))
            if table_id not in table_ids or table_id in seen_tables:
                raise ValueError(f"非法或重复表 ID: {table_id}")
            seen_tables.add(table_id)
            baseline_access = item.get("baseline_access")
            legacy_class = item.get("access_class")
            if baseline_access is None and legacy_class in ACCESS_CLASSES:
                baseline_access = baseline_access_from_legacy(legacy_class)
            if baseline_access not in BASELINE_ACCESS:
                raise ValueError(f"表 {table_id} 的基线访问无效")
            requires_individual_review = bool(
                item.get("requires_individual_review")
                or legacy_class == "restricted"
            )
            if bool(getattr(table_by_id[table_id], "is_sensitive", False)):
                baseline_access = "controlled"
                requires_individual_review = True
            relations, seen_orgs = [], set()
            for raw in item.get("department_relations") or []:
                org_id = int(raw.get("org_unit_id"))
                if org_id not in org_ids or org_id in seen_orgs:
                    raise ValueError(f"表 {table_id} 含非法或重复部门 ID")
                seen_orgs.add(org_id)
                role = raw.get("business_role", raw.get("relation_role"))
                if role not in GENERATED_BUSINESS_ROLES:
                    raise ValueError(f"表 {table_id} 的部门关系无效")
                relations.append({
                    "org_unit_id": org_id, "business_role": role,
                    "access_level": "hidden" if role == "none" else "visible",
                    "reason": str(raw.get("reason") or ""),
                    "confidence": _required_confidence(
                        raw.get("confidence"),
                        f"表 {table_id} 部门 {org_id} 的部门关系置信度",
                    ),
                    # Preserve the persisted schema without asking the model to
                    # infer row ownership. No row mapping is required for "all".
                    "row_scope": {"type": "all"},
                })
            if seen_orgs != org_ids:
                raise ValueError(f"表 {table_id} 未覆盖全部一级部门")
            exceptions, exception_keys = [], set()
            for raw in item.get("field_exceptions") or []:
                column_id, org_id = int(raw.get("column_id")), int(raw.get("org_unit_id"))
                if column_id not in column_by_table.get(table_id, set()) or org_id not in org_ids:
                    raise ValueError(f"表 {table_id} 含非法字段例外 ID")
                key = (column_id, org_id)
                if key in exception_keys:
                    raise ValueError(f"表 {table_id} 含重复字段例外")
                exception_keys.add(key)
                decision = raw.get("field_decision", raw.get("access_decision"))
                role = raw.get("business_role", raw.get("relation_role", "none"))
                # Field exceptions are optional recommendations. Unknown model
                # vocabulary must fail closed instead of aborting every table
                # batch; IDs remain strictly validated above.
                if decision not in {"visible", "hidden"}:
                    decision = "hidden"
                if role not in GENERATED_BUSINESS_ROLES:
                    role = "none"
                exceptions.append({
                    "column_id": column_id, "org_unit_id": org_id,
                    "field_decision": decision, "business_role": role,
                    "reason": str(raw.get("reason") or ""),
                    "confidence": _required_confidence(
                        raw.get("confidence"),
                        f"表 {table_id} 字段 {column_id} 部门 {org_id} 的字段例外置信度",
                    ),
                })
            output.append({
                "table_id": table_id, "baseline_access": baseline_access,
                "requires_individual_review": requires_individual_review,
                "reason": str(item.get("reason") or ""),
                "department_relations": relations, "field_exceptions": exceptions,
            })
        if seen_tables != table_ids:
            raise ValueError("模型未覆盖全部可问数表")
        return output

    @staticmethod
    def _unique_field_exception_assets(validated: list[dict[str, Any]]):
        """Return one asset seed per field while retaining per-department relations."""
        unique: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        for item in validated:
            for exception in item["field_exceptions"]:
                unique.setdefault(int(exception["column_id"]), (item, exception))
        return list(unique.values())

    async def _enabled_asset_ids(
        self, session, workspace_id: str, datasource_id: int,
    ) -> tuple[set[int], set[int]]:
        table_ids = set((await session.execute(select(SemanticTableModel.id).where(
            SemanticTableModel.workspace_id == workspace_id,
            SemanticTableModel.datasource_id == datasource_id,
            SemanticTableModel.status == "confirmed",
            SemanticTableModel.sync_state == "current",
            SemanticTableModel.is_queryable == True,  # noqa: E712
        ))).scalars())
        column_ids = set((await session.execute(select(SemanticColumnModel.id).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
            SemanticColumnModel.table_id.in_(table_ids or {-1}),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ))).scalars())
        return {int(item) for item in table_ids}, {int(item) for item in column_ids}

    async def _validation_blockers(self, session, evidence_set):
        enabled_table_ids, enabled_column_ids = await self._enabled_asset_ids(
            session, evidence_set.workspace_id, evidence_set.datasource_id,
        )
        assets = list((await session.execute(select(SemanticAccessEvidenceAssetModel).where(
            SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        relations = list((await session.execute(select(SemanticAccessEvidenceRelationModel).where(
            SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        assets = filter_enabled_evidence_items(
            assets, enabled_table_ids, enabled_column_ids,
        )
        relations = filter_enabled_evidence_items(
            relations, enabled_table_ids, enabled_column_ids,
        )
        blockers = []
        for row in assets:
            if row.asset_type != "table":
                continue
            baseline_access = self._baseline_access(row)
            if (
                baseline_access == "workspace_visible"
                and row.review_status not in MANUALLY_APPROVED
            ):
                blockers.append({"code": "public_access_pending", "asset_id": row.id})
            if (
                row.is_sensitive
                and baseline_access == "workspace_visible"
            ):
                blockers.append({
                    "code": "sensitive_table_cannot_be_public",
                    "asset_id": row.id,
                })
        for row in relations:
            if row.asset_type == "table":
                level = self._access_level(row)
                scope = row.row_scope_json or {"type": "all"}
                if level in {"visible", "partial"} and row.review_status not in MANUALLY_APPROVED:
                    blockers.append({"code": "table_grant_pending", "relation_id": row.id})
                if level == "visible" and scope.get("type", "all") != "all":
                    blockers.append({"code": "visible_scope_must_be_all", "relation_id": row.id})
                if level == "partial" and scope.get("type", "all") == "all":
                    blockers.append({"code": "partial_scope_required", "relation_id": row.id})
                if self._business_role(row) == "conditional_consumer" and level == "visible":
                    blockers.append({"code": "conditional_not_executable", "relation_id": row.id})
            elif self._field_decision(row) == "visible":
                if row.review_status not in MANUALLY_APPROVED:
                    blockers.append({"code": "field_grant_pending", "relation_id": row.id})
        mappings = {
            int(row.table_id): row for row in (await session.execute(
                select(SemanticOwnershipMappingModel).where(
                    SemanticOwnershipMappingModel.workspace_id == evidence_set.workspace_id,
                    SemanticOwnershipMappingModel.datasource_id == evidence_set.datasource_id,
                )
            )).scalars()
        }
        for row in relations:
            if row.asset_type != "table" or self._access_level(row) != "partial":
                continue
            scope_type = (row.row_scope_json or {}).get("type", "all")
            mapping = mappings.get(int(row.table_id))
            if scope_type in {"target_org", "target_org_tree", "primary_assignment", "all_assignments", "custom_org"}:
                if not mapping or not mapping.org_column_id:
                    blockers.append({"code": "org_mapping_missing", "table_id": row.table_id})
            if scope_type == "self" and (not mapping or not mapping.user_column_id):
                blockers.append({"code": "user_mapping_missing", "table_id": row.table_id})
        return blockers

    async def _compile_definitions(self, session, evidence_set):
        tables = {
            int(row.id): row for row in (await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.workspace_id == evidence_set.workspace_id,
                SemanticTableModel.datasource_id == evidence_set.datasource_id,
                SemanticTableModel.status == "confirmed",
                SemanticTableModel.sync_state == "current",
                SemanticTableModel.is_queryable == True,  # noqa: E712
            ))).scalars()
        }
        columns = list((await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == evidence_set.workspace_id,
            SemanticColumnModel.datasource_id == evidence_set.datasource_id,
            SemanticColumnModel.table_id.in_(list(tables) or [-1]),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ))).scalars())
        sensitive_by_table: dict[int, set[int]] = {}
        enabled_column_ids = {int(column.id) for column in columns}
        for column in columns:
            if column.is_sensitive:
                sensitive_by_table.setdefault(int(column.table_id), set()).add(int(column.id))
        assets = list((await session.execute(select(SemanticAccessEvidenceAssetModel).where(
            SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        table_assets = {int(row.asset_id): row for row in assets if row.asset_type == "table"}
        column_assets = {int(row.asset_id): row for row in assets if row.asset_type == "column"}
        relations = list((await session.execute(select(SemanticAccessEvidenceRelationModel).where(
            SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        baseline_rules = []
        for table_id, asset in sorted(table_assets.items()):
            if (
                table_id not in tables
                or self._baseline_access(asset) != "workspace_visible"
                or asset.is_sensitive
                or not grant_is_compilable(asset.review_status)
            ):
                continue
            baseline_rules.append({
                "table_id": table_id, "decision": "visible",
                "hidden_column_ids": sorted(sensitive_by_table.get(table_id, set())),
                "hidden_metric_ids": [], "row_scope": {"type": "all"},
            })
        definitions = [{
            "target_type": "baseline", "target_id": "*",
            "definition": {"name": "全员基线", "tables": baseline_rules},
        }]
        org_ids = sorted({
            int(row.org_unit_id) for row in relations if row.asset_type == "table"
        })
        for org_id in org_ids:
            rules = []
            for relation in sorted(
                (row for row in relations if row.asset_type == "table" and row.org_unit_id == org_id),
                key=lambda row: int(row.table_id),
            ):
                if int(relation.table_id) not in tables:
                    continue
                level = self._access_level(relation)
                if (
                    level not in {"visible", "partial"}
                    or not grant_is_compilable(relation.review_status)
                ):
                    continue
                hidden = set(sensitive_by_table.get(int(relation.table_id), set()))
                for exception in relations:
                    if (
                        exception.asset_type == "column"
                        and exception.table_id == relation.table_id
                        and exception.org_unit_id == org_id
                        and int(exception.asset_id) in enabled_column_ids
                    ):
                        field_asset = column_assets.get(int(exception.asset_id))
                        if (
                            self._field_decision(exception) == "visible"
                            and grant_is_compilable(exception.review_status)
                            and (not field_asset or field_asset.review_status != "rejected")
                        ):
                            hidden.discard(int(exception.asset_id))
                        elif self._field_decision(exception) == "hidden":
                            hidden.add(int(exception.asset_id))
                rules.append({
                    "table_id": int(relation.table_id), "decision": "visible",
                    "hidden_column_ids": sorted(hidden), "hidden_metric_ids": [],
                    "row_scope": (
                        relation.row_scope_json or {"type": "all"}
                        if level == "partial" else {"type": "all"}
                    ),
                })
            definitions.append({
                "target_type": "org_unit", "target_id": str(org_id),
                "definition": {"name": f"一级部门 {org_id}", "tables": rules},
            })
        # Canonical ordering makes the evidence-to-policy compilation byte-stable.
        return json.loads(json.dumps(definitions, ensure_ascii=False, sort_keys=True))

    async def _representative_accounts(self, session, workspace_id: str):
        departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.parent_id.is_(None),
            DepartmentModel.status == True,  # noqa: E712
        ).order_by(DepartmentModel.id))).scalars())
        results = []
        for department in departments:
            row = (await session.execute(
                select(AssignmentModel.user_id)
                .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
                .where(
                    AssignmentModel.workspace_id == workspace_id,
                    PositionModel.org_unit_id == department.id,
                    AssignmentModel.status == True,  # noqa: E712
                    AssignmentModel.is_primary == True,  # noqa: E712
                ).order_by(AssignmentModel.id).limit(1)
            )).scalar_one_or_none()
            results.append({"org_unit_id": int(department.id), "user_id": row})
        return results

    async def _refresh_progress(self, session, evidence_set):
        enabled_table_ids, enabled_column_ids = await self._enabled_asset_ids(
            session, evidence_set.workspace_id, evidence_set.datasource_id,
        )
        assets = list((await session.execute(select(SemanticAccessEvidenceAssetModel).where(
            SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        relations = list((await session.execute(select(SemanticAccessEvidenceRelationModel).where(
            SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
        ))).scalars())
        assets = filter_enabled_evidence_items(
            assets, enabled_table_ids, enabled_column_ids,
        )
        relations = filter_enabled_evidence_items(
            relations, enabled_table_ids, enabled_column_ids,
        )
        table_assets = [row for row in assets if row.asset_type == "table"]
        table_relations = [row for row in relations if row.asset_type == "table"]
        column_relations = [row for row in relations if row.asset_type == "column"]
        public_access = [
            row for row in table_assets
            if self._baseline_access(row) == "workspace_visible"
        ]
        table_grants = [
            row for row in table_relations
            if self._access_level(row) in {"visible", "partial"}
        ]
        field_grants = [
            row for row in column_relations
            if self._field_decision(row) == "visible"
        ]
        evidence_set.review_progress_json = {
            "public_access": {
                "reviewed": sum(row.review_status in MANUALLY_APPROVED for row in public_access),
                "total": len(public_access),
            },
            "table_grants": {
                "reviewed": sum(row.review_status in MANUALLY_APPROVED for row in table_grants),
                "total": len(table_grants),
            },
            "field_grants": {
                "reviewed": sum(row.review_status in MANUALLY_APPROVED for row in field_grants),
                "total": len(field_grants),
            },
            "auto_safe_count": sum(
                row.review_status == "auto_safe" for row in [*assets, *relations]
            ),
        }
        evidence_set.blockers_json = await self._validation_blockers(session, evidence_set)

    async def _set_payload(self, session, row, *, include_items):
        payload = {"set": self._set_summary(row)}
        if include_items:
            assets = list((await session.execute(select(SemanticAccessEvidenceAssetModel).where(
                SemanticAccessEvidenceAssetModel.evidence_set_id == row.id,
            ).order_by(SemanticAccessEvidenceAssetModel.table_id, SemanticAccessEvidenceAssetModel.asset_type))).scalars())
            relations = list((await session.execute(select(SemanticAccessEvidenceRelationModel).where(
                SemanticAccessEvidenceRelationModel.evidence_set_id == row.id,
            ).order_by(
                SemanticAccessEvidenceRelationModel.table_id,
                SemanticAccessEvidenceRelationModel.org_unit_id,
                SemanticAccessEvidenceRelationModel.asset_type,
            ))).scalars())
            payload["assets"] = [{
                "id": item.id, "asset_type": item.asset_type, "asset_id": item.asset_id,
                "table_id": item.table_id,
                "baseline_access": (
                    self._baseline_access(item) if item.asset_type == "table" else None
                ),
                "requires_individual_review": (
                    self._requires_individual_review(item) if item.asset_type == "table" else False
                ),
                "access_class": item.access_class,
                "is_sensitive": item.is_sensitive, "inherits_table": item.inherits_table,
                "review_status": item.review_status, "reason": item.reason,
                "evidence": item.evidence_json or [], "override_reason": item.override_reason,
            } for item in assets]
            payload["relations"] = [{
                "id": item.id, "asset_type": item.asset_type, "asset_id": item.asset_id,
                "table_id": item.table_id, "org_unit_id": item.org_unit_id,
                "business_role": self._business_role(item),
                "access_level": (
                    self._access_level(item) if item.asset_type == "table" else None
                ),
                "field_decision": (
                    self._field_decision(item) if item.asset_type == "column" else None
                ),
                "relation_role": item.relation_role,
                "access_decision": item.access_decision,
                "row_scope": item.row_scope_json or {}, "reason": item.reason,
                "evidence": item.evidence_json or [], "confidence": item.confidence,
                "review_status": item.review_status, "override_reason": item.override_reason,
            } for item in relations]
        return payload

    def _set_summary(self, row):
        return {
            "id": row.id, "datasource_id": row.datasource_id, "version": row.version,
            "model_version": int(getattr(row, "model_version", 1) or 1),
            "revision": row.revision, "status": row.status,
            "review_progress": row.review_progress_json or {},
            "review_summary": row.review_progress_json or {},
            "blockers": row.blockers_json or [],
            "fingerprints": {
                "org_profiles": row.org_profile_fingerprint,
                "schema": row.schema_fingerprint,
                "semantics": row.semantic_fingerprint,
            },
            "compile_result": row.compile_result_json or {},
            "error_message": row.error_message, "generated_at": _dt(row.generated_at),
            "confirmed_at": _dt(row.confirmed_at), "published_at": _dt(row.published_at),
        }

    @staticmethod
    def _run_payload(row: SemanticPermissionEvidenceRunModel) -> dict[str, Any]:
        return {
            "run_id": int(row.id),
            "datasource_id": int(row.datasource_id),
            "evidence_set_id": (
                int(row.evidence_set_id) if row.evidence_set_id is not None else None
            ),
            "status": row.status,
            "stage": row.stage,
            "progress": int(row.progress or 0),
            "error_code": row.error_code,
            "error_message": row.error_message,
            "created_at": _dt(row.created_at),
            "started_at": _dt(row.started_at),
            "finished_at": _dt(row.finished_at),
        }

    async def _set(self, session, workspace_id: str, set_id: int):
        row = await session.get(SemanticAccessEvidenceSetModel, set_id)
        if not row or row.workspace_id != workspace_id:
            raise PermissionEvidenceError("依据集不存在", code="evidence_set_not_found", status_code=404)
        return row

    async def _set_for_review(self, session, workspace_id, set_id, expected_revision):
        row = (await session.execute(select(SemanticAccessEvidenceSetModel).where(
            SemanticAccessEvidenceSetModel.id == set_id,
            SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
        ).with_for_update())).scalar_one_or_none()
        if not row:
            raise PermissionEvidenceError("依据集不存在", code="evidence_set_not_found", status_code=404)
        if row.status not in {"review_ready", "publish_failed"}:
            raise PermissionEvidenceError("当前依据集不可编辑", code="evidence_set_not_editable", status_code=409)
        if int(row.revision or 0) != expected_revision:
            raise PermissionEvidenceError("依据集已发生变化", code="revision_conflict", status_code=409)
        return row

    async def _datasource(self, session, workspace_id: str, datasource_id: int):
        row = await session.get(SemanticDatasourceModel, datasource_id)
        if not row or row.workspace_id != workspace_id:
            raise PermissionEvidenceError("数据源不存在", code="datasource_not_found", status_code=404)
        return row

    @staticmethod
    def _validate_row_scope(scope):
        if not isinstance(scope, dict) or scope.get("type", "all") not in ROW_SCOPE_TYPES:
            raise PermissionEvidenceError("行范围无效", code="invalid_row_scope")

    @staticmethod
    def _scope_rank(scope):
        return {
            "self": 0, "target_org": 1, "primary_assignment": 1,
            "target_org_tree": 2, "all_assignments": 2, "custom_org": 2,
            "custom": 2, "all": 3,
        }.get((scope or {}).get("type", "all"), 3)

    async def _mark_publish_failed(self, workspace_id, set_id, message, details):
        db = get_async_db_manager()
        async with db.session_scope() as session:
            row = await self._set(session, workspace_id, set_id)
            row.status = "publish_failed"
            row.error_message = message[:3000]
            row.blockers_json = details or []


_service: PermissionEvidenceService | None = None


def get_permission_evidence_service() -> PermissionEvidenceService:
    global _service
    if _service is None:
        _service = PermissionEvidenceService()
    return _service
