"""
Planner 公共工具函数
包含：
- Prompt 加载策略
- 上下文构建（Phase D: Context Budgeting）
- 多模态处理
- 步骤生成
"""
import base64
import logging
import mimetypes
import os
import uuid
from typing import Any, Optional

from app.config import get_settings
from app.core.llm.prompt_manager import get_prompt
from app.services.workspace_readiness_service import WorkspaceReadinessService

logger = logging.getLogger(__name__)


def _safe_non_negative_int(raw_value: Any, default: int) -> int:
    """安全解析非负整数，解析失败时回退默认值。"""
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _truncate_text(text: str, max_chars: int, suffix: str = "...") -> str:
    """截断文本并追加省略号。"""
    max_chars = max(0, int(max_chars))
    if max_chars == 0:
        return ""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)].rstrip() + suffix


def _extract_message_text(content: Any) -> str:
    """提取 LangChain message content 的可读文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                text_parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                text_parts.append(part)
        return " ".join(p for p in text_parts if p).strip() or "[多模态内容]"
    return str(content or "")


def _build_memory_dfs_info(memory_dfs: dict) -> str:
    """构建 memory_dfs 数据概览。"""
    if not memory_dfs:
        return "无"

    keys = list(memory_dfs.keys())
    lines = [f"已有 {len(keys)} 个数据集: {', '.join(keys)}"]
    for key, data in memory_dfs.items():
        if hasattr(data, "shape"):
            lines.append(f"- {key}: DataFrame {data.shape[0]}行x{data.shape[1]}列")
        elif isinstance(data, str):
            lines.append(f"- {key}: 文本 ({len(data)}字符)")
        elif isinstance(data, list):
            lines.append(f"- {key}: 列表 ({len(data)}项)")
        elif isinstance(data, dict):
            lines.append(f"- {key}: 字典 ({len(data)}键)")
        else:
            lines.append(f"- {key}: {type(data).__name__}")
    return "\n".join(lines)


def _extract_worker_round(result: dict) -> Optional[int]:
    """从 execution_result 中提取 worker_round。"""
    meta = result.get("meta") or {}
    raw_round = meta.get("worker_round")
    try:
        if raw_round is None:
            return None
        return int(raw_round)
    except (TypeError, ValueError):
        return None


def _select_recent_execution_results(execution_results: list, keep_rounds: int) -> tuple[list, int]:
    """
    选择最近 N 轮的执行结果。

    Returns:
        (selected_results, actual_kept_rounds)
    """
    keep_rounds = max(1, keep_rounds)
    if not execution_results:
        return [], 0

    round_values = []
    for result in execution_results:
        worker_round = _extract_worker_round(result)
        if worker_round is not None:
            round_values.append(worker_round)

    if round_values:
        unique_rounds = sorted(set(round_values))
        kept_rounds = set(unique_rounds[-keep_rounds:])
        selected = [
            r for r in execution_results
            if (_extract_worker_round(r) in kept_rounds)
        ]
        return selected, len(kept_rounds)

    # 兼容旧数据：没有 worker_round 时退化为保留最后若干条
    fallback_count = max(keep_rounds * 3, keep_rounds)
    selected = execution_results[-fallback_count:]
    return selected, 1 if selected else 0


def _build_history_section(messages: list, keep_rounds: int, max_chars: int) -> str:
    """构建 History 段（仅最近若干轮消息）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    keep_rounds = max(1, keep_rounds)
    # 1 轮≈1个用户+1个助手消息，按 2 条消息估算
    keep_message_count = keep_rounds * 2
    history_lines: list[str] = []

    for msg in messages[-keep_message_count:]:
        if isinstance(msg, HumanMessage):
            role = "用户"
        elif isinstance(msg, AIMessage):
            role = "助手"
        else:
            continue

        content = _extract_message_text(msg.content)
        content_preview = _truncate_text(content, 120)
        history_lines.append(f"- [{role}] {content_preview}")

    if not history_lines:
        return "无"

    raw_text = "\n".join(history_lines)
    return _truncate_text(raw_text, max_chars)


