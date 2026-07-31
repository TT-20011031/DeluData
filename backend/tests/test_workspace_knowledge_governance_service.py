import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.config.workspace_knowledge_governance import WorkspaceKnowledgeGovernance
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
    WorkspaceKnowledgeUsage,
)


def test_default_workspace_governance_keeps_existing_behavior():
    config = WorkspaceKnowledgeGovernanceService.build_default_config("ws_demo")
    flags = WorkspaceKnowledgeGovernanceService.build_feature_flags(config)

    assert flags["knowledge_upload_enabled"] is True
    assert flags["knowledge_delete_enabled"] is True
    assert flags["knowledge_rename_enabled"] is True
    assert flags["knowledge_move_enabled"] is True
    assert flags["knowledge_create_folder_enabled"] is True
    assert flags["knowledge_storage_quota_bytes"] is None
    assert flags["knowledge_max_upload_file_size_bytes"] is None


def test_delete_action_can_be_disabled_without_affecting_other_actions():
    config = WorkspaceKnowledgeGovernance(
        workspace_id="ws_demo",
        delete_enabled=False,
    )

    blocked = WorkspaceKnowledgeGovernanceService.evaluate_action_allowed(
        config,
        "delete",
    )
    allowed = WorkspaceKnowledgeGovernanceService.evaluate_action_allowed(
        config,
        "upload",
    )

    assert blocked.allowed is False
    assert blocked.code == "knowledge_delete_disabled"
    assert blocked.message
    assert allowed.allowed is True


def test_upload_quota_blocks_when_projected_usage_exceeds_limit():
    one_gb = 1024 ** 3
    config = WorkspaceKnowledgeGovernance(
        workspace_id="ws_demo",
        storage_quota_bytes=one_gb,
    )

    decision = WorkspaceKnowledgeGovernanceService.evaluate_upload_allowed(
        config,
        used_bytes=one_gb - 128,
        incoming_bytes=256,
    )

    assert decision.allowed is False
    assert decision.code == WorkspaceKnowledgeGovernanceService.QUOTA_EXCEEDED_CODE
    assert decision.message


def test_upload_file_size_limit_blocks_oversized_file():
    ten_mb = 10 * 1024 * 1024
    config = WorkspaceKnowledgeGovernance(
        workspace_id="ws_demo",
        max_upload_file_size_bytes=ten_mb,
    )

    decision = WorkspaceKnowledgeGovernanceService.evaluate_upload_allowed(
        config,
        used_bytes=0,
        incoming_bytes=ten_mb + 1,
    )

    assert decision.allowed is False
    assert (
        decision.code
        == WorkspaceKnowledgeGovernanceService.FILE_SIZE_EXCEEDED_CODE
    )
    assert decision.message


def test_upload_file_size_limit_allows_file_at_exact_limit():
    ten_mb = 10 * 1024 * 1024

    decision = WorkspaceKnowledgeGovernanceService.evaluate_max_upload_file_size_allowed(
        ten_mb,
        incoming_bytes=ten_mb,
    )

    assert decision.allowed is True


def test_usage_payload_handles_unlimited_quota_cleanly():
    config = WorkspaceKnowledgeGovernance(workspace_id="ws_demo")
    usage = WorkspaceKnowledgeUsage(used_bytes=2048, file_count=3)

    payload = WorkspaceKnowledgeGovernanceService.build_usage_payload(config, usage)

    assert payload["storage_quota_bytes"] is None
    assert payload["storage_remaining_bytes"] is None
    assert payload["storage_usage_ratio"] is None
    assert payload["storage_used_bytes"] == 2048
    assert payload["file_count"] == 3


def test_normalize_storage_quota_treats_non_positive_values_as_unlimited():
    assert WorkspaceKnowledgeGovernanceService.normalize_storage_quota_bytes(None) is None
    assert WorkspaceKnowledgeGovernanceService.normalize_storage_quota_bytes(0) is None
    assert WorkspaceKnowledgeGovernanceService.normalize_storage_quota_bytes(-1) is None
    assert WorkspaceKnowledgeGovernanceService.normalize_storage_quota_bytes(1024) == 1024


def test_normalize_max_upload_file_size_treats_non_positive_values_as_unlimited():
    assert (
        WorkspaceKnowledgeGovernanceService.normalize_max_upload_file_size_bytes(None)
        is None
    )
    assert (
        WorkspaceKnowledgeGovernanceService.normalize_max_upload_file_size_bytes(0)
        is None
    )
    assert (
        WorkspaceKnowledgeGovernanceService.normalize_max_upload_file_size_bytes(-1)
        is None
    )
    assert (
        WorkspaceKnowledgeGovernanceService.normalize_max_upload_file_size_bytes(2048)
        == 2048
    )
