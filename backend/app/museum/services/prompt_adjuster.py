"""
博物馆模块 - 提示词调节器

遵循设计原则：
- 配置外置原则 (No Hardcoding): 风格模板、音色名称、Prompt 模板均从配置读取
- 全异步 I/O (Async First)

功能：
- 根据人物类型动态调节输出风格
- 支持逐步推送调节过程 (用于前端可视化)
"""
import asyncio
import logging
from typing import Callable, Optional, Awaitable

from app.museum.config import (
    get_museum_settings,
    get_style_template,
    get_voice_display_name,
    STYLE_TEMPLATES,
    GUIDE_PROMPT_TEMPLATE,
)
from app.museum.services.emotion_parser import build_emotion_prompt_hint
from app.museum.models import AdjustmentStep, AdjustmentResult

logger = logging.getLogger(__name__)


class PromptAdjuster:
    """
    提示词调节器
    
    根据人物类型动态调节：
    - 语言风格 (专业/活泼/优雅/清晰)
    - 内容侧重 (收藏价值/趣味故事/美学/历史)
    - 词汇选择 (专业术语/简单易懂)
    - 语音风格 (对应 TTS 音色)
    
    特性：
    - 毫秒级响应 (无 LLM 调用)
    - 支持 SSE 逐步推送调节过程
    """
    
    def __init__(self):
        self.settings = get_museum_settings()
    
    async def adjust(
        self,
        person_type: str,
        base_context: str = "",
        person_features: Optional[list[str]] = None,
        emit_progress: Optional[Callable[[str, str, str, int], Awaitable[None]]] = None
    ) -> AdjustmentResult:
        """
        执行调节并逐步推送进度
        
        Args:
            person_type: 人物类型
            base_context: 基础上下文 (用户问题)
            person_features: 视觉识别特征 (e.g. ["wearing suit", "carrying briefcase"])
            emit_progress: 进度推送回调 (step, label, value, progress)
            
        Returns:
            AdjustmentResult: 调节结果
        """
        style = get_style_template(person_type)
        
        # 逐步推送调节过程
        steps = [
            ("style", "语言风格", style["tone"]),
            ("focus", "内容侧重", style["focus"]),
            ("vocabulary", "词汇选择", style["vocabulary"]),
            ("voice", "语音风格", get_voice_display_name(person_type)),  # 从配置读取
        ]
        
        for i, (step_id, label, value) in enumerate(steps):
            progress = int((i + 1) / len(steps) * 100)
            
            if emit_progress:
                await emit_progress(step_id, label, value, progress)
            
            # 模拟处理时间 (让前端有时间展示动画)
            await asyncio.sleep(0.15)
        
        # 构建调节后的提示词
        adjusted_prompt = self._build_prompt(style, base_context, person_type, person_features)
        
        # 获取 TTS 音色
        voice_id = self.settings.get_voice_for_person(person_type)
        
        return AdjustmentResult(
            adjusted_prompt=adjusted_prompt,
            voice_id=voice_id,
            style_config=style
        )
    
    def get_style(self, person_type: str) -> dict:
        """获取人物类型对应的风格配置"""
        return get_style_template(person_type)
    
    def _build_prompt(
        self, 
        style: dict, 
        base_context: str, 
        person_type: str,
        person_features: Optional[list[str]] = None
    ) -> str:
        """构建调节后的提示词 (使用外置模板)"""
        
        # 构建情感标记提示
        emotion_hint = build_emotion_prompt_hint(person_type)
        
        # 视觉线索增强
        visual_hint = ""
        if person_features:
            features = "、".join(person_features)
            visual_hint = f"\n\n【视觉线索】访客特征：{features}。请在回答中自然地结合这些特征（例如夸奖小朋友的衣服、注意老人的步伐等）。"
        
        # 使用配置中的模板
        prompt = GUIDE_PROMPT_TEMPLATE.format(
            person_type=person_type,
            tone=style['tone'],
            focus=style['focus'],
            vocabulary=style['vocabulary'],
            example=style.get('example', ''),
            emotion_hint=emotion_hint + visual_hint
        )
        
        if base_context:
            prompt += f"\n### 访客问题\n{base_context}\n"
        
        return prompt
    
    def list_available_styles(self) -> dict:
        """列出所有可用的风格配置"""
        return STYLE_TEMPLATES


# ========== 单例工厂 ==========

_prompt_adjuster: Optional[PromptAdjuster] = None


def get_prompt_adjuster() -> PromptAdjuster:
    """获取提示词调节器单例"""
    global _prompt_adjuster
    if _prompt_adjuster is None:
        _prompt_adjuster = PromptAdjuster()
    return _prompt_adjuster
