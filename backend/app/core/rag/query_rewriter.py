"""
查询改写模块 (Query Rewriter)

将用户口语化的 Query 改写为多个书面语变体，提升检索召回率
"""
import asyncio
import json
from typing import List, Optional, Any
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from app.core.llm.async_llm import get_async_llm
from app.config import get_settings


class QueryRewriter:
    """查询改写器"""
    
    def __init__(self):
        self._llm_client = None
    
    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_async_llm()
        return self._llm_client
    
    async def rewrite(
        self,
        query: str,
        original_query: Optional[str] = None,
        messages: List[BaseMessage] = [],
        num_variants: int = 3,
        include_original: bool = True
    ) -> List[str]:
        # ... (参数说明保持不变) ...
        """
        改写用户查询
        
        Args:
            query: 任务描述 / 查询
            original_query: 用户原始查询 (可选)
            num_variants: 生成变体数量（默认3个）
            include_original: 是否包含原始查询（默认True）
            
        Returns:
            查询变体列表（包含原始查询）
        """
        if not query or not query.strip():
            return [original_query] if (include_original and original_query) else []
        
        try:
            variants = await self._generate_variants(query, original_query, messages)
            
            # 确保结果有效
            if not variants or not isinstance(variants, list):
                return [query] if include_original else []
            
            # 去重
            unique_variants = list(dict.fromkeys(variants))
            
            # 添加原始查询 (任务描述)
            if include_original and query not in unique_variants:
                unique_variants.insert(0, query)
            
            # 如果有原始问题，也加进去作为备选（防止改写偏离）
            if include_original and original_query and original_query not in unique_variants:
                unique_variants.append(original_query)
            
            return unique_variants[:num_variants + (2 if include_original and original_query else 1)]
            
        except Exception:
            # 降级策略：返回原始查询
            return [query] if include_original else []
    
    async def _generate_variants(
        self, 
        query: str, 
        original_query: Optional[str] = None,
        messages: List[BaseMessage] = []
    ) -> List[str]:
        """调用 LLM 生成查询变体"""
        from app.core.llm.prompt_manager import get_prompt
        
        # 如果没有 original_query，就用 query 填充，避免 Prompt 格式错误
        original = original_query if original_query else query
        
        # 格式化历史消息
        history_text = "无历史消息"
        if messages:
            history_text = ""
            # 只取最近 5 条
            recent = messages[-5:]
            for msg in recent:
                role = "User" if isinstance(msg, HumanMessage) else "Assistant"
                history_text += f"{role}: {msg.content}\n"

        # 从 PromptManager 加载提示词
        prompt = get_prompt(
            "query_rewrite.rewrite_query", 
            query=query, 
            original_query=original,
            history=history_text
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        settings = get_settings()
        response = await self.llm_client.chat(messages, model=settings.llm.doc_worker_model)
        
        # 解析 JSON 响应
        return self._parse_response(response)
    
    def _parse_response(self, response: str) -> List[str]:
        """解析 LLM 响应中的 JSON 数组"""
        try:
            # 尝试直接解析
            if response.strip().startswith('['):
                return json.loads(response.strip())
            
            # 提取 JSON 代码块
            if '```json' in response:
                start = response.find('```json') + 7
                end = response.find('```', start)
                json_str = response[start:end].strip()
                return json.loads(json_str)
            
            if '```' in response:
                start = response.find('```') + 3
                end = response.find('```', start)
                json_str = response[start:end].strip()
                return json.loads(json_str)
            
            # 尝试提取方括号内容
            import re
            match = re.search(r'\[.*?\]', response, re.DOTALL)
            if match:
                return json.loads(match.group())
            
            return []
            
        except json.JSONDecodeError:
            return []


# 工厂函数
def get_query_rewriter() -> QueryRewriter:
    """获取查询改写器实例"""
    return QueryRewriter()
