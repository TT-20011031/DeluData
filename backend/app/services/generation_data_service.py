"""
Generation data service.

Unifies two related but distinct responsibilities for terminal generators:
1. overall relevance guard (existing entrypoint kept for compatibility)
2. query-focused source selection / context compaction
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
_GENERIC_TOKENS = {
    "用户",
    "问题",
    "任务",
    "数据",
    "内容",
    "结果",
    "来源",
    "摘要",
    "相关",
    "图表",
    "文档",
    "报告",
    "分析",
    "生成",
    "展示",
    "请",
    "帮我",
    "需要",
    "worker",
    "step",
    "result",
    "sample",
    "type",
    "preview",
    "query",
}
_TEXTUAL_COLUMNS = {
    "content",
    "text",
    "summary",
    "description",
    "body",
    "chunk",
    "passage",
    "excerpt",
}


@dataclass(frozen=True)
class SelectedGenerationSource:
    key: str
    source_type: str
    score: float
    total_items: int
    selected_items: int
    preview_text: str
    reason: str


def extract_query_tokens(query: str, *, max_tokens: int = 40) -> List[str]:
    if not query:
        return []
    tokens: List[str] = []
    seen: set[str] = set()
    for match in _TOKEN_PATTERN.findall(query.lower()):
        token = match.strip()
        if len(token) < 2:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token):
            expanded = _expand_chinese_token(token)
        else:
            expanded = [token]

        for item in expanded:
            if len(item) < 2 or item in _GENERIC_TOKENS or item in seen:
                continue
            seen.add(item)
            tokens.append(item)
            if len(tokens) >= max_tokens:
                return tokens
    return tokens


def _expand_chinese_token(token: str) -> List[str]:
    if len(token) <= 4:
        return [token]

    results = [token]
    max_window = min(4, len(token))
    for window in range(2, max_window + 1):
        for start in range(0, len(token) - window + 1):
            results.append(token[start : start + window])
    return results


def _trim_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(已截断)"


def _score_text(text: str, query_tokens: Iterable[str]) -> float:
    if not text:
        return 0.0
    haystack = text.lower()
    score = 0.0
    for token in query_tokens:
        if token in haystack:
            score += 3.0 if len(token) >= 4 else 2.0
    return score


def _json_preview(value: Any, *, max_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    return _trim_text(text, max_chars=max_chars)


def _row_to_text(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, value in row.items():
        parts.append(f"{key}: {value}")
    return " | ".join(parts)


def _blockify_text(text: str) -> List[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []

    cleaned = cleaned.replace("知识库检索结果：", "", 1).strip()
    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    if raw_blocks:
        return raw_blocks
    return [cleaned]


def _summarize_source_signature(key: str, value: Any) -> str:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    parts = [key]
    if pd is not None and isinstance(value, pd.DataFrame):
        columns = [str(col) for col in list(value.columns)[:20]]
        sample = value.head(5).to_dict(orient="records")
        parts.append(" ".join(columns))
        parts.append(_json_preview(sample, max_chars=1200))
        return "\n".join(parts)

    if isinstance(value, list):
        sample = value[:5]
        parts.append(_json_preview(sample, max_chars=1200))
        return "\n".join(parts)

    if isinstance(value, dict):
        sample = {str(k): v for k, v in list(value.items())[:12]}
        parts.append(_json_preview(sample, max_chars=1200))
        return "\n".join(parts)

    parts.append(_trim_text(str(value), max_chars=1200))
    return "\n".join(parts)


def _select_dataframe_preview(
    value: Any,
    query_tokens: List[str],
    *,
    max_rows: int,
    max_chars: int,
) -> tuple[str, int, int, str]:
    df = value
    total_rows = int(len(df))
    if total_rows <= 0:
        return "[]", 0, 0, "数据表为空"

    scan_limit = min(total_rows, max(max_rows * 8, 80))
    scan_df = df.head(scan_limit)
    scored_positions: List[tuple[float, int]] = []
    columns = [str(col) for col in list(scan_df.columns)]

    for pos, (_, row) in enumerate(scan_df.iterrows()):
        row_dict = row.to_dict()
        score = _score_text(_row_to_text(row_dict), query_tokens)
        if not score:
            text_cols = [col for col in columns if str(col).lower() in _TEXTUAL_COLUMNS]
            if text_cols:
                text_value = " ".join(str(row_dict.get(col, "")) for col in text_cols)
                score = _score_text(text_value, query_tokens)
        if score > 0:
            scored_positions.append((score, pos))

    if scored_positions:
        top_positions = sorted(
            {pos for _, pos in sorted(scored_positions, key=lambda item: (-item[0], item[1]))[:max_rows]}
        )
        selected_df = scan_df.iloc[top_positions]
        reason = f"按 query 命中的行筛选，保留 {len(selected_df)} / {total_rows} 行"
    else:
        selected_df = df.head(max_rows)
        if query_tokens:
            reason = f"未命中具体行，回退到主数据预览 {len(selected_df)} / {total_rows} 行"
        else:
            reason = f"用户问题缺少可筛关键词，保留主数据预览 {len(selected_df)} / {total_rows} 行"

    preview = _json_preview(selected_df.to_dict(orient="records"), max_chars=max_chars)
    return preview, total_rows, int(len(selected_df)), reason


def _select_list_preview(
    value: list,
    query_tokens: List[str],
    *,
    max_items: int,
    max_chars: int,
) -> tuple[str, int, int, str]:
    total_items = len(value)
    if total_items <= 0:
        return "[]", 0, 0, "列表为空"

    scan_limit = min(total_items, max(max_items * 8, 80))
    sample = value[:scan_limit]
    scored_items: List[tuple[float, int]] = []

    for idx, item in enumerate(sample):
        score = _score_text(_json_preview(item, max_chars=800), query_tokens)
        if score > 0:
            scored_items.append((score, idx))

    if scored_items:
        selected_indexes = sorted(
            {idx for _, idx in sorted(scored_items, key=lambda item: (-item[0], item[1]))[:max_items]}
        )
        selected = [sample[idx] for idx in selected_indexes]
        reason = f"按 query 命中的项筛选，保留 {len(selected)} / {total_items} 项"
    else:
        selected = value[:max_items]
        if query_tokens:
            reason = f"未命中具体项，回退到主数据预览 {len(selected)} / {total_items} 项"
        else:
            reason = f"用户问题缺少可筛关键词，保留主数据预览 {len(selected)} / {total_items} 项"

    preview = _json_preview(selected, max_chars=max_chars)
    return preview, total_items, len(selected), reason


def _select_dict_preview(
    value: Dict[str, Any],
    query_tokens: List[str],
    *,
    max_items: int,
    max_chars: int,
) -> tuple[str, int, int, str]:
    items = list(value.items())
    total_items = len(items)
    if total_items <= 0:
        return "{}", 0, 0, "字典为空"

    if query_tokens:
        filtered_items = []
        for key, item_value in items:
            text = f"{key}: {item_value}"
            if _score_text(text, query_tokens) > 0:
                filtered_items.append((key, item_value))
        if filtered_items:
            selected_items = filtered_items[:max_items]
            reason = f"按 query 命中的字段筛选，保留 {len(selected_items)} / {total_items} 项"
        else:
            selected_items = items[:max_items]
            reason = f"未命中字典字段，回退到主数据预览 {len(selected_items)} / {total_items} 项"
    else:
        selected_items = items[:max_items]
        reason = f"用户问题缺少可筛关键词，保留主数据预览 {len(selected_items)} / {total_items} 项"

    preview = _json_preview(dict(selected_items), max_chars=max_chars)
    return preview, total_items, len(selected_items), reason


def _select_text_preview(
    value: str,
    query_tokens: List[str],
    *,
    max_blocks: int,
    max_chars: int,
) -> tuple[str, int, int, str]:
    blocks = _blockify_text(value)
    total_blocks = len(blocks)
    if total_blocks <= 0:
        return "", 0, 0, "文本为空"

    if query_tokens:
        scored_blocks = []
        for idx, block in enumerate(blocks):
            score = _score_text(block, query_tokens)
            if score > 0:
                scored_blocks.append((score, idx, block))
        if scored_blocks:
            selected_entries = sorted(scored_blocks, key=lambda item: (-item[0], item[1]))[:max_blocks]
            selected_entries = sorted(selected_entries, key=lambda item: item[1])
            selected_blocks = [block for _score, _idx, block in selected_entries]
            reason = f"按 query 命中的片段筛选，保留 {len(selected_blocks)} / {total_blocks} 段"
        else:
            selected_blocks = blocks[:max_blocks]
            reason = f"未命中文本片段，回退到前 {len(selected_blocks)} / {total_blocks} 段"
    else:
        selected_blocks = blocks[:max_blocks]
        reason = f"用户问题缺少可筛关键词，保留前 {len(selected_blocks)} / {total_blocks} 段"

    preview = _trim_text("\n\n".join(selected_blocks), max_chars=max_chars)
    return preview, total_blocks, len(selected_blocks), reason


def _select_source_preview(
    key: str,
    value: Any,
    query_tokens: List[str],
    *,
    max_rows: int,
    max_chars: int,
) -> SelectedGenerationSource:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    if pd is not None and isinstance(value, pd.DataFrame):
        preview, total_items, selected_items, reason = _select_dataframe_preview(
            value,
            query_tokens,
            max_rows=max_rows,
            max_chars=max_chars,
        )
        source_type = "dataframe"
    elif isinstance(value, list):
        preview, total_items, selected_items, reason = _select_list_preview(
            value,
            query_tokens,
            max_items=max_rows,
            max_chars=max_chars,
        )
        source_type = "list"
    elif isinstance(value, dict):
        preview, total_items, selected_items, reason = _select_dict_preview(
            value,
            query_tokens,
            max_items=max_rows,
            max_chars=max_chars,
        )
        source_type = "dict"
    else:
        preview, total_items, selected_items, reason = _select_text_preview(
            str(value),
            query_tokens,
            max_blocks=max_rows,
            max_chars=max_chars,
        )
        source_type = "text"

    return SelectedGenerationSource(
        key=key,
        source_type=source_type,
        score=0.0,
        total_items=total_items,
        selected_items=selected_items,
        preview_text=preview,
        reason=reason,
    )


def select_generation_sources(
    query: str,
    memory_dfs: Optional[Dict[str, Any]],
    *,
    max_sources: int = 3,
    max_rows: int = 15,
    max_chars_per_source: int = 2500,
) -> List[SelectedGenerationSource]:
    if not isinstance(memory_dfs, dict) or not memory_dfs:
        return []

    query_tokens = extract_query_tokens(query)
    candidates = []
    source_count = len(memory_dfs)

    for idx, (key, value) in enumerate(memory_dfs.items()):
        signature = _summarize_source_signature(key, value)
        score = _score_text(signature, query_tokens)
        recency_bonus = (idx + 1) / max(source_count, 1) * 0.05
        candidates.append((score + recency_bonus, idx, key, value))

    positive = [item for item in candidates if item[0] > 0.05]
    if positive:
        selected_candidates = sorted(positive, key=lambda item: (-item[0], item[1]))[:max_sources]
    else:
        selected_candidates = candidates[-max_sources:]

    selected_sources: List[SelectedGenerationSource] = []
    for score, _idx, key, value in selected_candidates:
        selected = _select_source_preview(
            key,
            value,
            query_tokens,
            max_rows=max_rows,
            max_chars=max_chars_per_source,
        )
        selected_sources.append(
            SelectedGenerationSource(
                key=selected.key,
                source_type=selected.source_type,
                score=score,
                total_items=selected.total_items,
                selected_items=selected.selected_items,
                preview_text=selected.preview_text,
                reason=selected.reason,
            )
        )

    return selected_sources


def select_primary_generation_source(
    query: str,
    memory_dfs: Optional[Dict[str, Any]],
    *,
    max_rows: int = 50,
    max_chars_per_source: int = 5000,
) -> Optional[SelectedGenerationSource]:
    selected = select_generation_sources(
        query,
        memory_dfs,
        max_sources=1,
        max_rows=max_rows,
        max_chars_per_source=max_chars_per_source,
    )
    return selected[0] if selected else None


def build_query_focused_context(
    query: str,
    memory_dfs: Optional[Dict[str, Any]],
    *,
    context_title: str = "筛选后的可用数据",
    max_sources: int = 3,
    max_rows: int = 15,
    max_chars: int = 8000,
    max_chars_per_source: int = 2500,
) -> str:
    sources = select_generation_sources(
        query,
        memory_dfs,
        max_sources=max_sources,
        max_rows=max_rows,
        max_chars_per_source=max_chars_per_source,
    )
    if not sources:
        return ""

    result_parts = [f"\n\n## {context_title}\n"]
    current_chars = len(result_parts[0])

    for source in sources:
        if current_chars >= max_chars:
            result_parts.append("\n...(已达到上下文上限，省略其余数据源)\n")
            break

        fence = "json" if source.source_type in {"dataframe", "list", "dict"} else ""
        part = (
            f"\n### {source.key}\n"
            f"类型: {source.source_type}\n"
            f"筛选说明: {source.reason}\n"
            f"原始条目: {source.total_items}\n"
            f"保留条目: {source.selected_items}\n"
            f"预览:\n```{fence}\n{source.preview_text}\n```\n"
        )

        if current_chars + len(part) > max_chars:
            remaining = max_chars - current_chars - 50
            if remaining > 120:
                part = part[:remaining] + "\n...(已截断)\n"
            else:
                part = "\n...(已达到上下文上限)\n"

        result_parts.append(part)
        current_chars += len(part)

    result_parts.append(
        "\n**重要**: 只能使用上面筛选后的相关数据。未入选的数据源、行、字段、片段必须忽略。\n"
    )
    return "".join(result_parts)


def extract_query_focused_result_text(
    query: str,
    execution_results: Optional[List[Dict[str, Any]]],
    *,
    max_blocks: int = 8,
    max_chars: int = 6000,
) -> str:
    if not isinstance(execution_results, list) or not execution_results:
        return ""

    query_tokens = extract_query_tokens(query)
    collected_blocks: List[tuple[float, int, int, str]] = []
    fallback_blocks: List[str] = []

    for result_idx, result in enumerate(execution_results):
        if not isinstance(result, dict):
            continue
        worker = str(result.get("worker", ""))
        text = result.get("result")
        if not isinstance(text, str) or not text.strip():
            continue
        if worker != "doc_worker" and "知识库检索结果" not in text:
            continue

        blocks = _blockify_text(text)
        if not fallback_blocks and blocks:
            fallback_blocks = blocks

        for block_idx, block in enumerate(blocks):
            score = _score_text(block, query_tokens)
            if score > 0:
                collected_blocks.append((score, result_idx, block_idx, block))

    if collected_blocks:
        ordered = sorted(collected_blocks, key=lambda item: (-item[0], item[1], item[2]))[:max_blocks]
        unique_blocks: List[str] = []
        seen: set[str] = set()
        for _score, _result_idx, _block_idx, block in ordered:
            normalized = block.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_blocks.append(normalized)
        text = "\n\n".join(unique_blocks)
        return _trim_text(text, max_chars=max_chars)

    if fallback_blocks:
        return _trim_text("\n\n".join(fallback_blocks[:max_blocks]), max_chars=max_chars)

    return ""
