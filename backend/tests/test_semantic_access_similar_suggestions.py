import asyncio
from types import SimpleNamespace

import pytest

from app.services.semantic_access_similar_suggestion_service import (
    PermissionChange,
    SemanticAccessSimilarSuggestionService,
    _data_type_family,
    extract_permission_changes,
)


def _rule(*, hidden=(), scope=None):
    return {
        "table_id": 1,
        "decision": "visible",
        "hidden_column_ids": list(hidden),
        "hidden_metric_ids": [],
        "row_scope": scope or {"type": "all"},
    }


def test_extracts_field_visibility_changes_in_both_directions():
    changes = extract_permission_changes(_rule(hidden=(2,)), _rule(hidden=(3,)))

    assert changes == [
        PermissionChange("field_visibility", 2, True, False, False),
        PermissionChange("field_visibility", 3, False, True, True),
    ]


def test_extracts_supported_simple_row_condition_change():
    before = _rule(scope={
        "type": "custom",
        "condition": {"column_id": 4, "operator": "=", "value": "胡国粉"},
    })
    after = _rule(scope={
        "type": "custom",
        "condition": {"column_id": 4, "operator": "in", "value": ["胡国粉", "王芳"]},
    })

    changes = extract_permission_changes(before, after)

    assert len(changes) == 1
    assert changes[0].kind == "row_scope"
    assert changes[0].before == {"column_id": 4, "operator": "=", "value": "胡国粉"}
    assert changes[0].after == {"column_id": 4, "operator": "in", "value": ["胡国粉", "王芳"]}
    assert changes[0].restrictive is True


def test_ignores_complex_and_unsupported_row_conditions():
    complex_scope = {
        "type": "custom",
        "condition": {
            "op": "AND",
            "rules": [
                {"column_id": 4, "operator": "=", "value": "胡国粉"},
                {"column_id": 5, "operator": "contains", "value": "生产"},
            ],
        },
    }

    assert extract_permission_changes(_rule(), _rule(scope=complex_scope)) == []
    assert extract_permission_changes(
        _rule(),
        _rule(scope={"type": "custom", "condition": {"column_id": 4, "operator": "contains", "value": "胡"}}),
    ) == []


@pytest.mark.parametrize(
    ("left", "right", "compatible"),
    [
        ("varchar(128)", "text", True),
        ("int", "decimal(10,2)", True),
        ("datetime", "date", True),
        ("varchar", "int", False),
    ],
)
def test_data_type_family_compatibility(left, right, compatible):
    assert (_data_type_family(left) == _data_type_family(right)) is compatible


@pytest.mark.asyncio
async def test_exact_synonym_match_does_not_need_embedding():
    service = SemanticAccessSimilarSuggestionService()
    source = SimpleNamespace(
        business_name="跟进人",
        physical_name="follow_user",
        synonyms=["业务跟进人员"],
        description="负责订单跟进的员工",
        physical_comment="",
        data_type="varchar(64)",
    )
    target = SimpleNamespace(
        business_name="业务跟进人员",
        physical_name="owner_name",
        synonyms=[],
        description="",
        physical_comment="",
        data_type="text",
    )

    assert await service._best_match(source, [target]) is target


@pytest.mark.asyncio
async def test_ambiguous_exact_matches_are_rejected():
    service = SemanticAccessSimilarSuggestionService()
    source = SimpleNamespace(
        business_name="跟进人",
        physical_name="follow_user",
        synonyms=[],
        description="",
        physical_comment="",
        data_type="varchar",
    )
    candidates = [
        SimpleNamespace(
            business_name="跟进人",
            physical_name="primary_owner",
            synonyms=[],
            description="",
            physical_comment="",
            data_type="varchar",
        ),
        SimpleNamespace(
            business_name="跟进人",
            physical_name="secondary_owner",
            synonyms=[],
            description="",
            physical_comment="",
            data_type="varchar",
        ),
    ]

    assert await service._best_match(source, candidates) is None


@pytest.mark.asyncio
async def test_semantic_matches_for_multiple_tables_use_one_embedding_call(monkeypatch):
    service = SemanticAccessSimilarSuggestionService()
    source = SimpleNamespace(
        id=1,
        business_name="业务负责人",
        physical_name="business_owner",
        synonyms=[],
        description="负责业务交付的员工",
        physical_comment="",
        data_type="varchar(64)",
    )
    owner_a = SimpleNamespace(
        id=2,
        business_name="项目主理人员",
        physical_name="project_lead",
        synonyms=[],
        description="承担项目交付职责的员工",
        physical_comment="",
        data_type="text",
    )
    noise_a = SimpleNamespace(
        id=3,
        business_name="项目日期",
        physical_name="project_date",
        synonyms=[],
        description="项目建立日期",
        physical_comment="",
        data_type="varchar(32)",
    )
    owner_b = SimpleNamespace(
        id=4,
        business_name="任务经办员工",
        physical_name="task_operator",
        synonyms=[],
        description="承担任务交付职责的员工",
        physical_comment="",
        data_type="varchar(32)",
    )
    noise_b = SimpleNamespace(
        id=5,
        business_name="任务备注",
        physical_name="task_note",
        synonyms=[],
        description="任务补充信息",
        physical_comment="",
        data_type="text",
    )

    calls = []

    async def fake_embed(texts):
        calls.append(texts)
        return [
            [1.0, 0.0] if "员工" in text and ("交付" in text or "经办" in text) else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr(
        "app.services.semantic_access_similar_suggestion_service.get_async_embedding",
        lambda: SimpleNamespace(embed_texts=fake_embed),
    )

    matches = await service._resolve_column_matches(
        {1: source},
        {2: [owner_a, noise_a], 3: [owner_b, noise_b]},
    )

    assert len(calls) == 1
    assert matches[(1, 2)] is owner_a
    assert matches[(1, 3)] is owner_b


@pytest.mark.asyncio
async def test_embedding_timeout_keeps_exact_matches(monkeypatch):
    service = SemanticAccessSimilarSuggestionService()
    source = SimpleNamespace(
        id=1,
        business_name="负责人",
        physical_name="owner",
        synonyms=[],
        description="",
        physical_comment="",
        data_type="varchar(64)",
    )
    exact = SimpleNamespace(
        id=2,
        business_name="责任人",
        physical_name="responsible_person",
        synonyms=["负责人"],
        description="",
        physical_comment="",
        data_type="text",
    )
    semantic = SimpleNamespace(
        id=3,
        business_name="业务主理员工",
        physical_name="business_lead",
        synonyms=[],
        description="承担业务职责",
        physical_comment="",
        data_type="varchar(32)",
    )

    async def slow_embed(_texts):
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(
        "app.services.semantic_access_similar_suggestion_service.get_async_embedding",
        lambda: SimpleNamespace(embed_texts=slow_embed),
    )
    monkeypatch.setattr(
        "app.services.semantic_access_similar_suggestion_service.SEMANTIC_EMBEDDING_TIMEOUT_SECONDS",
        0.001,
    )

    matches = await service._resolve_column_matches(
        {1: source},
        {2: [exact], 3: [semantic]},
    )

    assert matches == {(1, 2): exact}
