"""
PageIndex 深度检索器
"""
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select

from app.config import get_settings
from app.api.events import emit_thinking_log
from app.core.db.database import get_async_db_context
from app.core.rag.base_retriever import BaseRetriever
from app.core.rag.hybrid_retriever import HybridRetriever, get_hybrid_retriever
from app.core.rag.pageindex.llm_client import get_pageindex_llm_client
from app.core.rag.pageindex.tree_store import get_tree_store
from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.models.knowledge.graph import File

logger = logging.getLogger(__name__)


class PageIndexRetriever(BaseRetriever):
    """
    两阶段检索：
    1) Hybrid 粗筛候选文档
    2) PageIndex 节点精排
    """

    def __init__(self, hybrid_retriever: Optional[HybridRetriever] = None):
        self.settings = get_settings().pageindex
        self.hybrid_retriever = hybrid_retriever or get_hybrid_retriever()
        self.tree_store = get_tree_store()
        self.llm_client = get_pageindex_llm_client()

    async def search(
        self,
        queries: List[str],
        user_context: UserContext,
        top_n: Optional[int] = None,
        **kwargs,
    ) -> List[DocumentChunk]:
        if not queries:
            return []
        session_id = kwargs.get("session_id", "")

        include_images = kwargs.get("include_images", True)
        file_ids = kwargs.get("file_ids")
        visibilities = kwargs.get("visibilities")
        dept_ids = kwargs.get("dept_ids")

        coarse_results = await self.hybrid_retriever.search(
            queries=queries,
            user_context=user_context,
            top_n=self.settings.coarse_top_n,
            include_images=include_images,
            file_ids=file_ids,
            visibilities=visibilities,
            dept_ids=dept_ids,
        )
        if not coarse_results:
            return []

        final_top_n = top_n or self.settings.final_top_n
        candidate_file_ids = self._collect_candidate_file_ids(coarse_results, self.settings.max_candidate_files)
        if not candidate_file_ids:
            return coarse_results[:final_top_n]

        ready_file_ids = await self._filter_ready_pageindex_files(candidate_file_ids, user_context.workspace_id)
        if not ready_file_ids:
            if session_id:
                await emit_thinking_log(session_id, "树索引未就绪，已切换为普通检索模式。")
            return coarse_results[:final_top_n]

        nodes = await self.tree_store.get_nodes_for_files(
            file_ids=ready_file_ids,
            workspace_id=user_context.workspace_id,
        )
        if not nodes:
            if session_id:
                await emit_thinking_log(session_id, "未找到可用树节点，已切换为普通检索模式。")
            return coarse_results[:final_top_n]

        ranked_nodes = await self._rank_nodes(queries[0], nodes)
        if not ranked_nodes:
            if session_id:
                await emit_thinking_log(session_id, "深度排序失败，已切换为普通检索模式。")
            return coarse_results[:final_top_n]

        ranked_focus_nodes = ranked_nodes[: max(final_top_n, 6)]
        focus_file_ids = self._collect_focus_file_ids_from_nodes(
            ranked_nodes=ranked_focus_nodes,
            max_files=self.settings.max_candidate_files,
        )

        guided_results: List[DocumentChunk] = []
        if focus_file_ids:
            guided_top_n = max(self.settings.coarse_top_n, final_top_n * 8)
            if session_id:
                await emit_thinking_log(
                    session_id,
                    "已定位高相关章节，正在执行定向二次检索以补全细粒度证据。",
                )
            try:
                guided_results = await self.hybrid_retriever.search(
                    queries=[queries[0]],
                    user_context=user_context,
                    top_n=guided_top_n,
                    include_images=include_images,
                    file_ids=focus_file_ids,
                    visibilities=visibilities,
                    dept_ids=dept_ids,
                )
            except Exception as error:
                logger.warning("[PageIndexRetriever] 二次检索失败，回退一次检索结果: %s", error)

        combined_candidates = self._merge_chunk_lists(guided_results, coarse_results)
        merged = self._merge_with_pageindex_guidance(
            coarse_results=combined_candidates,
            ranked_nodes=ranked_focus_nodes,
            final_top_n=final_top_n,
        )
        if not merged:
            return coarse_results[:final_top_n]

        file_name_map = self._build_file_name_map(combined_candidates)
        chapter_refs = self._build_pageindex_chapter_refs(ranked_focus_nodes, limit=3)
        nav_chunk = self._build_navigation_summary_chunk(
            ranked_nodes=ranked_focus_nodes,
            file_name_map=file_name_map,
            limit=3,
        )

        result_chunks = merged
        if nav_chunk:
            result_chunks = [nav_chunk] + merged

        if chapter_refs:
            for chunk in result_chunks:
                metadata = chunk.metadata or {}
                metadata["retrieval_mode"] = "pageindex_fusion"
                metadata["pageindex_chapters"] = chapter_refs
                chunk.metadata = metadata

        max_return = final_top_n + (1 if nav_chunk else 0)
        return result_chunks[:max_return]

    async def _filter_ready_pageindex_files(self, file_ids: List[str], workspace_id: str) -> List[str]:
        async with get_async_db_context() as session:
            stmt = select(File.id).where(
                File.id.in_(file_ids),
                File.workspace_id == workspace_id,
                File.pageindex_status == "ready",
            )
            result = await session.execute(stmt)
            ready_ids = {row[0] for row in result.fetchall()}
        return [file_id for file_id in file_ids if file_id in ready_ids]

    async def _rank_nodes(self, query: str, nodes: List[Any]) -> List[Tuple[Any, float]]:
        scored = []
        for node in nodes:
            heuristic = self._heuristic_score(query, node.title, node.summary, node.content)
            scored.append((node, heuristic))
        scored.sort(key=lambda item: item[1], reverse=True)

        if not self.settings.llm_enabled or not scored:
            return scored

        candidate_pairs = scored[: self.settings.max_nodes_for_llm]
        payload = []
        for node, score in candidate_pairs:
            payload.append(
                {
                    "node_id": node.node_id,
                    "title": node.title,
                    "summary": node.summary,
                    "content": (node.content or "")[: self.settings.max_node_content_chars],
                    "start_page": node.start_page,
                    "end_page": node.end_page,
                    "heuristic_score": score,
                }
            )

        try:
            llm_rank = await self.llm_client.rank_nodes(query, payload)
        except Exception as error:
            logger.warning("[PageIndexRetriever] LLM 排序失败，降级启发式: %s", error)
            return scored

        llm_score_map: Dict[str, float] = {}
        for item in llm_rank:
            node_id = str(item.get("node_id", ""))
            raw = item.get("score", 0)
            try:
                value = float(raw)
            except Exception:
                value = 0.0
            llm_score_map[node_id] = max(0.0, min(1.0, value))

        reranked = []
        for node, heuristic in scored:
            llm = llm_score_map.get(str(node.node_id))
            if llm is None:
                blended = heuristic
            else:
                blended = (llm * 0.85) + (heuristic * 0.15)
            reranked.append((node, blended))

        reranked.sort(key=lambda item: item[1], reverse=True)
        return reranked

    @staticmethod
    def _heuristic_score(query: str, title: Optional[str], summary: Optional[str], content: Optional[str]) -> float:
        text = " ".join([query or "", title or "", summary or "", (content or "")[:1200]]).lower()
        terms = [term for term in re.split(r"\s+", (query or "").lower()) if term]
        if not terms:
            return 0.0
        hits = sum(1 for term in terms if term in text)
        return min(1.0, hits / max(1, len(terms)))

    @staticmethod
    def _collect_candidate_file_ids(chunks: List[DocumentChunk], max_files: int) -> List[str]:
        file_ids = []
        seen = set()
        for chunk in chunks:
            file_id = str((chunk.metadata or {}).get("file_id") or "")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            file_ids.append(file_id)
            if len(file_ids) >= max_files:
                break
        return file_ids

    @staticmethod
    def _build_file_name_map(chunks: List[DocumentChunk]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for chunk in chunks:
            metadata = chunk.metadata or {}
            file_id = str(metadata.get("file_id") or "")
            if not file_id:
                continue
            name = metadata.get("source_file") or chunk.source_file or "未知文档"
            result[file_id] = name
        return result

    @staticmethod
    def _collect_focus_file_ids_from_nodes(
        ranked_nodes: List[Tuple[Any, float]],
        max_files: int,
    ) -> List[str]:
        result: List[str] = []
        seen = set()
        for node, _score in ranked_nodes:
            file_id = str(getattr(node, "file_id", "") or "")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            result.append(file_id)
            if len(result) >= max_files:
                break
        return result

    @staticmethod
    def _merge_chunk_lists(primary: List[DocumentChunk], fallback: List[DocumentChunk]) -> List[DocumentChunk]:
        merged: List[DocumentChunk] = []
        seen = set()
        for chunks in (primary, fallback):
            for chunk in chunks:
                chunk_id = chunk.chunk_id or f"no_id:{id(chunk)}"
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                merged.append(chunk)
        return merged

    @classmethod
    def _merge_with_pageindex_guidance(
        cls,
        coarse_results: List[DocumentChunk],
        ranked_nodes: List[Tuple[Any, float]],
        final_top_n: int,
    ) -> List[DocumentChunk]:
        if not coarse_results:
            return []

        page_ranges = cls._extract_page_ranges(ranked_nodes)
        if not page_ranges:
            return coarse_results[:final_top_n]

        boosted_chunks: List[DocumentChunk] = []
        neutral_chunks: List[DocumentChunk] = []
        fallback_chunks: List[DocumentChunk] = []

        for chunk in coarse_results:
            metadata = chunk.metadata or {}
            file_id = str(metadata.get("file_id") or "")
            page_numbers = cls._parse_page_numbers(metadata.get("page_numbers"))

            # 无页码的 chunk 保留，但不参与 PageIndex 页范围提权
            if not file_id or not page_numbers:
                neutral_chunks.append(chunk)
                continue

            ranges = page_ranges.get(file_id)
            if not ranges:
                fallback_chunks.append(chunk)
                continue

            matched = any(cls._page_in_ranges(page, ranges) for page in page_numbers)
            metadata["pageindex_matched"] = matched
            chunk.metadata = metadata

            if matched:
                boosted_chunks.append(chunk)
            else:
                fallback_chunks.append(chunk)

        merged = boosted_chunks + neutral_chunks + fallback_chunks
        deduped: List[DocumentChunk] = []
        seen_ids: Set[str] = set()
        for chunk in merged:
            chunk_id = chunk.chunk_id or f"no_id:{id(chunk)}"
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            deduped.append(chunk)
            if len(deduped) >= final_top_n:
                break
        return deduped

    @staticmethod
    def _extract_page_ranges(ranked_nodes: List[Tuple[Any, float]]) -> Dict[str, List[Tuple[int, int]]]:
        ranges: Dict[str, List[Tuple[int, int]]] = {}
        for node, _score in ranked_nodes:
            file_id = str(getattr(node, "file_id", "") or "")
            if not file_id:
                continue
            start = PageIndexRetriever._safe_positive_int(getattr(node, "start_page", None))
            end = PageIndexRetriever._safe_positive_int(getattr(node, "end_page", None))
            if start is None and end is None:
                continue
            if start is None:
                start = end
            if end is None:
                end = start
            if start is None or end is None:
                continue
            if start > end:
                start, end = end, start
            ranges.setdefault(file_id, []).append((start, end))
        return ranges

    @staticmethod
    def _parse_page_numbers(raw_page_numbers: Any) -> List[int]:
        values: List[int] = []

        if raw_page_numbers is None:
            return values

        if isinstance(raw_page_numbers, (list, tuple, set)):
            candidates = list(raw_page_numbers)
        else:
            candidates = re.findall(r"\d+", str(raw_page_numbers))

        seen = set()
        for value in candidates:
            page = PageIndexRetriever._safe_positive_int(value)
            if page is None or page in seen:
                continue
            seen.add(page)
            values.append(page)

        return values

    @staticmethod
    def _page_in_ranges(page: int, ranges: List[Tuple[int, int]]) -> bool:
        for start, end in ranges:
            if start <= page <= end:
                return True
        return False

    @staticmethod
    def _safe_positive_int(value: Any) -> Optional[int]:
        try:
            parsed = int(value)
        except Exception:
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _build_pageindex_chapter_refs(
        ranked_nodes: List[Tuple[Any, float]],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        for node, score in ranked_nodes:
            if len(refs) >= limit:
                break
            refs.append(
                {
                    "node_id": str(getattr(node, "node_id", "") or ""),
                    "title": str(getattr(node, "title", "") or "未命名章节"),
                    "start_page": PageIndexRetriever._safe_positive_int(getattr(node, "start_page", None)),
                    "end_page": PageIndexRetriever._safe_positive_int(getattr(node, "end_page", None)),
                    "score": round(float(score), 4),
                }
            )
        return refs

    @staticmethod
    def _build_navigation_summary_chunk(
        ranked_nodes: List[Tuple[Any, float]],
        file_name_map: Dict[str, str],
        limit: int = 3,
    ) -> Optional[DocumentChunk]:
        lines: List[str] = []
        for node, score in ranked_nodes:
            if len(lines) >= limit:
                break
            summary = str(getattr(node, "summary", "") or "").strip()
            if not summary:
                continue

            file_id = str(getattr(node, "file_id", "") or "")
            source_name = file_name_map.get(file_id, "未知文档")
            title = str(getattr(node, "title", "") or "未命名章节")
            start = PageIndexRetriever._safe_positive_int(getattr(node, "start_page", None))
            end = PageIndexRetriever._safe_positive_int(getattr(node, "end_page", None))
            if start and end:
                page_hint = f"P{start}-P{end}"
            elif start:
                page_hint = f"P{start}"
            else:
                page_hint = "页码未知"
            compact_summary = summary.replace("\n", " ").strip()
            lines.append(f"- {source_name} / {title} ({page_hint})：{compact_summary[:220]}")

        if not lines:
            return None

        content = "【结构化导航】\n" + "\n".join(lines)
        return DocumentChunk(
            content=content,
            source_file="PageIndex 导航",
            chunk_id="pageindex:navigation:summary",
            score=1.0,
            metadata={
                "type": "pageindex_navigation",
                "retrieval_mode": "pageindex_fusion",
            },
            summary="PageIndex 章节导航摘要",
        )
