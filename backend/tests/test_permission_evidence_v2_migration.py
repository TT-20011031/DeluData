from migrations.run_permission_evidence_v2_migration import (
    _legacy_signature,
    _v2_signature,
)


def test_v2_migration_preserves_published_authorization_signature():
    legacy_assets = [{
        "asset_type": "table",
        "table_id": 10,
        "access_class": "workspace_public",
        "is_sensitive": False,
    }]
    legacy_relations = [
        {
            "asset_type": "table",
            "asset_id": 10,
            "table_id": 10,
            "org_unit_id": 1,
            "relation_role": "required_consumer",
            "access_decision": "inherit",
            "row_scope_json": {"type": "target_org"},
        },
        {
            "asset_type": "column",
            "asset_id": 100,
            "table_id": 10,
            "org_unit_id": 1,
            "relation_role": "none",
            "access_decision": "hidden",
            "row_scope_json": {"type": "all"},
        },
    ]
    v2_assets = [{
        **legacy_assets[0],
        "baseline_access": "workspace_visible",
        "requires_individual_review": False,
    }]
    v2_relations = [
        {
            **legacy_relations[0],
            "business_role": "required_consumer",
            "access_level": "partial",
            "field_decision": None,
        },
        {
            **legacy_relations[1],
            "business_role": "none",
            "access_level": None,
            "field_decision": "hidden",
        },
    ]

    assert _legacy_signature(legacy_assets, legacy_relations) == _v2_signature(
        v2_assets, v2_relations,
    )


def test_v2_signature_excludes_hidden_table_relations():
    assets = [{
        "asset_type": "table",
        "table_id": 10,
        "baseline_access": "controlled",
        "is_sensitive": False,
    }]
    relations = [{
        "asset_type": "table",
        "asset_id": 10,
        "table_id": 10,
        "org_unit_id": 1,
        "access_level": "hidden",
        "field_decision": None,
        "row_scope_json": {"type": "all"},
    }]

    assert _v2_signature(assets, relations) == ((), (), ())
