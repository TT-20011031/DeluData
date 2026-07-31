"""
动态样式注入引擎

职责：
- 接收 style_config JSON，动态覆盖 Document 内置样式属性
- 提供 3 套预设主题（现代商务 / 学术论文 / 简洁清爽）
- 处理中文字体 XML 底层设置（w:eastAsia）

设计原则：
- Python 代码不硬编码样式值，只负责「注入配置到样式对象」
- 配置合并优先级：用户偏好 > workspace默认 > 系统内置默认
"""
import logging
from typing import Any

from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

logger = logging.getLogger(__name__)

# ========== 预设主题 ==========

PRESET_THEMES: dict[str, dict[str, Any]] = {
    "modern_business": {
        "normal": {
            "font_family_en": "Arial",
            "font_family_zh": "微软雅黑",
            "font_size_pt": 12,
            "line_spacing": 1.5,
            "first_line_indent_chars": 2,
            "color_hex": "333333",
        },
        "heading_1": {
            "font_family_zh": "黑体",
            "font_family_en": "Arial",
            "font_size_pt": 18,
            "color_hex": "1A1A1A",
            "alignment": "center",
            "bold": True,
            "space_before_pt": 24,
            "space_after_pt": 12,
        },
        "heading_2": {
            "font_family_zh": "黑体",
            "font_family_en": "Arial",
            "font_size_pt": 15,
            "color_hex": "1A1A1A",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 18,
            "space_after_pt": 8,
        },
        "heading_3": {
            "font_family_zh": "黑体",
            "font_family_en": "Arial",
            "font_size_pt": 13,
            "color_hex": "333333",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 12,
            "space_after_pt": 6,
        },
        "table_header_bg": "2B579A",
        "table_header_fg": "FFFFFF",
        "table_alt_row_bg": "F2F7FB",
        "table_style_name": "Table Grid",
    },
    "academic": {
        "normal": {
            "font_family_en": "Times New Roman",
            "font_family_zh": "宋体",
            "font_size_pt": 12,
            "line_spacing": 1.5,
            "first_line_indent_chars": 2,
            "color_hex": "000000",
        },
        "heading_1": {
            "font_family_zh": "黑体",
            "font_family_en": "Times New Roman",
            "font_size_pt": 16,
            "color_hex": "000000",
            "alignment": "center",
            "bold": True,
            "space_before_pt": 24,
            "space_after_pt": 12,
        },
        "heading_2": {
            "font_family_zh": "黑体",
            "font_family_en": "Times New Roman",
            "font_size_pt": 14,
            "color_hex": "000000",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 18,
            "space_after_pt": 8,
        },
        "heading_3": {
            "font_family_zh": "楷体",
            "font_family_en": "Times New Roman",
            "font_size_pt": 13,
            "color_hex": "000000",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 12,
            "space_after_pt": 6,
        },
        "table_header_bg": "333333",
        "table_header_fg": "FFFFFF",
        "table_alt_row_bg": "F5F5F5",
        "table_style_name": "Table Grid",
    },
    "clean_minimal": {
        "normal": {
            "font_family_en": "Calibri",
            "font_family_zh": "微软雅黑",
            "font_size_pt": 11,
            "line_spacing": 1.15,
            "first_line_indent_chars": 0,
            "color_hex": "444444",
        },
        "heading_1": {
            "font_family_zh": "微软雅黑",
            "font_family_en": "Calibri",
            "font_size_pt": 20,
            "color_hex": "222222",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 30,
            "space_after_pt": 10,
        },
        "heading_2": {
            "font_family_zh": "微软雅黑",
            "font_family_en": "Calibri",
            "font_size_pt": 16,
            "color_hex": "333333",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 20,
            "space_after_pt": 8,
        },
        "heading_3": {
            "font_family_zh": "微软雅黑",
            "font_family_en": "Calibri",
            "font_size_pt": 13,
            "color_hex": "555555",
            "alignment": "left",
            "bold": True,
            "space_before_pt": 14,
            "space_after_pt": 6,
        },
        "table_header_bg": "E8E8E8",
        "table_header_fg": "333333",
        "table_alt_row_bg": "FAFAFA",
        "table_style_name": "Table Grid",
    },
}

DEFAULT_THEME = "modern_business"


def _set_chinese_font(style, zh_font: str, en_font: str) -> None:
    """设置中英文字体（必须通过 XML 设置东亚字体）"""
    style.font.name = en_font
    rpr = style.font._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), zh_font)


