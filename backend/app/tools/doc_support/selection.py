import logging
import re
from collections import defaultdict
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.rag.data_context import estimate_token_count

logger = logging.getLogger(__name__)


def chunk_score(chunk) -> float:
    rerank = getattr(chunk, "rerank_score", None)
    if rerank is not None:
        return float(rerank)
    return float(getattr(chunk, "score", 0.0) or 0.0)


def chunk_tokens(chunk) -> int:
    return estimate_token_count(getattr(chunk, "content", "") or "")


def trim_chunk_to_token_budget(chunk, max_tokens: int):
    max_tokens = max(1, int(max_tokens))
    content = str(getattr(chunk, "content", "") or "")
    current_tokens = estimate_token_count(content)
    if current_tokens <= max_tokens:
        return chunk

    char_limit = max(
        1,
        min(
            len(content),
            int(len(content) * max_tokens / max(1, current_tokens) * 0.95),
        ),
    )
    trimmed_content = content[:char_limit].rstrip()
    while (
        len(trimmed_content) > 1
        and estimate_token_count(trimmed_content) > max_tokens
    ):
        trimmed_content = trimmed_content[: max(1, int(len(trimmed_content) * 0.9))].rstrip()

    metadata = dict(getattr(chunk, "metadata", None) or {})
    metadata["evidence_truncated"] = True
    metadata["original_token_count"] = current_tokens
    return replace(chunk, content=trimmed_content, metadata=metadata)


def parse_page_numbers(raw_page_numbers: Any) -> List[int]:
    if raw_page_numbers is None:
        return []
    if isinstance(raw_page_numbers, (list, tuple, set)):
        candidates = list(raw_page_numbers)
    else:
        candidates = [raw_page_numbers]

    pages: List[int] = []
    for candidate in candidates:
        for match in re.findall(r"\d+", str(candidate)):
            try:
                page_num = int(match)
            except (TypeError, ValueError):
                continue
            if page_num > 0:
                pages.append(page_num)
    return sorted(list(set(pages)))


def chunk_primary_page_number(chunk) -> Optional[int]:
    direct = getattr(chunk, "page_number", None)
    try:
        if direct is not None and int(direct) > 0:
            return int(direct)
    except (TypeError, ValueError):
        pass

    metadata = chunk.metadata or {}
    direct_meta = metadata.get("page_number")
    try:
        if direct_meta is not None and int(direct_meta) > 0:
            return int(direct_meta)
    except (TypeError, ValueError):
        pass

    page_numbers = parse_page_numbers(metadata.get("page_numbers"))
    return page_numbers[0] if page_numbers else None


def chunk_file_id(chunk) -> str:
    metadata = chunk.metadata or {}
    return str(metadata.get("file_id") or "").strip()


def chunk_source_name(chunk) -> str:
    source_file = str(getattr(chunk, "source_file", "") or "").strip()
    if source_file:
        return source_file
    metadata = chunk.metadata or {}
    return str(metadata.get("source_file") or "未知文件").strip() or "未知文件"


def chunk_page_label(chunk) -> str:
    pages = parse_page_numbers((chunk.metadata or {}).get("page_numbers"))
    if not pages:
        primary_page = chunk_primary_page_number(chunk)
        if primary_page:
            pages = [primary_page]
    if not pages:
        return "-"
    if len(pages) == 1:
        return str(pages[0])
    return ",".join(str(page) for page in pages[:3])


def log_text_chunk_decision(
    *,
    stage: str,
    chunk,
    reason_code: str,
    reason_detail: str,
) -> None:
    logger.info(
        "doc_tool: 文本块决策 阶段=%s 文件=%s 页码=%s rerank分=%.4f 原因码=%s 原因说明=%s",
        stage,
        chunk_source_name(chunk),
        chunk_page_label(chunk),
        chunk_score(chunk),
        reason_code,
        reason_detail,
    )


