"""
PageIndex 深度检索链路基础测试
"""
import sys

sys.path.insert(0, ".")

from app.config import get_settings


def test_start_session_schema_contains_deep_search():
    with open("app/api/chat/schemas.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "deep_search" in content


def test_pageindex_settings_available():
    settings = get_settings()
    assert settings.pageindex is not None


def test_doc_tool_contains_deep_search_passthrough():
    with open("app/tools/doc_tool.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "deep_search" in content
    assert "deep_search=deep_search" in content
