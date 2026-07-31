"""
PageIndexRetriever 融合逻辑测试
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.core.rag.pageindex.retriever import PageIndexRetriever
from app.models.common.execution import DocumentChunk


def _chunk(chunk_id: str, file_id: str, page_numbers=None) -> DocumentChunk:
    metadata = {"file_id": file_id}
    if page_numbers is not None:
        metadata["page_numbers"] = page_numbers
    return DocumentChunk(
        content=f"content-{chunk_id}",
        source_file="demo.pdf",
        chunk_id=chunk_id,
        metadata=metadata,
    )


def _ranked_node(file_id: str, start_page: int, end_page: int, score: float = 0.9):
    node = SimpleNamespace(
        file_id=file_id,
        node_id=f"n-{start_page}-{end_page}",
        title=f"chapter-{start_page}-{end_page}",
        start_page=start_page,
        end_page=end_page,
    )
    return (node, score)


def test_merge_keeps_chunks_without_page_numbers():
    coarse_results = [
        _chunk("hit", "f1", "2,3"),
        _chunk("no-page", "f1"),
        _chunk("out", "f1", "30"),
    ]
    ranked_nodes = [_ranked_node("f1", 1, 5)]

    merged = PageIndexRetriever._merge_with_pageindex_guidance(
        coarse_results=coarse_results,
        ranked_nodes=ranked_nodes,
        final_top_n=3,
    )

    assert [chunk.chunk_id for chunk in merged] == ["hit", "no-page", "out"]
    assert merged[0].metadata.get("pageindex_matched") is True
    assert "pageindex_matched" not in merged[1].metadata


def test_merge_no_page_chunks_are_not_filtered_out():
    coarse_results = [
        _chunk("no-page", "f1"),
        _chunk("unmatched", "f1", "40"),
    ]
    ranked_nodes = [_ranked_node("f1", 1, 5)]

    merged = PageIndexRetriever._merge_with_pageindex_guidance(
        coarse_results=coarse_results,
        ranked_nodes=ranked_nodes,
        final_top_n=1,
    )

    assert len(merged) == 1
    assert merged[0].chunk_id == "no-page"


def test_parse_page_numbers_handles_multiple_formats():
    assert PageIndexRetriever._parse_page_numbers("1,2,3") == [1, 2, 3]
    assert PageIndexRetriever._parse_page_numbers(["4", 5, 0, "x"]) == [4, 5]
    assert PageIndexRetriever._parse_page_numbers(None) == []


def test_collect_focus_file_ids_from_nodes():
    ranked_nodes = [
        _ranked_node("f1", 1, 2),
        _ranked_node("f1", 3, 4),
        _ranked_node("f2", 5, 6),
        _ranked_node("f3", 7, 8),
    ]

    result = PageIndexRetriever._collect_focus_file_ids_from_nodes(ranked_nodes, max_files=2)
    assert result == ["f1", "f2"]


def test_merge_chunk_lists_deduplicates_and_keeps_order():
    primary = [
        _chunk("a", "f1", "1"),
        _chunk("b", "f1", "2"),
    ]
    fallback = [
        _chunk("b", "f1", "2"),
        _chunk("c", "f2", "3"),
    ]

    merged = PageIndexRetriever._merge_chunk_lists(primary, fallback)
    assert [chunk.chunk_id for chunk in merged] == ["a", "b", "c"]


def test_build_navigation_summary_chunk_returns_compact_summary():
    ranked_nodes = [
        (
            SimpleNamespace(
                file_id="f1",
                node_id="n1",
                title="第五章 行业竞争",
                summary="本章分析了主要竞争对手、市场份额和渠道差异。",
                start_page=45,
                end_page=62,
            ),
            0.91,
        )
    ]
    file_name_map = {"f1": "market_report.pdf"}

    nav_chunk = PageIndexRetriever._build_navigation_summary_chunk(
        ranked_nodes=ranked_nodes,
        file_name_map=file_name_map,
        limit=3,
    )

    assert nav_chunk is not None
    assert nav_chunk.metadata["type"] == "pageindex_navigation"
    assert "market_report.pdf" in nav_chunk.content
    assert "第五章 行业竞争" in nav_chunk.content
