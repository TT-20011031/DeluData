import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.services.wiki_service import _build_wiki_graph_payload


def page(id, status="published", domain="general"):
    return SimpleNamespace(
        id=id,
        slug=f"slug-{id}",
        title=f"Page {id}",
        summary=f"Summary {id}",
        domain=domain,
        status=status,
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
    )


def link(id, source, target, link_type="mentions", status="active"):
    return SimpleNamespace(
        id=id,
        source_page_id=source,
        target_page_id=target,
        link_type=link_type,
        status=status,
        confidence=0.8,
        note="note",
        evidence_chunk_ids=["c1", "c2"],
    )


def source_item(page_id, file_id, file_name="source.md"):
    return {
        "src": SimpleNamespace(
            id=f"src-{page_id}-{file_id}",
            page_id=page_id,
            file_id=file_id,
            chunk_ids=["c1"],
            excerpt="source excerpt",
            relevance=0.9,
        ),
        "file_name": file_name,
        "file_type": "md",
        "status": "indexed",
        "is_deleted": False,
        "visibility": "public",
        "dept_id": None,
        "owner_id": "u1",
        "document_type": "manual",
        "business_domain": "product",
        "confidentiality_level": "internal",
        "effective_from": None,
        "effective_until": None,
        "external_ref": None,
        "live": True,
        "chunk_ids": ["c1"],
        "excerpt": "source excerpt",
        "relevance": 0.9,
    }


def test_wiki_graph_payload_keeps_only_trusted_pages_and_active_visible_edges():
    payload = _build_wiki_graph_payload(
        pages=[
            page("p1", "published", "policy"),
            page("p2", "verified", "product"),
            page("p3", "candidate", "policy"),
            page("p4", "archived", "policy"),
        ],
        links=[
            link("l1", "p1", "p2", "related", "active"),
            link("l2", "p1", "p3", "mentions", "active"),
            link("l3", "p2", "p1", "contradicts", "pending_review"),
            link("l4", "p2", "p4", "supersedes", "active"),
        ],
        source_scope_by_page={
            "p1": {"source_count": 1},
            "p2": {"source_count": 2},
        },
    )

    assert [node["id"] for node in payload["nodes"]] == ["p1", "p2"]
    assert [edge["id"] for edge in payload["edges"]] == ["l1"]
    assert payload["nodes"][0]["degree"] == 1
    assert payload["nodes"][1]["degree"] == 1
    assert payload["edges"][0]["evidence_count"] == 2
    assert payload["stats"] == {
        "page_count": 2,
        "link_count": 1,
        "isolated_count": 0,
        "domain_counts": {"policy": 1, "product": 1},
        "link_type_counts": {"related": 1},
        "entity_count": 2,
        "file_count": 0,
        "wiki_link_count": 1,
        "source_edge_count": 0,
        "total_edge_count": 1,
    }


def test_wiki_graph_payload_counts_isolated_nodes_and_link_types():
    payload = _build_wiki_graph_payload(
        pages=[
            page("p1", "published", "policy"),
            page("p2", "published", "policy"),
            page("p3", "verified", "term"),
        ],
        links=[
            link("l1", "p1", "p2", "mentions"),
            link("l2", "p2", "p1", "supersedes"),
            link("l3", "missing", "p1", "related"),
        ],
        source_scope_by_page={},
    )

    assert payload["stats"]["page_count"] == 3
    assert payload["stats"]["link_count"] == 2
    assert payload["stats"]["isolated_count"] == 1
    assert payload["stats"]["domain_counts"] == {"policy": 2, "term": 1}
    assert payload["stats"]["link_type_counts"] == {"mentions": 1, "supersedes": 1}
    assert payload["stats"]["entity_count"] == 3
    assert payload["stats"]["file_count"] == 0
    assert payload["stats"]["wiki_link_count"] == 2
    assert payload["stats"]["source_edge_count"] == 0
    assert payload["stats"]["total_edge_count"] == 2


def test_wiki_graph_payload_adds_source_file_nodes_and_edges():
    payload = _build_wiki_graph_payload(
        pages=[
            page("p1", "published", "policy"),
            page("p2", "verified", "term"),
        ],
        links=[link("l1", "p1", "p2", "related")],
        source_scope_by_page={
            "p1": {"source_count": 1},
            "p2": {"source_count": 1},
        },
        source_items_by_page={
            "p1": [source_item("p1", "f1", "architecture.md")],
            "p2": [source_item("p2", "f1", "architecture.md")],
        },
    )

    ids = {node["id"] for node in payload["nodes"]}
    assert ids == {"p1", "p2", "file:f1"}
    file_node = next(node for node in payload["nodes"] if node["id"] == "file:f1")
    assert file_node["node_type"] == "file"
    assert file_node["file_id"] == "f1"
    assert file_node["title"] == "architecture.md"
    assert file_node["degree"] == 2
    assert file_node["source_count"] == 2

    source_edges = [edge for edge in payload["edges"] if edge["link_type"] == "source"]
    assert len(source_edges) == 2
    assert source_edges[0]["source"] == "file:f1"
    assert payload["stats"]["page_count"] == 2
    assert payload["stats"]["link_count"] == 1
    assert payload["stats"]["entity_count"] == 2
    assert payload["stats"]["file_count"] == 1
    assert payload["stats"]["wiki_link_count"] == 1
    assert payload["stats"]["source_edge_count"] == 2
    assert payload["stats"]["total_edge_count"] == 3
