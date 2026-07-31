from pathlib import Path
from uuid import uuid4

from docx import Document

from app.core.rag.document_parser import DocumentParser


def test_parse_docx_extracts_table_text() -> None:
    docx_path = Path.cwd() / f"tmp_table_only_{uuid4().hex}.docx"

    doc = Document()
    table = doc.add_table(rows=4, cols=4)
    title_cell = table.rows[0].cells[0].merge(table.rows[0].cells[3])
    title_cell.text = "会议纪要"

    table.rows[1].cells[0].text = "会议主题"
    merged_topic = table.rows[1].cells[1].merge(table.rows[1].cells[2])
    merged_topic.text = "真爱集团有限公司"
    table.rows[1].cells[3].text = "2026020602"

    detail_cell = table.rows[2].cells[0].merge(table.rows[2].cells[3])
    detail_cell.text = "1. 演示图改图功能\n2. 确认项目排期"

    footer_left = table.rows[3].cells[0].merge(table.rows[3].cells[1])
    footer_left.text = "整理人员"
    footer_right = table.rows[3].cells[2].merge(table.rows[3].cells[3])
    footer_right.text = "申丰赫"

    doc.save(docx_path)

    try:
        parser = DocumentParser(max_file_size=1024 * 1024)
        content = parser._parse_docx(docx_path)
    finally:
        docx_path.unlink(missing_ok=True)

    assert "[PAGE:1]" in content
    assert "会议纪要" in content
    assert "真爱集团有限公司" in content
    assert "演示图改图功能" in content
    assert "整理人员" in content
    assert len(content) > 40
