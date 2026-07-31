"""
博物馆查询改写服务
负责处理上下文补全、话题切换检测和意图识别

[v2.8] 改进：先 RAG 检索相关文物名称，再提供给 LLM 选择，避免编造
"""
import os
import json
import logging
from typing import Dict, List, Optional, Any
from openai import AsyncOpenAI

from app.museum.config import get_museum_settings, get_prompt_settings

logger = logging.getLogger(__name__)


class QueryRewriterService:
    """查询改写服务"""
    
    def __init__(self):
        self.settings = get_museum_settings()
        self.prompt_settings = get_prompt_settings()
        
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("[QueryRewriter] 未找到 API Key (DASHSCOPE/OPENAI)，改写功能可能不可用")
            
        self.client = AsyncOpenAI(
            api_key=api_key or "EMPTY",
            base_url=self.settings.llm_base_url
        )
    
    async def _get_candidate_artifacts(self, query: str, top_k: int = 10) -> List[str]:
        """用 RAG 检索与查询相关的文物名称"""
        try:
            from app.skills.doc_skill import DocSkill
            from app.models.common.context import UserContext
            import re
            
            user_context = UserContext(
                user_id="system",
                workspace_id=self.settings.guide_workspace_id,
                dept_id=self.settings.guide_dept_id or None
            )
            
            doc_skill = DocSkill()
            results = await doc_skill.query_knowledge_base(
                query=query,
                user_context=user_context,
                top_k=top_k
            )
            
            # 提取 header_path 中的文物名称
            artifact_names = set()
            for r in results or []:
                # 处理 DocumentChunk 对象或字典
                metadata = r.metadata if hasattr(r, 'metadata') else r.get("metadata", {})
                header = metadata.get("header_path", "") if isinstance(metadata, dict) else ""
                if header:
                    name = re.sub(r'^\d+\.\s*', '', header).strip()
                    if name:
                        artifact_names.add(name)
            
            return list(artifact_names)[:top_k]
            
        except Exception as e:
            logger.warning(f"[QueryRewriter] RAG 检索候选文物失败: {e}")
            return []

    async def rewrite_query(self, query: str, history_messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        基于历史改写查询
        
        Args:
            query: 用户最新查询
            history_messages: 历史消息列表 [{"role": "user/assistant", "content": "..."}]
            
        Returns:
            Dict: {
                "rewritten_query": str,
                "intent": str,
                "original_topic_entity": str | None
            }
        """
        if not history_messages:
            logger.info(f"[QueryRewriter] 无历史消息，直接返回原始查询: {query}")
            return {
                "rewritten_query": query,
                "intent": "specific_query", # 默认为具体查询，后续由 Catalog判断是否宽泛
                "original_topic_entity": None
            }

        # 1. 格式化历史摘要 (取最近 2 轮，防止 Token 过多)
        history_text = ""
        # 过滤 active/system 等非对话消息，只保留 user/assistant
        valid_msgs = [m for m in history_messages if m.get("role") in ("user", "assistant")]
        recent_msgs = valid_msgs[-4:] 
        
        for msg in recent_msgs:
            role_name = "用户" if msg.get("role") == "user" else "导览员"
            content = str(msg.get("content", ""))[:200] # 截断单条过长消息
            history_text += f"{role_name}: {content}\n"
            
        if not history_text.strip():
             return {
                "rewritten_query": query,
                "intent": "specific_query",
                "original_topic_entity": None
            }

        # 2. [v2.8] RAG 检索候选文物名称
        candidate_artifacts = await self._get_candidate_artifacts(query, top_k=10)
        artifact_hint = ""
        if candidate_artifacts:
            artifact_hint = f"\n\n【馆内相关文物参考】（如需提及文物，请从以下列表中选择）\n{', '.join(candidate_artifacts)}"
            logger.info(f"[QueryRewriter] 候选文物: {candidate_artifacts}")

        # 3. 构建 Prompt
        base_prompt = self.prompt_settings.guide_query_rewrite_prompt.format(
            history_summary=history_text,
            current_query=query
        )
        prompt = base_prompt + artifact_hint

        # 3. 调用 LLM
        try:
            logger.debug(f"[QueryRewriter] Calling LLM for rewrite...")
            response = await self.client.chat.completions.create(
                model=self.settings.guide_llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, # 低温度保证稳定性
                response_format={"type": "json_object"} # 尝试强制 JSON
            )
            
            raw_content = response.choices[0].message.content
            
            # 4. 解析结果
            # 清理可能的 markdown 标记
            clean_content = raw_content.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean_content)
            
            # 确保字段存在
            rewritten = result.get("rewritten_query")
            intent = result.get("intent", "specific_query")
            
            # 兜底：如果 JSON 解析成功但内容为空
            if not rewritten:
                rewritten = query
                
            logger.info(f"[QueryRewriter] Original: '{query}' -> Rewritten: '{rewritten}' | Intent: {intent}")
            return result
            
        except Exception as e:
            logger.error(f"[QueryRewriter] 改写失败: {e}", exc_info=True)
            # 降级策略：尝试基于规则的简单指代消解
            return self._rule_based_fallback(query, history_messages)
    
    def _rule_based_fallback(self, query: str, history_messages: List[Dict[str, Any]] | None) -> Dict[str, Any]:
        """
        基于规则的降级策略
        
        当 LLM 改写失败时，尝试简单的指代消解：
        1. 检测常见代词（它、这个、那件等）
        2. 从历史中提取最近提到的文物名称
        3. 进行简单替换
        """
        import re
        
        # 常见代词模式
        PRONOUN_PATTERNS = [
            r'它(的|们|有|是|在)?',
            r'这个?',
            r'那个?',
            r'这件',
            r'那件',
            r'该(文物|展品|器物)',
        ]
        
        # 检测是否包含代词
        has_pronoun = any(re.search(p, query) for p in PRONOUN_PATTERNS)
        
        if not has_pronoun or not history_messages:
            # 无代词或无历史，直接返回原始查询
            return {
                "rewritten_query": query,
                "intent": "specific_query",
                "original_topic_entity": None
            }
        
        # 尝试从历史中提取文物名称
        # 简单策略：查找引号内容或常见文物名称模式
        entity = None
        for msg in reversed(history_messages):
            content = str(msg.get("content", ""))
            
            # 匹配引号内的名称
            quoted = re.findall(r'[「『""]([^」』""]+)[」』""]', content)
            if quoted:
                entity = quoted[0]
                break
            
            # 匹配常见文物名称模式（X鼎、X尊、X壶、X玉X等）
            artifact_match = re.search(r'[\u4e00-\u9fff]{2,}[鼎尊壶瓶罐盘碗钟簋鬲甗觚爵觥卣盉匜]|[\u4e00-\u9fff]*玉[\u4e00-\u9fff]+|[\u4e00-\u9fff]{2,}(俑|像|画|帛|简|璧)', content)
            if artifact_match:
                entity = artifact_match.group(0)
                break
        
        if entity:
            # 进行简单替换
            rewritten = query
            for pattern in PRONOUN_PATTERNS:
                rewritten = re.sub(pattern, entity, rewritten, count=1)
            
            logger.info(f"[QueryRewriter] Rule-based fallback: '{query}' -> '{rewritten}' (entity: {entity})")
            return {
                "rewritten_query": rewritten,
                "intent": "specific_query",
                "original_topic_entity": entity
            }
        
        # 无法提取实体，返回原始查询
        return {
            "rewritten_query": query,
            "intent": "specific_query",
            "original_topic_entity": None
        }

# 单例工厂
_rewriter_service: Optional[QueryRewriterService] = None

def get_query_rewriter() -> QueryRewriterService:
    global _rewriter_service
    if _rewriter_service is None:
        _rewriter_service = QueryRewriterService()
    return _rewriter_service