def log_image_page_decision(
    *,
    stage: str,
    file_name: str,
    page_number: int,
    chunk_score_value: float,
    support_score: float,
    reason_code: str,
    reason_detail: str,
) -> None:
    logger.info(
        "doc_tool: 图片页决策 阶段=%s 文件=%s 页码=%s chunk分数=%.4f 文件支持度=%.4f 原因码=%s 原因说明=%s",
        stage,
        file_name or "未知文件",
        page_number,
        chunk_score_value,
        support_score,
        reason_code,
        reason_detail,
    )


def log_image_alignment(text_chunks: List, images: List[Dict[str, Any]], file_info_map: Dict[str, Dict[str, Any]]) -> None:
    text_file_ids = {
        chunk_file_id(chunk)
        for chunk in text_chunks
        if chunk_file_id(chunk)
    }
    image_file_ids = {
        str(image.get("file_id") or "").strip()
        for image in images
        if str(image.get("file_id") or "").strip()
    }
    overlap = text_file_ids & image_file_ids
    overlap_ratio = 0.0
    if image_file_ids:
        overlap_ratio = len(overlap) / len(image_file_ids)

    def _file_names(file_ids: set[str]) -> str:
        names = []
        for file_id in sorted(file_ids):
            file_info = file_info_map.get(file_id) or {}
            names.append(str(file_info.get("name") or file_id))
        return ",".join(names) if names else "-"

    logger.info(
        "doc_tool: 文本图片对齐 文本证据文件数=%s 图片证据文件数=%s 文件交集数=%s 文件交集比例=%.4f 文本文件=%s 图片文件=%s 交集文件=%s",
        len(text_file_ids),
        len(image_file_ids),
        len(overlap),
        overlap_ratio,
        _file_names(text_file_ids),
        _file_names(image_file_ids),
        _file_names(overlap),
    )


def filter_effective_chunks(
    chunks: List,
    score_threshold: float,
    required_file_ids: Optional[set[str]] = None,
) -> Tuple[List, Optional[Any], int]:
    nav_chunk = None
    low_score_filtered = 0
    effective: List = []
    normalized_required_file_ids = {
        str(file_id).strip()
        for file_id in (required_file_ids or set())
        if str(file_id).strip()
    }

    for chunk in chunks:
        metadata = chunk.metadata or {}
        chunk_type = str(metadata.get("type") or "").lower()
        if chunk_type in {"pageindex_navigation", "navigation"}:
            if nav_chunk is None:
                nav_chunk = chunk
            continue

        score = chunk_score(chunk)
        content = (chunk.content or "").strip()
        if not content:
            continue

        if (
            score > 0
            and score < score_threshold
            and chunk_file_id(chunk) not in normalized_required_file_ids
        ):
            low_score_filtered += 1
            log_text_chunk_decision(
                stage="基础阈值过滤",
                chunk=chunk,
                reason_code="低于基础阈值",
                reason_detail=f"chunk 分数 {score:.4f} 低于基础阈值 {score_threshold:.4f}",
            )
            continue

        effective.append(chunk)

    return effective, nav_chunk, low_score_filtered


def chunks_to_df(chunks: List):
    rows = []
    for chunk in chunks:
        rows.append(
            {
                "content": chunk.content,
                "source": chunk.source_file,
                "score": chunk_score(chunk),
                "file_id": chunk_file_id(chunk),
                "token_count": chunk_tokens(chunk),
            }
        )
    return pd.DataFrame(rows)


