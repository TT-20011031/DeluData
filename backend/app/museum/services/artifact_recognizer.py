"""
博物馆模块 - 文物识别服务

遵循设计原则：
- 全异步 I/O (Async First)
- 配置外置原则 (No Hardcoding)

功能：
- 使用 VLM 识别图片中的文物
- 返回文物名称和简要描述，供 Query Rewriter 使用
"""
import asyncio
import json
import logging
import base64
from typing import Optional, Tuple
from pathlib import Path
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.museum.config import get_museum_settings, get_prompt_settings

logger = logging.getLogger(__name__)


@dataclass
class ArtifactRecognitionResult:
    """文物识别结果"""
    artifact_name: str  # 识别到的文物名称
    description: str    # 简要描述
    confidence: float   # 置信度 0-1
    fallback: bool      # 是否降级
    reason: str = ""    # 降级原因


class ArtifactRecognizer:
    """文物识别服务
    
    使用 VLM (通义千问视觉模型) 识别图片中的文物
    
    特性：
    - 与人物识别并行执行
    - 识别结果用于 Query Rewriter
    - 超时优雅降级
    """
    
    def __init__(self):
        self.settings = get_museum_settings()
        self.prompt_settings = get_prompt_settings()
        self._client: Optional[AsyncOpenAI] = None
    
    @property
    def client(self) -> AsyncOpenAI:
        """懒加载 DashScope 客户端"""
        if self._client is None:
            import os
            api_key = os.getenv("DASHSCOPE_API_KEY", "")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.llm_base_url
            )
        return self._client
    
    async def recognize(self, image_path: str, timeout: float = 10.0) -> ArtifactRecognitionResult:
        """
        识别图片中的文物
        
        Args:
            image_path: 图片文件路径或 URL
            timeout: 超时时间（秒）
            
        Returns:
            ArtifactRecognitionResult: 识别结果
        """
        try:
            result = await asyncio.wait_for(
                self._recognize_impl(image_path),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[ArtifactRecognizer] VLM 识别超时 ({timeout}s)")
            return ArtifactRecognitionResult(
                artifact_name="",
                description="",
                confidence=0.0,
                fallback=True,
                reason=f"VLM超时({timeout}s)"
            )
        except Exception as e:
            logger.error(f"[ArtifactRecognizer] 识别失败: {e}")
            return ArtifactRecognitionResult(
                artifact_name="",
                description="",
                confidence=0.0,
                fallback=True,
                reason=str(e)[:50]
            )
    
    async def _recognize_impl(self, image_path: str) -> ArtifactRecognitionResult:
        """实际的 VLM 识别实现"""
        # 准备图片内容
        image_content = self._prepare_image(image_path)
        
        # 构建识别 Prompt
        prompt = self.prompt_settings.artifact_recognition_prompt
        
        # 调用 VLM
        response = await self.client.chat.completions.create(
            model=self.settings.vlm_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": image_content},
                    {"type": "text", "text": prompt}
                ]
            }],
            max_tokens=500,
            temperature=0.3,
        )
        
        # 解析响应
        return self._parse_response(response)
    
    def _prepare_image(self, image_path: str) -> dict:
        """准备图片内容"""
        if image_path.startswith(('http://', 'https://')):
            return {"url": image_path}
        
        # 本地文件转 base64
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        
        with open(path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # 根据扩展名确定 MIME 类型
        suffix = path.suffix.lower()
        mime_map = {'.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.gif': 'gif', '.webp': 'webp'}
        mime_type = mime_map.get(suffix, 'jpeg')
        
        return {"url": f"data:image/{mime_type};base64,{image_data}"}
    
    def _parse_response(self, response) -> ArtifactRecognitionResult:
        """解析 VLM 响应"""
        try:
            content = response.choices[0].message.content
            logger.debug(f"[ArtifactRecognizer] VLM 原始响应: {content[:200]}...")
            
            # 尝试解析 JSON
            # 清理可能的 markdown 标记
            clean_content = content.replace("```json", "").replace("```", "").strip()
            
            try:
                result = json.loads(clean_content)
                
                # 检查是否是文物图片
                is_artifact = result.get("is_artifact", True)  # 默认True以兼容旧格式
                if not is_artifact:
                    # VLM明确判断不是文物（如人物照片）
                    logger.info(f"[ArtifactRecognizer] VLM判断非文物图片，跳过识别")
                    return ArtifactRecognitionResult(
                        artifact_name="",
                        description="",
                        confidence=0.0,
                        fallback=False,
                        reason="非文物图片"
                    )
                
                artifact_name = result.get("artifact_name", "")
                description = result.get("description", "")
                confidence = float(result.get("confidence", 0.5))
                
                if artifact_name:
                    logger.info(f"[ArtifactRecognizer] 识别成功: {artifact_name} (置信度: {confidence})")
                    return ArtifactRecognitionResult(
                        artifact_name=artifact_name,
                        description=description,
                        confidence=confidence,
                        fallback=False
                    )
            except json.JSONDecodeError:
                pass
            
            # JSON 解析失败，尝试从文本中提取
            return self._fallback_parse(content)
            
        except Exception as e:
            logger.error(f"[ArtifactRecognizer] 解析 VLM 响应失败: {e}")
            return ArtifactRecognitionResult(
                artifact_name="",
                description="",
                confidence=0.0,
                fallback=True,
                reason=f"解析失败: {str(e)[:30]}"
            )
    
    def _fallback_parse(self, text: str) -> ArtifactRecognitionResult:
        """降级解析：从文本中提取文物名称"""
        # 简单策略：查找引号内容或常见文物名称模式
        import re
        
        # 匹配引号内的名称
        quoted = re.findall(r'[「『""]([^」』""]+)[」』""]', text)
        if quoted:
            return ArtifactRecognitionResult(
                artifact_name=quoted[0],
                description=text[:100],
                confidence=0.6,
                fallback=True,
                reason="从引号提取"
            )
        
        # 匹配常见文物名称模式
        artifact_pattern = r'([\u4e00-\u9fff]{2,}[鼎尊壶瓶罐盘碗钟簋鬲甗觚爵觥卣盉匜剑戈矛钺镜印玺俑马]|[\u4e00-\u9fff]{2,}(玉衣|骨笛|铜器|陶器|瓷器|漆器))'
        match = re.search(artifact_pattern, text)
        if match:
            return ArtifactRecognitionResult(
                artifact_name=match.group(0),
                description=text[:100],
                confidence=0.5,
                fallback=True,
                reason="从模式提取"
            )
        
        # 无法识别
        return ArtifactRecognitionResult(
            artifact_name="",
            description=text[:100] if text else "",
            confidence=0.0,
            fallback=True,
            reason="无法识别文物"
        )


# ========== 单例工厂 ==========

_artifact_recognizer: Optional[ArtifactRecognizer] = None


def get_artifact_recognizer() -> ArtifactRecognizer:
    """获取文物识别服务单例"""
    global _artifact_recognizer
    if _artifact_recognizer is None:
        _artifact_recognizer = ArtifactRecognizer()
    return _artifact_recognizer
