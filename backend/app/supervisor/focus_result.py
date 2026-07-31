"""
会话当前焦点结果。

职责：
1. 从会话数据中提炼“当前正在讨论的结果”
2. 为快速判断节点提供轻量上下文
3. 为直接回答路径提供结构化结果预览
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field


class FocusResultSnapshot(BaseModel):
    """当前正在讨论的数据结果快照。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    source_kind: Literal["dataframe", "list", "dict", "unknown"] = "unknown"
    source_worker: str = ""
    round_index: int = 0
    row_count: int = 0
    column_count: int = 0
    columns: List[str] = Field(default_factory=list)
    preview_rows: List[Dict[str, Any]] = Field(default_factory=list)
    preview_text: str = ""
    summary: str = ""
    query: str = ""
    source_tables: List[str] = Field(default_factory=list)
    source_sql: str = ""
    source_rewrite: str = ""


class FollowupIntentDecision(BaseModel):
    """快速判断节点的结构化判定结果。"""

    model_config = ConfigDict(extra="forbid")

    intent_type: Literal["chitchat", "direct_answer", "tool_use"]
    is_followup_to_existing_result: bool
    has_sufficient_session_context: bool
    requires_new_tool: bool
    reason: str


_FOCUS_RESULT_KEY_PATTERN = re.compile(r"\b(df_[a-zA-Z0-9_]+)\b")


def load_focus_result(raw_value: Any) -> Optional[FocusResultSnapshot]:
    if not raw_value:
        return None
    try:
        snapshot = FocusResultSnapshot.model_validate(raw_value)
    except Exception:
        return None
    if not snapshot.key:
        return None
    if not _is_focus_snapshot_usable(snapshot):
        return None
    return snapshot


def dump_focus_result(snapshot: Optional[FocusResultSnapshot]) -> Dict[str, Any]:
    if snapshot is None:
        return {}
    return snapshot.model_dump(mode="python")


def build_recent_dialogue(messages: Any, *, max_messages: int = 4) -> str:
    if not isinstance(messages, list) or not messages:
        return "无"

    dialogue_lines: List[str] = []
    for message in messages[-max_messages:]:
        if isinstance(message, HumanMessage):
            role = "用户"
        elif isinstance(message, AIMessage):
            role = "助手"
        else:
            continue

        content = extract_message_text(message.content)
        if not content:
            continue
        dialogue_lines.append(f"- [{role}] {content[:140]}")

    return "\n".join(dialogue_lines) if dialogue_lines else "无"


def build_focus_result_section(snapshot: Optional[FocusResultSnapshot]) -> str:
    if snapshot is None:
        return "无"

    lines = [
        f"结果标识: {snapshot.key}",
        f"结果类型: {snapshot.source_kind}",
        f"来源工具: {snapshot.source_worker or '未知'}",
        f"生成轮次: {snapshot.round_index}",
    ]
    if snapshot.row_count:
        lines.append(f"行数: {snapshot.row_count}")
    if snapshot.column_count:
        lines.append(f"列数: {snapshot.column_count}")
    if snapshot.columns:
        lines.append(f"字段: {', '.join(snapshot.columns[:12])}")
    if snapshot.summary:
        lines.append(f"结果说明: {snapshot.summary[:220]}")
    if snapshot.source_tables:
        lines.append(f"实际引用表: {', '.join(snapshot.source_tables[:12])}")
    if snapshot.source_rewrite:
        lines.append(f"SQL改写问题: {snapshot.source_rewrite[:220]}")
    if snapshot.source_sql:
        lines.append(f"来源SQL: {snapshot.source_sql[:1200]}")
    if snapshot.preview_text:
        lines.append(f"结果预览: {snapshot.preview_text[:500]}")
    return "\n".join(lines)


def build_direct_answer_payload(
    *,
    summary: str,
    recent_dialogue: str,
    user_query: str,
    focus_result: Optional[FocusResultSnapshot],
    memory_dfs: Any,
) -> str:
    parts = [f"历史摘要：{summary or '无'}"]

    if recent_dialogue and recent_dialogue != "无":
        parts.append(f"最近对话：\n{recent_dialogue}")

    parts.append(f"用户问题：{user_query}")

    focus_section = build_focus_result_section(focus_result)
    if focus_section != "无":
        parts.append(f"当前正在讨论的结果：\n{focus_section}")

    if focus_result is not None:
        live_preview = build_live_result_preview(focus_result, memory_dfs)
        if live_preview:
            parts.append(f"当前结果详情：\n{live_preview}")

    parts.append(
        "回答要求：如果用户是在继续追问当前结果，请优先基于“当前正在讨论的结果”回答。"
        "若用户询问字段、数据、库存、来源或来自哪张表，请优先依据“来源SQL”和“实际引用表”判断；"
        "不要臆造不存在的列、行、表名或新的统计口径。"
    )
    return "\n\n".join(parts)


