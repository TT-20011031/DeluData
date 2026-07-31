"""WikiCompiler - Karpathy LLM Wiki 编译核心。

职责（与 plan 文档一致）：
1. 候选实体抽取：用 LLM 读文档摘要 + 切片标题，列出候选实体
2. 与现有 Wiki 对齐：按 slug + alias 在 wiki_pages 中查重
3. 增量更新 / 新建 / 冲突标记：分类执行
4. 双向链接补全：在 markdown_body 中解析 [[xx]]，写 wiki_links
5. 回溯绑定：把每条事实绑定到 chunk_ids，写 wiki_page_sources
6. 写修订历史：每次编译都记一条 wiki_revisions

设计原则：
- 编译过程不持有 DB 事务，只输出"操作意图"（CompileOutcome）
- 所有 DB 写入由 WikiService 在外层事务中执行（保证原子性）
- LLM 客户端、Embedding、ChunkLoader 可注入，便于单测
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.config import get_settings
from app.core.llm.async_llm import AsyncLLMClient, get_async_llm
from app.core.llm.prompt_manager import get_prompt_manager
from app.core.wiki.chunk_loader import (
    WikiChunk,
    WikiChunkLoader,
    get_wiki_chunk_loader,
)
from app.core.wiki.slug import (
    candidate_slugs,
    make_fallback_slug,
    normalize_slug,
)

logger = logging.getLogger("wiki.compiler")


_GENERIC_CONCEPT_SUFFIXES = (
    "product-overview",
    "core-modules",
    "core-module",
    "overview",
    "introduction",
    "intro",
    "architecture",
    "capabilities",
    "capability",
    "features",
    "feature",
    "modules",
    "module",
    "\u4ea7\u54c1\u603b\u89c8",  # product overview
    "\u6838\u5fc3\u6a21\u5757",  # core modules
    "\u603b\u89c8",
    "\u6982\u8ff0",
    "\u4ecb\u7ecd",
    "\u67b6\u6784",
    "\u6a21\u5757",
    "\u80fd\u529b",
    "\u529f\u80fd",
)


def _canonical_entity_key(raw: str) -> str:
    """Return a coarse concept key for duplicate detection only.

    This intentionally does not replace the public slug normalizer. It removes
    document-section suffixes such as "product overview" or "core modules" so
    candidates like "DeluData product overview" and "DeluData core modules"
    can converge on the same entity.
    """
    key = normalize_slug(str(raw or ""))
    if not key:
        return ""

    changed = True
    while changed:
        changed = False
        for suffix in sorted(_GENERIC_CONCEPT_SUFFIXES, key=len, reverse=True):
            if key == suffix:
                continue
            if key.endswith(suffix):
                trimmed = key[: -len(suffix)].strip("-")
                if trimmed:
                    key = trimmed
                    changed = True
                    break
    return key


def _candidate_match_key_list(candidate: "CandidateEntity") -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for raw in [candidate.slug, candidate.title, *(candidate.aliases or [])]:
        slug = normalize_slug(str(raw or ""))
        canonical = _canonical_entity_key(str(raw or ""))
        if slug:
            if slug not in seen:
                seen.add(slug)
                keys.append(slug)
        if canonical:
            if canonical not in seen:
                seen.add(canonical)
                keys.append(canonical)
    return keys


def _candidate_match_keys(candidate: "CandidateEntity") -> set[str]:
    return set(_candidate_match_key_list(candidate))


def _merge_unique_strings(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged


# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class CandidateEntity:
    """候选实体（LLM 抽取阶段输出，对齐前的中间表达）。

    多源支持：sources_by_file 记录"哪些文件 + 哪些 chunk"提到了本实体。
    _merge_candidates 在合并同 slug 候选时会合并 sources_by_file，
    使得同一实体被多个上传文件提及时不会丢失任一文件的出处。
    """

    slug: str
    title: str
    aliases: list[str] = field(default_factory=list)
    domain: str = "general"
    brief: str = ""
    evidence_chunk_ids: list[str] = field(default_factory=list)
    source_file_id: str = ""
    source_file_name: str = ""
    # 每个 file_id 提供的 chunk_id 列表（多源持久化用），key 为 file_id
    sources_by_file: dict[str, list[str]] = field(default_factory=dict)
    # 合并来源数：初始 1，每次精确/模糊去重合并时递增，用于质量追踪
    merged_from_count: int = 1

    def all_search_keys(self) -> list[str]:
        """返回所有可用于查重的 slug（自身 + alias 标准化）。"""
        return candidate_slugs(self.title, [self.slug, *self.aliases])


@dataclass
class CompiledPage:
    """单个实体页编译完成后的「操作意图」。

    operation:
        - create  : 新建
        - update  : 更新已有页
        - skip    : LLM 决定不入库（信息不足）
    """

    operation: str
    slug: str
    title: str
    summary: str
    domain: str
    markdown_body: str
    aliases: list[str] = field(default_factory=list)

    # 与已有页对齐时的目标（update 模式必填）
    existing_page_id: Optional[str] = None
    base_version: Optional[int] = None  # 基于哪个版本编辑（乐观锁）

    # 后置链接候选（target_slug 而非 page_id，因为目标可能尚未入库）
    outgoing_links: list[dict[str, Any]] = field(default_factory=list)

    # 冲突清单（写入 wiki_links link_type=contradicts，status=pending_review）
    contradicts: list[dict[str, Any]] = field(default_factory=list)

    # 出处溯源（兼容字段：单文件时填）
    evidence_chunk_ids: list[str] = field(default_factory=list)
    source_file_id: str = ""

    # 多源出处：file_id -> chunk_ids；优先使用此字段写 wiki_page_sources，
    # 兼容旧路径：若为空则退回到 source_file_id + evidence_chunk_ids 的单源写入。
    sources_by_file: dict[str, list[str]] = field(default_factory=dict)

    # 提交说明（写入 wiki_revisions.commit_message）
    ops_log: str = ""

    # LLM 用量（仅作记录）
    compile_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompileOutcome:
    """整次编译任务的总输出（多个 CompiledPage + 任务级元信息）。"""

    pages: list[CompiledPage] = field(default_factory=list)
    skipped_candidates: list[dict[str, Any]] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    total_candidates: int = 0
    # 软取消标志：若 True，本次编译在中途被用户取消；
    # `pages` 仍包含被取消之前已成功的候选，应被正常落库（"软取消"承诺）。
    cancelled: bool = False


# =============================================================================
# WikiCompiler
# =============================================================================


class WikiCompiler:
    """Wiki 编译核心。

    用法：
        compiler = WikiCompiler()
        outcome = await compiler.compile_for_files(
            workspace_id="ws-1",
            file_payloads=[{"file_id": "...", "name": "...", "domain_hint": "policy"}],
            existing_pages=[...],   # 当前 workspace 已有页 (用于对齐)
        )
        # 把 outcome 交给 WikiService 落库
    """

    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        chunk_loader: Optional[WikiChunkLoader] = None,
    ):
        self._llm = llm_client
        self._chunk_loader = chunk_loader
        self._settings = get_settings()
        self._wiki_settings = self._settings.wiki
        self._prompts = get_prompt_manager()

    @property
    def llm(self) -> AsyncLLMClient:
        if self._llm is None:
            self._llm = get_async_llm()
        return self._llm

    @property
    def chunk_loader(self) -> WikiChunkLoader:
        if self._chunk_loader is None:
            self._chunk_loader = get_wiki_chunk_loader()
        return self._chunk_loader

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def compile_for_files(
        self,
        *,
        workspace_id: str,
        file_payloads: list[dict[str, Any]],
        existing_pages: list[dict[str, Any]],
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> CompileOutcome:
        """对一组文件执行编译。

        Args:
            workspace_id: 租户 ID
            file_payloads: [{file_id, name, folder_name, domain_hint}, ...]
            existing_pages: 当前工作区现有 wiki_pages 的精简视图
                            [{id, slug, title, aliases, summary, markdown_body,
                              version, domain}, ...]
            progress_cb: 可选回调 (stage:str, done:int, total:int)。
                         stage='extract' - 候选抽取阶段，每个 file 完成后回调
                         stage='compile' - 候选编译阶段，每个 candidate 完成后回调
                         异常会被吞，不影响主流程；用于上报到任务队列等。
            cancel_check: 可选回调；返回 True 表示用户已请求取消。在抽取/编译
                         两阶段的关键检查点被调用：
                         - 抽取阶段：每个文件开始前检查；若取消则跳过尚未启动的文件，
                           已抽到的候选仍可继续进入编译阶段
                         - 编译阶段：每个候选 acquire semaphore 后检查；若取消则
                           不发起 LLM 调用直接短路
                         异常会被吞（视为 False）；建议短期缓存避免 DB 压力。
        """
        started = time.perf_counter()
        outcome = CompileOutcome()

        def _safe_cb(stage: str, done: int, total: int) -> None:
            if progress_cb is None:
                return
            try:
                progress_cb(stage, done, total)
            except Exception:  # noqa: BLE001 - cb 失败仅记日志，不影响编译
                logger.exception("[WikiCompiler] progress_cb 失败 stage=%s", stage)

        def _cancelled() -> bool:
            """轻量包装：cancel_check 异常视为 False，避免炸主流程。"""
            if cancel_check is None:
                return False
            try:
                return bool(cancel_check())
            except Exception:  # noqa: BLE001
                logger.exception("[WikiCompiler] cancel_check 抛错，视作未取消")
                return False

        # 1) 抽取候选（并发）---------------------------------------------
        # 改造点：原先 `for idx in file_payloads` 串行 await，3 文件耗时 = 3 × LLM 延迟；
        # 改为 Semaphore + gather 并发，受 wiki.extract_concurrency 控制。
        total_files = len(file_payloads)
        _safe_cb("extract", 0, total_files)
        extract_concurrency = max(
            1, int(getattr(self._wiki_settings, "extract_concurrency", 4))
        )
        extract_sem = asyncio.Semaphore(extract_concurrency)

        # 用列表保存有序结果（保留 file_payloads 顺序），方便日志/调试稳定性
        all_candidates: list[CandidateEntity] = []
        extract_done = 0
        # gather 中需要 nonlocal 累加进度
        extract_lock = asyncio.Lock()

        async def _extract_one(payload: dict[str, Any]) -> list[CandidateEntity]:
            nonlocal extract_done
            file_id = str(payload.get("file_id") or "").strip()
            try:
                # 抽取检查点：用户已取消则不发起 LLM
                if _cancelled():
                    return []
                if not file_id:
                    return []
                chunks = await self.chunk_loader.load_chunks_for_file(
                    workspace_id=workspace_id,
                    file_id=file_id,
                )
                if not chunks:
                    outcome.error_messages.append(
                        f"file_id={file_id} 未找到任何切片，跳过"
                    )
                    return []

                # acquire 之后再次检查（防止排队期间用户取消）
                async with extract_sem:
                    if _cancelled():
                        return []
                    try:
                        cands = await self._extract_candidates(
                            workspace_id=workspace_id,
                            file_payload=payload,
                            chunks=chunks,
                            existing_pages=existing_pages,
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcome.error_messages.append(
                            f"file_id={file_id} 候选抽取失败: {exc}"
                        )
                        logger.exception(
                            "[WikiCompiler] extract_candidates failed for file=%s",
                            file_id,
                        )
                        return []

                for cand in cands:
                    cand.source_file_id = file_id
                    cand.source_file_name = str(payload.get("name") or "")
                    # 多源持久化：保留本文件提供的 chunk_ids；merge 时合并多文件
                    cand.sources_by_file = {
                        file_id: list(cand.evidence_chunk_ids or [])
                    }
                return cands
            finally:
                async with extract_lock:
                    extract_done += 1
                    _safe_cb("extract", extract_done, total_files)

        per_file_results = await asyncio.gather(
            *(_extract_one(p) for p in file_payloads)
        )
        for cands in per_file_results:
            all_candidates.extend(cands)

        # 抽取阶段被取消：可能没有候选；标记 cancelled 但仍允许后续编译已抽到的候选
        cancelled_during_extract = _cancelled()

        outcome.total_candidates = len(all_candidates)
        if not all_candidates:
            outcome.cancelled = cancelled_during_extract
            outcome.elapsed_seconds = time.perf_counter() - started
            return outcome

        # 2) 候选去重 + 对齐 -------------------------------------------
        merged_candidates = self._merge_candidates(all_candidates)
        merged_candidates = self._deduplicate_candidates(merged_candidates)
        existing_index = self._build_existing_index(existing_pages)
        for cand in merged_candidates:
            existing_match = self._find_existing_match(cand, existing_index)
            existing_slug = normalize_slug(str((existing_match or {}).get("slug") or ""))
            if existing_slug and cand.slug != existing_slug:
                cand.aliases = _merge_unique_strings(
                    cand.aliases,
                    [cand.slug, cand.title],
                )
                cand.slug = existing_slug

        # 3) 并发编译每个候选 ------------------------------------------
        sem = asyncio.Semaphore(max(1, int(self._wiki_settings.compile_concurrency)))
        evidence_pool = await self._build_evidence_pool(
            workspace_id=workspace_id,
            candidates=merged_candidates,
        )

        # 进度上报：每完成一个候选（成功或失败）都触发 cb；失败的回调本身不影响主流程
        completed = 0
        total_candidates = len(merged_candidates)
        # 起始上报一次：抽取已完成进入编译阶段
        _safe_cb("compile", 0, total_candidates)

        async def _run(cand: CandidateEntity) -> Optional[CompiledPage]:
            nonlocal completed
            async with sem:
                # 取消检查点：拿到 sem 进入临界区后再次确认；
                # 若用户已取消则直接短路，不发起 LLM 调用，节省成本
                if _cancelled():
                    completed += 1
                    _safe_cb("compile", completed, total_candidates)
                    return None
                try:
                    return await self._compile_single(
                        candidate=cand,
                        existing_match=self._find_existing_match(cand, existing_index),
                        evidence_pool=evidence_pool,
                        linkable_entities_directory=linkable_directory,
                    )
                except Exception as exc:  # noqa: BLE001
                    outcome.error_messages.append(
                        f"slug={cand.slug} 编译失败: {exc}"
                    )
                    logger.exception(
                        "[WikiCompiler] compile_single failed slug=%s", cand.slug
                    )
                    return None
                finally:
                    completed += 1
                    _safe_cb("compile", completed, total_candidates)

        # linkable_entities 改为按 slug→title 字典传入；_compile_single 内部按候选裁剪。
        linkable_directory = self._build_linkable_directory_dict(
            existing_pages=existing_pages,
            extra=[(c.slug, c.title) for c in merged_candidates],
        )

        results = await asyncio.gather(*(_run(c) for c in merged_candidates))
        # 编译阶段是否被取消（短路）
        outcome.cancelled = cancelled_during_extract or _cancelled()
        for cand, page in zip(merged_candidates, results):
            if page is None:
                outcome.skipped_candidates.append(
                    {"slug": cand.slug, "title": cand.title, "reason": "compile_failed"}
                )
                continue
            if page.operation == "skip":
                outcome.skipped_candidates.append(
                    {"slug": cand.slug, "title": cand.title, "reason": page.ops_log or "skip"}
                )
                continue
            outcome.pages.append(page)

        outcome.elapsed_seconds = time.perf_counter() - started
        return outcome

    # ------------------------------------------------------------------
    # Step 1: 候选抽取
    # ------------------------------------------------------------------

    async def _extract_candidates(
        self,
        *,
        workspace_id: str,
        file_payload: dict[str, Any],
        chunks: list[WikiChunk],
        existing_pages: list[dict[str, Any]],
    ) -> list[CandidateEntity]:
        """对单个文件抽取候选实体。"""
        chunks_outline = self._format_chunks_outline(chunks)
        existing_brief = self._format_existing_brief(existing_pages)
        max_candidates = int(self._wiki_settings.max_candidates_per_compile)

        system_prompt = self._prompts.get(
            "wiki.extract_candidates.system",
            max_candidates=max_candidates,
        )
        user_prompt = self._prompts.get(
            "wiki.extract_candidates.user",
            file_name=file_payload.get("name") or file_payload.get("file_id") or "",
            folder_name=file_payload.get("folder_name") or "(根目录)",
            domain_hint=file_payload.get("domain_hint") or self._wiki_settings.default_domain,
            existing_entities=existing_brief,
            chunks_outline=chunks_outline,
        )

        raw = await self.llm.generate_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=self._wiki_settings.compile_model_create,
            temperature=self._wiki_settings.compile_temperature,
            max_tokens=self._wiki_settings.compile_max_tokens,
        )

        items = raw.get("candidates") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            logger.warning(
                "[WikiCompiler] LLM 返回非法 candidates 字段: %s",
                str(raw)[:200],
            )
            return []

        chunk_id_set = {c.chunk_id for c in chunks}
        results: list[CandidateEntity] = []
        for item in items[:max_candidates]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            if len(title) < int(self._wiki_settings.min_candidate_length):
                continue

            slug = normalize_slug(str(item.get("slug") or ""))
            if not slug:
                slug = make_fallback_slug(title, salt=workspace_id)

            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            aliases = [str(a).strip() for a in aliases if str(a).strip()]

            evidence_ids = item.get("evidence_chunk_ids") or []
            if isinstance(evidence_ids, str):
                evidence_ids = [evidence_ids]
            evidence_ids = [
                str(cid).strip() for cid in evidence_ids if str(cid).strip() in chunk_id_set
            ]
            if not evidence_ids:
                # 兜底：取该文件前两个切片作为证据
                evidence_ids = [c.chunk_id for c in chunks[:2]]

            results.append(
                CandidateEntity(
                    slug=slug,
                    title=title,
                    aliases=aliases,
                    domain=str(item.get("domain") or self._wiki_settings.default_domain),
                    brief=str(item.get("brief") or "").strip(),
                    evidence_chunk_ids=evidence_ids,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Step 2: 候选合并 / 对齐
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_candidates(candidates: list[CandidateEntity]) -> list[CandidateEntity]:
        """同 slug 候选合并：title 取首个、aliases/evidence/brief/sources_by_file 合并。

        关键：sources_by_file 必须按 file_id 合并，否则跨文件的出处会被丢弃，
        导致 wiki_page_sources 只能记录"首次出现的那个文件"——
        这是用户报告的"上传 3 个文件，但实体页只显示 1 个文件作为出处"的根因。
        """
        merged: dict[str, CandidateEntity] = {}
        for cand in candidates:
            existing = merged.get(cand.slug)
            if existing is None:
                merged[cand.slug] = cand
                continue
            existing.aliases = _merge_unique_strings(
                existing.aliases,
                [cand.slug, cand.title],
                cand.aliases,
            )
            existing.evidence_chunk_ids = list(
                {*existing.evidence_chunk_ids, *cand.evidence_chunk_ids}
            )
            existing.merged_from_count += 1
            if not existing.brief and cand.brief:
                existing.brief = cand.brief
            # 合并 sources_by_file：同 file_id 合并 chunk_ids，新 file_id 直接加入
            for fid, chunk_ids in (cand.sources_by_file or {}).items():
                if not fid:
                    continue
                existing_chunks = existing.sources_by_file.get(fid) or []
                existing.sources_by_file[fid] = list(
                    {*existing_chunks, *(chunk_ids or [])}
                )
        return list(merged.values())

    @staticmethod
    def _deduplicate_candidates(candidates: list[CandidateEntity]) -> list[CandidateEntity]:
        """模糊去重：在精确 slug 合并之后、编译之前执行。

        两路合并策略（任一命中即合并）：
        - slug 编辑相似度（SequenceMatcher ratio >= 0.85）：抓 typo / 连字符变体
        - 标题词元 Jaccard 相似度 >= 0.70：抓同义英文标题
        """
        if len(candidates) <= 1:
            return candidates

        from difflib import SequenceMatcher

        def _slug_similar(a: str, b: str) -> bool:
            return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85

        def _title_token_overlap(a: str, b: str) -> bool:
            import re
            tokens_a = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", a.lower()))
            tokens_b = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", b.lower()))
            if not tokens_a or not tokens_b:
                return False
            jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
            return jaccard >= 0.70

        merged: dict[str, CandidateEntity] = {}
        for cand in candidates:
            matched_key = None
            cand_keys = _candidate_match_keys(cand)
            for key, existing in merged.items():
                if (
                    cand_keys & _candidate_match_keys(existing)
                    or _slug_similar(cand.slug, existing.slug)
                    or _title_token_overlap(cand.title, existing.title)
                ):
                    matched_key = key
                    break

            if matched_key is None:
                merged[cand.slug] = cand
            else:
                existing = merged[matched_key]
                existing_keys = _candidate_match_keys(existing)
                common_canonical = sorted(
                    key
                    for key in (cand_keys & existing_keys)
                    if key and key not in {cand.slug, existing.slug}
                )
                if common_canonical:
                    old_slug = existing.slug
                    existing.slug = common_canonical[0]
                    existing.aliases = _merge_unique_strings(
                        existing.aliases,
                        [old_slug, existing.title],
                    )
                    merged.pop(matched_key)
                    merged[existing.slug] = existing
                    matched_key = existing.slug
                existing.aliases = _merge_unique_strings(
                    existing.aliases,
                    [cand.slug, cand.title],
                    cand.aliases,
                )
                existing.evidence_chunk_ids = list(
                    {*existing.evidence_chunk_ids, *cand.evidence_chunk_ids}
                )
                existing.merged_from_count += 1
                if not existing.brief and cand.brief:
                    existing.brief = cand.brief
                for fid, chunk_ids in (cand.sources_by_file or {}).items():
                    if not fid:
                        continue
                    existing_chunks = existing.sources_by_file.get(fid) or []
                    existing.sources_by_file[fid] = list(
                        {*existing_chunks, *(chunk_ids or [])}
                    )

        return list(merged.values())

    @staticmethod
    def _build_existing_index(
        existing_pages: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """以 slug 为键索引现有页（含 alias 反向映射，便于对齐）。"""
        index: dict[str, dict[str, Any]] = {}
        for page in existing_pages:
            slug = normalize_slug(str(page.get("slug") or ""))
            if slug:
                index[slug] = page
            title_key = _canonical_entity_key(str(page.get("title") or ""))
            if title_key and title_key not in index:
                index[title_key] = page
            # alias → page
            for alias in page.get("aliases") or []:
                a = normalize_slug(str(alias))
                if a and a not in index:
                    index[a] = page
                a_key = _canonical_entity_key(str(alias))
                if a_key and a_key not in index:
                    index[a_key] = page
        return index

    @staticmethod
    def _find_existing_match(
        candidate: CandidateEntity,
        existing_index: dict[str, dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        for key in _candidate_match_key_list(candidate):
            match = existing_index.get(key)
            if match:
                return match
        return None

    # ------------------------------------------------------------------
    # Step 3: 单页编译
    # ------------------------------------------------------------------

    async def _compile_single(
        self,
        *,
        candidate: CandidateEntity,
        existing_match: Optional[dict[str, Any]],
        evidence_pool: dict[str, WikiChunk],
        linkable_entities_directory: dict[str, str],
    ) -> CompiledPage:
        """对单个候选实体执行编辑/新建。

        linkable_entities_directory 是全量 slug→title 字典；本函数会按候选相关性
        裁剪到 wiki.linkable_top_k 条后再喂给 LLM，控制 prompt token 体积。
        """
        operation_mode = "UPDATE" if existing_match else "CREATE"
        evidence_chunks_text = self._format_evidence_chunks(
            chunk_ids=candidate.evidence_chunk_ids,
            evidence_pool=evidence_pool,
        )

        # 按候选相关性裁剪 linkable_entities，避免 prompt 因清单膨胀而 TTFT/TPS 双拖累
        linkable_entities = self._format_linkable_entities_for_candidate(
            candidate=candidate,
            directory=linkable_entities_directory,
        )

        max_chars = int(self._wiki_settings.max_page_chars)
        system_prompt = self._prompts.get(
            "wiki.compile_page.system",
            max_chars=max_chars,
        )
        user_prompt = self._prompts.get(
            "wiki.compile_page.user",
            operation_mode=operation_mode,
            slug=candidate.slug,
            title=candidate.title,
            brief=candidate.brief or "(无)",
            aliases=", ".join(candidate.aliases) if candidate.aliases else "(无)",
            domain=candidate.domain,
            existing_markdown=(
                (existing_match or {}).get("markdown_body") or "(无，本次为新建)"
            ),
            existing_summary=(existing_match or {}).get("summary") or "(无)",
            linkable_entities=linkable_entities,
            evidence_chunks=evidence_chunks_text,
        )

        model = (
            self._wiki_settings.compile_model_update
            if existing_match
            else self._wiki_settings.compile_model_create
        )
        started = time.perf_counter()
        # 单次 LLM 超时保护：偶发慢调用拖累整体；超时上抛交由 _run 捕获并跳过该候选
        timeout_sec = float(
            getattr(self._wiki_settings, "compile_llm_timeout_sec", 90)
        )
        raw, usage = await asyncio.wait_for(
            self.llm.generate_json_detailed(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=self._wiki_settings.compile_temperature,
                max_tokens=self._wiki_settings.compile_max_tokens,
            ),
            timeout=timeout_sec,
        )
        elapsed = time.perf_counter() - started

        if not isinstance(raw, dict):
            return CompiledPage(
                operation="skip",
                slug=candidate.slug,
                title=candidate.title,
                summary="",
                domain=candidate.domain,
                markdown_body="",
                aliases=list(candidate.aliases or []),
                ops_log=f"LLM 返回非法 JSON: {str(raw)[:120]}",
                evidence_chunk_ids=candidate.evidence_chunk_ids,
                source_file_id=candidate.source_file_id,
                sources_by_file=dict(candidate.sources_by_file or {}),
                compile_meta={"model": model, "elapsed_seconds": round(elapsed, 3),
                             "operation": operation_mode},
            )

        markdown_body = str(raw.get("markdown_body") or "").strip()
        summary = str(raw.get("summary") or "").strip() or candidate.brief
        title = str(raw.get("title") or candidate.title).strip()
        domain = str(raw.get("domain") or candidate.domain).strip() or candidate.domain

        if not markdown_body:
            return CompiledPage(
                operation="skip",
                slug=candidate.slug,
                title=title,
                summary=summary,
                domain=domain,
                markdown_body="",
                aliases=list(candidate.aliases or []),
                ops_log="LLM 未输出 markdown_body",
                evidence_chunk_ids=candidate.evidence_chunk_ids,
                source_file_id=candidate.source_file_id,
                sources_by_file=dict(candidate.sources_by_file or {}),
                compile_meta={"model": model, "elapsed_seconds": round(elapsed, 3),
                             "operation": operation_mode},
            )

        # 截断保护
        truncated = False
        if len(markdown_body) > max_chars:
            markdown_body = markdown_body[:max_chars] + "\n\n> _本页已截断到字数上限_"
            truncated = True

        # 链接清洗
        outgoing_links = self._clean_outgoing_links(raw.get("outgoing_links"))
        contradicts = self._clean_contradicts(raw.get("contradicts"))
        evidence_ids = list(raw.get("evidence_chunk_ids") or candidate.evidence_chunk_ids)
        evidence_ids = [str(cid) for cid in evidence_ids if str(cid).strip()]

        compile_meta = {
            "model": model,
            "elapsed_seconds": round(elapsed, 3),
            "operation": operation_mode,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "evidence_chunks": len(evidence_ids),
            "truncated": truncated,
            "outgoing_links_count": len(outgoing_links),
            "candidate_merge_count": candidate.merged_from_count,
        }

        return CompiledPage(
            operation="update" if existing_match else "create",
            slug=candidate.slug,
            title=title,
            summary=summary,
            domain=domain,
            markdown_body=markdown_body,
            aliases=list(candidate.aliases or []),
            existing_page_id=(existing_match or {}).get("id"),
            base_version=(existing_match or {}).get("version"),
            outgoing_links=outgoing_links,
            contradicts=contradicts,
            evidence_chunk_ids=evidence_ids or candidate.evidence_chunk_ids,
            source_file_id=candidate.source_file_id,
            sources_by_file=dict(candidate.sources_by_file or {}),
            ops_log=str(raw.get("ops_log") or "").strip(),
            compile_meta=compile_meta,
        )

    # ------------------------------------------------------------------
    # 工具：证据池 / 文本格式化
    # ------------------------------------------------------------------

    async def _build_evidence_pool(
        self,
        *,
        workspace_id: str,
        candidates: list[CandidateEntity],
    ) -> dict[str, WikiChunk]:
        """把所有候选涉及到的 chunk 按 file_id 一次性拉取，建索引。

        分组目的：避免对同一文件重复 IO；每个文件只查一次。
        """
        by_file: dict[str, set[str]] = {}
        for cand in candidates:
            if cand.sources_by_file:
                for file_id, chunk_ids in cand.sources_by_file.items():
                    if not file_id:
                        continue
                    by_file.setdefault(file_id, set()).update(chunk_ids or [])
                continue
            if cand.source_file_id:
                by_file.setdefault(cand.source_file_id, set()).update(
                    cand.evidence_chunk_ids
                )

        pool: dict[str, WikiChunk] = {}
        for file_id in by_file.keys():
            chunks = await self.chunk_loader.load_chunks_for_file(
                workspace_id=workspace_id,
                file_id=file_id,
            )
            for c in chunks:
                pool[c.chunk_id] = c
        return pool

    @staticmethod
    def _format_chunks_outline(chunks: list[WikiChunk], max_items: int = 60) -> str:
        """把文件切片转成给 LLM 看的清单（chunk_id + header_path + 预览）。"""
        rows: list[str] = []
        for c in chunks[:max_items]:
            header = c.header_path or "(无层级)"
            rows.append(
                f"- chunk_id={c.chunk_id} | header={header} | preview={c.short_preview(160)}"
            )
        if len(chunks) > max_items:
            rows.append(f"... 共 {len(chunks)} 个切片，已省略后续 {len(chunks) - max_items} 项")
        return "\n".join(rows) or "(空)"

    @staticmethod
    def _format_existing_brief(existing_pages: list[dict[str, Any]], max_items: int = 80) -> str:
        if not existing_pages:
            return "(尚无已有实体页)"
        rows: list[str] = []
        for p in existing_pages[:max_items]:
            slug = p.get("slug")
            title = p.get("title")
            summary = (p.get("summary") or "").replace("\n", " ")
            rows.append(f"- {slug} | {title} | {summary[:60]}")
        if len(existing_pages) > max_items:
            rows.append(f"... 已省略 {len(existing_pages) - max_items} 项")
        return "\n".join(rows)

    @staticmethod
    def _build_linkable_directory_dict(
        existing_pages: list[dict[str, Any]],
        extra: list[tuple[str, str]],
    ) -> dict[str, str]:
        """构建全量 slug → title 字典（已有页 + 本次候选），不做裁剪。

        返回 dict 而非字符串，以便 `_compile_single` 在每次 LLM 调用前按候选
        相关性裁剪到 top_k 条；可显著降低 prompt token 体积，加速生成。
        """
        directory: dict[str, str] = {}
        for p in existing_pages:
            slug = normalize_slug(str(p.get("slug") or ""))
            if slug:
                directory.setdefault(slug, str(p.get("title") or slug))
        for slug, title in extra:
            slug_norm = normalize_slug(slug)
            if slug_norm:
                directory.setdefault(slug_norm, title)
        return directory

    def _format_linkable_entities_for_candidate(
        self,
        *,
        candidate: "CandidateEntity",
        directory: dict[str, str],
    ) -> str:
        """按候选相关性裁剪到 wiki.linkable_top_k 条，渲染为 prompt 字符串。

        优先级：
          A. 候选自身 slug + aliases（标准化命中）
          B. 候选 brief 文本中包含的 slug（子串匹配，作为"上下文相关"信号）
          C. directory 中剩余条目，按字典插入序兜底填满 top_k
        """
        if not directory:
            return "(暂无可链接实体)"
        top_k = max(
            1, int(getattr(self._wiki_settings, "linkable_top_k", 30))
        )
        ordered: list[str] = []
        seen: set[str] = set()

        # A: 候选自身 / aliases
        for raw in [candidate.slug, *candidate.aliases]:
            slug_norm = normalize_slug(str(raw))
            if slug_norm and slug_norm in directory and slug_norm not in seen:
                ordered.append(slug_norm)
                seen.add(slug_norm)
                if len(ordered) >= top_k:
                    break

        # B: brief 子串匹配
        if len(ordered) < top_k and candidate.brief:
            brief_lower = candidate.brief.lower()
            for slug in directory:
                if slug in seen:
                    continue
                if slug.lower() in brief_lower:
                    ordered.append(slug)
                    seen.add(slug)
                    if len(ordered) >= top_k:
                        break

        # C: 兜底填满 top_k
        if len(ordered) < top_k:
            for slug in directory:
                if slug in seen:
                    continue
                ordered.append(slug)
                seen.add(slug)
                if len(ordered) >= top_k:
                    break

        return "\n".join(f"- [[{slug}]] {directory[slug]}" for slug in ordered)

    @staticmethod
    def _format_evidence_chunks(
        *,
        chunk_ids: list[str],
        evidence_pool: dict[str, WikiChunk],
        per_chunk_chars: int = 600,
    ) -> str:
        if not chunk_ids:
            return "(无切片证据)"
        rows: list[str] = []
        for cid in chunk_ids[:8]:
            ch = evidence_pool.get(cid)
            if ch is None:
                rows.append(f"[chunk_id={cid}] (未找到)")
                continue
            rows.append(
                f"[chunk_id={cid}] page={ch.page_numbers} header={ch.header_path}\n"
                f"{(ch.content or '')[:per_chunk_chars]}"
            )
        return "\n\n---\n\n".join(rows)

    # ------------------------------------------------------------------
    # 输出清洗
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_outgoing_links(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        cleaned: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = normalize_slug(str(item.get("target_slug") or ""))
            if not target:
                continue
            link_type = str(item.get("link_type") or "mentions").strip().lower()
            if link_type not in {"mentions", "related", "supersedes", "contradicts"}:
                link_type = "mentions"
            key = (target, link_type)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(
                {
                    "target_slug": target,
                    "link_type": link_type,
                    "note": str(item.get("note") or "")[:512],
                }
            )
        return cleaned

    @staticmethod
    def _clean_contradicts(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            with_existing = str(item.get("with_existing") or "").strip()
            new_claim = str(item.get("new_claim") or "").strip()
            if not with_existing or not new_claim:
                continue
            evidence = item.get("evidence_chunk_ids") or []
            if isinstance(evidence, str):
                evidence = [evidence]
            cleaned.append(
                {
                    "with_existing": with_existing[:512],
                    "new_claim": new_claim[:512],
                    "evidence_chunk_ids": [str(x) for x in evidence if str(x).strip()],
                }
            )
        return cleaned


# 单例
_compiler: Optional[WikiCompiler] = None


def get_wiki_compiler() -> WikiCompiler:
    global _compiler
    if _compiler is None:
        _compiler = WikiCompiler()
    return _compiler
