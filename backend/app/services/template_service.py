"""
模板服务（兼容层）

此文件保留用于向后兼容，新代码请使用:
    from app.services.template import get_template_service
"""
from app.services.template import (
    TemplateService,
    get_template_service,
    TemplateAnalyzer,
    get_template_analyzer,
    TemplateConverter,
    get_template_converter,
    TemplateRenderer,
    get_template_renderer,
)

__all__ = [
    "TemplateService",
    "get_template_service",
    "TemplateAnalyzer",
    "get_template_analyzer",
    "TemplateConverter",
    "get_template_converter",
    "TemplateRenderer",
    "get_template_renderer",
]