def build_live_result_preview(
    snapshot: FocusResultSnapshot,
    memory_dfs: Any,
    *,
    max_rows: int = 5,
    max_chars: int = 1200,
) -> str:
    if not isinstance(memory_dfs, dict):
        return snapshot.preview_text[:max_chars]

    value = memory_dfs.get(snapshot.key)
    if value is None:
        return snapshot.preview_text[:max_chars]

    preview_text = _preview_text_from_value(value, max_rows=max_rows, max_chars=max_chars)
    if not preview_text:
        return snapshot.preview_text[:max_chars]
    return preview_text


def resolve_focus_result(
    *,
    current_focus_raw: Any,
    memory_dfs: Any,
    pending_artifacts: Any,
    execution_results: Any,
    user_query: str,
    round_index: int,
) -> Dict[str, Any]:
    """
    计算当前会话应保留的焦点结果。

    规则：
    1. 优先使用本轮新产出的结构化结果
    2. 无新结果时继续沿用现有焦点
    3. 现有焦点失效时，回退到会话中最新的可聚焦结果
    """
    current_focus = load_focus_result(current_focus_raw)
    combined_memory = {}
    if isinstance(memory_dfs, dict):
        combined_memory.update(memory_dfs)
    if isinstance(pending_artifacts, dict):
        combined_memory.update(pending_artifacts)

    latest_result = _latest_focus_execution_result(execution_results)
    latest_worker = str(latest_result.get("worker", "")) if latest_result else ""
    latest_summary = _build_result_summary(latest_result)
    latest_source_meta = _extract_source_metadata_from_result(latest_result)
    preferred_keys = _extract_focus_keys_from_result(latest_result)

    candidate = _select_focus_candidate(pending_artifacts, preferred_keys=preferred_keys)
    if candidate is None and preferred_keys:
        candidate = _select_focus_candidate(combined_memory, preferred_keys=preferred_keys)

    if candidate is not None:
        key, value = candidate
        snapshot = _build_snapshot_from_value(
            key=key,
            value=value,
            source_worker=latest_worker or (current_focus.source_worker if current_focus else ""),
            round_index=round_index or (current_focus.round_index if current_focus else 0),
            summary=latest_summary or (current_focus.summary if current_focus else ""),
            query=user_query or (current_focus.query if current_focus else ""),
            source_tables=latest_source_meta["source_tables"] or (current_focus.source_tables if current_focus else []),
            source_sql=latest_source_meta["source_sql"] or (current_focus.source_sql if current_focus else ""),
            source_rewrite=latest_source_meta["source_rewrite"] or (current_focus.source_rewrite if current_focus else ""),
        )
        return dump_focus_result(snapshot)

    if current_focus is not None:
        return dump_focus_result(current_focus)

    candidate = _select_focus_candidate(combined_memory)
    if candidate is not None:
        key, value = candidate
        snapshot = _build_snapshot_from_value(
            key=key,
            value=value,
            source_worker=latest_worker,
            round_index=round_index,
            summary=latest_summary,
            query=user_query,
            source_tables=latest_source_meta["source_tables"],
            source_sql=latest_source_meta["source_sql"],
            source_rewrite=latest_source_meta["source_rewrite"],
        )
        return dump_focus_result(snapshot)

    return {}


def focus_result_memory_keys(raw_value: Any) -> set[str]:
    snapshot = load_focus_result(raw_value)
    if snapshot is None or not snapshot.key:
        return set()
    return {snapshot.key}


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and "text" in item:
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                text_parts.append(item)
        return " ".join(part for part in text_parts if part).strip()
    return str(content or "").strip()


