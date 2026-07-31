"""
博物馆模块 - 情感标记解析器

遵循设计原则：
- 配置外置原则 (No Hardcoding): 情感标记映射从 config.py 读取
- 全异步 I/O (Async First)
- 零技术债策略 (Zero Tech Debt): 模块化设计，职责单一

功能：
- 解析 LLM 输出中的情感标记 [excited], [mysterious], [serious] 等
- 将情感标记映射为 TTS speaking_style 参数
- 支持流式文本中的情感标记提取

Author: Museum AI Guide System
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

from app.museum.config import (
    EMOTION_TAGS,
    PERSON_TYPE_DEFAULT_EMOTIONS,
    get_emotion_style,
    get_person_default_emotion,
)

logger = logging.getLogger(__name__)


@dataclass
class EmotionSegment:
    """情感文本片段"""
    text: str
    emotion: Optional[str] = None
    speaking_style: Optional[str] = None


def parse_emotion_tags(text: str, default_emotion: Optional[str] = None) -> list[EmotionSegment]:
    """
    解析文本中的情感标记
    
    Args:
        text: 包含情感标记的文本，如 "[excited]小朋友快看！[mysterious]你知道吗？"
        default_emotion: 默认情感（无标记时使用）
        
    Returns:
        list[EmotionSegment]: 情感片段列表
    """
    if not text:
        return []
    
    # 匹配情感标记: [emotion]
    pattern = r'\[([a-zA-Z_]+)\]'
    
    segments = []
    current_emotion = default_emotion
    last_end = 0
    
    for match in re.finditer(pattern, text):
        # 获取标记前的文本
        if match.start() > last_end:
            prefix_text = text[last_end:match.start()].strip()
            if prefix_text:
                segments.append(EmotionSegment(
                    text=prefix_text,
                    emotion=current_emotion,
                    speaking_style=get_speaking_style(current_emotion)
                ))
        
        # 更新当前情感
        emotion_tag = match.group(1).lower()
        if emotion_tag in EMOTION_TAGS:
            current_emotion = emotion_tag
        else:
            logger.warning(f"[EmotionParser] 未知情感标记: {emotion_tag}")
        
        last_end = match.end()
    
    # 处理最后一段文本
    if last_end < len(text):
        remaining_text = text[last_end:].strip()
        if remaining_text:
            segments.append(EmotionSegment(
                text=remaining_text,
                emotion=current_emotion,
                speaking_style=get_speaking_style(current_emotion)
            ))
    
    # 如果没有任何标记，返回整个文本作为一个片段
    if not segments and text.strip():
        segments.append(EmotionSegment(
            text=text.strip(),
            emotion=default_emotion,
            speaking_style=get_speaking_style(default_emotion)
        ))
    
    return segments


def strip_emotion_tags(text: str) -> str:
    """
    移除文本中的情感标记，只保留纯文本
    
    Args:
        text: 包含情感标记的文本
        
    Returns:
        str: 移除标记后的纯文本（保留原有空格）
    """
    pattern = r'\[[a-zA-Z_]+\]'
    # 不使用 .strip()，保留原有空格，避免流式文本拼接问题
    return re.sub(pattern, '', text)


def get_speaking_style(emotion: Optional[str]) -> Optional[str]:
    """获取情感对应的 speaking_style（委托给 config 模块）"""
    return get_emotion_style(emotion)


def get_default_emotion(person_type: str) -> str:
    """获取人物类型的默认情感（委托给 config 模块）"""
    return get_person_default_emotion(person_type)


def build_emotion_prompt_hint(person_type: str) -> str:
    """
    构建情感标记提示词
    
    Args:
        person_type: 人物类型
        
    Returns:
        str: 添加到系统提示词的情感标记说明
    """
    default_emotion = get_default_emotion(person_type)
    
    # 根据人物类型选择推荐的情感标记
    if person_type == "儿童":
        recommended = ["excited", "curious", "wow", "questioning", "happy"]
    elif person_type == "商务人士":
        recommended = ["serious", "formal", "explaining", "respectful"]
    elif person_type == "妇女":
        recommended = ["gentle", "warm", "amazed", "friendly"]
    elif person_type == "老年人":
        recommended = ["warm", "respectful", "explaining", "gentle"]
    else:
        recommended = ["friendly", "curious", "explaining", "happy"]
    
    tags_list = ", ".join([f"[{tag}]" for tag in recommended])
    
    return f"""
### 情感表达指南

请在回答中使用情感标记来增强表达效果。在句子开头添加情感标记，TTS 会据此调整语气。

**可用标记**: {tags_list}

**示例**:
- "[excited]哇，你看这个！" - 兴奋地说
- "[curious]你知道这是什么吗？" - 好奇地问
- "[mysterious]这里有个小秘密..." - 神秘地说

**注意**: 根据内容自然切换情感，不要每句都加标记。默认使用 [{default_emotion}] 风格。
"""