def select_chunks_for_synthesizer(
    chunks: List,
    token_budget: int,
    score_threshold: float,
    relative_margin: float,
    max_chunks: int,
    required_file_ids: Optional[set[str]] = None,
    required_max_chunks: int = 3,
    required_token_budget_ratio: float = 0.5,
) -> Tuple[List, int, bool]:
    if not chunks:
        return [], 0, False

    ranked = sorted(chunks, key=chunk_score, reverse=True)
    top_score = chunk_score(ranked[0])
    dynamic_threshold = max(float(score_threshold), float(top_score - relative_margin))

    selected: List = []
    used_tokens = 0
    trimmed = False

    normalized_required_ids = {
        str(file_id).strip()
        for file_id in (required_file_ids or set())
        if str(file_id).strip()
    }
    if normalized_required_ids:
        ordinary_ranked = [
            chunk
            for chunk in ranked
            if chunk_file_id(chunk) not in normalized_required_ids
        ]
        ordinary_dynamic_threshold = float(score_threshold)
        if ordinary_ranked:
            ordinary_dynamic_threshold = max(
                float(score_threshold),
                float(chunk_score(ordinary_ranked[0]) - relative_margin),
            )
        required_max_chunks = max(1, int(required_max_chunks))
        required_ratio = min(max(float(required_token_budget_ratio), 0.0), 1.0)
        required_token_budget = min(
            int(token_budget),
            max(1, int(token_budget * required_ratio)),
        )
        required_groups: Dict[str, List] = defaultdict(list)
        required_order: List[str] = []
        for chunk in ranked:
            file_id = chunk_file_id(chunk)
            if file_id not in normalized_required_ids:
                continue
            if file_id not in required_groups:
                required_order.append(file_id)
            required_groups[file_id].append(chunk)

        first_chunk_budget_per_file = max(
            1,
            required_token_budget // max(1, len(required_order)),
        )
        for index in range(required_max_chunks):
            for file_id in required_order:
                file_chunks = required_groups[file_id]
                if index >= len(file_chunks):
                    continue
                chunk = file_chunks[index]
                tokens = chunk_tokens(chunk)
                if len(selected) >= max_chunks:
                    trimmed = True
                    continue
                remaining_required_budget = required_token_budget - used_tokens
                allowed_tokens = remaining_required_budget
                if index == 0:
                    allowed_tokens = min(
                        allowed_tokens,
                        first_chunk_budget_per_file,
                    )
                if tokens > allowed_tokens and index == 0 and allowed_tokens > 0:
                    chunk = trim_chunk_to_token_budget(chunk, allowed_tokens)
                    tokens = chunk_tokens(chunk)
                    trimmed = True
                if allowed_tokens <= 0 or tokens > allowed_tokens:
                    trimmed = True
                    log_text_chunk_decision(
                        stage="指定文件预算裁剪",
                        chunk=chunk,
                        reason_code="超过指定文件预算",
                        reason_detail=f"指定文件证据最多使用 token 预算 {required_token_budget}",
                    )
                    continue
                selected.append(chunk)
                used_tokens += tokens
                log_text_chunk_decision(
                    stage="指定文件证据保底",
                    chunk=chunk,
                    reason_code="保留指定文件证据",
                    reason_detail=(
                        f"文件 {file_id} 的第 {index + 1} 个证据块，"
                        f"指定文件预算 {required_token_budget}"
                    ),
                )

        for file_chunks in required_groups.values():
            if len(file_chunks) > required_max_chunks:
                trimmed = True

        for chunk in ordinary_ranked:
            score = chunk_score(chunk)
            if score < ordinary_dynamic_threshold:
                trimmed = True
                log_text_chunk_decision(
                    stage="相对分差截断",
                    chunk=chunk,
                    reason_code="被相对分差截断",
                    reason_detail=(
                        f"chunk 分数 {score:.4f} 低于普通候选动态阈值 "
                        f"{ordinary_dynamic_threshold:.4f}"
                    ),
                )
                continue
            if len(selected) >= max_chunks:
                trimmed = True
                log_text_chunk_decision(
                    stage="文本上限裁剪",
                    chunk=chunk,
                    reason_code="超过文本上限",
                    reason_detail=f"最终文本证据上限为 {max_chunks} 个 chunk",
                )
                continue
            tokens = chunk_tokens(chunk)
            if used_tokens + tokens > token_budget:
                trimmed = True
                log_text_chunk_decision(
                    stage="文本上限裁剪",
                    chunk=chunk,
                    reason_code="超过文本上限",
                    reason_detail=f"追加后将超过 token 预算 {token_budget}",
                )
                continue
            selected.append(chunk)
            used_tokens += tokens
            log_text_chunk_decision(
                stage="文本证据保留",
                chunk=chunk,
                reason_code="保留为高分证据",
                reason_detail=(
                    f"chunk 分数 {score:.4f} 满足普通候选动态阈值 "
                    f"{ordinary_dynamic_threshold:.4f}"
                ),
            )

        if not selected and ranked:
            fallback_chunk = ranked[0]
            selected = [fallback_chunk]
            used_tokens = chunk_tokens(fallback_chunk)
            trimmed = True
            log_text_chunk_decision(
                stage="文本兜底",
                chunk=fallback_chunk,
                reason_code="使用文本兜底首块",
                reason_detail="指定文件与普通候选均未进入预算，退回最高分 chunk",
            )
        return selected, used_tokens, trimmed

    for chunk in ranked:
        score = chunk_score(chunk)
        if score < dynamic_threshold:
            trimmed = True
            log_text_chunk_decision(
                stage="相对分差截断",
                chunk=chunk,
                reason_code="被相对分差截断",
                reason_detail=f"chunk 分数 {score:.4f} 低于动态阈值 {dynamic_threshold:.4f}",
            )
            continue

        if len(selected) >= max_chunks:
            trimmed = True
            log_text_chunk_decision(
                stage="文本上限裁剪",
                chunk=chunk,
                reason_code="超过文本上限",
                reason_detail=f"最终文本证据上限为 {max_chunks} 个 chunk",
            )
            continue

        tokens = chunk_tokens(chunk)
        if used_tokens + tokens > token_budget and selected:
            trimmed = True
            log_text_chunk_decision(
                stage="文本上限裁剪",
                chunk=chunk,
                reason_code="超过文本上限",
                reason_detail=f"追加后将超过 token 预算 {token_budget}",
            )
            continue

        selected.append(chunk)
        used_tokens += tokens
        log_text_chunk_decision(
            stage="文本证据保留",
            chunk=chunk,
            reason_code="保留为高分证据",
            reason_detail=f"chunk 分数 {score:.4f} 满足动态阈值 {dynamic_threshold:.4f}",
        )

    if not selected and ranked:
        fallback_chunk = ranked[0]
        selected = [fallback_chunk]
        used_tokens = chunk_tokens(fallback_chunk)
        trimmed = True
        log_text_chunk_decision(
            stage="文本兜底",
            chunk=fallback_chunk,
            reason_code="使用文本兜底首块",
            reason_detail="相对分差截断后无可用文本证据，退回最高分 chunk",
        )

    return selected, used_tokens, trimmed


