"""
阿里云语音识别 ASR 服务

兼容性模块：包装器 (Wrapper) 模式，保持 Museum 原有 API 向后兼容。

【关键修复】使用组合代替继承 (Composition over Inheritance)

问题：如果继承 AliyunASRService 并重写 get_token 为同步方法，
父类的 transcribe() 调用 await self.get_token() 会导致:
TypeError: object str can't be used in 'await' expression

解决：ASRService 不继承，而是包装一个 _core_service 实例，
这样核心服务内部的 await self.get_token() 调用的是自己的异步方法。
"""

import asyncio
from typing import Dict, Any, Optional

from app.core.voice.asr import (
    AliyunASRService as _AliyunASRService, 
    ASRResult,
)


class ASRService:
    """
    Museum 兼容的 ASR 服务
    
    使用组合模式包装核心服务，避免继承导致的 Sync/Async 冲突
    """
    
    def __init__(self):
        """初始化时创建核心服务实例"""
        self._core_service = _AliyunASRService()
    
    @property
    def appkey(self) -> Optional[str]:
        """兼容旧代码的 appkey 属性访问"""
        return self._core_service.get_appkey()
    
    def get_appkey(self) -> Optional[str]:
        """获取 AppKey（方法形式）"""
        return self._core_service.get_appkey()
    
    def get_token(self) -> Optional[str]:
        """
        同步获取 Token（兼容旧 API）
        
        注意：旧代码调用 get_token() 是同步的
        核心服务的 get_token 是异步的，需要包装
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop and loop.is_running():
            # 在异步上下文中，返回缓存的 token
            from app.core.voice.asr.aliyun_asr import _token_cache
            return _token_cache.get('token')
        else:
            # 在同步上下文中，使用 asyncio.run
            return asyncio.run(self._core_service.get_token())
    
    async def get_token_async(self) -> Optional[str]:
        """异步获取 Token"""
        return await self._core_service.get_token()
    
    async def transcribe_audio_file(
        self, 
        audio_content: bytes, 
        content_type: str = ""
    ) -> Dict[str, Any]:
        """
        语音转文字（兼容旧 API）
        
        这是旧 API 方法，返回 {'success': bool, 'text': str, 'error': str}
        
        【关键】调用核心服务的 transcribe_file，核心服务内部会用自己的
        异步 get_token()，不受本类同步 get_token 的影响
        """
        result: ASRResult = await self._core_service.transcribe_file(
            audio_content, 
            content_type
        )
        
        return {
            'success': result.success,
            'text': result.text,
            'error': result.error_message or result.user_message or ''
        }
    
    async def transcribe(
        self,
        audio_content: bytes,
        audio_format: str = 'wav',
        sample_rate: int = 16000
    ) -> ASRResult:
        """
        语音转文字（新 API，返回 ASRResult）
        
        透传到核心服务
        """
        return await self._core_service.transcribe(
            audio_content, 
            audio_format, 
            sample_rate
        )
    
    async def transcribe_file(
        self,
        audio_content: bytes,
        content_type: str = ""
    ) -> ASRResult:
        """
        处理上传的音频文件（新 API）
        
        透传到核心服务
        """
        return await self._core_service.transcribe_file(
            audio_content,
            content_type
        )


# 单例
_asr_service_instance: Optional[ASRService] = None


def get_asr_service() -> ASRService:
    """
    获取 Museum 兼容的 ASR 服务单例
    """
    global _asr_service_instance
    if _asr_service_instance is None:
        _asr_service_instance = ASRService()
    return _asr_service_instance
