"""
模板变量分析器

职责：
- 提取模板中的 Jinja2 变量
- 推断变量类型
- 生成建议的 schema

设计原则遵循：
- Async First: 使用 asyncio.to_thread 异步化
"""
import re
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class TemplateAnalyzer:
    """模板变量分析器"""

    async def analyze_async(
        self,
        file_path: str,
        file_type: str
    ) -> Dict[str, Any]:
        """
        异步分析模板变量

        Args:
            file_path: 模板文件路径
            file_type: 文件类型 (docx/xlsx)

        Returns:
            {"variables": [...], "suggested_schema": {...}}
        """
        return await asyncio.to_thread(
            self._analyze_sync,
            file_path,
            file_type
        )

    def _analyze_sync(
        self,
        file_path: str,
        file_type: str
    ) -> Dict[str, Any]:
        """
        同步分析模板变量

        Args:
            file_path: 模板文件路径
            file_type: 文件类型 (docx/xlsx)

        Returns:
            {"variables": [...], "suggested_schema": {...}}
        """
        variables = set()

        try:
            if file_type == 'docx':
                from docxtpl import DocxTemplate
                doc = DocxTemplate(file_path)
                variables = doc.get_undeclared_template_variables()

            elif file_type == 'xlsx':
                import openpyxl
                wb = openpyxl.load_workbook(file_path)
                pattern = re.compile(r'\{\{(\w+)\}\}')

                for sheet in wb.worksheets:
                    for row in sheet.iter_rows():
                        for cell in row:
                            if cell.value and isinstance(cell.value, str):
                                matches = pattern.findall(cell.value)
                                variables.update(matches)
        except Exception as e:
            logger.warning(f"分析模板变量失败: {e}")

        # 生成建议的 schema
        suggested_schema = self._infer_schema(variables)

        return {
            "variables": list(variables),
            "suggested_schema": suggested_schema
        }

    def _infer_schema(self, variables: set) -> Dict[str, Dict[str, str]]:
        """
        根据变量名推断类型

        Args:
            variables: 变量名集合

        Returns:
            变量 schema 字典
        """
        schema = {}
        for var in variables:
            var_lower = var.lower()
            if 'image' in var_lower or 'chart' in var_lower or 'photo' in var_lower:
                schema[var] = {"type": "image", "desc": ""}
            elif 'date' in var_lower or 'time' in var_lower:
                schema[var] = {"type": "date", "desc": ""}
            elif 'amount' in var_lower or 'price' in var_lower or 'total' in var_lower or 'num' in var_lower:
                schema[var] = {"type": "number", "desc": ""}
            elif 'table' in var_lower or 'list' in var_lower or 'data' in var_lower:
                schema[var] = {"type": "table", "desc": ""}
            else:
                schema[var] = {"type": "text", "desc": ""}
        return schema


# 单例
_analyzer: TemplateAnalyzer = None


def get_template_analyzer() -> TemplateAnalyzer:
    """获取分析器单例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = TemplateAnalyzer()
    return _analyzer