def select_page_images_for_synthesizer(
    chunks: List,
    file_info_map: Dict[str, Dict[str, Any]],
    score_threshold: float,
    pdf_relative_margin: float,
    secondary_best_delta: float,
    secondary_support_ratio: float,
    page_window: int,
    max_files: int,
    max_images: int,
    primary_anchor_pages: int,
    secondary_anchor_pages: int,
) -> List[Dict[str, Any]]:
    if not chunks or max_images <= 0 or max_files <= 0:
        return []

    ranked_chunks = sorted(chunks, key=chunk_score, reverse=True)
    pdf_candidates: List[Dict[str, Any]] = []

    for chunk in ranked_chunks:
        file_id = chunk_file_id(chunk)
        if not file_id:
            continue

        file_info = file_info_map.get(file_id) or {}
        file_name = str(file_info.get("name") or chunk_source_name(chunk))
        file_type = str(file_info.get("type") or "").lower().strip()
        storage_path = str(file_info.get("storage_path") or "").strip()
        page_number = chunk_primary_page_number(chunk) or 0
        score = chunk_score(chunk)

        if (file_type and file_type != "pdf") or (not file_type and not storage_path.lower().endswith(".pdf")):
            log_image_page_decision(
                stage="PDF候选过滤",
                file_name=file_name,
                page_number=page_number,
                chunk_score_value=score,
                support_score=0.0,
                reason_code="不是PDF",
                reason_detail="当前图片证据仅从 PDF 页面中选择",
            )
            continue

        if page_number <= 0:
            continue

        pdf_candidates.append(
            {
                "chunk": chunk,
                "file_id": file_id,
                "file_name": file_name,
                "page_number": page_number,
                "chunk_score": score,
            }
        )

    if not pdf_candidates:
        return []

    best_pdf_score = pdf_candidates[0]["chunk_score"]
    dynamic_threshold = max(float(score_threshold), float(best_pdf_score - pdf_relative_margin))
    high_conf_candidates = [item for item in pdf_candidates if item["chunk_score"] >= dynamic_threshold]
    if not high_conf_candidates:
        high_conf_candidates = [pdf_candidates[0]]

    file_candidates: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for candidate in high_conf_candidates:
        file_candidates[candidate["file_id"]].append(candidate)

    file_stats: List[Dict[str, Any]] = []
    for file_id, candidates in file_candidates.items():
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (item["chunk_score"], -item["page_number"]),
            reverse=True,
        )
        support_score = sum(item["chunk_score"] for item in sorted_candidates[:2])
        file_stats.append(
            {
                "file_id": file_id,
                "file_name": sorted_candidates[0]["file_name"],
                "support_score": support_score,
                "best_score": sorted_candidates[0]["chunk_score"],
                "candidates": sorted_candidates,
                "high_conf_count": len(sorted_candidates),
            }
        )

    file_stats.sort(key=lambda item: (item["support_score"], item["best_score"]), reverse=True)
    primary_file = file_stats[0]
    selected_files = [primary_file]

    for file_stat in file_stats[1:]:
        if len(selected_files) >= max_files:
            log_image_page_decision(
                stage="第二文件判定",
                file_name=file_stat["file_name"],
                page_number=file_stat["candidates"][0]["page_number"],
                chunk_score_value=file_stat["best_score"],
                support_score=file_stat["support_score"],
                reason_code="第二文件未通过",
                reason_detail=f"仅允许最多 {max_files} 个 PDF 文件进入图片证据",
            )
            continue

        support_ok = file_stat["support_score"] >= primary_file["support_score"] * secondary_support_ratio
        best_ok = file_stat["best_score"] >= primary_file["best_score"] - secondary_best_delta
        if support_ok or best_ok:
            selected_files.append(file_stat)
            continue

        log_image_page_decision(
            stage="第二文件判定",
            file_name=file_stat["file_name"],
            page_number=file_stat["candidates"][0]["page_number"],
            chunk_score_value=file_stat["best_score"],
            support_score=file_stat["support_score"],
            reason_code="第二文件未通过",
            reason_detail=(
                f"支持度 {file_stat['support_score']:.4f} 与最佳页分数 {file_stat['best_score']:.4f} "
                f"均未达到主文件放宽条件（支持度比例 {secondary_support_ratio:.2f} / 最佳页分差 {secondary_best_delta:.2f}）"
            ),
        )

    def _pick_anchor_pages(file_stat: Dict[str, Any], limit: int, stage: str) -> List[Dict[str, Any]]:
        anchors: List[Dict[str, Any]] = []
        seen_pages: set[int] = set()
        for candidate in file_stat["candidates"]:
            page_number = int(candidate["page_number"])
            if page_number in seen_pages:
                log_image_page_decision(
                    stage=stage,
                    file_name=file_stat["file_name"],
                    page_number=page_number,
                    chunk_score_value=candidate["chunk_score"],
                    support_score=file_stat["support_score"],
                    reason_code="页已去重",
                    reason_detail="同一文件的重复锚点页仅保留分数更高的记录",
                )
                continue
            anchors.append(
                {
                    "file_id": file_stat["file_id"],
                    "file_name": file_stat["file_name"],
                    "page_number": page_number,
                    "chunk_score": candidate["chunk_score"],
                    "support_score": file_stat["support_score"],
                    "kind": stage,
                }
            )
            seen_pages.add(page_number)
            if len(anchors) >= limit:
                break
        return anchors

    primary_anchor_items = _pick_anchor_pages(primary_file, primary_anchor_pages, "主文件锚点页")
    secondary_anchor_items: List[Dict[str, Any]] = []
    if len(selected_files) > 1:
        secondary_anchor_items = _pick_anchor_pages(selected_files[1], secondary_anchor_pages, "第二文件锚点页")

    primary_expansion_items: List[Dict[str, Any]] = []
    primary_anchor_pages_set = {item["page_number"] for item in primary_anchor_items}
    if page_window > 0 and primary_anchor_items:
        if primary_file["high_conf_count"] >= 2:
            expansion_map: Dict[int, Dict[str, Any]] = {}
            for anchor in primary_anchor_items:
                anchor_page = int(anchor["page_number"])
                for distance in range(1, page_window + 1):
                    for page_number in (anchor_page - distance, anchor_page + distance):
                        if page_number <= 0 or page_number in primary_anchor_pages_set:
                            continue
                        existing = expansion_map.get(page_number)
                        candidate = {
                            "file_id": primary_file["file_id"],
                            "file_name": primary_file["file_name"],
                            "page_number": page_number,
                            "chunk_score": anchor["chunk_score"],
                            "support_score": primary_file["support_score"],
                            "kind": "主文件扩展页",
                            "distance": distance,
                        }
                        if existing is None or (
                            candidate["chunk_score"],
                            -candidate["distance"],
                            -candidate["page_number"],
                        ) > (
                            existing["chunk_score"],
                            -existing["distance"],
                            -existing["page_number"],
                        ):
                            expansion_map[page_number] = candidate
            primary_expansion_items = sorted(
                expansion_map.values(),
                key=lambda item: (-item["chunk_score"], item["distance"], item["page_number"]),
            )
            for item in primary_expansion_items:
                log_image_page_decision(
                    stage="主文件扩页",
                    file_name=item["file_name"],
                    page_number=item["page_number"],
                    chunk_score_value=item["chunk_score"],
                    support_score=item["support_score"],
                    reason_code="扩展相邻页",
                    reason_detail=f"主文件高置信 PDF chunk 数为 {primary_file['high_conf_count']}，允许扩展相邻页",
                )
        else:
            for anchor in primary_anchor_items:
                log_image_page_decision(
                    stage="主文件扩页",
                    file_name=anchor["file_name"],
                    page_number=anchor["page_number"],
                    chunk_score_value=anchor["chunk_score"],
                    support_score=anchor["support_score"],
                    reason_code="未扩相邻页",
                    reason_detail="主文件高置信 PDF chunk 少于 2 个，仅保留锚点页",
                )

    for anchor in secondary_anchor_items:
        log_image_page_decision(
            stage="第二文件扩页",
            file_name=anchor["file_name"],
            page_number=anchor["page_number"],
            chunk_score_value=anchor["chunk_score"],
            support_score=anchor["support_score"],
            reason_code="未扩相邻页",
            reason_detail="第一版不为第二文件扩展相邻页，只保留锚点页",
        )

    ordered_items = primary_anchor_items + secondary_anchor_items + primary_expansion_items
    if not ordered_items and pdf_candidates:
        fallback = pdf_candidates[0]
        log_image_page_decision(
            stage="图片兜底",
            file_name=fallback["file_name"],
            page_number=fallback["page_number"],
            chunk_score_value=fallback["chunk_score"],
            support_score=fallback["chunk_score"],
            reason_code="使用图片兜底",
            reason_detail="严格筛选后无图片页，退回主文件最高分 PDF 页",
        )
        return [
            {
                "file_id": fallback["file_id"],
                "file_name": fallback["file_name"],
                "page_number": fallback["page_number"],
                "chunk_score": fallback["chunk_score"],
                "support_score": fallback["chunk_score"],
                "kind": "图片兜底页",
            }
        ]

    selected_items = ordered_items[:max_images]
    for item in ordered_items[max_images:]:
        log_image_page_decision(
            stage="图片上限裁剪",
            file_name=item["file_name"],
            page_number=item["page_number"],
            chunk_score_value=item["chunk_score"],
            support_score=item["support_score"],
            reason_code="超过图片上限",
            reason_detail=f"最终图片页上限为 {max_images} 页，按固定顺序裁剪",
        )

    for item in selected_items:
        item.pop("distance", None)
    return selected_items
