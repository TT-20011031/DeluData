"""
异步 LLM 客户端

基于 OpenAI 兼容端点，统一调用 DashScope (Qwen) 系列模型
"""
import logging
import json
import re
from typing import AsyncGenerator, Optional, Any

from openai import AsyncOpenAI

from app.config import get_settings


class AsyncLLMClient:
    """
    异步 LLM 客户端
    
    封装 DashScope API 的异步调用，避免阻塞主线程
    """
    
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
        )
        self.model = settings.llm.model
        self.temperature = settings.llm.temperature
        self.max_tokens = settings.llm.max_tokens
        self.flash_disable_thinking = bool(getattr(settings.llm, "flash_disable_thinking", True))

    @staticmethod
    def _is_flash_model(model_name: Optional[str]) -> bool:
        return "flash" in str(model_name or "").strip().lower()

    def _prepare_request_kwargs(
        self,
        *,
        model_name: str,
        force_disable_thinking: bool = False,
        extra_body: Any = None,
    ) -> dict:
        request_kwargs: dict[str, Any] = {}
        extra_body_payload = dict(extra_body) if isinstance(extra_body, dict) else {}

        if force_disable_thinking or (
            self.flash_disable_thinking and self._is_flash_model(model_name)
        ):
            extra_body_payload["enable_thinking"] = False

        if extra_body_payload:
            request_kwargs["extra_body"] = extra_body_payload
        return request_kwargs

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        统一聊天接口（自动识别多模态）
        
        所有模型统一走 OpenAI 兼容端点，无需区分文本/多模态路径
        """
        _model = model or self.model
        _temperature = temperature if temperature is not None else self.temperature
        _max_tokens = max_tokens or self.max_tokens
        normalized = self._normalize_to_openai_format(messages)
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            extra_body=kwargs.get("extra_body"),
        )

        response = await self.client.chat.completions.create(
            model=_model,
            messages=normalized,
            temperature=_temperature,
            max_tokens=_max_tokens,
            **request_kwargs,
        )
        return response.choices[0].message.content or ""
    
    def _normalize_to_openai_format(self, messages: list[dict]) -> list[dict]:
        """
        将各种消息格式统一为 OpenAI 兼容格式：
        - 纯文本: {"role": "...", "content": "string"}
        - 多模态: {"role": "user", "content": [{"type":"text","text":"..."},{"type":"image_url","image_url":{"url":"..."}}]}
        """
        normalized: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                normalized.append({"role": role, "content": content})
                continue

            if role in ("system", "tool"):
                text = self._flatten_content_to_text(content)
                normalized.append({"role": role, "content": text})
                continue

            if isinstance(content, list):
                parts: list[dict] = []
                for item in content:
                    if isinstance(item, str):
                        if item.strip():
                            parts.append({"type": "text", "text": item})
                        continue
                    if not isinstance(item, dict):
                        continue
                    part = self._to_openai_content_part(item)
                    if part:
                        parts.append(part)
                normalized.append({"role": role, "content": parts if parts else ""})
            else:
                normalized.append({"role": role, "content": str(content)})
        return normalized

    @staticmethod
    def _to_openai_content_part(item: dict) -> Optional[dict]:
        """将单个 content item 转换为 OpenAI 格式。"""
        if "type" in item:
            return item

        if "text" in item:
            text = item["text"]
            return {"type": "text", "text": str(text)} if text is not None else None
        if "image" in item:
            url = item["image"]
            return {"type": "image_url", "image_url": {"url": str(url)}} if url else None
        if "image_url" in item:
            raw = item["image_url"]
            url = raw.get("url") if isinstance(raw, dict) else raw
            return {"type": "image_url", "image_url": {"url": str(url)}} if url else None
        return None

    @staticmethod
    def _flatten_content_to_text(content) -> str:
        """将 list/dict content 降级为纯文本（用于 system/tool 角色）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
            return " ".join(p for p in parts if p)
        return str(content)

    def _extract_text_from_message_content(self, content: Any) -> str:
        """统一解析流式/非流式响应中的文本。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        text_parts.append(str(text))
                elif isinstance(item, str):
                    text_parts.append(item)
            return "".join(text_parts)
        return str(content) if content is not None else ""

    def _parse_json_with_repair(self, text: str) -> dict:
        """
        统一 JSON 修复逻辑（DRY）：
        1) 去注释
        2) Python 字面量转 JSON 字面量
        3) json.loads
        4) json_repair fallback
        """
        logger = logging.getLogger(__name__)

        fixed = text or ""
        fixed = re.sub(r"//.*?(?=\n|$)", "", fixed)  # 单行注释
        fixed = re.sub(r"/\*[\s\S]*?\*/", "", fixed)  # 多行注释
        fixed = re.sub(r"\bTrue\b", "true", fixed)
        fixed = re.sub(r"\bFalse\b", "false", fixed)
        fixed = re.sub(r"\bNone\b", "null", fixed)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # 尾逗号

        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        try:
            import json_repair

            return json_repair.loads(fixed)
        except ImportError:
            logger.warning("json_repair 未安装，无法自动修复 JSON")
        except Exception as e:
            logger.warning(f"json_repair 修复失败: {e}")

        return json.loads(text)

    def _extract_json_candidate(self, text: str) -> str:
        """
        从 Markdown/code block/普通文本中提取最可能的 JSON 片段。
        """
        if not text:
            return ""

        json_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_block_match:
            return json_block_match.group(1).strip()

        brace_match = re.search(r"(\{[\s\S]*\})", text)
        if brace_match:
            return brace_match.group(1).strip()

        bracket_match = re.search(r"(\[[\s\S]*\])", text)
        if bracket_match:
            return bracket_match.group(1).strip()

        return text.strip()

    
    def _parse_json_from_text(self, text: str) -> dict:
        """
        从 VL 模型输出中提取和修复 JSON
        
        支持：
        1. 纯 JSON 字符串
        2. markdown ```json 代码块
        3. 带前后文本的 JSON
        4. 部分格式错误的 JSON 修复
        """
        logger = logging.getLogger(__name__)
        try:
            candidate = self._extract_json_candidate(text)
            return self._parse_json_with_repair(candidate)
        except Exception:
            logger.error(f"JSON 修复失败，返回空对象: {str(text)[:200]}...")
            return {"error": "JSON 解析失败", "raw": text[:500]}
    
    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> dict:
        """
        带工具调用的异步聊天（OpenAI 兼容端点 Function Calling）
        
        Args:
            messages: 消息列表，格式：
                [{"role": "user", "content": "..."},
                 {"role": "assistant", "content": "...", "tool_calls": [...]},
                 {"role": "tool", "tool_call_id": "...", "content": "..."}]
            tools: 工具定义列表，OpenAI 兼容格式：
                [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]
            model: 模型名称
            temperature: 温度
            max_tokens: 最大 token
            
        Returns:
            dict: {
                "content": str,           # 文本内容（工具调用时可能为空）
                "tool_calls": list,       # 工具调用列表
                "finish_reason": str      # 结束原因：tool_calls / stop / length
            }
        """
        _model = model or self.model
        _temperature = temperature if temperature is not None else self.temperature
        _max_tokens = max_tokens or self.max_tokens
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            extra_body=kwargs.get("extra_body"),
        )

        response = await self.client.chat.completions.create(
            model=_model,
            messages=messages,
            tools=tools,
            temperature=_temperature,
            max_tokens=_max_tokens,
            **request_kwargs,
        )
        choice = response.choices[0]
        msg = choice.message

        tool_calls_out: list[dict] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_out.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        return {
            "content": msg.content or "",
            "tool_calls": tool_calls_out,
            "finish_reason": choice.finish_reason or "stop",
        }
    
    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        异步流式聊天
        
        返回异步生成器，支持 SSE 推送
        """
        _model = model or self.model
        _temperature = temperature if temperature is not None else self.temperature
        _max_tokens = max_tokens or self.max_tokens
        normalized = self._normalize_to_openai_format(messages)
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            extra_body=kwargs.get("extra_body"),
        )

        logger = logging.getLogger(__name__)
        for msg in normalized:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_count = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "text")
                image_count = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image_url")
                image_urls = [
                    c["image_url"]["url"][:120]
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "image_url" and isinstance(c.get("image_url"), dict)
                ]
                logger.info("[chat_stream] role=%s texts=%s images=%s urls=%s", role, text_count, image_count, image_urls)

        response = await self.client.chat.completions.create(
            model=_model,
            messages=normalized,
            temperature=_temperature,
            max_tokens=_max_tokens,
            stream=True,
            **request_kwargs,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def chat_stream_with_thinking(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[tuple[str, str], None]:
        """
        支持推理令牌的异步流式聊天（Qwen3 thinking 模式）

        Yield (chunk_type, text) 二元组：
        - chunk_type = 'reasoning': 思维链内容（reasoning_content）
        - chunk_type = 'content'  : 最终回答内容（content）

        对不支持 thinking 的模型，reasoning 字段为空，退化为纯 content 流。
        """
        _model = model or self.model
        _temperature = temperature if temperature is not None else self.temperature
        _max_tokens = max_tokens or self.max_tokens
        normalized = self._normalize_to_openai_format(messages)
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            extra_body=kwargs.get("extra_body"),
        )

        logger = logging.getLogger(__name__)
        response = await self.client.chat.completions.create(
            model=_model,
            messages=normalized,
            temperature=_temperature,
            max_tokens=_max_tokens,
            stream=True,
            **request_kwargs,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            # Qwen3 reasoning_content 通过 model_extra 或直接属性透传
            reasoning: str | None = (
                getattr(delta, "reasoning_content", None)
                or (delta.model_extra or {}).get("reasoning_content")
            )
            if reasoning:
                logger.debug("[chat_stream_with_thinking] reasoning chunk len=%s", len(reasoning))
                yield ("reasoning", reasoning)
            if delta.content:
                yield ("content", delta.content)
    
    async def generate_structured(
        self,
        messages: list[dict],
        tool_schema: dict,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> dict:
        """
        使用 Native Tool Calling 生成结构化输出（强制 Schema 约束）
        
        利用 tool_choice="required" 强制模型输出符合 Schema 的 JSON，
        彻底避免 generate_json 中的文本解析问题。
        
        Args:
            messages: 消息列表
            tool_schema: 工具定义，OpenAI 兼容格式：
                {"type": "function", "function": {"name": "...", "parameters": {...}}}
            model: 模型名称
            temperature: 温度
            max_tokens: 最大 token
            
        Returns:
            解析后的工具参数 dict（即 function.arguments 解析结果）
            
        Raises:
            ValueError: 如果模型未返回 tool_calls
        """
        logger = logging.getLogger(__name__)
        
        _model = model or self.model
        _temperature = temperature if temperature is not None else self.temperature
        _max_tokens = max_tokens or self.max_tokens
        normalized = self._normalize_to_openai_format(messages)
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            force_disable_thinking=True,
            extra_body=kwargs.get("extra_body"),
        )

        response = await self.client.chat.completions.create(
            model=_model,
            messages=normalized,
            tools=[tool_schema],
            tool_choice="required",
            temperature=_temperature,
            max_tokens=_max_tokens,
            # [修复] tool_choice=required 与 thinking 模式不兼容，显式关闭
            **request_kwargs,
        )
        choice = response.choices[0]
        msg = choice.message

        if msg.tool_calls:
            func_args = msg.tool_calls[0].function.arguments
            parsed = self._parse_json_with_repair(func_args)
            logger.info(f"[generate_structured] 解析成功: tool_calls 路径, keys={list(parsed.keys())}")
            return parsed

        content = msg.content or ""
        if content:
            logger.warning("generate_structured: 模型未返回 tool_calls，尝试从 content 解析")
            candidate = self._extract_json_candidate(content)
            parsed = self._parse_json_with_repair(candidate)
            logger.warning(f"[generate_structured] 解析成功: content 路径, keys={list(parsed.keys())}")
            return parsed

        raise ValueError("模型未返回 tool_calls，也无法从 content 解析 JSON")
    
    async def generate_json(
        self,
        messages: list[dict],
        **kwargs
    ) -> dict:
        """
        生成 JSON 格式输出（带容错处理）
        """
        result = await self.chat(messages, **kwargs)
        candidate = self._extract_json_candidate(result)
        try:
            return self._parse_json_with_repair(candidate)
        except Exception:
            raise ValueError(f"无法解析 JSON（已尝试修复）\n原始输出: {result[:500]}...")

    async def generate_json_detailed(
        self,
        messages: list[dict],
        **kwargs
    ) -> tuple[dict, dict]:
        """与 generate_json 等价，但额外返回 LLM API 的 usage 信息。

        不走 self.chat()（chat 丢弃了 response.usage），直接调用 API。
        用法：
            parsed, usage = await llm.generate_json_detailed(messages, ...)
            # usage = {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        """
        logger = logging.getLogger(__name__)
        _model = kwargs.pop("model", self.model)
        _temperature = kwargs.pop("temperature", self.temperature)
        _max_tokens = kwargs.pop("max_tokens", self.max_tokens)
        normalized = self._normalize_to_openai_format(messages)
        request_kwargs = self._prepare_request_kwargs(
            model_name=_model,
            extra_body=kwargs.pop("extra_body", None),
        )

        response = await self.client.chat.completions.create(
            model=_model,
            messages=normalized,
            temperature=_temperature,
            max_tokens=_max_tokens,
            **request_kwargs,
        )
        text = response.choices[0].message.content or ""
        candidate = self._extract_json_candidate(text)
        parsed = self._parse_json_with_repair(candidate)

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }

        return parsed, usage


# 单例
_client: Optional[AsyncLLMClient] = None


def get_async_llm() -> AsyncLLMClient:
    """获取异步 LLM 客户端单例"""
    global _client
    if _client is None:
        _client = AsyncLLMClient()
    return _client
