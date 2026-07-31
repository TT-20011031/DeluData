"""
Synthesizer 节点

汇总执行结果，生成最终答案
"""
import asyncio
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from langchain_core.messages import AIMessage

from app.supervisor.state import SupervisorState
from app.core.llm.async_llm import get_async_llm
from app.config import get_settings
from app.api.events import (
    emit_message_chunk,
    emit_message_end,
    emit_chart_status,
    emit_step_update,
    emit_reasoning_chunk,
    emit_reasoning_end,
)
from app.supervisor.focus_result import (
    build_direct_answer_payload,
    build_recent_dialogue,
    load_focus_result,
    resolve_focus_result,
)

logger = logging.getLogger(__name__)


def _resolve_normal_reply_model(state: SupervisorState, settings) -> str:
    configured = str(state.get("final_reply_model") or "").strip()
    if configured:
        return configured
    return str(
        getattr(
            settings.llm,
            "final_reply_model_flash",
            getattr(settings.llm, "final_reply_model", settings.llm.synthesizer_model),
        )
    ).strip()


def _safe_non_negative_int(raw_value: Any, default: int = 0) -> int:
    """
    安全解析非负整数，配置异常时回退到 default。
    """
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _looks_like_html_payload(value: Any, probe_chars: int = 256) -> bool:
    """
    轻量判断字符串是否为 HTML 文本，避免对超大字符串执行全量 lower()。
    """
    if not isinstance(value, str) or not value:
        return False
    header_snippet = value[:probe_chars].lower()
    return "<html" in header_snippet or "<!doctype" in header_snippet


def _sanitize_reasoning_text(text: str) -> str:
    """
    对外展示前清理 reasoning 内容，避免泄露内部规则和提示词工程细节。
    """
    if not isinstance(text, str) or not text:
        return ""

    sanitized = text
    # 清理代码块、XML/标签化片段，避免原样泄露内部模板内容
    sanitized = re.sub(r"```[\s\S]*?```", "", sanitized)
    sanitized = re.sub(r"<[^>]+>", "", sanitized)
    # 清理内部引用标记
    sanitized = re.sub(r"\[\[(?:CITATION|IMAGE):[^\]]+\]\]", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\[(?:REF|IMGREF|CITATION|IMAGE):[^\]]+\]", "", sanitized, flags=re.IGNORECASE)
    # 清理可能暴露提示词工程的片段
    sanitized = re.sub(
        r"(?i)(system\s*prompt|prompt\s*engineering|提示词工程|内部规则|根据规则|规则指出|tool\s*call|function\s*call)",
        "",
        sanitized,
    )
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    if not sanitized.strip():
        return ""
    return sanitized


_SENSITIVE_REASONING_PATTERNS = (
    r"(?i)(system\s*prompt|developer\s*prompt|内部规则|提示词|策略|参数|执行结果|引用检查|修正|开始撰写)",
    r"(?i)(workspace_id|user_id|api[_\s-]*key|token|secret|function\s*call|tool\s*call)",
    r"(?i)(\[ref:|\[imgref:|\[citation:|\[\[citation:|\[\[img:)",
    r"(?i)(ref.*编号|imgref.*末尾|引用规|每个.*ref.*次|只搬运|不创造.*ref|引用.*白名单)",
    r"(?i)(确保.*ref|验证.*引用|引用.*规则|按.*规则.*引用|我.*先.*引用|搬运.*标记)",
    r"(?i)(只出现.*次|放在.*末尾|最相关.*句子|不编造|禁止编造.*ref)",
)


def _contains_sensitive_reasoning(text: str) -> bool:
    if not text:
        return False
    for pattern in _SENSITIVE_REASONING_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def _redact_sensitive_reasoning(text: str) -> str:
    """
    尽量保留可读思考过程，同时移除/脱敏敏感行。
    """
    if not text:
        return ""

    redacted_lines: list[str] = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if _contains_sensitive_reasoning(candidate):
            continue
        # 屏蔽可能含内部参数的代码片段和引号内容（保留语义）
        candidate = re.sub(r"`[^`]{1,200}`", "[已隐藏参数]", candidate)
        candidate = re.sub(r"'[^']{1,200}'", "'[已隐藏]'", candidate)
        candidate = re.sub(r"\"[^\"]{1,200}\"", "\"[已隐藏]\"", candidate)
        redacted_lines.append(candidate)

    if not redacted_lines:
        return ""
    return "\n".join(redacted_lines)


def _append_reasoning_content(current: str, incoming: str, max_chars: int = 12000) -> str:
    """
    滚动保留最新思考文本，避免 checkpoint 过大。
    """
    if not incoming:
        return current
    merged = (current or "") + incoming
    if len(merged) <= max_chars:
        return merged
    return merged[-max_chars:]


_VISIBLE_REASONING_STEPS = (
    "正在理解问题，并拆解需要核对的信息。\n",
    "正在核对知识库证据，优先依据已检索到的材料组织回答。\n",
    "正在检查回答是否与可引用依据一致。\n",
    "正在整理中文答复。\n",
)


def _next_visible_reasoning_step(emitted_count: int) -> str:
    if emitted_count < len(_VISIBLE_REASONING_STEPS):
        return _VISIBLE_REASONING_STEPS[emitted_count]
    return ""