def _apply_heading_config(style, cfg: dict[str, Any]) -> None:
    """将配置应用到标题样式"""
    zh = cfg.get("font_family_zh", "黑体")
    en = cfg.get("font_family_en", "Arial")
    _set_chinese_font(style, zh, en)

    if "font_size_pt" in cfg:
        style.font.size = Pt(cfg["font_size_pt"])

    if "color_hex" in cfg:
        style.font.color.rgb = RGBColor.from_string(cfg["color_hex"])

    if cfg.get("bold") is not None:
        style.font.bold = cfg["bold"]

    alignment_map = {
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    if "alignment" in cfg:
        style.paragraph_format.alignment = alignment_map.get(
            cfg["alignment"], WD_ALIGN_PARAGRAPH.LEFT
        )

    if "space_before_pt" in cfg:
        style.paragraph_format.space_before = Pt(cfg["space_before_pt"])
    if "space_after_pt" in cfg:
        style.paragraph_format.space_after = Pt(cfg["space_after_pt"])

    style.paragraph_format.first_line_indent = None


def _apply_normal_config(style, cfg: dict[str, Any]) -> None:
    """将配置应用到 Normal 样式"""
    zh = cfg.get("font_family_zh", "微软雅黑")
    en = cfg.get("font_family_en", "Arial")
    _set_chinese_font(style, zh, en)

    if "font_size_pt" in cfg:
        style.font.size = Pt(cfg["font_size_pt"])

    if "color_hex" in cfg:
        style.font.color.rgb = RGBColor.from_string(cfg["color_hex"])

    if "line_spacing" in cfg:
        style.paragraph_format.line_spacing = cfg["line_spacing"]

    indent_chars = cfg.get("first_line_indent_chars", 2)
    if indent_chars and indent_chars > 0:
        font_size = cfg.get("font_size_pt", 12)
        indent_cm = indent_chars * font_size * 0.0353
        style.paragraph_format.first_line_indent = Cm(indent_cm)
    else:
        style.paragraph_format.first_line_indent = None

    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(cfg.get("space_after_pt", 6))


def resolve_style_config(
    user_config: dict[str, Any] | None = None,
    workspace_config: dict[str, Any] | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    """
    合并样式配置（优先级：user > workspace > preset > 内置默认）

    Args:
        user_config: 用户个人偏好
        workspace_config: 工作空间默认
        preset_name: 预设主题名

    Returns:
        合并后的完整样式配置
    """
    theme_name = preset_name or DEFAULT_THEME
    base = dict(PRESET_THEMES.get(theme_name, PRESET_THEMES[DEFAULT_THEME]))

    if workspace_config:
        _deep_merge(base, workspace_config)
    if user_config:
        _deep_merge(base, user_config)

    return base


def _deep_merge(base: dict, override: dict) -> None:
    """深度合并字典（override 覆盖 base）"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def apply_dynamic_styles(doc, style_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    将样式配置动态注入到 Document 对象

    Args:
        doc: python-docx Document 对象
        style_config: 完整的样式配置字典（由 resolve_style_config 生成）

    Returns:
        实际使用的样式配置（供 markdown_converter 引用表格颜色等）
    """
    if style_config is None:
        style_config = PRESET_THEMES[DEFAULT_THEME]

    # Normal 样式
    normal_cfg = style_config.get("normal", {})
    if normal_cfg:
        try:
            _apply_normal_config(doc.styles["Normal"], normal_cfg)
        except Exception as e:
            logger.warning("Normal 样式注入失败: %s", e)

    # Heading 1~3
    for level in range(1, 4):
        key = f"heading_{level}"
        cfg = style_config.get(key, {})
        if cfg:
            style_name = f"Heading {level}"
            try:
                _apply_heading_config(doc.styles[style_name], cfg)
            except Exception as e:
                logger.warning("%s 样式注入失败: %s", style_name, e)

    # List 样式继承 Normal 字体
    zh = normal_cfg.get("font_family_zh", "微软雅黑")
    en = normal_cfg.get("font_family_en", "Arial")
    for list_style_name in ["List Bullet", "List Bullet 2", "List Bullet 3", "List Number"]:
        try:
            _set_chinese_font(doc.styles[list_style_name], zh, en)
        except (KeyError, Exception):
            pass

    logger.info("动态样式注入完成, 主题配置键: %s", list(style_config.keys()))
    return style_config