def _build_execution_section(
    execution_results: list,
    keep_rounds: int,
    item_max_chars: int,
    max_chars: int,
) -> tuple[str, int]:
    """构建 Execution Summary 段，并返回实际保留轮数。"""
    selected, actual_kept_rounds = _select_recent_execution_results(execution_results, keep_rounds)
    if not selected:
        return "无", 0

    lines: list[str] = []
    seen_signatures: set[tuple[str, str]] = set()
    item_max_chars = max(40, item_max_chars)

    for item in selected:
        step_id = str(item.get("step_id", "?"))
        worker = str(item.get("worker", "unknown"))
        worker_round = _extract_worker_round(item)
        round_tag = str(worker_round) if worker_round is not None else "?"

        if item.get("error"):
            summary = f"失败: {item.get('error')}"
        else:
            summary = str(item.get("result", ""))
        summary = _truncate_text(summary.replace("\n", " ").strip(), item_max_chars)

        # 去重：避免同一 worker 重复输出近似摘要
        signature = (worker, summary)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        lines.append(f"- [R{round_tag}|Step {step_id}|{worker}] {summary}")

    if not lines:
        return "无", actual_kept_rounds

    section_text = "\n".join(lines)
    return _truncate_text(section_text, max_chars), actual_kept_rounds


def _compress_skill_context(skill_context: str, max_chars: int) -> str:
    """
    压缩 Skill 文本，优先保留关键元信息：
    模板ID / 文档范围 / 输出文件 / 工具 / 步骤标题。
    """
    skill_context = str(skill_context or "").strip()
    if not skill_context:
        return "无"

    priority_tokens = ("###", "步骤", "模板ID", "文档范围", "输出文件", "工具")
    lines = [line.strip() for line in skill_context.splitlines() if line.strip()]
    priority_lines: list[str] = []
    normal_lines: list[str] = []

    for line in lines:
        if any(token in line for token in priority_tokens):
            priority_lines.append(line)
        else:
            normal_lines.append(line)

    merged = priority_lines + normal_lines
    compact = "\n".join(merged)
    return _truncate_text(compact, max_chars)


def _build_current_goal_section(
    *,
    query: str,
    summary: str,
    file_info: str,
    memory_dfs_info: str,
    sources_section: str,
    task_plan: list,
    max_chars: int,
    knowledge_path_hint: str = "",
) -> str:
    """构建 Current Goal 段。

    knowledge_path_hint：M3.3 引入的可选路由提示。仅当 KnowledgeRouter
    判定结果为非默认（wiki/both）时由调用方拼好传入，让 Planner 知晓 doc_worker
    将装载 Wiki 内容；为空时沉默不输出，保持开关关闭场景的 Prompt 字节级稳定。
    """
    plan_lines: list[str] = []
    for step in task_plan[:8]:
        status = step.get("status", "pending")
        step_id = step.get("step_id", "?")
        worker = step.get("worker", "unknown")
        desc = _truncate_text(str(step.get("description", "")), 60)
        plan_lines.append(f"- [{status}] {step_id}/{worker}: {desc}")

    if not plan_lines:
        plan_lines.append("- 无")

    parts = [
        f"用户当前问题: {query or '无'}",
        f"长期摘要: {summary or '无'}",
        f"当前文件: {file_info or '无'}",
        f"会话数据: {memory_dfs_info or '无'}",
        "当前任务计划:",
        "\n".join(plan_lines),
        "工作区可用源:",
        sources_section or "无",
    ]
    if knowledge_path_hint:
        parts.append(knowledge_path_hint)
    raw = "\n".join(parts)
    return _truncate_text(raw, max_chars)


