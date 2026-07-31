import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.workspace_readiness_service import (
    DBReadiness,
    KnowledgeReadiness,
    WorkspaceReadiness,
    WorkspaceReadinessService,
)


def _readiness(has_db: bool, has_knowledge: bool) -> WorkspaceReadiness:
    return WorkspaceReadiness(
        has_db=has_db,
        has_knowledge=has_knowledge,
        db=DBReadiness(connected=has_db),
        knowledge=KnowledgeReadiness(total_count=1 if has_knowledge else 0),
        reasons=[],
    )


def test_readiness_from_state_defaults_available_when_missing():
    readiness = WorkspaceReadinessService.readiness_from_state({}, default_available=True)

    assert readiness.has_db is True
    assert readiness.has_knowledge is True
    assert readiness.reasons == []


def test_readiness_from_state_uses_snapshot_fields():
    readiness = WorkspaceReadinessService.readiness_from_state(
        {
            "has_db_connection": False,
            "has_knowledge_base": True,
            "readiness_reasons": ["NO_DB_CONNECTION"],
        },
        default_available=True,
    )

    assert readiness.has_db is False
    assert readiness.has_knowledge is True
    assert readiness.reasons == ["NO_DB_CONNECTION"]


def test_check_worker_availability_blocks_sql_worker_without_db():
    readiness = _readiness(has_db=False, has_knowledge=True)

    result = WorkspaceReadinessService.check_worker_availability("sql_worker", readiness)
    assert result.allowed is False
    assert result.reason_code == WorkspaceReadinessService.REASON_WORKER_DB_REQUIRED


def test_check_worker_availability_blocks_doc_worker_without_knowledge():
    readiness = _readiness(has_db=True, has_knowledge=False)

    result = WorkspaceReadinessService.check_worker_availability("doc_worker", readiness)
    assert result.allowed is False
    assert result.reason_code == WorkspaceReadinessService.REASON_WORKER_KNOWLEDGE_REQUIRED


def test_check_worker_availability_allows_chart_when_only_knowledge_available():
    readiness = _readiness(has_db=False, has_knowledge=True)

    result = WorkspaceReadinessService.check_worker_availability("chart_worker", readiness)
    assert result.allowed is True


def test_check_worker_availability_blocks_chart_without_any_source():
    readiness = _readiness(has_db=False, has_knowledge=False)

    result = WorkspaceReadinessService.check_worker_availability("chart_worker", readiness)
    assert result.allowed is False
    assert result.reason_code == WorkspaceReadinessService.REASON_WORKER_SOURCE_REQUIRED


def test_build_sources_section_matrix():
    both = WorkspaceReadinessService.build_sources_section(_readiness(True, True))
    assert "doc_worker, sql_worker, chart_worker, office_worker" in both

    only_db = WorkspaceReadinessService.build_sources_section(_readiness(True, False))
    assert "sql_worker, chart_worker, office_worker" in only_db
    assert "禁止规划 doc_worker" in only_db

    only_knowledge = WorkspaceReadinessService.build_sources_section(_readiness(False, True))
    assert "当前允许的 Worker: doc_worker, chart_worker, office_worker" in only_knowledge

    none = WorkspaceReadinessService.build_sources_section(_readiness(False, False))
    assert "当前允许的 Worker: finish" in none
