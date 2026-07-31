from __future__ import annotations

from types import SimpleNamespace

from scripts.semantic_seed_regression_assets import (
    CORE_METRIC_NAMES,
    METRIC_SPECS,
    NOISY_GENERATED_METRIC_PREFIXES,
    RELATIONSHIP_SPECS,
    apply_label_patch,
)


def test_seed_label_patch_dry_run_does_not_mutate():
    obj = SimpleNamespace(
        physical_name="clean_jf_now_details",
        business_name="clean_jf_now_details",
        description="",
        synonyms=[],
    )

    changed = apply_label_patch(
        obj,
        business_name="当前库存明细表",
        description="当前库存明细数据",
        synonyms=["库存明细"],
        apply=False,
    )

    assert changed is True
    assert obj.business_name == "clean_jf_now_details"
    assert obj.description == ""
    assert obj.synonyms == []


def test_seed_label_patch_preserves_manual_name_and_merges_desired_name_as_synonym():
    obj = SimpleNamespace(
        physical_name="clean_jf_now_details",
        business_name="人工确认库存表",
        description="",
        synonyms=["现存量"],
    )

    changed = apply_label_patch(
        obj,
        business_name="当前库存明细表",
        description="当前库存明细数据",
        synonyms=["库存明细", "现存量"],
        apply=True,
    )

    assert changed is True
    assert obj.business_name == "人工确认库存表"
    assert obj.description == "当前库存明细数据"
    assert obj.synonyms == ["现存量", "当前库存明细表", "库存明细"]


def test_seed_metric_specs_are_unique_and_have_formulas():
    names = [item["name"] for item in METRIC_SPECS]

    assert len(names) == len(set(names))
    assert all(item["formula"] for item in METRIC_SPECS)
    assert "sales_amount" in names
    assert "current_inventory_quantity" in names


def test_seed_relationship_specs_include_sales_order_detail_join():
    pairs = {
        (
            item["left_table"],
            item["left_column"],
            item["right_table"],
            item["right_column"],
        )
        for item in RELATIONSHIP_SPECS
    }

    assert (
        "clean_jf_order_detail",
        "main_code",
        "clean_jf_sale_order",
        "sale_order_no",
    ) in pairs
    assert (
        "clean_make_order_proofing_detail",
        "main_code",
        "clean_make_order_proofing",
        "make_order_no",
    ) in pairs


def test_seed_noisy_metric_policy_keeps_core_metrics():
    assert "avg_sales_unit_price" in CORE_METRIC_NAMES
    assert "sales_amount" in CORE_METRIC_NAMES
    assert "avg_sales_unit_price".startswith(NOISY_GENERATED_METRIC_PREFIXES) is False
    assert "avg_unit_price_clean_jf_order_detail".startswith(NOISY_GENERATED_METRIC_PREFIXES)
    assert "sum_clean_dispatch_order_dispatch_number".startswith(NOISY_GENERATED_METRIC_PREFIXES)