def _public_trace_preview(value: Any, max_chars: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _public_trace_list(values: list[str], max_items: int = 5) -> str:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return "、".join(items)


def _build_public_reasoning_trace(state: SupervisorState, results: list[dict]) -> list[str]:
    """Build user-visible reasoning summaries without exposing raw hidden chain-of-thought."""
    chunks: list[str] = []
    user_query = _public_trace_preview(state.get("user_query", ""), 160)
    intent_type = str(state.get("intent_type") or "tool_use")
    execution_mode = str(state.get("execution_mode") or "auto")
    reply_model_key = str(state.get("reply_model_key") or state.get("final_reply_model") or "")

    chunks.append(
        "### 问题理解\n"
        f"- 用户问题：{user_query or '未提供明确问题'}\n"
        f"- 识别意图：{intent_type}\n"
        f"- 执行模式：{execution_mode}\n"
    )

    task_plan = state.get("task_plan") or []
    if isinstance(task_plan, list) and task_plan:
        lines = ["### 任务拆解"]
        for idx, step in enumerate(task_plan[:6], 1):
            if not isinstance(step, dict):
                continue
            worker = str(step.get("worker") or "unknown")
            status = str(step.get("status") or "pending")
            desc = _public_trace_preview(step.get("description") or step.get("task") or "", 110)
            lines.append(f"{idx}. {worker} · {status}：{desc or '执行子任务'}")
        chunks.append("\n".join(lines) + "\n")

    worker_names: list[str] = []
    result_summaries: list[str] = []
    doc_final_context: list[dict] = []
    doc_unused_context: list[dict] = []
    doc_issues: list[str] = []
    knowledge_paths: list[str] = []
    source_titles: list[str] = []

    for item in results or []:
        if not isinstance(item, dict):
            continue
        worker = str(item.get("worker") or "").strip()
        if worker:
            worker_names.append(worker)
        preview = _public_trace_preview(item.get("result"), 130)
        if preview:
            result_summaries.append(f"{worker or 'worker'}：{preview}")

        meta = item.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        path = str(meta.get("knowledge_path") or "").strip()
        if path:
            knowledge_paths.append(path)
        for issue in meta.get("issues") or []:
            if isinstance(issue, str) and issue:
                doc_issues.append(issue)
        for ctx in meta.get("final_context") or []:
            if isinstance(ctx, dict):
                doc_final_context.append(ctx)
                title = str(ctx.get("title") or ctx.get("file_name") or ctx.get("slug") or "").strip()
                if title:
                    source_titles.append(title)
        for ctx in meta.get("unused_context") or []:
            if isinstance(ctx, dict):
                doc_unused_context.append(ctx)

    worker_summary = _public_trace_list(worker_names, 8)
    if worker_summary or result_summaries:
        lines = ["### 执行观察"]
        if worker_summary:
            lines.append(f"- 已调用能力：{worker_summary}")
        for idx, summary in enumerate(result_summaries[:5], 1):
            lines.append(f"- 结果摘要 {idx}：{summary}")
        chunks.append("\n".join(lines) + "\n")

    if knowledge_paths or doc_final_context or doc_issues:
        wiki_count = sum(1 for ctx in doc_final_context if str(ctx.get("source") or "") == "wiki")
        rag_count = sum(1 for ctx in doc_final_context if str(ctx.get("source") or "") == "rag")
        lines = ["### 知识检索与证据"]
        if knowledge_paths:
            lines.append(f"- 路由路径：{_public_trace_list(knowledge_paths, 4)}")
        lines.append(f"- 采纳证据：Wiki {wiki_count} 条，RAG {rag_count} 条")
        if source_titles:
            lines.append(f"- 主要来源：{_public_trace_list(source_titles, 6)}")
        if doc_issues:
            issue_text = _public_trace_list(doc_issues, 8)
            lines.append(f"- 检索提示：{issue_text}")
            if "wiki_missing" in doc_issues and rag_count > 0:
                lines.append("- Wiki 未命中时，已回退使用 RAG 原文证据，避免无依据拒答。")
        chunks.append("\n".join(lines) + "\n")

    if doc_final_context:
        lines = ["### 证据片段核对"]
        for idx, ctx in enumerate(doc_final_context[:6], 1):
            source = str(ctx.get("source") or "unknown").upper()
            title = _public_trace_preview(ctx.get("title") or ctx.get("file_name") or ctx.get("slug") or "未命名资料", 80)
            page = ctx.get("page_number")
            preview = _public_trace_preview(ctx.get("preview"), 120)
            page_part = f" P{page}" if page else ""
            lines.append(f"{idx}. {source} {title}{page_part}：{preview or '已纳入上下文'}")
        chunks.append("\n".join(lines) + "\n")

    constraint_lines = [
        "### 回答生成约束",
        "- 使用中文回答。",
        "- 优先基于已检索到的证据组织答案。",
        "- 有来源时给出引用；没有可靠依据时明确说明不确定性。",
    ]
    if reply_model_key:
        constraint_lines.append(f"- 回复模型/档位：{reply_model_key}")
    if doc_unused_context:
        constraint_lines.append(f"- 有 {len(doc_unused_context)} 条候选证据未被采纳，主要答案不会依赖这些内容。")
    chunks.append("\n".join(constraint_lines) + "\n")
    return chunks


def _collect_mm_image_urls(results: list, max_images: int) -> list[str]:
    """
    从 execution_results[*].meta.mm_evidence 聚合可用图片 URL。
    去重优先级：(file_id,page_number)；缺失时退化到 URL 去重。
    """
    urls: list[str] = []
    seen_keys: set[tuple[str, int]] = set()
    seen_urls: set[str] = set()

    for result in results:
        meta = result.get("meta") or {}
        mm_evidence = meta.get("mm_evidence") or {}
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


def _parse_citation_slot(raw_slot: str, page_param_key: str = "page") -> tuple[str, int | None]:
    """
    解析 citation 的 file_id 槽位，兼容：
    - file_id
    - file_id?page=12
    """
    slot = str(raw_slot or "").strip()
    if not slot:
        return "", None

    if "?" not in slot:
        return slot, None

    clean_file_id, query = slot.split("?", 1)
    clean_file_id = clean_file_id.strip()
    if not clean_file_id:
        return "", None

    params = parse_qs(query, keep_blank_values=False)
    page_values = params.get(page_param_key) or params.get("page") or []
    page_number: int | None = None
    if page_values:
        try:
            parsed = int(str(page_values[0]).strip())
            if parsed > 0:
                page_number = parsed
        except (TypeError, ValueError):
            page_number = None
    return clean_file_id, page_number


def _collect_citations(results: list, page_param_key: str = "page") -> list[dict]:
    """
    聚合并去重引用。
    去重维度：(clean_file_id, page_number, file_name)。
    """
    citations: list[dict] = []
    seen_keys: set[tuple[str, int | None, str]] = set()
    citation_pattern = r"\[\[CITATION:([^:\]]+):([^\]]+)\]\]"

    for result in results:
        result_text = str(result.get("result") or "")
        if not result_text:
            continue
        found_citations = re.findall(citation_pattern, result_text)
        for raw_slot, raw_name in found_citations:
            file_slot = str(raw_slot or "").strip()
            file_name = str(raw_name or "").strip()
            if not file_slot or not file_name:
                continue

            clean_file_id, page_number = _parse_citation_slot(file_slot, page_param_key=page_param_key)
            dedupe_key = (clean_file_id, page_number, file_name)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            citations.append(
                {
                    "file_slot": file_slot,
                    "file_name": file_name,
                    "clean_file_id": clean_file_id,
                    "page_number": page_number,
                }
            )
    return citations


def _build_wiki_entities_block(results: list[dict]) -> str:
    """[M3.4] 从 doc_worker meta 中提取 wiki_entities，构造给 Synthesizer 的引用提示块。

    返回值仅在存在 wiki 实体时为非空，避免开关关闭/纯 RAG 路径下污染 prompt。
    """
    seen: set[str] = set()
    entries: list[dict] = []
    for r in results or []:
        meta = r.get("meta") or {}
        for ent in (meta.get("wiki_entities") or []):
            if not isinstance(ent, dict):
                continue
            slug = str(ent.get("slug") or "").strip()
            title = str(ent.get("title") or "").strip()
            if not slug or not title or slug in seen:
                continue
            seen.add(slug)
            entries.append({
                "slug": slug,
                "title": title,
                "summary": str(ent.get("summary") or "").strip(),
            })
    if not entries:
        return ""

    lines = ["", "## Wiki 实体页索引（已装载）"]
    for ent in entries:
        if ent["summary"]:
            lines.append(f"- [[{ent['slug']}|{ent['title']}]] — {ent['summary']}")
        else:
            lines.append(f"- [[{ent['slug']}|{ent['title']}]]")
    lines.extend([
        "",
        "## Wiki 引用规范",
        "- 在最终答案中如需引用以上实体，请使用 `[[slug|title]]` 格式（保留双方括号）。",
        "- 仅可引用「Wiki 实体页索引」列出的 slug，禁止编造或修改 slug。",
        "- title 字段保留原文显示名（不要翻译/缩写）。",
        "- `[[slug|title]]` 与 `[REF:N]` / `[IMGREF:N]` 可并存于同一答案中。",
    ])
    return "\n".join(lines) + "\n"


def _build_structured_evidence_block(results: list[dict]) -> str:
    """Build a compact evidence map from doc_worker structured meta."""
    final_items: list[dict] = []
    unused_items: list[dict] = []
    issues: list[str] = []
    paths: list[str] = []
    source_counts = {"wiki": 0, "rag": 0}

    for r in results or []:
        if r.get("worker") != "doc_worker":
            continue
        meta = r.get("meta") or {}
        path = str(meta.get("knowledge_path") or "").strip()
        if path:
            paths.append(path)
        for issue in meta.get("issues") or []:
            if isinstance(issue, str) and issue:
                issues.append(issue)
        for item in meta.get("final_context") or []:
            if isinstance(item, dict):
                final_items.append(item)
                source = str(item.get("source") or "")
                if source in source_counts:
                    source_counts[source] += 1
        for item in meta.get("unused_context") or []:
            if isinstance(item, dict):
                unused_items.append(item)

    if not final_items and not issues and not paths:
        return ""

    def _line(item: dict, idx: int) -> str:
        source = str(item.get("source") or "unknown")
        title = str(item.get("title") or item.get("file_name") or item.get("slug") or "未命名证据")
        slug = str(item.get("slug") or "")
        page = item.get("page_number")
        preview = str(item.get("preview") or "")
        ref = f"[[{slug}|{title}]]" if source == "wiki" and slug else title
        page_part = f" P{page}" if page else ""
        risk = " 缺少来源文件" if source == "wiki" and item.get("has_source_files") is False else ""
        return f"{idx}. {source.upper()} {ref}{page_part}{risk}: {preview}"

    lines = [
        "",
        "## 结构化证据地图",
        f"- 路由路径: {', '.join(dict.fromkeys(paths)) or 'unknown'}",
        f"- 最终使用证据: Wiki {source_counts['wiki']} 条 / RAG {source_counts['rag']} 条",
    ]
    if issues:
        lines.append(f"- 问题码: {', '.join(dict.fromkeys(issues))}")
    if "wiki_missing" in issues and source_counts["rag"] > 0:
        lines.append("- Wiki 未命中但 RAG 原文证据可用：必须基于 RAG 证据回答，不要输出“知识库暂无可靠依据”。")
    if not final_items:
        lines.extend([
            "",
            "### 最终使用证据",
            "- 无可用证据。请明确告知用户当前知识库没有可用于回答的问题依据，不要编造。",
        ])
    else:
        lines.append("")
        lines.append("### 最终使用证据")
        for idx, item in enumerate(final_items[:10], 1):
            lines.append(_line(item, idx))
    if unused_items:
        lines.append("")
        lines.append("### 未使用证据（不要作为主要依据）")
        for idx, item in enumerate(unused_items[:5], 1):
            lines.append(_line(item, idx))
    lines.extend([
        "",
        "### 回答约束",
        "- 概念解释优先使用 Wiki 证据；原文、页码和事实落点优先使用 RAG 原文证据。",
        "- Wiki 引用只能使用上方证据或 Wiki 实体页索引中出现的 `[[slug|title]]`。",
        "- 文件引用只能使用执行结果中已有的 REF/CITATION，不要编造文件引用。",
    ])
    return "\n".join(lines) + "\n"


def _format_ref_tag(ref_num: int, file_name: str, page_number: int | None) -> str:
    """
    生成传递给 LLM 的 REF 标签。
    为同名文件的多页引用补充页码提示，降低 REF 误绑概率。
    """
    safe_name = str(file_name or "").strip()
    if isinstance(page_number, int) and page_number > 0:
        return f"[REF:{ref_num}:P{page_number}:{safe_name}]"
    return f"[REF:{ref_num}:{safe_name}]"


async def synthesizer_node(state: SupervisorState) -> SupervisorState:
    """
    Synthesizer 节点：汇总执行结果，生成最终答案
    
    [异步流式响应] 同时启动后台终结类任务，实现"一边输出一边生图"
    [Fast Path] 对于 chitchat/direct_answer 直接回复，不需要 execution_results
    """
    intent_type = state.get("intent_type", "tool_use")
    plan_id = state.get("plan_id")
    
    # [Fast Path] 闲聊/直接回答：不需要 execution_results
    if intent_type in ["chitchat", "direct_answer"]:
        settings = get_settings()
        logger.info(
            "[FinalReply] fast_path session_id=%s plan_id=%s intent=%s model=%s reply_model_key=%s requested_final_model=%s",
            state.get("session_id"),
            plan_id,
            intent_type,
            str(settings.llm.fast_model),
            state.get("reply_model_key"),
            state.get("final_reply_model"),
        )
        return await _handle_direct_response(state)
    
    
    settings = get_settings()
    llm = get_async_llm()
    results = state.get("execution_results", [])
    session_id = state.get("session_id")
    # [Session Round] 获取当前轮次供文字流关联
    round_index = state.get("round_index", 0)

    if state.get("is_direct_execution") and results:
        latest = results[-1] if isinstance(results[-1], dict) else {}
        meta = latest.get("meta") or {}
        if latest.get("worker") == "sql_worker" and meta.get("semantic_fallback_blocked"):
            final_answer = meta.get("error") or latest.get("result") or "当前用户无权访问相关数据表或字段。"
            if meta.get("error_type") == "permission_rewrite_required":
                final_answer = f"{final_answer}\n\n请改问你有权限访问的数据范围，或联系管理员调整语义模型可见范围。"
            elif meta.get("error_type") == "semantic_unavailable":
                final_answer = f"{final_answer}\n\n请联系管理员启用语义模型后再使用 SQL 查询。"
            else:
                final_answer = f"{final_answer}\n\n本次请求已按权限策略终止，没有生成或执行自由 SQL。"

            assistant_message_id = str(plan_id or uuid.uuid4())
            if session_id and plan_id:
                await emit_message_chunk(session_id, plan_id, final_answer, round_index=round_index)
                await emit_message_end(session_id, plan_id, final_answer, round_index=round_index)
            return {
                "final_answer": final_answer,
                "messages": [AIMessage(content=final_answer, id=assistant_message_id)],
                "task_plan": state.get("task_plan", []),
                "execution_results": results,
                "memory_dfs": state.get("memory_dfs", {}),
                "pending_artifacts": {},
                "plan_status": "error",
            }
    
    # ========== [异步流式响应] 后台启动终结类任务 ==========
    background_tasks = []
    bg_task_outputs = []
    task_plan = state.get("task_plan", [])
    
    # 查找 waiting/pending 状态的终结类任务
    from app.worker_categories import is_terminal_worker
    from app.supervisor.nodes.executor import execute_worker_task, _sanitize_office_step_params
    
    pending_terminal_steps = [
        s for s in task_plan 
        if s.get("status") in ("waiting", "pending") and is_terminal_worker(s.get("worker", ""))
    ]
    
    if pending_terminal_steps:
        
        # [修复] 合并 memory_dfs 和 pending_artifacts，确保后台任务能获取前序数据
        combined_memory = {}
        combined_memory.update(state.get("memory_dfs", {}))
        combined_memory.update(state.get("pending_artifacts", {}))
        template_cache: dict = {}
        
        # [Session Round] 获取当前轮次
        round_index = state.get("round_index", 0)
        
        async def bg_execute_terminal(step):
            """后台执行终结类任务"""
            from app.api.events import emit_artifact
            
            step_id = step.get("step_id")
            worker = step.get("worker", "")
            description = step.get("description", "")
            
            step["status"] = "running"
            if session_id:
                await emit_step_update(session_id, step_id, "running", f"正在执行: {description[:30]}...", round_index=round_index)
                if worker == "chart_worker":
                    await emit_chart_status(session_id, step_id, "generating", description[:50], round_index=round_index)
            
            try:
                user_context = state.get("user_context", {})
                messages = state.get("messages", [])
                
                step_params = step.get("params", {}) or {}
                if worker == "office_worker":
                    step_params = await _sanitize_office_step_params(step, state, user_context, template_cache)

                exec_output = await execute_worker_task(
                    worker=worker,
                    description=description,
                    user_context=user_context,
                    session_id=session_id,
                    parent_step_id=step_id,
                    user_query=state.get("user_query", ""),
                    messages=messages,
                    memory_dfs=combined_memory,  # [修复] 使用合并后的内存
                    round_index=round_index,  # [Session Round] 透传轮次
                    execution_results=state.get("execution_results", []),
                    summary=state.get("summary", ""),
                    current_focus_result=state.get("current_focus_result", {}),
                    step_params=step_params,  # [Fix] 透传模板参数
                )
                
                # [修复] 从 worker meta 中解析真实状态，不再硬编码 completed
                from app.supervisor.nodes.executor import _resolve_step_status, _build_step_update_label
                meta = exec_output.get("meta") or {}
                step_status = _resolve_step_status(meta)
                step["status"] = step_status
                step["result"] = exec_output["result"]
                
                if session_id:
                    await emit_step_update(session_id, step_id, step_status, _build_step_update_label(step_status, description), result=exec_output["result"], round_index=round_index)
                    
                    # [修复] 仅在真正 completed 时发送 ARTIFACT 事件
                    if worker == "chart_worker" and step_status == "completed":
                        await emit_chart_status(session_id, step_id, "completed", description[:50], round_index=round_index)
                        artifacts = exec_output.get("memory_update", {})
                        for _, value in artifacts.items():
                            if _looks_like_html_payload(value):
                                await emit_artifact(session_id, "html_report", {
                                    "report_id": f"{step_id}_chart",
                                    "step_id": step_id,
                                    "title": description[:30],
                                    "html_content": value
                                }, round_index=round_index)
                                break
                    elif worker == "chart_worker" and step_status == "invalid_data":
                        await emit_chart_status(session_id, step_id, "invalid_data", "数据无效，已跳过", round_index=round_index)
                
                return {
                    "step_id": step_id,
                    "worker": worker,
                    "result": exec_output.get("result", ""),
                    "memory_update": exec_output.get("memory_update", {}) or {}
                }
                
            except Exception as e:
                logger.error(f"[异步流式] 后台任务 {step_id} 失败: {e}")
                step["status"] = "error"
                if session_id:
                    await emit_step_update(session_id, step_id, "error", str(e)[:50], round_index=round_index)
                    if worker == "chart_worker":
                        await emit_chart_status(session_id, step_id, "error", str(e)[:50], round_index=round_index)
                return {"step_id": step_id, "worker": worker, "error": str(e)}
        
        # 创建后台任务（不阻塞主流程）
        for step in pending_terminal_steps:
            task = asyncio.create_task(bg_execute_terminal(step))
            background_tasks.append(task)
    
    # ========== 主流程：流式生成答案 ==========
    citation_page_param_key = str(getattr(settings.rag, "citation_page_param_key", "page") or "page")
    citations = _collect_citations(results, page_param_key=citation_page_param_key)
    
    # ========== [REF 编号方案] 隐藏 UUID，降低 LLM 幻觉 ==========
    # 构建 ref_map: {编号: (file_slot, file_name, page_number)}，LLM 只看到 REF 编号+页码提示
    ref_map: dict[int, tuple[str, str, int | None]] = {}
    for idx, citation in enumerate(citations, 1):
        ref_map[idx] = (
            str(citation.get("file_slot") or ""),
            str(citation.get("file_name") or ""),
            citation.get("page_number"),
        )
    
    # 先构建原始 results_text
    results_text = "\n".join([
        f"步骤 {r['step_id']} ({r['worker']}): {r.get('result', r.get('error', 'N/A'))}"
        for r in results
    ])
    
    # 将 [[CITATION:file_slot:filename]] 替换为 [REF:N:Pm:filename]（LLM 不再接触 UUID）
    for ref_num, (file_slot, file_name, page_number) in ref_map.items():
        results_text = results_text.replace(
            f"[[CITATION:{file_slot}:{file_name}]]",
            _format_ref_tag(ref_num, file_name, page_number)
        )

    # ========== [IMGREF 编号方案] 隐藏图片真实 ID，降低幻觉风险 ==========
    img_ref_guard_enabled = bool(getattr(settings.supervisor, "image_ref_guard_enabled", True))
    img_ref_map: dict[int, tuple[str, str]] = {}
    img_pair_to_ref: dict[tuple[str, str], int] = {}

    # [DEBUG] 检查原始 results 中的图片标签
    raw_image_tags = re.findall(r'\[\[(?:IMG|IMAGE):([^:\]]+):([^:\]]+)(?::[^\]]*)?\]\]', results_text)
    if raw_image_tags:
        logger.info("[Synthesizer] 原始结果中发现 %s 个图片标签: %s", len(raw_image_tags), raw_image_tags[:5])
    else:
        logger.info("[Synthesizer] 原始结果中未发现图片标签")

    if img_ref_guard_enabled:
        image_tag_pattern = r'\[\[(?:IMG|IMAGE):([^:\]]+):([^:\]]+)(?::[^\]]*)?\]\]'

        def _replace_image_tag_for_llm(match: re.Match) -> str:
            file_id = str(match.group(1) or "").strip()
            image_id = str(match.group(2) or "").strip()
            if not file_id or not image_id:
                return ""

            pair = (file_id, image_id)
            ref_num = img_pair_to_ref.get(pair)
            if ref_num is None:
                ref_num = len(img_ref_map) + 1
                img_ref_map[ref_num] = pair
                img_pair_to_ref[pair] = ref_num
            return f"[IMGREF:{ref_num}]"

        results_text = re.sub(image_tag_pattern, _replace_image_tag_for_llm, results_text)
    
    plan_status = state.get("plan_status", "completed")
    error_note = "注意：任务在执行过程中发生了错误，部分步骤未完成。" if plan_status == "error" else ""
    
    # [NEW] 使用三明治架构构建 Prompt（从 State 透传，无需查库）
    from app.templates_config.synthesizer_templates import build_synthesizer_prompt, SYSTEM_TEMPLATES
    
    active_template_prompt = state.get("active_template_prompt") or SYSTEM_TEMPLATES["default"]["prompt"]
    custom_prompt = state.get("synthesizer_custom_prompt") or ""
    
    system_prompt = build_synthesizer_prompt(
        template_prompt=active_template_prompt,
        custom_prompt=custom_prompt,
        error_note=error_note
    )
    system_prompt += (
        "\n\n【输出约束】最终回答必须使用中文。"
        "如果 Wiki 未命中但 RAG 原文证据可用，请基于 RAG 证据回答，"
        "不要误判为“知识库暂无可靠依据”。"
    )
    
    # ========== 双层上下文构建 ==========
    # 1. 长期记忆（压缩摘要）
    summary = state.get("summary", "")
    
    # 2. 短期记忆（最近 2 轮对话，4 条消息）
    from langchain_core.messages import HumanMessage as HM, AIMessage as AM
    history_messages = state.get("messages", [])
    history_text = ""
    if history_messages:
        for msg in history_messages[-4:]:  # 最近 2 轮
            role = "用户" if isinstance(msg, HM) else "助手"
            content = msg.content[:300] if len(msg.content) > 300 else msg.content
            history_text += f"[{role}]: {content}\n"
    
    # 构建 user content（包含上下文）
    context_block = ""
    if summary:
        context_block += f"## 对话背景摘要\n{summary}\n\n"
    if history_text:
        context_block += f"## 最近对话\n{history_text}\n"

    # [M3.4] Wiki 实体清单 + 引用规范：仅当本次执行装载到 wiki 实体页时注入
    wiki_entities_block = _build_wiki_entities_block(results)
    structured_evidence_block = _build_structured_evidence_block(results)

    user_content = f"""{context_block}## 用户当前问题
{state['user_query']}

## 执行结果
{results_text}
{structured_evidence_block}
{wiki_entities_block}
请结合上述背景回答用户问题。"""

    text_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    mm_max_images = _safe_non_negative_int(
        getattr(settings.rag, "mm_max_images", 9),
        default=9,
    )
    mm_image_urls = _collect_mm_image_urls(
        results=results,
        max_images=mm_max_images,
    )
    use_multimodal = len(mm_image_urls) > 0
    if use_multimodal:
        logger.info("[Synthesizer] 启用多模态输入，图片数量=%s", len(mm_image_urls))
        for idx, url in enumerate(mm_image_urls):
            logger.info("[Synthesizer] 图片[%s] URL=%s", idx, url[:200])
        user_mm_content = [{"type": "text", "text": user_content}]
        user_mm_content.extend(
            {"type": "image_url", "image_url": {"url": url}}
            for url in mm_image_urls
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_mm_content},
        ]
    else:
        messages = text_messages
    
    plan_id = state.get("plan_id")
    final_answer = ""
    emitted_any_chunk = False
    enable_thinking = bool(getattr(settings.llm, "synthesizer_enable_thinking", True))
    reply_model = _resolve_normal_reply_model(state, settings)
    persisted_reasoning_text = ""
    persisted_reasoning_duration_ms = 0
    visible_reasoning_count = 0
    public_reasoning_chunks = _build_public_reasoning_trace(state, results)
    public_reasoning_emitted = False

    logger.info(
        "[FinalReply] normal_path session_id=%s plan_id=%s intent=%s model=%s reply_model_key=%s thinking=%s direct=%s",
        session_id,
        plan_id,
        intent_type,
        reply_model,
        state.get("reply_model_key"),
        enable_thinking,
        bool(state.get("is_direct_execution", False)),
    )

    async def _emit_public_reasoning_trace():
        """Emit detailed public reasoning summaries once per assistant response."""
        nonlocal persisted_reasoning_text, visible_reasoning_count, public_reasoning_emitted
        if public_reasoning_emitted:
            return
        public_reasoning_emitted = True
        for visible_text in public_reasoning_chunks:
            if not visible_text:
                continue
            if not visible_text.endswith("\n"):
                visible_text += "\n"
            visible_reasoning_count += 1
            persisted_reasoning_text = _append_reasoning_content(
                persisted_reasoning_text,
                visible_text + "\n",
            )
            if session_id and plan_id:
                await emit_reasoning_chunk(
                    session_id,
                    plan_id,
                    visible_text,
                    round_index=round_index,
                )

    async def _stream_and_emit(stream_messages):
        """流式生成并推送，支持 Qwen3 推理令牌双通道。"""
        nonlocal final_answer, emitted_any_chunk, persisted_reasoning_text, persisted_reasoning_duration_ms, visible_reasoning_count
        reasoning_start_ts = time.monotonic()
        reasoning_emitted_end = False

        await _emit_public_reasoning_trace()

        async for chunk_type, text in llm.chat_stream_with_thinking(
            stream_messages, model=reply_model
        ):
            if not text:
                continue
            if chunk_type == "reasoning":
                safe_text = _sanitize_reasoning_text(text)
                if not safe_text:
                    continue
                redacted_text = _redact_sensitive_reasoning(safe_text)
                if not redacted_text:
                    continue
                visible_text = _next_visible_reasoning_step(visible_reasoning_count)
                if not visible_text:
                    continue
                visible_reasoning_count += 1
                persisted_reasoning_text = _append_reasoning_content(persisted_reasoning_text, visible_text)
                if session_id and plan_id:
                    await emit_reasoning_chunk(
                        session_id,
                        plan_id,
                        visible_text,
                        round_index=round_index,
                    )
            else:
                # chunk_type == 'content'
                if not reasoning_emitted_end:
                    duration_ms = int((time.monotonic() - reasoning_start_ts) * 1000)
                    persisted_reasoning_duration_ms = duration_ms
                    if session_id and plan_id:
                        await emit_reasoning_end(
                            session_id, plan_id,
                            duration_ms=duration_ms,
                            round_index=round_index,
                        )
                    reasoning_emitted_end = True
                final_answer += text
                emitted_any_chunk = True
                if session_id and plan_id:
                    await emit_message_chunk(session_id, plan_id, text, round_index=round_index)

        # 边界：只有 reasoning 无 content（异常情况）也需发 end
        if not reasoning_emitted_end and session_id and plan_id:
            if visible_reasoning_count == 0:
                visible_text = _next_visible_reasoning_step(visible_reasoning_count)
                if visible_text:
                    visible_reasoning_count += 1
                    persisted_reasoning_text = _append_reasoning_content(persisted_reasoning_text, visible_text)
                    await emit_reasoning_chunk(
                        session_id,
                        plan_id,
                        visible_text,
                        round_index=round_index,
                    )
            duration_ms = int((time.monotonic() - reasoning_start_ts) * 1000)
            persisted_reasoning_duration_ms = duration_ms
            await emit_reasoning_end(
                session_id, plan_id,
                duration_ms=duration_ms,
                round_index=round_index,
            )

    async def _stream_and_emit_plain(stream_messages):
        """不带推理令牌的降级流式路径（多模态失败时使用）。"""
        nonlocal final_answer, emitted_any_chunk, persisted_reasoning_duration_ms
        reasoning_start_ts = time.monotonic()
        await _emit_public_reasoning_trace()
        if session_id and plan_id:
            persisted_reasoning_duration_ms = int((time.monotonic() - reasoning_start_ts) * 1000)
            await emit_reasoning_end(
                session_id,
                plan_id,
                duration_ms=persisted_reasoning_duration_ms,
                round_index=round_index,
            )
        async for chunk in llm.chat_stream(stream_messages, model=reply_model):
            if not chunk:
                continue
            final_answer += chunk
            emitted_any_chunk = True
            if session_id and plan_id:
                await emit_message_chunk(session_id, plan_id, chunk, round_index=round_index)

    try:
        try:
            if enable_thinking:
                await _stream_and_emit(messages)
            else:
                await _stream_and_emit_plain(messages)
        except Exception as mm_error:
            # URL 拉取失败等场景：若尚未产生可见输出，自动降级文本-only
            if use_multimodal and not emitted_any_chunk:
                logger.warning(
                    "[Synthesizer] 多模态流式失败，降级文本-only: %s",
                    mm_error,
                )
                final_answer = ""
                await _stream_and_emit_plain(text_messages)
            else:
                raise
        
        if not final_answer:
            final_answer = "（系统提示：生成的回答为空）"
            if session_id and plan_id:
                await emit_message_chunk(session_id, plan_id, final_answer, round_index=round_index)

        logger.info(
            "[Synthesizer] LLM 原始输出 (len=%s): %s",
            len(final_answer),
            final_answer[:500],
        )
        # ========== [REF→CITATION 还原] 将 LLM 输出的 REF 编号还原为完整 CITATION ==========
        for ref_num, (file_slot, file_name, _page_number) in ref_map.items():
            # 兼容任意扩展标签格式：[REF:N] / [REF:N:filename] / [REF:N:Pm:filename]
            final_answer = re.sub(
                rf"\[REF:{ref_num}(?::[^\]]*)?\]",
                f"[[CITATION:{file_slot}:{file_name}]]",
                final_answer,
            )
        # 清理 LLM 可能残留的未匹配 REF 标记（防止泄漏到前端）
        final_answer = re.sub(r'\[REF:\d+(?::[^\]]*)?\]', '', final_answer)

        # ========== [IMGREF→IMG 还原 + 白名单过滤] ==========
        if img_ref_guard_enabled:
            imgref_pattern = r'\[IMGREF:(\d+)(?::[^\]]*)?\]'

            def _restore_imgref(match: re.Match) -> str:
                ref_raw = match.group(1)
                try:
                    ref_num = int(ref_raw)
                except (TypeError, ValueError):
                    return ""
                pair = img_ref_map.get(ref_num)
                if not pair:
                    return ""
                file_id, image_id = pair
                return f"[[IMG:{file_id}:{image_id}:]]"

            final_answer = re.sub(imgref_pattern, _restore_imgref, final_answer)

            allowed_pairs = set(img_pair_to_ref.keys())
            invalid_image_tag_count = 0
            image_tag_pattern = r'\[\[(?:IMG|IMAGE):([^:\]]+):([^:\]]+)(?::[^\]]*)?\]\]'

            def _filter_invalid_image_tag(match: re.Match) -> str:
                nonlocal invalid_image_tag_count
                file_id = str(match.group(1) or "").strip()
                image_id = str(match.group(2) or "").strip()
                if (file_id, image_id) in allowed_pairs:
                    return f"[[IMG:{file_id}:{image_id}:]]"
                invalid_image_tag_count += 1
                return ""

            final_answer = re.sub(image_tag_pattern, _filter_invalid_image_tag, final_answer)
            if invalid_image_tag_count > 0:
                logger.warning(
                    "[Synthesizer] 过滤了 %s 个非白名单图片标签（疑似幻觉）",
                    invalid_image_tag_count,
                )
            
            # [DEBUG] 检查最终答案中的图片标签
            final_image_tags = re.findall(r'\[\[(?:IMG|IMAGE):([^:\]]+):([^:\]]+)(?::[^\]]*)?\]\]', final_answer)
            if final_image_tags:
                logger.info("[Synthesizer] 最终答案包含 %s 个图片标签: %s", len(final_image_tags), final_image_tags[:5])
            else:
                logger.info("[Synthesizer] 最终答案不包含图片标签")
        else:
            final_answer = re.sub(r'\[IMGREF:\d+(?::[^\]]*)?\]', '', final_answer)
        
        # [NEW] 发送结束信号，通知前端停止 Loading（内容已还原为 CITATION 格式）
        if session_id and plan_id:
             await emit_message_end(session_id, plan_id, final_answer, round_index=round_index)
        
        # ========== [关键修复] 等待后台终结类任务完成 (带超时保护) ==========
        # 确保 chart_worker/office_worker 的状态更新能被发送到前端
        if background_tasks:
            timeout = settings.supervisor.background_task_timeout
            try:
                done, pending_tasks = await asyncio.wait(background_tasks, timeout=timeout)
                for t in done:
                    try:
                        res = t.result()
                    except Exception as e:
                        res = {"error": str(e)}
                    if res:
                        bg_task_outputs.append(res)
                if pending_tasks:
                    logger.warning(f"[异步流式] 后台任务等待超时 ({timeout}s)！部分图表/文件可能未完成写入 Checkpoint。")
                    if session_id and plan_id:
                        await emit_step_update(
                            session_id,
                            "synthesizer",
                            "running",
                            "后台任务处理较慢，请稍后刷新查看...",
                            round_index=round_index
                        )
            except Exception as e:
                logger.error(f"[异步流式] 等待后台任务时发生未知错误: {e}")
             
        # [NEW] 使用 plan_id 作为消息 ID，保证 SSE 与历史恢复对齐
        assistant_message_id = str(plan_id or uuid.uuid4())
        ai_msg = AIMessage(content=final_answer, id=assistant_message_id)
        reasoning_traces = []
        if persisted_reasoning_text:
            reasoning_traces.append(
                {
                    "message_id": assistant_message_id,
                    "content": persisted_reasoning_text,
                    "duration_ms": max(0, int(persisted_reasoning_duration_ms)),
                    "round_index": round_index,
                    "safe": True,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        
        # [延迟保存] 将 pending_artifacts 正式合并到 memory_dfs
        pending = state.get("pending_artifacts", {})
        if bg_task_outputs:
            merged_results = list(results)
            merged_artifacts = {}
            for out in bg_task_outputs:
                if out.get("error"):
                    continue
                step_id = out.get("step_id", "")
                worker = out.get("worker", "")
                merged_results.append({
                    "step_id": step_id,
                    "worker": worker,
                    "result": out.get("result", "")
                })
                memory_update = out.get("memory_update", {}) or {}
                for key, value in memory_update.items():
                    if isinstance(key, str) and key.startswith("df_") and not key.startswith(f"df_{step_id}_"):
                        base_key = key.replace("df_", "", 1)
                        merged_artifacts[f"df_{step_id}_{base_key}"] = value
                    else:
                        merged_artifacts[key] = value
            if merged_artifacts:
                pending = {**pending, **merged_artifacts}
            results = merged_results

        updated_focus_result = resolve_focus_result(
            current_focus_raw=state.get("current_focus_result", {}),
            memory_dfs=state.get("memory_dfs", {}),
            pending_artifacts=pending,
            execution_results=results,
            user_query=state.get("user_query", ""),
            round_index=round_index,
        )
              
        return {
            "final_answer": final_answer,
            "messages": [ai_msg],
            "memory_dfs": pending,  # [关键] 最终保存 DataFrame
            "current_focus_result": updated_focus_result,
            "pending_artifacts": {},  # 清空暂存
            "task_plan": task_plan,
            "execution_results": results,
            "reasoning_traces": reasoning_traces,
        }
    except Exception as e:
        error_msg = f"回答生成出错: {str(e)}"
        if session_id and plan_id:
             await emit_message_chunk(session_id, plan_id, error_msg, round_index=round_index)
             # 出错也要发 END
             await emit_message_end(session_id, plan_id, error_msg, round_index=round_index) 
        
        # [关键修复] 即使出错也等待后台任务完成
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        
        # 即使出错也保存最后的 pending_artifacts
        pending = state.get("pending_artifacts", {})
             
        assistant_message_id = str(plan_id or uuid.uuid4())
        reasoning_traces = []
        if persisted_reasoning_text:
            reasoning_traces.append(
                {
                    "message_id": assistant_message_id,
                    "content": persisted_reasoning_text,
                    "duration_ms": max(0, int(persisted_reasoning_duration_ms)),
                    "round_index": round_index,
                    "safe": True,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        return {
            "final_answer": error_msg,
            "messages": [AIMessage(content=error_msg, id=assistant_message_id)],
            "memory_dfs": pending,
            "current_focus_result": state.get("current_focus_result", {}),
            "pending_artifacts": {},
            "reasoning_traces": reasoning_traces,
        }


async def _handle_direct_response(state: SupervisorState) -> SupervisorState:
    """
    处理直接回答场景（无需工具调用）
    
    用于 chitchat（闲聊）和 direct_answer（上下文追问）
    """
    settings = get_settings()
    llm = get_async_llm()
    session_id = state.get("session_id")
    round_index = state.get("round_index", 0)
    plan_id = state.get("plan_id") or str(uuid.uuid4())
    
    # 构建上下文
    summary = state.get("summary", "")
    user_query = state.get("user_query", "")
    intent_type = state.get("intent_type", "chitchat")
    focus_result = load_focus_result(state.get("current_focus_result", {}))
    memory_dfs = state.get("memory_dfs", {})
    recent_dialogue = build_recent_dialogue(state.get("messages", []))
    
    # 根据意图类型选择不同的 Prompt
    if intent_type == "chitchat":
        system_prompt = "你是 DeluData 智能助手。请友好地回应用户的问候或感谢。保持简洁自然。"
        user_content = user_query
    else:  # direct_answer
        system_prompt = (
            "你是 DeluData 智能助手。请根据当前会话里已经存在的结果回答用户。"
            "如果用户是在继续追问当前结果，请优先解释字段、含义、范围和示例值。"
            "如果现有信息仍不足，请明确说明缺什么。"
        )
        user_content = build_direct_answer_payload(
            summary=summary,
            recent_dialogue=recent_dialogue,
            user_query=user_query,
            focus_result=focus_result,
            memory_dfs=memory_dfs,
        )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    logger.info(
        "[FinalReply] direct_response session_id=%s plan_id=%s intent=%s model=%s reply_model_key=%s requested_final_model=%s",
        session_id,
        plan_id,
        intent_type,
        str(settings.llm.fast_model),
        state.get("reply_model_key"),
        state.get("final_reply_model"),
    )
    
    final_answer = ""
    public_reasoning_chunks = _build_public_reasoning_trace(state, [])
    persisted_reasoning_text = ""
    persisted_reasoning_duration_ms = 0

    async def _emit_direct_public_reasoning_trace() -> None:
        nonlocal persisted_reasoning_text, persisted_reasoning_duration_ms
        reasoning_start_ts = time.monotonic()
        for visible_text in public_reasoning_chunks:
            if not visible_text:
                continue
            if not visible_text.endswith("\n"):
                visible_text += "\n"
            persisted_reasoning_text = _append_reasoning_content(
                persisted_reasoning_text,
                visible_text + "\n",
            )
            if session_id:
                await emit_reasoning_chunk(
                    session_id,
                    plan_id,
                    visible_text,
                    round_index=round_index,
                )
        persisted_reasoning_duration_ms = int((time.monotonic() - reasoning_start_ts) * 1000)
        if session_id:
            await emit_reasoning_end(
                session_id,
                plan_id,
                duration_ms=persisted_reasoning_duration_ms,
                round_index=round_index,
            )

    try:
        await _emit_direct_public_reasoning_trace()
        async for chunk in llm.chat_stream(messages, model=settings.llm.fast_model, extra_body={"enable_thinking": False}):
            if chunk:
                final_answer += chunk
                if session_id:
                    await emit_message_chunk(session_id, plan_id, chunk, round_index=round_index)
        
        if not final_answer:
            final_answer = "你好！有什么可以帮助你的吗？"
            if session_id:
                await emit_message_chunk(session_id, plan_id, final_answer, round_index=round_index)
        
        if session_id:
            await emit_message_end(session_id, plan_id, final_answer, round_index=round_index)

        reasoning_traces = []
        if persisted_reasoning_text:
            reasoning_traces.append(
                {
                    "id": f"{plan_id}:reasoning",
                    "message_id": str(plan_id),
                    "content": persisted_reasoning_text,
                    "duration_ms": max(0, int(persisted_reasoning_duration_ms)),
                    "round_index": round_index,
                    "safe": True,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        
        return {
            "final_answer": final_answer,
            "messages": [AIMessage(content=final_answer, id=str(plan_id))],
            "current_focus_result": state.get("current_focus_result", {}),
            "reasoning_traces": reasoning_traces,
        }
        
    except Exception as e:
        error_msg = f"回答生成出错: {str(e)}"
        logger.error(f"[FastPath] 生成失败: {e}")
        if session_id:
            await emit_message_chunk(session_id, plan_id, error_msg, round_index=round_index)
            await emit_message_end(session_id, plan_id, error_msg, round_index=round_index)
        
        return {
            "final_answer": error_msg,
            "messages": [AIMessage(content=error_msg, id=str(plan_id))],
            "current_focus_result": state.get("current_focus_result", {}),
            "reasoning_traces": [
                {
                    "id": f"{plan_id}:reasoning",
                    "message_id": str(plan_id),
                    "content": persisted_reasoning_text,
                    "duration_ms": max(0, int(persisted_reasoning_duration_ms)),
                    "round_index": round_index,
                    "safe": True,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            ]
            if persisted_reasoning_text
            else [],
        }
