"""
PageIndex PDF 解析适配器
"""
from typing import List, Tuple

from app.core.rag.pageindex.utils import get_page_tokens


def parse_pdf_pages(pdf_path: str) -> List[Tuple[str, int]]:
    """
    返回 [(page_text, token_count), ...]
    """
    return get_page_tokens(pdf_path, pdf_parser="PyMuPDF")

