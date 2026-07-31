"""
模板空白字段检测器

职责：
- 识别段落型空白（下划线/边框/括号）
- 识别表格型空白（空单元格+邻近标签）
- 生成候选变量列表

设计原则遵循：
- Async First: 使用 asyncio.to_thread 异步化阻塞操作
- Schema Validation: 使用 Pydantic 模型严格校验
- No Hardcoding: 检测规则从配置文件读取
- Design Rigor: 与 converter/analyzer 平行的独立模块
"""
import re
import asyncio
import logging
from typing import List, Optional, Literal, Tuple, Generator
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)


# ========== Pydantic 模型 ==========

class FieldLocation(BaseModel):
    """字段位置"""
    type: Literal["paragraph", "table_cell"]
    paragraph_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    selected_text: str = ""
    # 与 HTML data-id 一致的标识符，用于前端联动高亮
    mapping_id: Optional[str] = None


class CandidateField(BaseModel):
    """候选字段"""
    key: str  # f_0001
    label: str  # 项目名称
    type: str = "text"
    location: FieldLocation
    confidence: float  # 0.0 - 1.0
    source: str = "auto_detect"
    context: str = ""  # 周围文本上下文，包含填空处之前的文字


class LLMDetectedField(BaseModel):
    """LLM 识别的字段（用于 Tool Calling Schema）"""
    id: str                    # 对应的 data-id 或 blank-hint id
    label: str                 # 推断的字段名
    type: str = "text"         # 字段类型
    context: str = ""          # 周边原始文本（用于原文锚定校验）


class LLMDetectionResult(BaseModel):
    """LLM 检测结果 Schema"""
    fields: List[LLMDetectedField]


# ========== 检测器实现 ==========

