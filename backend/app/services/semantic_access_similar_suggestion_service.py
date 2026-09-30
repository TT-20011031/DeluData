"""One-shot similar permission suggestions for department semantic access.

Suggestions are never persisted.  Preview and apply both rebuild the candidate
set from the active policy (and, during bootstrap, the current review draft),
then protect the apply with a deterministic batch fingerprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, text

from app.core.db.database import get_async_db_manager
from app.core.db.mysql_connection_policy import create_mysql_engine
from app.core.llm.async_embedding import get_async_embedding
from app.models.config.db_config import get_workspace_db_config_async
from app.models.config.permission_evidence import SemanticAccessEvidenceRelationModel
from app.models.config.semantic import (
    SemanticAccessBootstrapRunModel,
    SemanticAccessBootstrapTargetModel,
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticPolicyBindingModel,
    SemanticPolicyVersionModel,
    SemanticTableModel,
)
from app.services.authorization_service import (
    bump_authorization_revision,
    record_authorization_audit,
)
from app.services.semantic_policy_binding_service import (
    SemanticBindingError,
    _assert_actor_scope_v3,
    persist_target_policy_version_in_session,
)


logger = logging.getLogger(__name__)

SEMANTIC_THRESHOLD = 0.88
AMBIGUITY_MARGIN = 0.05
MAX_FIELD_CANDIDATES = 20
MAX_TABLE_SUGGESTIONS = 50
SEMANTIC_EMBEDDING_TIMEOUT_SECONDS = 5.0
SUPPORTED_ROW_OPERATORS = {"=", "!=", "in", "not in", "is null", "is not null"}
APPROVED_REVIEW_STATUSES = {"accepted", "modified"}


@dataclass(slots=True)
class PermissionChange:
    kind: str
    source_column_id: int
    before: Any
    after: Any
    restrictive: bool


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _normalize_term(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _column_terms(column: SemanticColumnModel) -> set[str]:
    values = [column.business_name, column.physical_name, *(column.synonyms or [])]
    return {term for term in (_normalize_term(value) for value in values) if term}


def _column_text(column: SemanticColumnModel) -> str:
    return " | ".join(str(value).strip() for value in (
        column.business_name,
        column.description,
        *(column.synonyms or []),
        column.physical_name,
        column.physical_comment,
    ) if str(value or "").strip())


def _data_type_family(value: str) -> str:
    normalized = str(value or "").lower()
    if any(token in normalized for token in ("char", "text", "enum", "set", "json")):
        return "text"
    if any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "real", "bit")):
        return "number"
    if any(token in normalized for token in ("date", "time", "year")):
        return "datetime"
    if "bool" in normalized or "tinyint(1)" in normalized:
        return "boolean"
    if any(token in normalized for token in ("binary", "blob")):
        return "binary"
    return normalized.split("(", 1)[0].strip() or "unknown"


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def _rule_by_table(definition: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(rule["table_id"]): deepcopy(rule)
        for rule in definition.get("tables") or []
        if int(rule.get("table_id") or 0) > 0
    }


def _definition_with_rules(definition: dict[str, Any], rules: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        **deepcopy(definition),
        "tables": [rules[table_id] for table_id in sorted(rules)],
    }


def _simple_condition(scope: Any) -> dict[str, Any] | None:
    if not isinstance(scope, dict) or scope.get("type") != "custom":
        return None
    condition = scope.get("condition")
    if not isinstance(condition, dict) or "rules" in condition or condition.get("value_source"):
        return None
    operator = str(condition.get("operator") or "").strip().lower()
    try:
        column_id = int(condition.get("column_id") or 0)
    except (TypeError, ValueError):
        return None
    if not column_id or operator not in SUPPORTED_ROW_OPERATORS:
        return None
    result = {"column_id": column_id, "operator": operator}
    if operator not in {"is null", "is not null"}:
        result["value"] = deepcopy(condition.get("value"))
    return result


def extract_permission_changes(
    before_rule: dict[str, Any] | None,
    after_rule: dict[str, Any] | None,
) -> list[PermissionChange]:
    """Return only field visibility and one-clause custom row changes."""
    before = before_rule or {}
    after = after_rule or {}
    changes: list[PermissionChange] = []
    before_hidden = {int(value) for value in before.get("hidden_column_ids") or []}
    after_hidden = {int(value) for value in after.get("hidden_column_ids") or []}
    for column_id in sorted(before_hidden ^ after_hidden):
        old_hidden = column_id in before_hidden
        new_hidden = column_id in after_hidden
        changes.append(PermissionChange(
            kind="field_visibility",
            source_column_id=column_id,
            before=old_hidden,
            after=new_hidden,
            restrictive=new_hidden,
        ))

    before_scope = before.get("row_scope") or {"type": "all"}
    after_scope = after.get("row_scope") or {"type": "all"}
    before_condition = _simple_condition(before_scope)
    after_condition = _simple_condition(after_scope)
    if _stable_json(before_scope) != _stable_json(after_scope):
        if before_condition and after_condition:
            source_column_id = int(after_condition["column_id"])
        elif after_condition and before_scope.get("type") == "all":
            source_column_id = int(after_condition["column_id"])
        elif before_condition and after_scope.get("type") == "all":
            source_column_id = int(before_condition["column_id"])
        else:
            return changes
        changes.append(PermissionChange(
            kind="row_scope",
            source_column_id=source_column_id,
            before=before_condition,
            after=after_condition,
            restrictive=after_condition is not None,
        ))
    return changes


def _translated_condition(condition: dict[str, Any] | None, target_column_id: int) -> dict[str, Any] | None:
    if condition is None:
        return None
    return {**deepcopy(condition), "column_id": target_column_id}


def _condition_scope(condition: dict[str, Any] | None) -> dict[str, Any]:
    return {"type": "all"} if condition is None else {"type": "custom", "condition": condition}


def _condition_matches(scope: Any, condition: dict[str, Any] | None) -> bool:
    return _stable_json(scope or {"type": "all"}) == _stable_json(_condition_scope(condition))


def _candidate_public(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "table_id": candidate["table_id"],
        "table_name": candidate["table_name"],
        "matched_fields": candidate["matched_fields"],
        "adjustments": candidate["adjustments"],
        "apply_status": candidate["apply_status"],
        "expands_access": candidate["expands_access"],
        "default_selected": candidate["default_selected"],
    }


class SemanticAccessSimilarSuggestionService:
    async def preview(
        self,
        workspace_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = await self._build_snapshot(workspace_id, actor_id, payload)
        return {
            "batch_fingerprint": snapshot["batch_fingerprint"],
            "binding_revision": snapshot["binding_revision"],
            "active_version_id": snapshot["active_version_id"],
            "bootstrap_revision": snapshot["bootstrap_revision"],
            "schema_fingerprint": snapshot["schema_fingerprint"],
            "candidates": [_candidate_public(item) for item in snapshot["candidates"]],
        }

    async def apply(
        self,
        workspace_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        selected_ids = {str(value) for value in payload.get("candidate_ids") or []}
        if not selected_ids:
            raise SemanticBindingError("请至少选择一张表", code="suggestion_selection_empty")

        snapshot = await self._build_snapshot(workspace_id, actor_id, payload)
        if snapshot["batch_fingerprint"] != payload.get("batch_fingerprint"):
            raise SemanticBindingError(
                "相似修改建议已经过期，请重新保存后再试",
                code="suggestion_stale",
                status_code=409,
            )
        candidates_by_id = {item["candidate_id"]: item for item in snapshot["candidates"]}
        if not selected_ids.issubset(candidates_by_id):
            raise SemanticBindingError(
                "所选建议已经发生变化，请重新保存后再试",
                code="suggestion_stale",
                status_code=409,
            )
        selected = [candidates_by_id[candidate_id] for candidate_id in sorted(selected_ids)]

        db = get_async_db_manager()
        async with db.session_scope() as session:
            binding = (await session.execute(select(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.workspace_id == workspace_id,
                SemanticPolicyBindingModel.datasource_id == int(payload["datasource_id"]),
                SemanticPolicyBindingModel.target_type == "org_unit",
                SemanticPolicyBindingModel.target_id == str(payload["org_unit_id"]),
            ).with_for_update())).scalar_one_or_none()
            if not binding or int(binding.revision or 0) != snapshot["binding_revision"]:
                raise SemanticBindingError(
                    "部门权限已经被修改，请刷新后重试",
                    code="suggestion_revision_conflict",
                    status_code=409,
                )
            await _assert_actor_scope_v3(
                session, workspace_id, actor_id, "semantic_access:manage",
                "org_unit", str(payload["org_unit_id"]), bool(binding.include_descendants),
            )
            datasource = await session.get(SemanticDatasourceModel, int(payload["datasource_id"]))
            if not datasource or datasource.workspace_id != workspace_id:
                raise SemanticBindingError("数据源不存在", code="datasource_not_found", status_code=404)
            if datasource.schema_fingerprint != snapshot["schema_fingerprint"]:
                raise SemanticBindingError(
                    "语义模型已经变化，请重新保存后再试",
                    code="schema_changed",
                    status_code=409,
                )

            active_version = await session.get(SemanticPolicyVersionModel, binding.active_version_id)
            if not active_version or int(active_version.id) != snapshot["active_version_id"]:
                raise SemanticBindingError(
                    "部门权限已经被修改，请刷新后重试",
                    code="suggestion_revision_conflict",
                    status_code=409,
                )
            active_definition = deepcopy(active_version.definition_json or {"tables": []})
            active_rules = _rule_by_table(active_definition)

            run = None
            target = None
            bootstrap_run_id = payload.get("bootstrap_run_id")
            if bootstrap_run_id is not None:
                run = (await session.execute(select(SemanticAccessBootstrapRunModel).where(
                    SemanticAccessBootstrapRunModel.id == int(bootstrap_run_id),
                    SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                    SemanticAccessBootstrapRunModel.datasource_id == int(payload["datasource_id"]),
                ).with_for_update())).scalar_one_or_none()
                if not run or int(run.revision or 0) != int(snapshot["bootstrap_revision"] or -1):
                    raise SemanticBindingError(
                        "首次配置草稿已经变化，请重新保存后再试",
                        code="suggestion_bootstrap_conflict",
                        status_code=409,
                    )
                target = (await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                    SemanticAccessBootstrapTargetModel.run_id == run.id,
                    SemanticAccessBootstrapTargetModel.target_type == "org_unit",
                    SemanticAccessBootstrapTargetModel.target_id == str(payload["org_unit_id"]),
                ).with_for_update())).scalar_one_or_none()
                if not target:
                    raise SemanticBindingError(
                        "首次配置草稿不存在",
                        code="suggestion_bootstrap_conflict",
                        status_code=409,
                    )

            direct_ids: list[int] = []
            review_ids: list[int] = []
            for candidate in selected:
                table_id = int(candidate["table_id"])
                if candidate["apply_status"] == "direct":
                    active_rules[table_id] = deepcopy(candidate["patched_rule"])
                    direct_ids.append(table_id)
                else:
                    review_ids.append(table_id)

            version = None
            if direct_ids:
                version, _compilation, _warnings = await persist_target_policy_version_in_session(
                    session,
                    binding,
                    _definition_with_rules(active_definition, active_rules),
                    actor_id,
                    source_text=f"相似权限批量调整：{','.join(map(str, sorted(direct_ids)))}",
                    source_type="similar_suggestion",
                    source_ref=f"source_table:{int(payload['source_table_id'])}",
                    confirm_warnings=True,
                )

            if target:
                draft_definition = deepcopy(target.definition_json or {"tables": []})
                draft_rules = _rule_by_table(draft_definition)
                for candidate in selected:
                    draft_rules[int(candidate["table_id"])] = deepcopy(candidate["patched_rule"])
                # Keep already-published tables aligned with the newly active version too.
                for table_id in direct_ids:
                    draft_rules[table_id] = deepcopy(active_rules[table_id])
                target.definition_json = _definition_with_rules(draft_definition, draft_rules)
                target.edited_by = actor_id
                if version:
                    target.base_binding_id = binding.id
                    target.base_revision = binding.revision
                    target.validation_json = {
                        **dict(target.validation_json or {}),
                        "base_active_version_id": version.id,
                    }
                run.revision += 1

            authorization_revision = None
            if direct_ids:
                authorization_revision = await bump_authorization_revision(session, workspace_id)
            audit = await record_authorization_audit(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="semantic_policy.similar_suggestion.apply",
                target_type="semantic_org_unit",
                target_id=str(payload["org_unit_id"]),
                before={
                    "binding_revision": snapshot["binding_revision"],
                    "bootstrap_revision": snapshot["bootstrap_revision"],
                },
                after={
                    "direct_table_ids": sorted(direct_ids),
                    "review_table_ids": sorted(review_ids),
                    "binding_revision": binding.revision,
                    "bootstrap_revision": int(run.revision) if run else None,
                    "authorization_revision": authorization_revision,
                },
                reason=f"source_table:{int(payload['source_table_id'])}",
            )
            await session.flush()
            return {
                "binding_revision": int(binding.revision or 0),
                "active_version_id": int(version.id) if version else int(active_version.id),
                "bootstrap_revision": int(run.revision) if run else None,
                "direct_table_ids": sorted(direct_ids),
                "review_table_ids": sorted(review_ids),
                "audit_id": int(audit.id),
            }

    async def _build_snapshot(
        self,
        workspace_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        datasource_id = int(payload.get("datasource_id") or 0)
        org_unit_id = str(payload.get("org_unit_id") or "")
        source_table_id = int(payload.get("source_table_id") or 0)
        if not datasource_id or not org_unit_id or not source_table_id:
            raise SemanticBindingError("建议上下文不完整", code="suggestion_context_invalid")

        db = get_async_db_manager()
        async with db.session_scope() as session:
            binding = (await session.execute(select(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.workspace_id == workspace_id,
                SemanticPolicyBindingModel.datasource_id == datasource_id,
                SemanticPolicyBindingModel.target_type == "org_unit",
                SemanticPolicyBindingModel.target_id == org_unit_id,
            ))).scalar_one_or_none()
            if not binding or not binding.active_version_id:
                raise SemanticBindingError("部门权限尚未生效", code="binding_not_found", status_code=404)
            await _assert_actor_scope_v3(
                session, workspace_id, actor_id, "semantic_access:manage",
                "org_unit", org_unit_id, bool(binding.include_descendants),
            )
            if payload.get("expected_binding_revision") is not None and int(payload["expected_binding_revision"]) != int(binding.revision or 0):
                raise SemanticBindingError(
                    "部门权限已经变化，请刷新后重试",
                    code="suggestion_revision_conflict",
                    status_code=409,
                )
            datasource = await session.get(SemanticDatasourceModel, datasource_id)
            active_version = await session.get(SemanticPolicyVersionModel, binding.active_version_id)
            if not datasource or not active_version:
                raise SemanticBindingError("权限版本不存在", code="binding_not_found", status_code=404)
            active_definition = deepcopy(active_version.definition_json or {"tables": []})

            table_rows = list((await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource_id,
                SemanticTableModel.status == "confirmed",
                SemanticTableModel.is_queryable == True,  # noqa: E712
                SemanticTableModel.sync_state == "current",
            ))).scalars())
            column_rows = list((await session.execute(select(SemanticColumnModel).where(
                SemanticColumnModel.workspace_id == workspace_id,
                SemanticColumnModel.datasource_id == datasource_id,
                SemanticColumnModel.status == "confirmed",
                SemanticColumnModel.is_queryable == True,  # noqa: E712
                SemanticColumnModel.sync_state == "current",
            ))).scalars())
            tables = [SimpleNamespace(
                id=int(row.id),
                business_name=row.business_name,
                physical_name=row.physical_name,
            ) for row in table_rows]
            columns = [SimpleNamespace(
                id=int(row.id),
                table_id=int(row.table_id),
                business_name=row.business_name,
                physical_name=row.physical_name,
                data_type=row.data_type,
                description=row.description,
                physical_comment=row.physical_comment,
                synonyms=list(row.synonyms or []),
            ) for row in column_rows]
            binding_revision = int(binding.revision or 0)
            active_version_id = int(active_version.id)
            schema_fingerprint = datasource.schema_fingerprint

            run = None
            target = None
            bootstrap_revision = None
            pending_table_ids: set[int] = set()
            bootstrap_run_id = payload.get("bootstrap_run_id")
            candidate_definition = active_definition
            if bootstrap_run_id is not None:
                run = await session.get(SemanticAccessBootstrapRunModel, int(bootstrap_run_id))
                if (
                    not run or run.workspace_id != workspace_id or run.datasource_id != datasource_id
                    or run.status not in {"review_ready", "partial"}
                ):
                    raise SemanticBindingError(
                        "首次配置草稿已经失效",
                        code="suggestion_bootstrap_conflict",
                        status_code=409,
                    )
                if payload.get("expected_bootstrap_revision") is not None and int(payload["expected_bootstrap_revision"]) != int(run.revision or 0):
                    raise SemanticBindingError(
                        "首次配置草稿已经变化，请刷新后重试",
                        code="suggestion_bootstrap_conflict",
                        status_code=409,
                    )
                target = (await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                    SemanticAccessBootstrapTargetModel.run_id == run.id,
                    SemanticAccessBootstrapTargetModel.target_type == "org_unit",
                    SemanticAccessBootstrapTargetModel.target_id == org_unit_id,
                ))).scalar_one_or_none()
                if not target:
                    raise SemanticBindingError("首次配置草稿不存在", code="suggestion_bootstrap_conflict", status_code=409)
                candidate_definition = deepcopy(target.definition_json or {"tables": []})
                bootstrap_revision = int(run.revision or 0)
                pending_table_ids = await self._pending_table_ids(
                    session, run, target, int(org_unit_id),
                )

        rules = _rule_by_table(candidate_definition)
        source_after_rule = _rule_by_table(active_definition).get(source_table_id)
        source_before_rule = payload.get("source_before_rule")
        changes = extract_permission_changes(source_before_rule, source_after_rule)
        if not changes:
            candidates: list[dict[str, Any]] = []
        else:
            candidates = await self._generate_candidates(
                workspace_id,
                tables,
                columns,
                rules,
                source_table_id,
                changes,
                pending_table_ids,
            )
        context = {
            "workspace_id": workspace_id,
            "datasource_id": datasource_id,
            "org_unit_id": org_unit_id,
            "source_table_id": source_table_id,
            "source_before_rule": source_before_rule,
            "binding_revision": binding_revision,
            "active_version_id": active_version_id,
            "bootstrap_run_id": int(run.id) if run else None,
            "bootstrap_revision": bootstrap_revision,
            "schema_fingerprint": schema_fingerprint,
            "candidates": [
                {
                    "candidate_id": item["candidate_id"],
                    "table_id": item["table_id"],
                    "patched_rule": item["patched_rule"],
                    "apply_status": item["apply_status"],
                }
                for item in candidates
            ],
        }
        return {
            **context,
            # Candidate discovery can legitimately degrade to exact-only when the
            # embedding provider is slow.  Keep the batch identity tied to the
            # policy/schema context; selected candidate IDs are revalidated by
            # apply against the freshly rebuilt candidate set below.
            "batch_fingerprint": _fingerprint({
                key: value for key, value in context.items() if key != "candidates"
            }),
            "candidates": candidates,
        }

    async def _pending_table_ids(
        self,
        session,
        run: SemanticAccessBootstrapRunModel,
        target: SemanticAccessBootstrapTargetModel,
        org_unit_id: int,
    ) -> set[int]:
        pending = {
            int(item.get("table_id") or 0)
            for item in (target.candidates_json or [])
            if item.get("review_state", "pending") not in {"accepted", "modified", "rejected"}
            and (
                bool(item.get("selected"))
                or str(item.get("decision") or "") == "visible"
            )
        }
        if run.evidence_set_id:
            rows = list((await session.execute(select(SemanticAccessEvidenceRelationModel).where(
                SemanticAccessEvidenceRelationModel.evidence_set_id == run.evidence_set_id,
                SemanticAccessEvidenceRelationModel.org_unit_id == org_unit_id,
            ))).scalars())
            granted_tables = {
                int(row.table_id) for row in rows
                if row.asset_type == "table" and (row.access_level in {"visible", "partial"} or row.access_decision == "visible")
            }
            for row in rows:
                if row.review_status in APPROVED_REVIEW_STATUSES:
                    continue
                if row.asset_type == "table" and int(row.table_id) in granted_tables:
                    pending.add(int(row.table_id))
                if row.asset_type == "column" and int(row.table_id) in granted_tables and (row.field_decision == "visible" or row.access_decision == "visible"):
                    pending.add(int(row.table_id))
        return {value for value in pending if value > 0}

    async def _generate_candidates(
        self,
        workspace_id: str,
        tables: list[SemanticTableModel],
        columns: list[SemanticColumnModel],
        rules: dict[int, dict[str, Any]],
        source_table_id: int,
        changes: list[PermissionChange],
        pending_table_ids: set[int],
    ) -> list[dict[str, Any]]:
        columns_by_id = {int(column.id): column for column in columns}
        columns_by_table: dict[int, list[SemanticColumnModel]] = {}
        for column in columns:
            columns_by_table.setdefault(int(column.table_id), []).append(column)
        table_by_id = {int(table.id): table for table in tables}
        relevant_rules = {
            table_id: rule for table_id, rule in rules.items()
            if table_id != source_table_id
            and table_id in table_by_id
            and rule.get("decision", "visible") == "visible"
        }
        source_columns = {
            change.source_column_id: columns_by_id.get(change.source_column_id)
            for change in changes
        }
        source_columns = {key: value for key, value in source_columns.items() if value is not None}
        matches = await self._resolve_column_matches(
            source_columns,
            {
                table_id: columns_by_table.get(table_id, [])
                for table_id in relevant_rules
            },
        )

        candidates: list[dict[str, Any]] = []
        for table_id, original_rule in relevant_rules.items():
            patched = deepcopy(original_rule)
            matched_fields: list[str] = []
            adjustments: list[str] = []
            expands_access = False
            applied_any = False
            for change in changes:
                target_column = matches.get((change.source_column_id, table_id))
                if not target_column:
                    continue
                if change.kind == "field_visibility":
                    hidden = {int(value) for value in patched.get("hidden_column_ids") or []}
                    if (int(target_column.id) in hidden) != bool(change.before):
                        continue
                    if change.after:
                        hidden.add(int(target_column.id))
                        adjustments.append(f"隐藏字段“{target_column.business_name}”")
                    else:
                        hidden.discard(int(target_column.id))
                        adjustments.append(f"开放字段“{target_column.business_name}”")
                        expands_access = True
                    patched["hidden_column_ids"] = sorted(hidden)
                    matched_fields.append(str(target_column.business_name))
                    applied_any = True
                    continue

                before_condition = _translated_condition(change.before, int(target_column.id))
                after_condition = _translated_condition(change.after, int(target_column.id))
                if not _condition_matches(patched.get("row_scope"), before_condition):
                    continue
                if after_condition and not await self._values_exist(
                    workspace_id,
                    table_by_id[table_id],
                    target_column,
                    after_condition,
                ):
                    continue
                patched["row_scope"] = _condition_scope(after_condition)
                matched_fields.append(str(target_column.business_name))
                if after_condition:
                    adjustments.append(self._row_adjustment(target_column.business_name, after_condition))
                else:
                    adjustments.append("取消行级限制")
                    expands_access = True
                applied_any = True
            if not applied_any or _stable_json(patched) == _stable_json(original_rule):
                continue
            identity = {
                "table_id": table_id,
                "patched_rule": patched,
                "apply_status": "review" if table_id in pending_table_ids else "direct",
            }
            candidates.append({
                **identity,
                "candidate_id": _fingerprint(identity)[:24],
                "table_name": table_by_id[table_id].business_name,
                "matched_fields": list(dict.fromkeys(matched_fields)),
                "adjustments": adjustments,
                "expands_access": expands_access,
                "default_selected": not expands_access,
            })
        return sorted(candidates, key=lambda item: (item["expands_access"], item["table_name"]))[:MAX_TABLE_SUGGESTIONS]

    async def _best_match(
        self,
        source: SemanticColumnModel,
        candidates: list[SemanticColumnModel],
    ) -> SemanticColumnModel | None:
        matches = await self._resolve_column_matches(
            {0: source},
            {0: candidates},
        )
        return matches.get((0, 0))

    def _exact_match(
        self,
        source: SemanticColumnModel,
        compatible: list[SemanticColumnModel],
    ) -> tuple[SemanticColumnModel | None, bool]:
        source_terms = _column_terms(source)
        exact = [candidate for candidate in compatible if source_terms.intersection(_column_terms(candidate))]
        if len(exact) == 1:
            return exact[0], True
        if len(exact) > 1:
            physical = [candidate for candidate in exact if _normalize_term(candidate.physical_name) == _normalize_term(source.physical_name)]
            return (physical[0] if len(physical) == 1 else None), True
        return None, False

    async def _resolve_column_matches(
        self,
        source_columns: dict[int, SemanticColumnModel],
        columns_by_table: dict[int, list[SemanticColumnModel]],
    ) -> dict[tuple[int, int], SemanticColumnModel]:
        """Resolve all table matches with one bounded embedding request.

        Exact and synonym matches are resolved synchronously for every table.
        Only unresolved semantic candidates consume the per-source budget, and
        an embedding timeout preserves all exact results.
        """
        matches: dict[tuple[int, int], SemanticColumnModel] = {}
        semantic_groups: dict[
            tuple[int, int],
            list[SemanticColumnModel],
        ] = {}

        for source_id, source in sorted(source_columns.items()):
            remaining_budget = MAX_FIELD_CANDIDATES
            for table_id, table_columns in sorted(columns_by_table.items()):
                compatible = [
                    candidate for candidate in table_columns
                    if _data_type_family(candidate.data_type) == _data_type_family(source.data_type)
                ]
                if not compatible:
                    continue
                exact, exact_seen = self._exact_match(source, compatible)
                if exact is not None:
                    matches[(source_id, table_id)] = exact
                    continue
                # Multiple exact terms without a unique physical-name winner are
                # intentionally ambiguous and must not fall through to embedding.
                if exact_seen or remaining_budget <= 0:
                    continue
                selected = compatible[:remaining_budget]
                if selected:
                    semantic_groups[(source_id, table_id)] = selected
                    remaining_budget -= len(selected)

        if not semantic_groups:
            return matches

        text_items: list[str] = []
        text_index: dict[tuple[str, int], int] = {}

        def add_text(key: tuple[str, int], value: str) -> None:
            if key in text_index:
                return
            text_index[key] = len(text_items)
            text_items.append(value)

        for (source_id, _table_id), candidates in semantic_groups.items():
            source = source_columns[source_id]
            add_text(("source", source_id), _column_text(source))
            for candidate in candidates:
                add_text(("candidate", int(candidate.id)), _column_text(candidate))

        try:
            vectors = await asyncio.wait_for(
                get_async_embedding().embed_texts(text_items),
                timeout=SEMANTIC_EMBEDDING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.info(
                "Similar permission embedding timed out after %.1fs; exact matches only",
                SEMANTIC_EMBEDDING_TIMEOUT_SECONDS,
            )
            return matches
        except Exception as exc:
            logger.info("Similar permission embedding unavailable; exact matching only: %s", type(exc).__name__)
            return matches
        if len(vectors) != len(text_items):
            return matches

        for (source_id, table_id), candidates in semantic_groups.items():
            source_vector = vectors[text_index[("source", source_id)]]
            ranked = sorted(
                (
                    (
                        _cosine(source_vector, vectors[text_index[("candidate", int(candidate.id))]]),
                        candidate,
                    )
                    for candidate in candidates
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if not ranked or ranked[0][0] < SEMANTIC_THRESHOLD:
                continue
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < AMBIGUITY_MARGIN:
                continue
            matches[(source_id, table_id)] = ranked[0][1]
        return matches

    async def _values_exist(
        self,
        workspace_id: str,
        table: SemanticTableModel,
        column: SemanticColumnModel,
        condition: dict[str, Any],
    ) -> bool:
        operator = str(condition.get("operator") or "").lower()
        if operator in {"is null", "is not null"}:
            return True
        value = condition.get("value")
        values = value if isinstance(value, list) else [value]
        values = list(dict.fromkeys(_stable_json(item) for item in values))
        decoded_values = [json.loads(item) for item in values]
        if not decoded_values:
            return False
        config = await get_workspace_db_config_async(workspace_id)
        if not config:
            return False
        connection_url = config.get_readonly_connection_url()
        if not connection_url:
            return False

        def check() -> bool:
            engine = create_mysql_engine(connection_url, pool_pre_ping=True)
            try:
                table_name = "`" + str(table.physical_name).replace("`", "``") + "`"
                column_name = "`" + str(column.physical_name).replace("`", "``") + "`"
                with engine.connect() as connection:
                    for candidate_value in decoded_values:
                        row = connection.execute(
                            text(f"SELECT 1 FROM {table_name} WHERE {column_name} = :value LIMIT 1"),
                            {"value": candidate_value},
                        ).first()
                        if row is None:
                            return False
                return True
            finally:
                engine.dispose()

        try:
            return await asyncio.to_thread(check)
        except Exception as exc:
            logger.info("Similar permission row value validation unavailable: %s", type(exc).__name__)
            return False

    def _row_adjustment(self, field_name: str, condition: dict[str, Any]) -> str:
        operator = str(condition.get("operator") or "")
        if operator in {"is null", "is not null"}:
            return f"设置行权限：{field_name} {operator}"
        value = condition.get("value")
        rendered = "、".join(str(item) for item in value) if isinstance(value, list) else str(value)
        return f"设置行权限：{field_name} {operator} {rendered}"


_service: SemanticAccessSimilarSuggestionService | None = None


def get_semantic_access_similar_suggestion_service() -> SemanticAccessSimilarSuggestionService:
    global _service
    if _service is None:
        _service = SemanticAccessSimilarSuggestionService()
    return _service
