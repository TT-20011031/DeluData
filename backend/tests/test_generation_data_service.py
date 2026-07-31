import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.generation_data_service import (
    build_query_focused_context,
    extract_query_focused_result_text,
    extract_query_tokens,
    select_primary_generation_source,
)


def test_extract_query_tokens_splits_chinese_sentence_into_matchable_phrases():
    tokens = extract_query_tokens("帮我画华东地区销售趋势图")

    assert "华东" in tokens
    assert "销售" in tokens
    assert "趋势" in tokens
    assert "帮我" not in tokens


def test_select_primary_generation_source_prefers_matching_dataset():
    memory_dfs = {
        "employee_list": pd.DataFrame(
            [
                {"name": "张三", "department": "人事部"},
                {"name": "李四", "department": "财务部"},
            ]
        ),
        "sales_by_region": pd.DataFrame(
            [
                {"region": "华东", "month": "2025-01", "sales": 120},
                {"region": "华南", "month": "2025-01", "sales": 90},
            ]
        ),
    }

    selected = select_primary_generation_source("帮我画华东地区销售趋势图", memory_dfs)

    assert selected is not None
    assert selected.key == "sales_by_region"
    assert "华东" in selected.preview_text
    assert "张三" not in selected.preview_text


def test_build_query_focused_context_filters_irrelevant_rag_rows():
    rag_df = pd.DataFrame(
        [
            {"content": "AI 芯片销量同比增长 30%", "source": "report_a"},
            {"content": "员工食堂改造计划已经立项", "source": "report_b"},
        ]
    )

    context = build_query_focused_context(
        "请总结 AI 芯片销量",
        {"rag_chunks": rag_df},
        max_sources=1,
        max_rows=5,
        max_chars=4000,
        max_chars_per_source=2000,
    )

    assert "AI 芯片销量同比增长 30%" in context
    assert "员工食堂改造计划已经立项" not in context


def test_extract_query_focused_result_text_keeps_only_relevant_blocks():
    execution_results = [
        {
            "worker": "doc_worker",
            "result": (
                "知识库检索结果：\n"
                "来源: report_a\nAI 芯片销量同比增长 30%\n\n"
                "来源: report_b\n员工食堂改造计划已经立项"
            ),
        }
    ]

    text = extract_query_focused_result_text("请总结 AI 芯片销量", execution_results)

    assert "AI 芯片销量同比增长 30%" in text
    assert "员工食堂改造计划已经立项" not in text
