"""
模板服务模块

提供模板管理的完整功能：
- CRUD 业务逻辑
- 变量分析
- 文档转换
- 模板渲染与编译
- 空白字段检测
"""
from .service import TemplateService, get_template_service
from .analyzer import TemplateAnalyzer, get_template_analyzer
from .converter import TemplateConverter, get_template_converter
from .renderer import TemplateRenderer, get_template_renderer
from .detector import (
    BlankFieldDetector,
    get_blank_field_detector,
    CandidateField,
    FieldLocation,
)

__all__ = [
    # 主服务
    "TemplateService",
    "get_template_service",
    # 分析器
    "TemplateAnalyzer",
    "get_template_analyzer",
    # 转换器
    "TemplateConverter",
    "get_template_converter",
    # 渲染器
    "TemplateRenderer",
    "get_template_renderer",
    # 检测器
    "BlankFieldDetector",
    "get_blank_field_detector",
    "CandidateField",
    "FieldLocation",
]

