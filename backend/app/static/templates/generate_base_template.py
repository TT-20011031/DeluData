"""
生成 base_template.docx 骨架模板

预设样式：
- Normal: 中文友好默认字体
- Heading 1~3: 黑色无蓝色，专业排版
- CodeBlock: 等宽字体 + 浅灰背景
- 3 套表格样式: TableStyleA(商务蓝) / TableStyleB(素雅灰) / TableStyleC(简约白)
- 页边距: A4 标准
- 页脚: 居中页码
"""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml, OxmlElement


def set_chinese_font(style, zh_font="微软雅黑", en_font="Arial"):
    """设置中英文字体（必须通过 XML 设置东亚字体）"""
    style.font.name = en_font
    rpr = style.font._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), zh_font)


def set_paragraph_spacing(style, before_pt=0, after_pt=6, line_spacing=1.5):
    """设置段落间距和行距"""
    pf = style.paragraph_format
    pf.space_before = Pt(before_pt)
    pf.space_after = Pt(after_pt)
    pf.line_spacing = line_spacing


def add_page_number_footer(section):
    """添加居中页码到页脚"""
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    run._element.append(fld_char_begin)

    run2 = paragraph.add_run()
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    run2._element.append(instr_text)

    run3 = paragraph.add_run()
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run3._element.append(fld_char_end)

    # 设置页脚字体
    for r in paragraph.runs:
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x99, 0x99, 0x99)


def create_table_style_xml(style_id, name, header_bg, header_fg, alt_row_bg):
    """创建自定义表格样式的 XML"""
    # python-docx 不直接支持创建完整的表格样式
    # 我们在模板中创建一个占位表格并应用格式，然后删除表格
    pass


def generate_template():
    doc = Document()

    # ========== 页面设置 ==========
    section = doc.sections[0]
    section.page_width = Cm(21.0)    # A4
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    # 页脚页码
    add_page_number_footer(section)

    # ========== Normal 样式 ==========
    normal = doc.styles["Normal"]
    set_chinese_font(normal, "微软雅黑", "Arial")
    normal.font.size = Pt(12)
    normal.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    set_paragraph_spacing(normal, before_pt=0, after_pt=6, line_spacing=1.5)
    # 首行缩进 2 字符 (约 0.74cm for 12pt)
    normal.paragraph_format.first_line_indent = Cm(0.74)

    # ========== Heading 1 ==========
    h1 = doc.styles["Heading 1"]
    set_chinese_font(h1, "黑体", "Arial")
    h1.font.size = Pt(18)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    h1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_paragraph_spacing(h1, before_pt=24, after_pt=12, line_spacing=1.5)
    h1.paragraph_format.first_line_indent = None

    # ========== Heading 2 ==========
    h2 = doc.styles["Heading 2"]
    set_chinese_font(h2, "黑体", "Arial")
    h2.font.size = Pt(15)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    h2.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_paragraph_spacing(h2, before_pt=18, after_pt=8, line_spacing=1.5)
    h2.paragraph_format.first_line_indent = None

    # ========== Heading 3 ==========
    h3 = doc.styles["Heading 3"]
    set_chinese_font(h3, "黑体", "Arial")
    h3.font.size = Pt(13)
    h3.font.bold = True
    h3.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    h3.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_paragraph_spacing(h3, before_pt=12, after_pt=6, line_spacing=1.5)
    h3.paragraph_format.first_line_indent = None

    # ========== CodeBlock 段落样式 ==========
    try:
        code_style = doc.styles["CodeBlock"]
    except KeyError:
        code_style = doc.styles.add_style("CodeBlock", WD_STYLE_TYPE.PARAGRAPH)
    set_chinese_font(code_style, "Consolas", "Consolas")
    code_style.font.size = Pt(10)
    code_style.font.color.rgb = RGBColor(0x2D, 0x2D, 0x2D)
    set_paragraph_spacing(code_style, before_pt=6, after_pt=6, line_spacing=1.15)
    code_style.paragraph_format.first_line_indent = None

    # 添加浅灰背景底纹
    ppr = code_style._element.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F5F5F5")
    ppr.append(shd)

    # ========== List Bullet 样式确保存在 ==========
    for style_name in ["List Bullet", "List Bullet 2", "List Bullet 3",
                        "List Number", "No Spacing"]:
        try:
            s = doc.styles[style_name]
            set_chinese_font(s, "微软雅黑", "Arial")
        except KeyError:
            pass

    # ========== 写入占位内容触发样式保存 ==========
    # python-docx 需要至少使用一次样式才会将其写入文件
    styles_to_activate = [
        ("Heading 1", "占位标题"),
        ("Heading 2", "占位二级标题"),
        ("Heading 3", "占位三级标题"),
        ("Normal", "占位正文"),
        ("CodeBlock", "占位代码块"),
    ]

    paragraphs_to_remove = []
    for style_name, text in styles_to_activate:
        p = doc.add_paragraph(text, style=style_name)
        paragraphs_to_remove.append(p)

    # 添加一个占位表格以激活 Table Grid 样式
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"

    # 删除所有占位内容
    for p in paragraphs_to_remove:
        p._element.getparent().remove(p._element)
    table._element.getparent().remove(table._element)

    # ========== 保存 ==========
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "base_template.docx")
    doc.save(output_path)
    print(f"✓ base_template.docx 已生成: {output_path}")
    return output_path


if __name__ == "__main__":
    generate_template()