class BlankFieldDetector:
    """
    空白字段检测器
    
    支持检测：
    - 连续下划线 ____、———、......
    - Run 下划线样式（空文本+下划线格式）
    - 段落底边框
    - 括号空白 （）、【】、〔〕
    - 空表格单元格
    """
    
    def __init__(self):
        self._settings = None
        self._field_counter = 0
    
    @property
    def settings(self):
        """懒加载配置"""
        if self._settings is None:
            self._settings = get_settings().template_detector
        return self._settings
    
    # ========== 辅助方法 ==========
    
    def _generate_key(self) -> str:
        """生成唯一的字段 key"""
        self._field_counter += 1
        return f"f_{self._field_counter:04d}"
    
    def _generate_default_label(self) -> str:
        """生成默认标签"""
        return f"字段_{self._field_counter:02d}"

    # ========== LLM 智能检测 ==========
    
    async def detect_via_llm_async(
        self, 
        dense_html: str, 
        mapping: List[dict]
    ) -> List[CandidateField]:
        """
        使用 LLM 语义分析检测空白字段
        
        [Async First] 异步调用 LLM
        [No Hardcoding] Prompts 从 YAML 配置文件读取
        [Schema Validation] 使用 generate_structured 强制结构化输出
        
        Args:
            dense_html: 经过 to_dense_html 清洗后的 HTML
            mapping: 元素 ID 到文本的映射（用于原文锚定校验）
            
        Returns:
            候选字段列表
        """
        from app.core.llm.async_llm import get_async_llm
        
        settings = self.settings
        if not settings.llm_detection_enabled:
            logger.info("LLM 检测已禁用，返回空列表")
            return []
        
        # 构建 ID -> 文本 的映射表（用于双重验证）
        id_to_text = {item["id"]: item.get("text", "") for item in mapping}
        # 构建 ID -> 选中文本 的映射表（用于编译时替换）
        id_to_selected_text = {item["id"]: item.get("selected_text", "") for item in mapping}
        # 构建 ID -> parent_id 的映射表（用于 blank_hint 回退验证）
        id_to_parent = {
            item["id"]: item.get("parent_id", "")
            for item in mapping if item.get("type") == "blank_hint"
        }
        
        # 加载 Prompts
        prompts = self._load_prompts()
        if not prompts:
            logger.error("无法加载 Prompt 配置文件")
            return []
        
        # 构建消息
        system_prompt = prompts.get("blank_detection_system", "")
        user_prompt = prompts.get("blank_detection_user", "").format(
            document_html=dense_html
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # 构建 Tool Schema（用于 generate_structured）
        tool_schema = {
            "type": "function",
            "function": {
                "name": "submit_detected_fields",
                "description": "提交识别到的填空字段",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "description": "识别到的字段列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "description": "data-id 或 blank-hint id"},
                                    "label": {"type": "string", "description": "推断的字段名"},
                                    "type": {"type": "string", "enum": ["text", "date", "number", "phone", "email"]},
                                    "context": {"type": "string", "description": "周边原始文本"}
                                },
                                "required": ["id", "label"]
                            }
                        }
                    },
                    "required": ["fields"]
                }
            }
        }
        
        try:
            llm = get_async_llm()
            result = await llm.generate_structured(
                messages=messages,
                tool_schema=tool_schema,
                model=settings.llm_detection_model,
                temperature=settings.llm_temperature
            )
            
            # 解析结果
            raw_fields = result.get("fields", [])
            logger.info(f"LLM 检测返回 {len(raw_fields)} 个候选字段")
            
            # 双重验证 + 转换为 CandidateField
            candidates = self._validate_and_convert(
                raw_fields, id_to_text, id_to_parent, id_to_selected_text
            )
            
            return candidates
            
        except Exception as e:
            logger.error(f"LLM 检测失败: {e}")
            return []
    
    def _load_prompts(self) -> dict:
        """
        从 YAML 配置文件加载 Prompts
        
        [No Hardcoding] Prompts 外置于配置文件
        """
        import yaml
        from pathlib import Path
        
        prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"
        prompt_file = prompts_dir / self.settings.llm_prompt_file
        
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"加载 Prompt 文件失败: {prompt_file}, 错误: {e}")
            return {}
    
    def _validate_and_convert(
        self, 
        raw_fields: List[dict], 
        id_to_text: dict,
        id_to_parent: dict = None,
        id_to_selected_text: dict = None
    ) -> List[CandidateField]:
        """
        双重验证并转换为 CandidateField
        
        验证规则：
        1. ID 存在性检查
        2. 原文锚定检查（Grounding Check）
        
        Args:
            raw_fields: LLM 返回的原始字段列表
            id_to_text: ID 到文本的映射
            id_to_parent: ID 到 parent_id 的映射（用于 blank_hint 回退）
        """
        from difflib import SequenceMatcher
        
        candidates = []
        self._field_counter = 0  # 重置计数器
        threshold = self.settings.validation_fuzzy_threshold
        id_to_parent = id_to_parent or {}
        id_to_selected_text = id_to_selected_text or {}
        
        for field in raw_fields:
            field_id = field.get("id", "")
            field_label = field.get("label", "")
            field_type = field.get("type", "text")
            field_context = field.get("context", "")
            
            # 验证 1: ID 存在性
            if field_id not in id_to_text:
                logger.warning(f"验证失败 - ID 不存在: {field_id}")
                continue
            
            # 验证 2: 原文锚定（如果提供了 context）
            if field_context:
                # 获取原文：优先使用当前 ID 的文本，若为空则回退到 parent_id
                original_text = id_to_text.get(field_id, "")
                if not original_text and field_id in id_to_parent:
                    parent_id = id_to_parent[field_id]
                    original_text = id_to_text.get(parent_id, "")
                    logger.debug(f"blank_hint 回退: {field_id} -> {parent_id}")
                
                # 标准化后模糊匹配
                normalized_context = self._normalize_text(field_context)
                normalized_original = self._normalize_text(original_text)

                if not normalized_original:
                    # 原文为空，无法锚定：视为中等置信度
                    confidence = 0.85
                else:
                    similarity = SequenceMatcher(
                        None, normalized_context, normalized_original
                    ).ratio()
                    
                    if similarity < threshold:
                        logger.warning(
                            f"验证失败 - 原文锚定不匹配: {field_id}, "
                            f"相似度={similarity:.2f}, 阈值={threshold}"
                        )
                        # 不直接跳过，降低置信度
                        confidence = 0.6  # 低置信度
                    else:
                        confidence = 0.95  # 高置信度
            else:
                confidence = 0.85  # 中等置信度（无 context 无法验证）
            
            # 解析位置信息
            location = self._parse_location_from_id(field_id)
            if field_id in id_to_selected_text:
                location.selected_text = id_to_selected_text.get(field_id, "") or ""
            
            # 生成 CandidateField
            candidate = CandidateField(
                key=self._generate_key(),
                label=field_label or self._generate_default_label(),
                type=field_type,
                location=location,
                confidence=confidence,
                source="llm_detect",
                context=field_context
            )
            candidates.append(candidate)
        
        logger.info(f"双重验证后保留 {len(candidates)}/{len(raw_fields)} 个字段")
        return candidates
    
    def _normalize_text(self, text: str) -> str:
        """标准化文本（用于模糊匹配）"""
        if not text:
            return ""
        # 移除换行、多余空格
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
    
    def _parse_location_from_id(self, field_id: str) -> FieldLocation:
        """
        从 ID 解析位置信息
        
        ID 格式：
        - 段落: p-{index}
        - 表格单元格: t{table}-r{row}-c{cell}
        - blank-hint: {parent_id}_blank_{index}
        """
        # 提取父 ID（对于 blank-hint）
        if "_blank_" in field_id:
            parent_id = field_id.split("_blank_")[0]
            return self._parse_location_from_id(parent_id)
        
        # 段落格式: p-{index}
        p_match = re.match(r'^p-(\d+)$', field_id)
        if p_match:
            return FieldLocation(
                type="paragraph",
                paragraph_index=int(p_match.group(1)),
                mapping_id=field_id
            )
        
        # 表格格式: t{table}-r{row}-c{cell}
        t_match = re.match(r'^t(\d+)-r(\d+)-c(\d+)$', field_id)
        if t_match:
            return FieldLocation(
                type="table_cell",
                table_index=int(t_match.group(1)),
                row_index=int(t_match.group(2)),
                cell_index=int(t_match.group(3)),
                mapping_id=field_id
            )
        
        # 无法解析，返回默认
        logger.warning(f"无法解析位置 ID: {field_id}")
        return FieldLocation(
            type="table_cell",
            mapping_id=field_id
        )


# ========== 单例工厂 ==========

_detector: BlankFieldDetector = None


def get_blank_field_detector() -> BlankFieldDetector:
    """获取检测器单例"""
    global _detector
    if _detector is None:
        _detector = BlankFieldDetector()
    return _detector
