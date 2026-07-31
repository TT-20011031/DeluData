"""
Markdown 到 Word 格式转换器

使用 markdown-it-py 解析 Markdown AST，并映射到 python-docx 格式属性。

设计原则：
- 不用正则删除 **，而是正确解析 AST 并保留格式语义
- 支持嵌套格式（如 **粗体中的*斜体***）
- 支持列表嵌套、表格、代码块等复杂元素
"""
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class TextStyle(Enum):
    """文本样式枚举"""
    NORMAL = auto()
    BOLD = auto()
    ITALIC = auto()
    BOLD_ITALIC = auto()
    CODE = auto()
    STRIKETHROUGH = auto()


@dataclass
class TextRun:
    """文本片段，包含内容和样式"""
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strikethrough: bool = False


def parse_inline_formatting(text: str) -> List[TextRun]:
    """
    解析行内 Markdown 格式，返回带样式的 TextRun 列表
    
    支持:
    - **加粗** 或 __加粗__
    - *斜体* 或 _斜体_
    - ***粗斜体*** 或 ___粗斜体___
    - `代码`
    - ~~删除线~~
    """
    try:
        from markdown_it import MarkdownIt
        from markdown_it.tree import SyntaxTreeNode
    except ImportError:
        logger.warning("markdown-it-py 未安装，回退到简单文本处理")
        return [TextRun(text=text)]
    
    md = MarkdownIt("commonmark", {"typographer": True})
    # 启用删除线支持
    try:
        md.enable("strikethrough")
    except Exception:
        pass  # 某些版本可能不支持
    
    # 解析为 tokens
    tokens = md.parse(text)
    runs: List[TextRun] = []
    
    def process_inline_tokens(tokens, bold=False, italic=False, code=False, strike=False):
        """递归处理内联 tokens"""
        for token in tokens:
            if token.type == "text":
                runs.append(TextRun(
                    text=token.content,
                    bold=bold,
                    italic=italic,
                    code=code,
                    strikethrough=strike
                ))
            elif token.type == "strong_open":
                # 进入加粗区域
                pass
            elif token.type == "strong_close":
                pass
            elif token.type == "em_open":
                # 进入斜体区域
                pass
            elif token.type == "em_close":
                pass
            elif token.type == "code_inline":
                runs.append(TextRun(
                    text=token.content,
                    code=True
                ))
            elif token.type == "s_open":
                # 删除线开始
                pass
            elif token.type == "s_close":
                pass
            elif token.type == "softbreak":
                runs.append(TextRun(text="\n"))
            elif token.type == "hardbreak":
                runs.append(TextRun(text="\n"))
            elif token.type == "inline":
                # 递归处理内联内容，需要追踪状态
                _process_with_state(token.children or [])
    
    def _process_with_state(children):
        """带状态追踪的处理"""
        state_stack = []  # 样式状态栈
        current_bold = False
        current_italic = False
        current_strike = False
        
        for child in children:
            if child.type == "strong_open":
                state_stack.append("strong")
                current_bold = True
            elif child.type == "strong_close":
                if state_stack and state_stack[-1] == "strong":
                    state_stack.pop()
                current_bold = "strong" in state_stack
            elif child.type == "em_open":
                state_stack.append("em")
                current_italic = True
            elif child.type == "em_close":
                if state_stack and state_stack[-1] == "em":
                    state_stack.pop()
                current_italic = "em" in state_stack
            elif child.type == "s_open":
                state_stack.append("s")
                current_strike = True
            elif child.type == "s_close":
                if state_stack and state_stack[-1] == "s":
                    state_stack.pop()
                current_strike = "s" in state_stack
            elif child.type == "text":
                runs.append(TextRun(
                    text=child.content,
                    bold=current_bold,
                    italic=current_italic,
                    strikethrough=current_strike
                ))
            elif child.type == "code_inline":
                runs.append(TextRun(
                    text=child.content,
                    code=True
                ))
            elif child.type == "softbreak" or child.type == "hardbreak":
                runs.append(TextRun(text="\n", bold=current_bold, italic=current_italic))
    
    # 处理 tokens
    for token in tokens:
        if token.type == "paragraph_open":
            continue
        elif token.type == "paragraph_close":
            continue
        elif token.type == "inline":
            _process_with_state(token.children or [])
    
    # 如果没有解析出任何 runs，返回原始文本
    if not runs:
        return [TextRun(text=text)]
    
    return runs


def apply_runs_to_paragraph(para, runs: List[TextRun]):
    """
    将 TextRun 列表应用到 python-docx 的 Paragraph 对象
    
    Args:
        para: python-docx 的 Paragraph 对象
        runs: TextRun 列表
    """
    from docx.shared import Pt
    from docx.oxml.ns import qn
    
    for run_data in runs:
        run = para.add_run(run_data.text)
        
        if run_data.bold:
            run.bold = True
        
        if run_data.italic:
            run.italic = True
        
        if run_data.code:
            # 代码样式：等宽字体 + 灰色背景
            run.font.name = "Consolas"
            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Consolas')
            # 可选：设置字体大小
            run.font.size = Pt(10)
        
        if run_data.strikethrough:
            run.font.strike = True


def convert_markdown_line_to_paragraph(line: str, para):
    """
    将单行 Markdown 文本转换并应用到段落
    
    Args:
        line: Markdown 文本行
        para: python-docx 的 Paragraph 对象
    """
    runs = parse_inline_formatting(line)
    apply_runs_to_paragraph(para, runs)