def _apply_budget_with_priority(
    *,
    current_goal_section: str,
    skill_section: str,
    execution_section: str,
    history_section: str,
    budget_chars: int,
    exec_min_chars: int,
    skill_min_chars: int,
    goal_min_chars: int,
) -> tuple[dict, dict]:
    """
    按固定优先级进行上下文裁剪：
    Current Goal > Skill Metadata > Execution Summary > History

    固定顺序（先裁剪低优先级）：
    1) History -> 0
    2) Execution -> exec_min_chars
    3) Skill -> skill_min_chars
    4) Goal -> goal_min_chars（不可清空）
    """
    sections = {
        "current_goal_section": current_goal_section or "",
        "skill_section": skill_section or "",
        "execution_section": execution_section or "",
        "history_section": history_section or "",
    }

    raw_lengths = {k: len(v) for k, v in sections.items()}
    raw_total = sum(raw_lengths.values())
    budget_chars = max(200, budget_chars)

    def _total_len() -> int:
        return (
            len(sections["current_goal_section"])
            + len(sections["skill_section"])
            + len(sections["execution_section"])
            + len(sections["history_section"])
        )

    def _shrink_to(name: str, target_len: int) -> None:
        target_len = max(0, target_len)
        if len(sections[name]) <= target_len:
            return
        sections[name] = _truncate_text(sections[name], target_len)

    # Phase 1: 按固定策略裁剪到最小保留线
    if _total_len() > budget_chars:
        _shrink_to("history_section", 0)
    if _total_len() > budget_chars:
        _shrink_to("execution_section", max(0, exec_min_chars))
    if _total_len() > budget_chars:
        _shrink_to("skill_section", max(0, skill_min_chars))
    if _total_len() > budget_chars:
        # Goal 不可清空，保留最少说明信息
        _shrink_to("current_goal_section", max(80, goal_min_chars))

    # Phase 2: 极端情况下继续强制收敛（仍遵循同样优先级）
    for name, floor in (
        ("history_section", 0),
        ("execution_section", 0),
        ("skill_section", 0),
        ("current_goal_section", 80),
    ):
        if _total_len() <= budget_chars:
            break
        overflow = _total_len() - budget_chars
        reducible = max(0, len(sections[name]) - floor)
        if reducible <= 0:
            continue
        reduce_by = min(reducible, overflow)
        _shrink_to(name, len(sections[name]) - reduce_by)

    final_lengths = {k: len(v) for k, v in sections.items()}
    final_total = sum(final_lengths.values())
    metrics = {
        "raw_lengths": raw_lengths,
        "raw_total": raw_total,
        "final_lengths": final_lengths,
        "final_total": final_total,
        "budget_chars": budget_chars,
        "trimmed": final_total < raw_total,
    }
    return sections, metrics