def _latest_success_execution_result(execution_results: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(execution_results, list):
        return None

    for item in reversed(execution_results):
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        return item
    return None


def _latest_focus_execution_result(execution_results: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(execution_results, list):
        return None

    for item in reversed(execution_results):
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        if _extract_focus_keys_from_result(item):
            return item

    return _latest_success_execution_result(execution_results)


def _build_result_summary(result: Optional[Dict[str, Any]], *, max_chars: int = 260) -> str:
    if not result:
        return ""
    text = str(result.get("result", "") or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _extract_source_metadata_from_result(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source_meta = {"source_tables": [], "source_sql": "", "source_rewrite": ""}
    if not isinstance(result, dict):
        return source_meta

    result_text = str(result.get("result", "") or "")
    if not result_text:
        return source_meta

    sql_match = re.search(r"SQL:\s*(.*?)(?:\n\n改写后的问题:|\Z)", result_text, re.DOTALL)
    rewrite_match = re.search(r"改写后的问题:\s*(.*?)(?:\n实际引用表:|\Z)", result_text, re.DOTALL)
    tables_match = re.search(r"实际引用表:\s*(.*?)(?:\n数据引用:|\Z)", result_text, re.DOTALL)

    if sql_match:
        source_meta["source_sql"] = re.sub(r"\s+", " ", sql_match.group(1).strip())[:2000]
    if rewrite_match:
        source_meta["source_rewrite"] = re.sub(r"\s+", " ", rewrite_match.group(1).strip())[:500]
    if tables_match:
        raw_tables = re.split(r"[,，、\s]+", tables_match.group(1).strip())
        source_meta["source_tables"] = [table for table in raw_tables if table and table != "未知"][:20]
    return source_meta


def _extract_focus_keys_from_result(result: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(result, dict):
        return []

    ordered_keys: List[str] = []
    seen_keys: set[str] = set()

    def _append_key(raw_key: Any) -> None:
        key = str(raw_key or "").strip()
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        ordered_keys.append(key)

    quality_signal = result.get("quality_signal")
    if isinstance(quality_signal, dict):
        _append_key(quality_signal.get("selected_data_source"))

    meta = result.get("meta")
    if isinstance(meta, dict):
        _append_key(meta.get("selected_data_source"))

    result_text = str(result.get("result", "") or "")
    for key in _FOCUS_RESULT_KEY_PATTERN.findall(result_text):
        _append_key(key)

    return ordered_keys


def _select_focus_candidate(
    memory_mapping: Any,
    *,
    preferred_keys: Optional[Iterable[str]] = None,
) -> Optional[tuple[str, Any]]:
    if not isinstance(memory_mapping, dict) or not memory_mapping:
        return None

    for key in preferred_keys or []:
        value = memory_mapping.get(key)
        if _is_focusable_candidate(key, value):
            return key, value

    for key, value in reversed(list(memory_mapping.items())):
        if _is_focusable_candidate(key, value):
            return key, value
    return None


def _is_focusable_candidate(key: str, value: Any) -> bool:
    if not key or key == "template_preview" or not str(key).startswith("df_"):
        return False

    kind = _detect_source_kind(value)
    if kind == "dataframe":
        return True
    if kind == "list" and value and isinstance(value[0], dict):
        return True
    return False


def _is_focus_snapshot_usable(snapshot: FocusResultSnapshot) -> bool:
    if not snapshot.key or not snapshot.key.startswith("df_"):
        return False
    if snapshot.source_kind == "dataframe":
        return True
    if snapshot.source_kind == "list":
        return bool(snapshot.columns and snapshot.preview_rows)
    return False


def _build_snapshot_from_value(
    *,
    key: str,
    value: Any,
    source_worker: str,
    round_index: int,
    summary: str,
    query: str,
    source_tables: Optional[List[str]] = None,
    source_sql: str = "",
    source_rewrite: str = "",
) -> FocusResultSnapshot:
    kind = _detect_source_kind(value)
    columns = _extract_columns(value)
    row_count = _extract_row_count(value)
    preview_rows = _extract_preview_rows(value)
    preview_text = _preview_text_from_value(value)
    column_count = len(columns)

    return FocusResultSnapshot(
        key=key,
        source_kind=kind,
        source_worker=source_worker,
        round_index=int(round_index or 0),
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        preview_rows=preview_rows,
        preview_text=preview_text,
        summary=summary,
        query=query,
        source_tables=source_tables or [],
        source_sql=source_sql,
        source_rewrite=source_rewrite,
    )


def _detect_source_kind(value: Any) -> Literal["dataframe", "list", "dict", "unknown"]:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    if pd is not None and isinstance(value, pd.DataFrame):
        return "dataframe"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "unknown"


def _extract_columns(value: Any, *, max_columns: int = 16) -> List[str]:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    if pd is not None and isinstance(value, pd.DataFrame):
        return [str(column) for column in list(value.columns)[:max_columns]]

    if isinstance(value, list) and value and isinstance(value[0], dict):
        first_row = value[0]
        return [str(column) for column in list(first_row.keys())[:max_columns]]

    if isinstance(value, dict):
        return [str(column) for column in list(value.keys())[:max_columns]]

    return []


def _extract_row_count(value: Any) -> int:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    if pd is not None and isinstance(value, pd.DataFrame):
        return int(value.shape[0])
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def _extract_preview_rows(value: Any, *, max_rows: int = 5) -> List[Dict[str, Any]]:
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None

    if pd is not None and isinstance(value, pd.DataFrame):
        rows = value.head(max_rows).to_dict(orient="records")
        return [_normalize_record(row) for row in rows]

    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return [_normalize_record(row) for row in value[:max_rows]]
        return [{"value": _normalize_scalar(item)} for item in value[:max_rows]]

    if isinstance(value, dict):
        items = list(value.items())[:max_rows]
        return [{"key": str(key), "value": _normalize_scalar(item_value)} for key, item_value in items]

    return []


def _preview_text_from_value(value: Any, *, max_rows: int = 5, max_chars: int = 1200) -> str:
    preview_rows = _extract_preview_rows(value, max_rows=max_rows)
    if preview_rows:
        preview_text = _json_dumps(preview_rows)
    else:
        preview_text = _json_dumps(_normalize_scalar(value))

    if len(preview_text) <= max_chars:
        return preview_text
    return preview_text[:max_chars].rstrip() + "..."


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key): _normalize_scalar(value) for key, value in record.items()}


def _normalize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_normalize_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_scalar(item) for key, item in value.items()}
    return str(value)


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)
