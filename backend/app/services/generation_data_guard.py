"""
Generation data relevance guard.

Used by terminal generators (chart/doc) to skip generation when
available data is very likely unrelated to the user task.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.services.generation_data_service import extract_query_tokens

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _extract_keywords(text: str, *, max_tokens: int = 80) -> set[str]:
    if not text:
        return set()
    return set(extract_query_tokens(text, max_tokens=max_tokens))


def _has_keyword_overlap(query: str, *contexts: Any) -> bool:
    query_tokens = _extract_keywords(query, max_tokens=80)
    if not query_tokens:
        return False

    context_tokens: set[str] = set()
    for ctx in contexts:
        context_tokens.update(_extract_keywords(_stringify(ctx), max_tokens=160))
        if len(context_tokens) >= 200:
            break

    overlap = query_tokens & context_tokens
    return bool(overlap)


def _dataset_summaries(
    memory_dfs: Optional[Dict[str, Any]],
    *,
    max_items: int = 3,
    max_text: int = 500,
    max_rows: int = 50,
) -> List[Dict[str, Any]]:
    if not isinstance(memory_dfs, dict):
        return []

    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    items: List[Dict[str, Any]] = []
    for idx, (key, value) in enumerate(memory_dfs.items()):
        if idx >= max_items:
            break
        if pd is not None and isinstance(value, pd.DataFrame):
            sample = value.head(max_rows).to_dict(orient="records")
            items.append(
                {
                    "key": key,
                    "type": "dataframe",
                    "rows": int(len(value)),
                    "columns": [str(c) for c in list(value.columns)[:30]],
                    "sample": sample,
                }
            )
            continue
        if isinstance(value, list):
            preview = value[:max_rows]
            items.append(
                {
                    "key": key,
                    "type": "list",
                    "size": len(value),
                    "sample": preview,
                }
            )
            continue
        if isinstance(value, dict):
            sample_items = list(value.items())[:max_rows]
            sample_dict = {str(k): v for k, v in sample_items}
            items.append(
                {
                    "key": key,
                    "type": "dict",
                    "size": len(value),
                    "sample": sample_dict,
                }
            )
            continue
        if isinstance(value, str):
            items.append(
                {
                    "key": key,
                    "type": "text",
                    "length": len(value),
                    "sample": value[:max_text],
                }
            )
            continue
        items.append(
            {
                "key": key,
                "type": type(value).__name__,
                "sample": str(value)[:max_text],
            }
        )
    return items


def _result_summaries(
    execution_results: Optional[List[Dict[str, Any]]],
    *,
    max_items: int = 4,
    max_text: int = 500,
) -> List[Dict[str, Any]]:
    if not isinstance(execution_results, list):
        return []
    items: List[Dict[str, Any]] = []
    for idx, item in enumerate(execution_results):
        if idx >= max_items:
            break
        if not isinstance(item, dict):
            continue
        worker = str(item.get("worker", ""))
        result_text = str(item.get("result", ""))[:max_text]
        reason_code = (
            (item.get("quality_signal") or {}).get("reason_code")
            if isinstance(item.get("quality_signal"), dict)
            else None
        )
        items.append(
            {
                "worker": worker,
                "reason_code": reason_code,
                "result_preview": result_text,
            }
        )
    return items


def _message_text(content: Any, max_chars: int = 180) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:max_chars]
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
        return " ".join(parts)[:max_chars]
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)[:max_chars]
    return str(content)[:max_chars]


def _user_history_summaries(
    messages: Optional[List[Any]],
    *,
    max_rounds: int = 5,
) -> List[Dict[str, str]]:
    if not isinstance(messages, list):
        return []

    summaries: List[Dict[str, str]] = []
    for msg in reversed(messages):
        if len(summaries) >= max_rounds:
            break
        role = ""
        content: Any = ""
        if isinstance(msg, dict):
            role = str(msg.get("role", ""))
            content = msg.get("content", "")
        else:
            role = str(getattr(msg, "role", "") or getattr(msg, "type", ""))
            content = getattr(msg, "content", "")

        role = role.lower().strip()
        if role in {"human", "user"}:
            role = "user"
        else:
            continue

        text = _message_text(content)
        if not text.strip():
            continue
        summaries.append({"role": "user", "content": text})
    summaries.reverse()
    return summaries


def _collect_mm_image_urls(
    execution_results: Optional[List[Dict[str, Any]]],
    *,
    max_images: int = 3,
) -> List[str]:
    if not isinstance(execution_results, list):
        return []

    urls: List[str] = []
    seen_keys: set[tuple[str, int]] = set()
    seen_urls: set[str] = set()

    for result in execution_results:
        if not isinstance(result, dict):
            continue
        meta = result.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        mm_evidence = meta.get("mm_evidence") or {}
        if not isinstance(mm_evidence, dict):
            continue
        images = mm_evidence.get("images") or []
        if not isinstance(images, list):
            continue

        for image in images:
            if not isinstance(image, dict):
                continue
            url = str(image.get("url") or "").strip()
            if not url:
                continue

            file_id = str(image.get("file_id") or "").strip()
            page_number = image.get("page_number")
            deduped = False
            try:
                if file_id and page_number is not None:
                    dedup_key = (file_id, int(page_number))
                    if dedup_key in seen_keys:
                        deduped = True
                    else:
                        seen_keys.add(dedup_key)
            except (TypeError, ValueError):
                pass

            if deduped:
                continue
            if url in seen_urls:
                continue

            seen_urls.add(url)
            urls.append(url)
            if len(urls) >= max_images:
                return urls
    return urls


async def evaluate_generation_data_relevance(
    *,
    query: str,
    memory_dfs: Optional[Dict[str, Any]],
    execution_results: Optional[List[Dict[str, Any]]] = None,
    messages: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Decide whether generation should continue.

    Returns:
        {
            "is_relevant": bool,
            "confidence": float,
            "reason": str,
            "source": "local" | "llm" | "fallback"
        }
    """
    dataset_summary = _dataset_summaries(memory_dfs)
    result_summary = _result_summaries(execution_results)
    history_summary = _user_history_summaries(messages)
    image_urls = _collect_mm_image_urls(execution_results, max_images=3)
    if not dataset_summary and not result_summary and not image_urls:
        return {
            "is_relevant": False,
            "confidence": 1.0,
            "reason": "没有可用数据",
            "source": "local",
        }

    settings = get_settings()
    llm = get_async_llm()
    tool_schema = {
        "type": "function",
        "function": {
            "name": "judge_generation_data_relevance",
            "description": "判断给定数据是否与生成任务直接相关",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_relevant": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["is_relevant", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
    }

    system_prompt = (
        "你是数据相关性判定器。你的目标是仅筛除“明显无关”的数据。"
        "只要数据与任务存在可用关联线索（即使不完整），应输出 is_relevant=true。"
        "仅当数据与任务主题明显不相关时，输出 is_relevant=false。"
        "只依据输入，不编造不存在的信息。"
    )
    prompt = (
        "注意：以下数据是部分摘要，不是全量原始数据，请基于摘要做保守判断。\n\n"
        f"任务描述:\n{query}\n\n"
        f"最近用户历史（最多5轮，JSON）:\n{json.dumps(history_summary, ensure_ascii=False, default=str)}\n\n"
        f"数据摘要（文本最多500字，表格最多50行，JSON）:\n"
        f"{json.dumps(dataset_summary, ensure_ascii=False, default=str)}\n\n"
        f"上游结果摘要（文本最多500字，JSON）:\n"
        f"{json.dumps(result_summary, ensure_ascii=False, default=str)}\n\n"
        f"附带图片证据数量: {len(image_urls)}（最多3张）"
    )
    llm_messages: List[Dict[str, Any]]
    if image_urls:
        user_mm_content: List[Dict[str, Any]] = [{"text": prompt}]
        user_mm_content.extend({"image": url} for url in image_urls)
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_mm_content},
        ]
    else:
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    try:
        try:
            parsed = await llm.generate_structured(
                messages=llm_messages,
                tool_schema=tool_schema,
                model=settings.llm.generation_guard_model,
                temperature=0.0,
                max_tokens=220,
            )
        except Exception as mm_err:
            if not image_urls:
                raise
            logger.warning("generation_data_guard: 多模态判定失败，降级文本-only: %s", mm_err)
            parsed = await llm.generate_structured(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                tool_schema=tool_schema,
                model=settings.llm.generation_guard_model,
                temperature=0.0,
                max_tokens=220,
            )
        llm_is_relevant = bool(parsed.get("is_relevant", True))
        llm_confidence = max(0.0, min(1.0, _safe_float(parsed.get("confidence"), 0.0)))
        llm_reason = str(parsed.get("reason", "")).strip() or "LLM 未给出原因"

        # 宽松策略：仅高置信且无关键词重叠时才拦截，避免“相关但不完整”被误杀
        overlap = _has_keyword_overlap(query, dataset_summary, result_summary)
        should_block = (not llm_is_relevant) and (llm_confidence >= 0.90) and (not overlap) and (not image_urls)
        if should_block:
            return {
                "is_relevant": False,
                "confidence": llm_confidence,
                "reason": llm_reason,
                "source": "llm",
            }

        if not llm_is_relevant:
            logger.info(
                "generation_data_guard: 宽松放行负判 (confidence=%.2f overlap=%s images=%s reason=%s)",
                llm_confidence,
                overlap,
                len(image_urls),
                llm_reason,
            )
            return {
                "is_relevant": True,
                "confidence": max(0.0, 1.0 - llm_confidence),
                "reason": f"{llm_reason}（存在相关线索，已放行）",
                "source": "llm",
            }

        return {
            "is_relevant": True,
            "confidence": llm_confidence,
            "reason": llm_reason,
            "source": "llm",
        }
    except Exception as err:
        logger.warning("generation_data_guard: LLM 判定失败，降级放行: %s", err)
        return {
            "is_relevant": True,
            "confidence": 0.0,
            "reason": "相关性判定失败，保守放行",
            "source": "fallback",
        }