def _encode_image_to_base64(image_path: str, max_size_mb: float = 7.0) -> str:
    """
    读取本地图片并转换为 DashScope 兼容的 Base64 格式
    遵循阿里云文档规范：
    - 格式: data:image/{fmt};base64,{base64_str}
    - 小于 7MB 的图片直接编码
    - 大于 7MB 的图片进行压缩后编码
    Args:
        image_path: 本地图片绝对路径
        max_size_mb: 最大文件大小（MB），超过则压缩
    Returns:
        Base64 编码的 Data URI 字符串
    Raises:
        FileNotFoundError: 图片文件不存在
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")
    file_size = os.path.getsize(image_path)
    max_bytes = max_size_mb * 1024 * 1024
    # 自动推断 MIME type
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/png"  # 默认兜底
    if file_size > max_bytes:
        # 大文件：使用 Pillow 压缩
        logger.info(f"[Base64] 图片 {os.path.basename(image_path)} 大小 {file_size/1024/1024:.2f}MB，进行压缩")
        try:
            from PIL import Image
            import io
            with Image.open(image_path) as img:
                buffer = io.BytesIO()
                # 转换为 RGB 避免 PNG 透明通道问题
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                # 压缩保存到内存，quality=85 通常肉眼难辨差异
                img.save(buffer, format="JPEG", quality=85, optimize=True)
                encoded_string = base64.b64encode(buffer.getvalue()).decode("utf-8")
                mime_type = "image/jpeg"
        except ImportError:
            logger.warning("Pillow 未安装，无法压缩大图片，直接编码原图")
            with open(image_path, "rb") as f:
                encoded_string = base64.b64encode(f.read()).decode("utf-8")
    else:
        # 正常大小：直接编码
        with open(image_path, "rb") as f:
            encoded_string = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded_string}"


def get_planner_prompt(is_vl_mode: bool = False, **kwargs) -> str:
    """
    根据模式加载对应的 Planner Prompt
    Args:
        is_vl_mode: 是否为 VL（多模态图片）模式
        **kwargs: Prompt 模板变量
    """
    if is_vl_mode:
        # VL 模式：使用包含 JSON 输出格式的提示词
        return get_prompt("supervisor.supervisor_planner.first_plan_system_vl", **kwargs)
    # 普通模式：使用精简提示词（Schema 已强制输出格式）
    return get_prompt("supervisor.supervisor_planner.first_plan_system", **kwargs)


def build_planner_context(state: dict) -> dict:
    """
    构建 Planner 上下文信息（Phase D）

    固定优先级：
    Current Goal > Skill Metadata > Execution Summary > History
    """
    settings = get_settings()
    llm_settings = settings.llm

    summary = (
        state.get("interaction_summary")
        or state.get("summary")
        or "无历史"
    )
    query = state.get("user_query", "")

    # 1) 基础片段
    memory_dfs = state.get("memory_dfs", {})
    memory_dfs_info = _build_memory_dfs_info(memory_dfs)
    user_context = state.get("user_context", {})
    file_info = "无"
    if user_context and user_context.get("file_context"):
        file_ctx = user_context["file_context"]
        file_name = file_ctx.get("file_name", "未知文件")
        file_path = file_ctx.get("file_path", "")
        file_info = f"文件名: {file_name}, 路径: {file_path}"

    readiness = WorkspaceReadinessService.readiness_from_state(
        state,
        default_available=False,
    )
    sources_section = WorkspaceReadinessService.build_sources_section(readiness)

    # [M3.3] Wiki-First 路由提示：开关关闭/默认 rag 时不输出，保持 Prompt 稳定
    knowledge_path = (state.get("knowledge_path") or "rag").strip().lower()
    knowledge_path_reason = (state.get("knowledge_path_reason") or "").strip()
    knowledge_path_hint = ""
    if knowledge_path == "wiki":
        knowledge_path_hint = (
            "知识检索路由: wiki（doc_worker 将装载预编译实体页，无需切片检索）"
            + (f"；判定原因: {knowledge_path_reason[:80]}" if knowledge_path_reason else "")
        )
    elif knowledge_path == "both":
        knowledge_path_hint = (
            "知识检索路由: both（doc_worker 将并行装载 Wiki 实体页与 RAG 切片，Wiki 优先）"
            + (f"；判定原因: {knowledge_path_reason[:80]}" if knowledge_path_reason else "")
        )

    # 2) 四段上下文（先做局部限长）
    current_goal_section = _build_current_goal_section(
        query=query,
        summary=summary,
        file_info=file_info,
        memory_dfs_info=memory_dfs_info,
        sources_section=sources_section,
        task_plan=state.get("task_plan", []),
        max_chars=_safe_non_negative_int(llm_settings.planner_goal_max_chars, 1600),
        knowledge_path_hint=knowledge_path_hint,
    )

    skill_context = state.get("skill_context", "")
    skill_name = state.get("selected_skill_name", "")
    raw_skill = _compress_skill_context(
        skill_context,
        _safe_non_negative_int(llm_settings.planner_skill_max_chars, 1400),
    )
    if raw_skill != "无":
        skill_section = (
            f"匹配手册: {skill_name or '未命名手册'}\n"
            f"{raw_skill}\n"
            "说明: 优先遵循手册步骤与参数约束。"
        )
    else:
        skill_section = "无"

    execution_section, actual_exec_rounds = _build_execution_section(
        execution_results=state.get("execution_results", []),
        keep_rounds=_safe_non_negative_int(llm_settings.planner_exec_keep_rounds, 2) or 2,
        item_max_chars=_safe_non_negative_int(llm_settings.planner_exec_item_max_chars, 120) or 120,
        max_chars=_safe_non_negative_int(llm_settings.planner_exec_max_chars, 1200),
    )

    history_section = _build_history_section(
        messages=state.get("messages", []),
        keep_rounds=_safe_non_negative_int(llm_settings.planner_history_keep_rounds, 5) or 5,
        max_chars=_safe_non_negative_int(llm_settings.planner_history_max_chars, 900),
    )

    # 3) 全局预算裁剪（固定顺序）
    sections, metrics = _apply_budget_with_priority(
        current_goal_section=current_goal_section,
        skill_section=skill_section,
        execution_section=execution_section,
        history_section=history_section,
        budget_chars=_safe_non_negative_int(llm_settings.planner_context_budget_chars, 5200),
        exec_min_chars=_safe_non_negative_int(llm_settings.planner_exec_min_chars, 300),
        skill_min_chars=_safe_non_negative_int(llm_settings.planner_skill_min_chars, 200),
        goal_min_chars=_safe_non_negative_int(llm_settings.planner_goal_min_chars, 400),
    )

    # 4) 诊断日志（默认关闭详细日志）
    if llm_settings.planner_context_debug_log:
        logger.info(
            "[Planner Context Budget] raw=%s final=%s budget=%s trimmed=%s exec_keep_rounds=%s actual_exec_rounds=%s",
            metrics["raw_lengths"],
            metrics["final_lengths"],
            metrics["budget_chars"],
            metrics["trimmed"],
            _safe_non_negative_int(llm_settings.planner_exec_keep_rounds, 2),
            actual_exec_rounds,
        )
    elif metrics["trimmed"]:
        logger.info(
            "[Planner Context Budget] trimmed raw_total=%s final_total=%s budget=%s exec_rounds=%s",
            metrics["raw_total"],
            metrics["final_total"],
            metrics["budget_chars"],
            actual_exec_rounds,
        )

    context_budgeting_rules = (
        "优先级: Current Goal > Skill Metadata > Execution Summary > History。\n"
        "超预算裁剪顺序: History -> Execution -> Skill -> Current Goal。\n"
        "Execution Summary 默认仅保留最近 "
        f"{_safe_non_negative_int(llm_settings.planner_exec_keep_rounds, 2) or 2} 轮。"
    )

    # 兼容旧模板字段：保留 recent_messages/file_context/memory_dfs_info/sources_section
    return {
        "summary": summary,
        "original_query": query,
        "file_context": file_info,
        "memory_dfs_info": memory_dfs_info,
        "sources_section": sources_section,
        "recent_messages": sections["history_section"],
        "skill_section": sections["skill_section"],
        # 新四段上下文
        "current_goal_section": sections["current_goal_section"],
        "execution_section": sections["execution_section"],
        "history_section": sections["history_section"],
        "context_budgeting_rules": context_budgeting_rules,
    }


def handle_multimodal_images(state: dict, round_index: int) -> tuple[list, Optional[dict]]:
    """
    处理多模态图片
    Returns:
        (all_images, new_asset) - 图片列表和新资产信息
    """
    user_context = state.get("user_context", {})
    image_url = user_context.get("image_url")
    active_assets = state.get("active_assets", [])
    all_images = []
    new_asset = None
    # 1. 添加历史图片
    for asset in active_assets:
        if asset.get("type") == "image":
            all_images.append({
                "id": asset.get("id"),
                "url": asset.get("url")
            })
    # 2. 添加当前图片（如果是新图）
    if image_url:
        if not any(img["url"] == image_url for img in all_images):
            new_id = f"img_{str(uuid.uuid4())[:8]}"
            all_images.append({"id": new_id, "url": image_url})
            new_asset = {
                "id": new_id,
                "type": "image",
                "url": image_url,
                "round_added": round_index,
                "last_mentioned": round_index
            }
    return all_images, new_asset


def build_llm_messages(
    system_prompt: str,
    user_query: str,
    all_images: list,
    user_context: dict
) -> list:
    """
    构建 LLM 消息列表（支持多模态）
    """
    settings = get_settings()
    if all_images:
        # 多模态模式
        user_content = []
        added_image_count = 0
        for idx, img in enumerate(all_images, 1):
            img_url = img["url"]
            # 如果是 HTTP URL 则保留，否则视为本地路径转 Base64
            if not img_url.startswith(("http://", "https://")):
                # [修复] 支持绝对路径和相对路径两种情况
                # image_handler 现在返回绝对路径，但历史会话可能存储了相对路径
                if os.path.isabs(img_url):
                    full_path = img_url  # 已是绝对路径
                else:
                    # 相对路径：拼接 sandbox_path
                    sandbox_path = user_context.get("sandbox_path", settings.sandbox.base_dir)
                    full_path = os.path.join(sandbox_path, img_url)
                try:
                    img_url = _encode_image_to_base64(full_path)
                    logger.info(f"[Multimodal] 图片 {idx} 已转换为 Base64")
                except FileNotFoundError as e:
                    logger.error(f"[Multimodal] 图片 {idx} 加载失败: {e}")
                    continue  # 跳过无效图片
                except Exception as e:
                    logger.error(f"[Multimodal] 图片 {idx} 编码失败: {e}")
                    continue
            user_content.append({"image": img_url})
            added_image_count += 1
        image_labels = ", ".join([f"[Image {i+1}]" for i in range(added_image_count)])
        if not image_labels:
            image_labels = "无"
        user_content.append({"text": f"用户需求：{user_query}\n\n会话中的图片: {image_labels}"})
        return [
            {"role": "system", "content": [{"text": system_prompt}]},
            {"role": "user", "content": user_content}
        ]
    else:
        # 纯文本模式
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户需求：{user_query}"}
        ]


def map_and_create_steps(
    llm_steps: list,
    id_prefix: str = "",
    description_prefix: str = "",
    retry_depth: int = 0,
    origin_step_id: str = ""
) -> list:
    """
    将 LLM 输出的步骤列表转换为 TaskStep 格式
    Args:
        llm_steps: LLM 返回的步骤列表
        id_prefix: ID 前缀（用于重试轮次区分）
        description_prefix: 描述前缀（如 "[补充] "）
        retry_depth: 重试深度（0=原始任务, 1=第一次重试, 2=第二次重试）
        origin_step_id: 祖先步骤 ID（追踪血缘）
    """
    # [Fix] Skill 步骤的顶层字段需要合并到 params
    step_param_fields = {"template_id", "template_version", "template_mode", "output_filename", "doc_scope"}

    steps = []
    id_mapping = {}  # 原始 temp_id -> 实际 step_id
    for idx, step in enumerate(llm_steps, start=1):
        step_id = f"{id_prefix}{idx}" if id_prefix else str(idx)
        temp_id = step.get("temp_id", str(idx))
        id_mapping[temp_id] = step_id
        description = step.get("description", "")
        if description_prefix and not description.startswith(description_prefix):
            description = f"{description_prefix}{description}"

        # [Fix] 合并顶层字段到 params，防止 Skill 定义的 template_id 等丢失
        base_params = step.get("params", {}) or {}
        for field in step_param_fields:
            if field in step and step[field] is not None:
                base_params[field] = step[field]

        new_step = {
            "step_id": step_id,
            "description": description,
            "worker": step.get("worker", "sql_worker"),
            "status": "pending",
            "result": None,
            "editable": step.get("editable", False),
            "params": base_params,
            # 血缘追踪字段
            "retry_depth": retry_depth,
            "origin_step_id": origin_step_id
        }
        if new_step["worker"] == "office_worker":
            params = new_step.get("params") or {}
            mode = (params.get("template_mode") or "").lower()
            if mode in ["draft", "preview"]:
                params["template_mode"] = "render"
            elif params.get("template_id") and not mode:
                params["template_mode"] = "render"
            new_step["params"] = params
        steps.append(new_step)
    return steps
