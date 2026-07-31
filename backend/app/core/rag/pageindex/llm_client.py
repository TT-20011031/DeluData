"""
PageIndex LLM 客户端
"""
import json
import re
from typing import Any, Dict, List

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm


class PageIndexLLMClient:
    def __init__(self):
        self._llm = get_async_llm()
        self._settings = get_settings().pageindex

    async def rank_nodes(self, query: str, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not nodes:
            return []

        candidates = []
        for node in nodes:
            candidates.append(
                {
                    "node_id": node.get("node_id"),
                    "title": node.get("title", ""),
                    "summary": node.get("summary", "") or "",
                    "content": (node.get("content", "") or "")[: self._settings.max_node_content_chars],
                    "start_page": node.get("start_page"),
                    "end_page": node.get("end_page"),
                }
            )

        prompt = (
            "你是文档检索排序助手。给定问题与候选节点，返回最相关节点。\n"
            "输出 JSON 数组，元素格式：{\"node_id\":\"...\",\"score\":0-1,\"reason\":\"...\"}。\n"
            f"问题：{query}\n"
            f"候选节点：{json.dumps(candidates, ensure_ascii=False)}"
        )

        response = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            model=self._settings.llm_model,
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
        )
        return self._parse_json_array(response)

    @staticmethod
    def _parse_json_array(content: str) -> List[Dict[str, Any]]:
        if not content:
            return []
        text = content.strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:
            pass

        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            snippet = text[start:end].strip() if end != -1 else text[start:].strip()
            try:
                data = json.loads(snippet)
                return data if isinstance(data, list) else []
            except Exception:
                pass

        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, list) else []
        except Exception:
            return []


_client: PageIndexLLMClient | None = None


def get_pageindex_llm_client() -> PageIndexLLMClient:
    global _client
    if _client is None:
        _client = PageIndexLLMClient()
    return _client

