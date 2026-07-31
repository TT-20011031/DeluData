from app.services.semantic_schema_diff import (
    diff_schema_snapshots,
    normalize_schema_snapshot,
    schema_fingerprint,
    summarize_diff,
)


def _snapshot(column_type: str = "int", *, include_column: bool = True, comment: str = ""):
    columns = []
    if include_column:
        columns.append(
            {
                "name": "amount",
                "type": column_type,
                "comment": comment,
                "primary_key": False,
                "indexed": True,
                "ordinal_position": 0,
            }
        )
    return normalize_schema_snapshot(
        [{"name": "orders", "kind": "table", "comment": "订单", "columns": columns}]
    )


def test_schema_fingerprint_is_stable_for_input_order():
    first = normalize_schema_snapshot(
        [
            {"name": "b", "columns": [{"name": "id", "type": "INT"}]},
            {"name": "a", "columns": [{"name": "name", "type": "VARCHAR(20)"}]},
        ]
    )
    second = normalize_schema_snapshot(
        [
            {"name": "a", "columns": [{"name": "name", "type": "VARCHAR(20)"}]},
            {"name": "b", "columns": [{"name": "id", "type": "INT"}]},
        ]
    )

    assert schema_fingerprint(first) == schema_fingerprint(second)
    assert diff_schema_snapshots(first, second) == []


def test_column_type_change_is_blocking_but_comment_change_is_not():
    type_diff = diff_schema_snapshots(_snapshot("int"), _snapshot("decimal(12,2)"))
    comment_diff = diff_schema_snapshots(_snapshot(comment=""), _snapshot(comment="含税金额"))

    assert len(type_diff) == 1
    assert type_diff[0]["blocking"] is True
    assert type_diff[0]["severity"] == "critical"
    assert len(comment_diff) == 1
    assert comment_diff[0]["blocking"] is False
    assert comment_diff[0]["severity"] == "info"


def test_removed_column_is_reported_without_guessing_rename():
    diff = diff_schema_snapshots(_snapshot(), _snapshot(include_column=False))

    assert diff == [
        {
            "object_type": "column",
            "change_type": "removed",
            "physical_identity": "orders.amount",
            "table_name": "orders",
            "before": {
                "name": "amount",
                "type": "int",
                "comment": "",
                "primary_key": False,
                "indexed": True,
                "ordinal_position": 0,
            },
            "after": None,
            "severity": "critical",
            "blocking": True,
        }
    ]
    assert summarize_diff(diff) == {"added": 0, "modified": 0, "removed": 1, "blocking": 1, "total": 1}
