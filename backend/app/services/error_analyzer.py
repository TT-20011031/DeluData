"""
DeluData 智能问数系统 - 错误分析服务

将硬编码的错误处理逻辑抽离为配置化服务，实现：
1. 错误模式匹配（关键词/错误码）
2. 用户友好提示生成
3. 可配置的错误映射
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple
import logging
import re

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)


# ========== 配置类 ==========

class ErrorMappingSettings(BaseSettings):
    """
    错误映射配置
    
    格式说明：
    - 每行一个映射规则，用 `|||` 分隔字段
    - 格式: 匹配模式|||错误类型|||原因说明|||建议操作
    - 匹配模式支持多个关键词，用 `|` 分隔（OR 逻辑）
    
    可通过环境变量 ERROR_MAPPINGS 覆盖默认配置
    """
    mappings: str = """
1146|doesn't exist|does not exist|table.*not found|||table_not_found|||SQL查询引用了不存在的表/视图（常见错误码 1146）|||1）确认数据库中表名是否一致；2）检查当前连接的数据库(schema)是否正确；3）告诉我正确的表名，我会重新生成 SQL
1044|1045|access denied|permission denied|||permission_denied|||数据库账号权限不足或认证失败|||1）在数据库配置里更新账号密码；2）给该账号授予查询相关表的权限；3）确认允许从当前IP连接
connection refused|can't connect|unable to connect|timeout|||connection_error|||无法连接到数据库服务器|||1）检查数据库服务是否正常运行；2）确认网络连接和防火墙设置；3）验证数据库连接配置是否正确
syntax error|you have an error in your sql|||sql_syntax_error|||SQL语法错误|||1）请提供更详细的查询描述；2）告诉我报错信息，我会修正 SQL
unknown column|column.*not found|||column_not_found|||SQL引用了不存在的字段|||1）确认字段名称是否正确；2）告诉我正确的字段名，我会重新生成 SQL
"""
    
    model_config = SettingsConfigDict(
        env_prefix="ERROR_",
        extra="ignore"
    )


# ========== 数据模型 ==========

@dataclass
class ErrorMapping:
    """错误映射规则"""
    patterns: List[str]  # 匹配模式列表（关键词或正则）
    error_type: str      # 错误类型标识
    reason: str          # 原因说明
    suggestions: str     # 建议操作


@dataclass
class ErrorAnalysisResult:
    """错误分析结果"""
    error_type: str          # 错误类型
    reason: str              # 原因说明
    suggestions: str         # 建议操作
    raw_error: str           # 原始错误信息
    matched_pattern: Optional[str] = None  # 匹配到的模式
    
    def to_user_message(self) -> str:
        """生成用户友好的错误消息"""
        lines = [
            "执行失败，我已停止后续步骤。",
            "",
            f"**错误详情**：{self.raw_error}",
            "",
            f"**可能原因**：{self.reason}",
            "",
            f"**建议操作**：{self.suggestions}"
        ]
        return "\n".join(lines)


# ========== 错误分析器 ==========

class ErrorAnalyzer:
    """
    错误分析器
    
    根据配置的错误映射规则，分析错误消息并生成用户友好提示。
    """
    
    # 默认兜底规则
    DEFAULT_REASON = "数据库结构/字段与生成的 SQL 不匹配，或连接配置异常"
    DEFAULT_SUGGESTIONS = "1）在数据库配置页重新连接；2）确认相关表与字段是否存在；3）把报错信息发我，我会据此调整 SQL"
    
    def __init__(self, settings: Optional[ErrorMappingSettings] = None):
        self.settings = settings or ErrorMappingSettings()
        self.mappings = self._parse_mappings()
    
    def _parse_mappings(self) -> List[ErrorMapping]:
        """解析配置中的错误映射规则"""
        mappings = []
        
        for line in self.settings.mappings.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            parts = line.split("|||")
            if len(parts) != 4:
                logger.warning(f"[ErrorAnalyzer] 无效的映射规则格式: {line}")
                continue
            
            patterns_str, error_type, reason, suggestions = parts
            patterns = [p.strip().lower() for p in patterns_str.split("|") if p.strip()]
            
            if patterns and error_type and reason:
                mappings.append(ErrorMapping(
                    patterns=patterns,
                    error_type=error_type.strip(),
                    reason=reason.strip(),
                    suggestions=suggestions.strip()
                ))
        
        logger.info(f"[ErrorAnalyzer] 加载 {len(mappings)} 条错误映射规则")
        return mappings
    
    def analyze(self, error_msg: str) -> ErrorAnalysisResult:
        """
        分析错误消息
        
        Args:
            error_msg: 原始错误消息
        
        Returns:
            ErrorAnalysisResult: 分析结果
        """
        lower_msg = error_msg.lower()
        
        # 遍历映射规则，查找匹配
        for mapping in self.mappings:
            matched_pattern = self._match_patterns(lower_msg, mapping.patterns)
            if matched_pattern:
                logger.debug(f"[ErrorAnalyzer] 匹配到规则: {mapping.error_type}, 模式: {matched_pattern}")
                return ErrorAnalysisResult(
                    error_type=mapping.error_type,
                    reason=mapping.reason,
                    suggestions=mapping.suggestions,
                    raw_error=error_msg,
                    matched_pattern=matched_pattern
                )
        
        # 无匹配，使用默认兜底
        logger.debug(f"[ErrorAnalyzer] 未匹配任何规则，使用默认提示")
        return ErrorAnalysisResult(
            error_type="unknown",
            reason=self.DEFAULT_REASON,
            suggestions=self.DEFAULT_SUGGESTIONS,
            raw_error=error_msg
        )
    
    def _match_patterns(self, text: str, patterns: List[str]) -> Optional[str]:
        """检查文本是否匹配任一模式"""
        for pattern in patterns:
            # 检查是否是正则表达式（包含正则特殊字符）
            if any(c in pattern for c in ['.*', '.+', '\\d', '\\w', '[', ']', '^', '$']):
                try:
                    if re.search(pattern, text):
                        return pattern
                except re.error:
                    # 正则无效，当作普通字符串匹配
                    if pattern in text:
                        return pattern
            else:
                # 普通关键词匹配
                if pattern in text:
                    return pattern
        return None


# ========== 单例获取 ==========

_error_analyzer: Optional[ErrorAnalyzer] = None


@lru_cache(maxsize=1)
def get_error_analyzer() -> ErrorAnalyzer:
    """获取错误分析器单例"""
    return ErrorAnalyzer()
