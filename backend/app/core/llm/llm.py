"""
DeluData 智能问数系统 - LLM 客户端封装

统一管理 LLM 调用，支持通义千问
"""
from typing import Optional, List, Dict, Any, AsyncGenerator
import json
import dashscope
from dashscope import Generation
from dashscope.api_entities.dashscope_response import GenerationResponse

from app.config import get_settings


class LLMClient:
    """
    LLM 客户端
    
    封装通义千问 API 调用，提供统一接口
    """
    
    def __init__(self, model_name: Optional[str] = None):
        """
        初始化 LLM 客户端
        
        Args:
            model_name: 模型名称，默认使用配置中的模型
        """
        settings = get_settings()
        self.api_key = settings.llm.dashscope_api_key
        self.flash_disable_thinking = bool(getattr(settings.llm, "flash_disable_thinking", True))
        # 兼容旧代码，优先使用构造函数传入的模型，否则使用配置中的默认模型
        self.model_name = model_name or settings.llm.model
        
        # 设置全局 API Key (DashScope SDK 要求)
        if self.api_key:
            dashscope.api_key = self.api_key

    @staticmethod
    def _is_flash_model(model_name: Optional[str]) -> bool:
        return "flash" in str(model_name or "").strip().lower()

    def _thinking_kwargs(self) -> Dict[str, Any]:
        if self.flash_disable_thinking and self._is_flash_model(self.model_name):
            return {"enable_thinking": False}
        return {}
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> str:
        """
        异步对话 (包装同步 SDK 调用)
        """
        import asyncio
        
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        
        def _sync_call():
            response = Generation.call(
                model=self.model_name,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                result_format="message",
                **self._thinking_kwargs(),
            )
            if response.status_code == 200:
                return response.output.choices[0].message.content
            else:
                raise Exception(f"LLM 调用失败: {response.code} - {response.message}")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)
    
    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        流式对话 (异步队列转换)
        """
        import asyncio
        
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        
        params = {
            "model": self.model_name,
            "messages": full_messages,
            "temperature": temperature,
            "result_format": "message",
            "stream": True,
            "incremental_output": True,
        }
        params.update(self._thinking_kwargs())

        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _producer():
            try:
                responses = Generation.call(**params)
                for response in responses:
                    loop.call_soon_threadsafe(queue.put_nowait, response)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)

        loop.run_in_executor(None, _producer)

        while True:
            item = await queue.get()
            if item is None: break
            if isinstance(item, Exception): raise item
            
            if item.status_code == 200:
                content = item.output.choices[0].message.content
                if content: yield content
            else:
                raise Exception(f"LLM 流式调用失败: {item.code}")
    
    async def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成 JSON 格式输出
        
        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            schema_hint: JSON 格式提示
            
        Returns:
            解析后的 JSON 对象
        """
        enhanced_system = system_prompt or ""
        enhanced_system += "\n\n请用 JSON 格式回复，不要包含其他文字。"
        
        if schema_hint:
            enhanced_system += f"\n\nJSON 格式要求:\n{schema_hint}"
        
        response = await self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=enhanced_system,
            temperature=0.3
        )
        
        # 尝试提取 JSON
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        
        return json.loads(response.strip())


# 全局 LLM 客户端实例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
