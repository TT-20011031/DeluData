"""
博物馆模块 - 人物识别服务

遵循设计原则：
- 全异步 I/O (Async First)
- 配置外置原则 (No Hardcoding)

功能：
- 使用 VLM 分析图片中的人物
- 5 秒超时优雅降级
"""
import asyncio
import json
import logging
import base64
from typing import Optional
from pathlib import Path

from app.museum.config import get_museum_settings, get_prompt_settings
from app.museum.models import PersonAnalysisResult, PersonType

logger = logging.getLogger(__name__)


class PersonRecognizer:
    """
    人物识别服务
    
    使用 VLM (通义千问视觉模型) 分析图片中的人物类型
    
    特性：
    - 独立 VLM 调用，不影响主系统
    - 5 秒超时优雅降级
    - 支持多种图片输入方式
    """
    
    def __init__(self):
        self.settings = get_museum_settings()
        self.prompt_settings = get_prompt_settings()
        self._client = None
    
    @property
    def client(self):
        """懒加载 DashScope 客户端"""
        if self._client is None:
            try:
                from dashscope import MultiModalConversation
                self._client = MultiModalConversation
            except ImportError:
                logger.error("[PersonRecognizer] dashscope 未安装")
                raise ImportError("请安装 dashscope: pip install dashscope")
        return self._client
    
    async def analyze(self, image_path: str) -> PersonAnalysisResult:
        """
        分析图片中的人物
        
        Args:
            image_path: 图片文件路径或 URL
            
        Returns:
            PersonAnalysisResult: 识别结果
        """
        try:
            # 使用 asyncio.wait_for 实现超时
            return await asyncio.wait_for(
                self._analyze_impl(image_path),
                timeout=self.settings.vlm_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[PersonRecognizer] VLM 超时 ({self.settings.vlm_timeout}s)，降级为通用访客"
            )
            return PersonAnalysisResult(
                person_type=PersonType.GENERAL,
                confidence=0.0,
                features=[],
                fallback=True,
                reason=f"识别超时 ({self.settings.vlm_timeout}s)，已切换为通用模式"
            )
        except Exception as e:
            logger.error(f"[PersonRecognizer] VLM 调用失败: {e}")
            return PersonAnalysisResult(
                person_type=PersonType.GENERAL,
                confidence=0.0,
                features=[],
                fallback=True,
                reason=f"识别失败: {str(e)}"
            )
    
    async def _analyze_impl(self, image_path: str) -> PersonAnalysisResult:
        """实际的 VLM 分析实现"""
        
        # 准备图片内容
        image_content = await self._prepare_image(image_path)
        
        # 构建消息
        messages = [
            {
                "role": "user",
                "content": [
                    image_content,
                    {"text": self.prompt_settings.person_recognizer_prompt}
                ]
            }
        ]
        
        # 调用 VLM (使用 asyncio.to_thread 避免阻塞)
        response = await asyncio.to_thread(
            self.client.call,
            model=self.settings.vlm_model,
            messages=messages,
            max_tokens=self.settings.vlm_max_tokens,
        )
        
        # 解析响应
        return self._parse_response(response)
    
    async def _prepare_image(self, image_path: str) -> dict:
        """准备图片内容"""
        
        # URL 格式
        if image_path.startswith(('http://', 'https://')):
            return {"image": image_path}
        
        # 本地文件
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        
        # 读取并编码为 base64
        image_bytes = await asyncio.to_thread(path.read_bytes)
        base64_data = base64.b64encode(image_bytes).decode()
        
        # 根据扩展名确定 MIME 类型
        ext = path.suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        mime_type = mime_map.get(ext, 'image/jpeg')
        
        return {"image": f"data:{mime_type};base64,{base64_data}"}
    
    def _parse_response(self, response) -> PersonAnalysisResult:
        """解析 VLM 响应"""
        try:
            # 提取文本内容
            if response.status_code != 200:
                raise ValueError(f"API 调用失败: {response.message}")
            
            content = response.output.choices[0].message.content
            
            # 如果是列表格式，取第一个文本
            if isinstance(content, list):
                text = None
                for item in content:
                    if isinstance(item, dict) and 'text' in item:
                        text = item['text']
                        break
                if not text:
                    raise ValueError("响应中未找到文本内容")
            else:
                text = str(content)
            
            # 尝试解析 JSON
            # 查找 JSON 块
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = text[json_start:json_end]
                data = json.loads(json_str)
                
                # 映射人物类型
                person_type_str = data.get('person_type', '通用访客')
                try:
                    person_type = PersonType(person_type_str)
                except ValueError:
                    person_type = PersonType.GENERAL
                
                return PersonAnalysisResult(
                    person_type=person_type,
                    confidence=float(data.get('confidence', 0.8)),
                    features=data.get('features', []),
                    fallback=False,
                    reason=None
                )
            
            # 如果无法解析 JSON，尝试关键词匹配
            return self._fallback_parse(text)
            
        except json.JSONDecodeError as e:
            logger.warning(f"[PersonRecognizer] JSON 解析失败: {e}")
            return self._fallback_parse(str(content) if content else "")
        except Exception as e:
            logger.error(f"[PersonRecognizer] 响应解析失败: {e}")
            return PersonAnalysisResult(
                person_type=PersonType.GENERAL,
                confidence=0.5,
                features=[],
                fallback=True,
                reason=f"响应解析失败: {str(e)}"
            )
    
    def _fallback_parse(self, text: str) -> PersonAnalysisResult:
        """降级解析：关键词匹配"""
        text_lower = text.lower()
        
        if '商务' in text or '西装' in text or '职业' in text:
            return PersonAnalysisResult(
                person_type=PersonType.BUSINESS,
                confidence=0.6,
                features=['关键词匹配'],
                fallback=True,
                reason="基于关键词匹配"
            )
        elif '儿童' in text or '小孩' in text or '孩子' in text:
            return PersonAnalysisResult(
                person_type=PersonType.CHILD,
                confidence=0.6,
                features=['关键词匹配'],
                fallback=True,
                reason="基于关键词匹配"
            )
        elif '老年' in text or '老人' in text:
            return PersonAnalysisResult(
                person_type=PersonType.ELDERLY,
                confidence=0.6,
                features=['关键词匹配'],
                fallback=True,
                reason="基于关键词匹配"
            )
        elif '女' in text or '妇' in text:
            return PersonAnalysisResult(
                person_type=PersonType.WOMAN,
                confidence=0.6,
                features=['关键词匹配'],
                fallback=True,
                reason="基于关键词匹配"
            )
        
        return PersonAnalysisResult(
            person_type=PersonType.GENERAL,
            confidence=0.5,
            features=[],
            fallback=True,
            reason="无法识别具体类型"
        )


# ========== 单例工厂 ==========

_person_recognizer: Optional[PersonRecognizer] = None


def get_person_recognizer() -> PersonRecognizer:
    """获取人物识别服务单例"""
    global _person_recognizer
    if _person_recognizer is None:
        _person_recognizer = PersonRecognizer()
    return _person_recognizer
