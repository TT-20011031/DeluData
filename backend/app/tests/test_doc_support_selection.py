from app.models.common.execution import DocumentChunk
from app.tools.doc_support import selection


def _make_chunk(
    *,
    file_id: str,
    source_file: str,
    rerank_score: float,
    page_number: int,
    file_type: str = "pdf",
    content: str = "这是一个用于测试的文本块。" * 5,
) -> DocumentChunk:
    return DocumentChunk(
        content=content,
        source_file=source_file,
        page_number=page_number,
        chunk_id=f"{file_id}_{page_number}_{rerank_score}",
        rerank_score=rerank_score,
        metadata={
            "file_id": file_id,
            "source_file": source_file,
            "page_numbers": [page_number],
            "file_type": file_type,
        },
    )


def test_select_chunks_for_synthesizer_keeps_only_high_confidence_without_padding(caplog):
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.91, page_number=8),
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.86, page_number=9),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.40, page_number=3),
        _make_chunk(file_id="C", source_file="c.pdf", rerank_score=0.30, page_number=5),
    ]

    with caplog.at_level("INFO"):
        selected, used_tokens, trimmed = selection.select_chunks_for_synthesizer(
            chunks=chunks,
            token_budget=5000,
            score_threshold=0.12,
            relative_margin=0.08,
            max_chunks=5,
        )

    assert [chunk.chunk_id for chunk in selected] == [
        chunks[0].chunk_id,
        chunks[1].chunk_id,
    ]
    assert used_tokens > 0
    assert trimmed is True
    assert any("原因码=被相对分差截断" in record.message for record in caplog.records)


def test_select_chunks_for_synthesizer_caps_max_chunk_count():
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.91, page_number=1),
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.90, page_number=2),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.89, page_number=3),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.88, page_number=4),
        _make_chunk(file_id="C", source_file="c.pdf", rerank_score=0.87, page_number=5),
        _make_chunk(file_id="D", source_file="d.pdf", rerank_score=0.86, page_number=6),
    ]

    selected, _, trimmed = selection.select_chunks_for_synthesizer(
        chunks=chunks,
        token_budget=10000,
        score_threshold=0.12,
        relative_margin=0.08,
        max_chunks=5,
    )

    assert [chunk.chunk_id for chunk in selected] == [chunk.chunk_id for chunk in chunks[:5]]
    assert trimmed is True


def test_select_chunks_for_synthesizer_guarantees_required_file_and_keeps_related():
    chunks = [
        _make_chunk(file_id="target", source_file="target.pdf", rerank_score=0.99, page_number=3),
        _make_chunk(file_id="target", source_file="target.pdf", rerank_score=0.98, page_number=4),
        _make_chunk(file_id="target", source_file="target.pdf", rerank_score=0.97, page_number=5),
        _make_chunk(file_id="target", source_file="target.pdf", rerank_score=0.96, page_number=6),
        _make_chunk(file_id="related-A", source_file="related-a.pdf", rerank_score=0.82, page_number=1),
        _make_chunk(file_id="related-B", source_file="related-b.pdf", rerank_score=0.80, page_number=2),
    ]

    selected, used_tokens, trimmed = selection.select_chunks_for_synthesizer(
        chunks=chunks,
        token_budget=9000,
        score_threshold=0.12,
        relative_margin=0.08,
        max_chunks=20,
        required_file_ids={"target"},
        required_max_chunks=3,
        required_token_budget_ratio=0.5,
    )

    selected_file_ids = [selection.chunk_file_id(chunk) for chunk in selected]
    assert selected_file_ids.count("target") == 3
    assert "related-A" in selected_file_ids
    assert "related-B" in selected_file_ids
    assert used_tokens <= 9000
    assert trimmed is True


def test_select_chunks_for_synthesizer_guarantees_each_required_file():
    chunks = [
        _make_chunk(file_id="related", source_file="related.pdf", rerank_score=0.96, page_number=1),
        _make_chunk(file_id="target-A", source_file="target-a.pdf", rerank_score=0.60, page_number=2),
        _make_chunk(file_id="target-B", source_file="target-b.pdf", rerank_score=0.55, page_number=3),
    ]

    selected, used_tokens, _ = selection.select_chunks_for_synthesizer(
        chunks=chunks,
        token_budget=9000,
        score_threshold=0.12,
        relative_margin=0.08,
        max_chunks=20,
        required_file_ids={"target-A", "target-B"},
        required_max_chunks=3,
        required_token_budget_ratio=0.5,
    )

    selected_file_ids = {selection.chunk_file_id(chunk) for chunk in selected}
    assert selected_file_ids == {"target-A", "target-B", "related"}
    assert used_tokens <= 9000


def test_select_chunks_for_synthesizer_trims_required_evidence_to_half_budget():
    chunks = [
        _make_chunk(
            file_id="target",
            source_file="target.pdf",
            rerank_score=0.99,
            page_number=1,
            content="目标文件证据" * 2000,
        ),
        _make_chunk(
            file_id="related",
            source_file="related.pdf",
            rerank_score=0.90,
            page_number=2,
            content="普通相关证据",
        ),
    ]

    selected, used_tokens, trimmed = selection.select_chunks_for_synthesizer(
        chunks=chunks,
        token_budget=2000,
        score_threshold=0.12,
        relative_margin=0.08,
        max_chunks=20,
        required_file_ids={"target"},
        required_max_chunks=3,
        required_token_budget_ratio=0.5,
    )

    target = next(
        chunk for chunk in selected if selection.chunk_file_id(chunk) == "target"
    )
    assert selection.chunk_tokens(target) <= 1000
    assert target.metadata["evidence_truncated"] is True
    assert "related" in {selection.chunk_file_id(chunk) for chunk in selected}
    assert used_tokens <= 2000
    assert trimmed is True


