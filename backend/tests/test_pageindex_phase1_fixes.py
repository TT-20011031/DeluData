"""
Phase 1 审计修复项回归测试（静态断言）
"""
import sys

sys.path.insert(0, ".")


def test_pageindex_settings_contains_min_pages():
    with open("app/config.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "class PageIndexSettings" in content
    assert "min_pages: int = 20" in content


def test_tree_builder_checks_pdf_page_threshold():
    with open("app/core/rag/pageindex/tree_builder.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "page_count = self._get_pdf_page_count(path)" in content
    assert "if page_count < self._settings.min_pages" in content


def test_ingestion_service_uses_pageindex_worker_enqueue():
    with open("app/services/ingestion_service.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "get_pageindex_worker" in content
    assert "await worker.enqueue(" in content
    assert "PageIndexTask(" in content
    assert "self._build_pageindex_tree" not in content


def test_doc_skill_skips_expansion_in_deep_search():
    with open("app/skills/doc_skill.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "(not deep_search) and settings.rag.enable_context_expansion" in content
    assert "深度检索模式已跳过上下文扩展" in content


def test_pageindex_retriever_dead_code_removed():
    with open("app/core/rag/pageindex/retriever.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "def _to_document_chunks(" not in content
