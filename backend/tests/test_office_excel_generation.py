import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.office.handlers.base import TaskContext
from app.agents.office.handlers.excel_creation import ExcelCreationHandler
from app.agents.office_worker import OfficeWorker


def test_office_worker_detects_excel_creation_mode():
    worker = OfficeWorker()

    assert worker._detect_task_mode(
        TaskContext(
            task_description="请生成 Excel 销售报表",
            sandbox_path="sandbox",
            session_id="s-1",
        )
    ) == "excel_creation"

    assert worker._detect_task_mode(
        TaskContext(
            task_description="生成月度统计",
            sandbox_path="sandbox",
            session_id="s-1",
            output_filename="monthly_report.xlsx",
        )
    ) == "excel_creation"

    assert worker._detect_task_mode(
        TaskContext(
            task_description="生成月度统计",
            sandbox_path="sandbox",
            session_id="s-1",
            output_filename="weekly_excel_report",
        )
    ) == "excel_creation"

    assert worker._detect_task_mode(
        TaskContext(
            task_description="生成月度统计",
            sandbox_path="sandbox",
            session_id="s-1",
            output_filename="销售工作簿",
        )
    ) == "excel_creation"

    assert worker._detect_task_mode(
        TaskContext(
            task_description="请生成 Word 报告",
            sandbox_path="sandbox",
            session_id="s-1",
        )
    ) == "creation"


def test_excel_creation_handler_renders_structured_xlsx(tmp_path):
    handler = ExcelCreationHandler()
    output_path = tmp_path / "sales.xlsx"

    workbook_spec = {
        "title": "销售报表",
        "sheets": [
            {
                "name": "销售报表",
                "columns": [
                    {"header": "城市", "key": "city", "type": "text", "summary": None},
                    {"header": "销售额", "key": "sales", "type": "currency", "summary": "sum"},
                    {"header": "同比增长", "key": "growth", "type": "percent", "summary": "average"},
                ],
                "rows": [
                    ["北京", 100000, 0.12],
                    ["上海", 150000, 0.08],
                    ["广州", 80000, 0.15],
                ],
                "chart": {
                    "type": "column",
                    "title": "销售额对比",
                    "category_key": "city",
                    "value_key": "sales",
                },
            }
        ],
    }

    handler._render_workbook(workbook_spec, str(output_path))

    assert output_path.exists()

    workbook = openpyxl.load_workbook(output_path)
    worksheet = workbook["销售报表"]

    assert worksheet.freeze_panes == "A2"
    assert len(worksheet.tables) == 1
    assert "SUBTOTAL" in str(worksheet["B5"].value).upper()
    assert len(worksheet._charts) == 1


def test_excel_creation_handler_normalizes_percentage_values():
    handler = ExcelCreationHandler()

    assert handler._normalize_cell_value(0.12, "percentage") == 0.12
    assert handler._normalize_cell_value(12, "percentage") == 0.12
    assert handler._normalize_cell_value(12.5, "percentage") == 0.125
    assert handler._normalize_cell_value("12%", "percentage") == 0.12
    assert handler._normalize_cell_value("-8%", "percentage") == -0.08