# ========== 高级功能：完整文档转换 ==========

def convert_markdown_to_docx_elements(
    markdown_content: str,
    doc,
    style_config: dict | None = None,
) -> None:
    """
    将完整 Markdown 内容转换为 Word 文档元素
    
    支持:
    - 标题 (#, ##, ###)
    - 列表 (无序、有序、嵌套)
    - 表格（带表头高亮 + 交替行色）
    - 代码块（CodeBlock 样式）
    - 水平分隔线
    - 普通段落（带行内格式）
    
    Args:
        markdown_content: Markdown 文本
        doc: python-docx 的 Document 对象
        style_config: 样式配置字典（由 docx_style_engine 生成）
    """
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    import re
    
    lines = markdown_content.split("\n")
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # 水平分隔线
        if line.strip() in ("---", "***", "___"):
            _add_horizontal_rule(doc)
            i += 1
            continue
        
        # 代码块
        if line.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if code_lines:
                try:
                    code_para = doc.add_paragraph(style="CodeBlock")
                except KeyError:
                    code_para = doc.add_paragraph()
                run = code_para.add_run("\n".join(code_lines))
                run.font.name = "Consolas"
                run.font.size = Pt(10)
            i += 1
            continue
        
        # 标题（显式清除首行缩进，确保顶格）
        if line.startswith("### "):
            heading = doc.add_heading(line[4:], level=3)
            heading.paragraph_format.first_line_indent = None
            i += 1
            continue
        elif line.startswith("## "):
            heading = doc.add_heading(line[3:], level=2)
            heading.paragraph_format.first_line_indent = None
            i += 1
            continue
        elif line.startswith("# "):
            heading = doc.add_heading(line[2:], level=1)
            heading.paragraph_format.first_line_indent = None
            i += 1
            continue
        
        # 表格：收集所有表格行一起处理
        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                # 跳过分隔行
                if not re.match(r"^\|[\s\-:|]+$", lines[i].replace(" ", "")):
                    cells = [c.strip() for c in lines[i].split("|")[1:-1]]
                    if cells:
                        table_lines.append(cells)
                i += 1
            
            if table_lines:
                cols = len(table_lines[0])
                table = doc.add_table(rows=len(table_lines), cols=cols)
                table_style = (style_config or {}).get("table_style_name", "Table Grid")
                try:
                    table.style = table_style
                except KeyError:
                    table.style = "Table Grid"
                
                header_bg = (style_config or {}).get("table_header_bg", "2B579A")
                header_fg = (style_config or {}).get("table_header_fg", "FFFFFF")
                alt_row_bg = (style_config or {}).get("table_alt_row_bg", "F2F7FB")
                
                for row_idx, row_data in enumerate(table_lines):
                    row = table.rows[row_idx]
                    for col_idx, cell_text in enumerate(row_data):
                        if col_idx < len(row.cells):
                            cell = row.cells[col_idx]
                            cell_para = cell.paragraphs[0]
                            cell_para.clear()
                            cell_para.paragraph_format.first_line_indent = None
                            cell_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            runs = parse_inline_formatting(cell_text)
                            apply_runs_to_paragraph(cell_para, runs)
                            
                            # 垂直居中
                            tc = cell._element
                            tcPr = tc.get_or_add_tcPr()
                            vAlign = OxmlElement("w:vAlign")
                            vAlign.set(qn("w:val"), "center")
                            tcPr.append(vAlign)
                            
                            if row_idx == 0:
                                _set_cell_shading(cell, header_bg)
                                for r in cell_para.runs:
                                    r.bold = True
                                    r.font.color.rgb = RGBColor.from_string(header_fg)
                            elif row_idx % 2 == 0:
                                _set_cell_shading(cell, alt_row_bg)
            continue
        
        # 无序列表
        if line.startswith("- ") or line.startswith("* "):
            para = doc.add_paragraph(style="List Bullet")
            convert_markdown_line_to_paragraph(line[2:], para)
            i += 1
            continue
        
        # 有序列表
        if re.match(r"^\d+\. ", line):
            para = doc.add_paragraph(style="List Number")
            text = re.sub(r"^\d+\. ", "", line)
            convert_markdown_line_to_paragraph(text, para)
            i += 1
            continue
        
        # 嵌套列表（缩进）
        if line.startswith("  - ") or line.startswith("  * "):
            para = doc.add_paragraph(style="List Bullet 2")
            convert_markdown_line_to_paragraph(line[4:], para)
            i += 1
            continue
        
        if line.startswith("    - ") or line.startswith("    * "):
            para = doc.add_paragraph(style="List Bullet 3")
            convert_markdown_line_to_paragraph(line[6:], para)
            i += 1
            continue
        
        # 普通段落
        if line.strip():
            para = doc.add_paragraph()
            convert_markdown_line_to_paragraph(line, para)
        
        i += 1


def _set_cell_shading(cell, color_hex: str) -> None:
    """设置单元格背景色"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color_hex)
    cell._element.get_or_add_tcPr().append(shading)


def _add_horizontal_rule(doc) -> None:
    """添加水平分隔线"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    
    para = doc.add_paragraph()
    para.paragraph_format.space_before = None
    para.paragraph_format.space_after = None
    para.paragraph_format.first_line_indent = None
    
    pPr = para._element.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "CCCCCC")
    pBdr.append(bottom)
    pPr.append(pBdr)
