import sys

import pytest

sys.path.insert(0, ".")

from app.core.wiki.chunk_loader import WikiChunk
from app.core.wiki.compiler import CandidateEntity, WikiCompiler


def candidate(slug, title, file_id, chunk_id, aliases=None):
    return CandidateEntity(
        slug=slug,
        title=title,
        aliases=aliases or [],
        domain="product",
        brief=f"{title} brief",
        evidence_chunk_ids=[chunk_id],
        source_file_id=file_id,
        sources_by_file={file_id: [chunk_id]},
    )


def test_canonical_merge_collapses_document_view_titles_into_entity():
    candidates = [
        candidate("deludata-product-overview", "DeluData产品总览", "f1", "c1"),
        candidate("deludata-core-modules", "DeluData核心模块", "f3", "c3"),
    ]

    merged = WikiCompiler._deduplicate_candidates(
        WikiCompiler._merge_candidates(candidates)
    )

    assert len(merged) == 1
    entity = merged[0]
    assert entity.slug == "deludata"
    assert entity.merged_from_count == 2
    assert entity.sources_by_file == {"f1": ["c1"], "f3": ["c3"]}
    assert "DeluData核心模块" in entity.aliases
    assert "deludata-product-overview" in entity.aliases


def test_acronym_aliases_merge_into_stable_term_slug():
    candidates = [
        candidate(
            "rag",
            "RAG",
            "f1",
            "c1",
            aliases=["Retrieval-Augmented Generation"],
        ),
        candidate(
            "retrieval-augmented-generation",
            "检索增强生成",
            "f2",
            "c2",
            aliases=["RAG"],
        ),
    ]

    merged = WikiCompiler._deduplicate_candidates(
        WikiCompiler._merge_candidates(candidates)
    )

    assert len(merged) == 1
    assert merged[0].slug == "rag"
    assert merged[0].sources_by_file == {"f1": ["c1"], "f2": ["c2"]}


def test_existing_index_matches_canonical_title_key():
    index = WikiCompiler._build_existing_index(
        [
            {
                "id": "p1",
                "slug": "deludata",
                "title": "DeluData",
                "aliases": [],
            }
        ]
    )
    cand = candidate("deludata-core-modules", "DeluData核心模块", "f3", "c3")

    match = WikiCompiler._find_existing_match(cand, index)

    assert match is not None
    assert match["slug"] == "deludata"


class FakeChunkLoader:
    def __init__(self):
        self.calls = []

    async def load_chunks_for_file(self, workspace_id, file_id):
        self.calls.append((workspace_id, file_id))
        return [
            WikiChunk(
                chunk_id=f"{file_id}-chunk",
                chunk_index=0,
                content=f"{file_id} content",
                page_numbers=[],
                header_path="",
                summary="",
                source_file=f"{file_id}.md",
            )
        ]


@pytest.mark.asyncio
async def test_evidence_pool_loads_every_file_from_sources_by_file():
    loader = FakeChunkLoader()
    compiler = WikiCompiler(chunk_loader=loader)
    cand = CandidateEntity(
        slug="deludata",
        title="DeluData",
        evidence_chunk_ids=["f1-chunk", "f2-chunk"],
        source_file_id="f1",
        sources_by_file={"f1": ["f1-chunk"], "f2": ["f2-chunk"]},
    )

    pool = await compiler._build_evidence_pool(
        workspace_id="ws1",
        candidates=[cand],
    )

    assert loader.calls == [("ws1", "f1"), ("ws1", "f2")]
    assert set(pool) == {"f1-chunk", "f2-chunk"}