def test_select_page_images_for_synthesizer_keeps_secondary_single_strong_page():
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.92, page_number=8),
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.89, page_number=9),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.88, page_number=3),
    ]
    file_info_map = {
        "A": {"name": "a", "type": "pdf", "storage_path": "a.pdf"},
        "B": {"name": "b", "type": "pdf", "storage_path": "b.pdf"},
    }

    pages = selection.select_page_images_for_synthesizer(
        chunks=chunks,
        file_info_map=file_info_map,
        score_threshold=0.12,
        pdf_relative_margin=0.06,
        secondary_best_delta=0.05,
        secondary_support_ratio=0.75,
        page_window=1,
        max_files=2,
        max_images=5,
        primary_anchor_pages=2,
        secondary_anchor_pages=1,
    )

    assert [(item["file_id"], item["page_number"]) for item in pages] == [
        ("A", 8),
        ("A", 9),
        ("B", 3),
        ("A", 7),
        ("A", 10),
    ]


def test_select_page_images_for_synthesizer_does_not_expand_primary_when_only_one_high_conf_chunk():
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.92, page_number=8),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.60, page_number=3),
    ]
    file_info_map = {
        "A": {"name": "a", "type": "pdf", "storage_path": "a.pdf"},
        "B": {"name": "b", "type": "pdf", "storage_path": "b.pdf"},
    }

    pages = selection.select_page_images_for_synthesizer(
        chunks=chunks,
        file_info_map=file_info_map,
        score_threshold=0.12,
        pdf_relative_margin=0.06,
        secondary_best_delta=0.05,
        secondary_support_ratio=0.75,
        page_window=1,
        max_files=2,
        max_images=5,
        primary_anchor_pages=2,
        secondary_anchor_pages=1,
    )

    assert [(item["file_id"], item["page_number"]) for item in pages] == [("A", 8)]


def test_select_page_images_for_synthesizer_handles_generic_multi_file_competition():
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.90, page_number=8),
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.87, page_number=9),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.89, page_number=3),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.84, page_number=4),
        _make_chunk(file_id="C", source_file="c.pdf", rerank_score=0.88, page_number=11),
    ]
    file_info_map = {
        "A": {"name": "a", "type": "pdf", "storage_path": "a.pdf"},
        "B": {"name": "b", "type": "pdf", "storage_path": "b.pdf"},
        "C": {"name": "c", "type": "pdf", "storage_path": "c.pdf"},
    }

    pages = selection.select_page_images_for_synthesizer(
        chunks=chunks,
        file_info_map=file_info_map,
        score_threshold=0.12,
        pdf_relative_margin=0.06,
        secondary_best_delta=0.05,
        secondary_support_ratio=0.75,
        page_window=1,
        max_files=2,
        max_images=5,
        primary_anchor_pages=2,
        secondary_anchor_pages=1,
    )

    assert [(item["file_id"], item["page_number"]) for item in pages] == [
        ("A", 8),
        ("A", 9),
        ("B", 3),
        ("A", 7),
        ("A", 10),
    ]
    assert all(item["file_id"] != "C" for item in pages)


def test_selection_logs_use_chinese_structured_contract(caplog):
    chunks = [
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.91, page_number=8),
        _make_chunk(file_id="A", source_file="a.pdf", rerank_score=0.89, page_number=9),
        _make_chunk(file_id="B", source_file="b.pdf", rerank_score=0.88, page_number=3),
        _make_chunk(file_id="C", source_file="c.pdf", rerank_score=0.20, page_number=6),
    ]
    file_info_map = {
        "A": {"name": "a", "type": "pdf", "storage_path": "a.pdf"},
        "B": {"name": "b", "type": "pdf", "storage_path": "b.pdf"},
        "C": {"name": "c", "type": "pdf", "storage_path": "c.pdf"},
    }

    with caplog.at_level("INFO"):
        selected, _, _ = selection.select_chunks_for_synthesizer(
            chunks=chunks,
            token_budget=10000,
            score_threshold=0.12,
            relative_margin=0.08,
            max_chunks=2,
        )
        images = selection.select_page_images_for_synthesizer(
            chunks=chunks,
            file_info_map=file_info_map,
            score_threshold=0.12,
            pdf_relative_margin=0.06,
            secondary_best_delta=0.05,
            secondary_support_ratio=0.75,
            page_window=1,
            max_files=2,
            max_images=3,
            primary_anchor_pages=2,
            secondary_anchor_pages=1,
        )
        selection.log_image_alignment(selected, images, file_info_map)

    assert any("文本块决策 阶段=" in record.message and "原因码=" in record.message and "原因说明=" in record.message for record in caplog.records)
    assert any("图片页决策 阶段=" in record.message and "原因码=" in record.message and "原因说明=" in record.message for record in caplog.records)
    assert any("文本图片对齐 文本证据文件数=" in record.message and "文件交集比例=" in record.message for record in caplog.records)
