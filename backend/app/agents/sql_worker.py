"""
DeluData 智能问数系统 - SqlWorker 取数工智能体

职责：
1. 接收 Supervisor 的取数任务
2. 调用 XiYan GBI 生成 SQL (问题改写 → 表选择 → SQL生成)
3. 使用用户配置的 MySQL 本地执行 SQL
4. 通过 SSE 推送子步骤进度
"""
import logging
import asyncio
from datetime import datetime
import uuid
import re
from typing import Dict, Any, Literal, Optional, Sequence, Set
import pandas as pd
from sqlalchemy import inspect, text # Also missing text import used in execute_task

from app.api.database import get_user_engine
from app.api.events import emit_step_update
from app.core.db.read_only_executor import ReadOnlyExecutor
from app.models.config.db_config import (
    get_user_db_config_async,
    get_workspace_admin_db_config_async,
    get_workspace_db_config_async,
)
from app.services.semantic_query_service import (
    SemanticQueryError,
    get_semantic_query_service,
)
from app.services.generation_authorization_guard import stamp_semantic_dataframe
from app.services.semantic_evidence import compare_execution_results
from app.services.sql_result_diagnostics import (
    analyze_sql_result,
    append_diagnostics_text,
)
from app.skills.xiyan_skill import get_xiyan_skill

logger = logging.getLogger(__name__)


_SQL_IDENTIFIER = (
    r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
    r'(?:\s*\.\s*(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*))*'
)
_TABLE_REF_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN|UPDATE|INTO)\s+({_SQL_IDENTIFIER}(?:\s*,\s*{_SQL_IDENTIFIER})*)",
    re.IGNORECASE,
)
_CTE_PATTERN = re.compile(r"\bWITH\s+([A-Za-z_][\w$]*)\s+AS\s*\(", re.IGNORECASE)
_PSEUDO_SOURCE_COLUMNS = {
    "表名",
    "数据表",
    "来源表",
    "引用表",
    "源表",
    "数据来源",
    "table",
    "table_name",
    "source_table",
    "source",
    "data_source",
}
_PSEUDO_SOURCE_ALIAS_PATTERN = re.compile(
    r"""(?P<literal>'(?:''|[^'])*'|"(?:\"\"|[^"])*")\s+AS\s+(?P<alias>`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*|[\u4e00-\u9fff]+)""",
    re.IGNORECASE,
)
_TURNOVER_TERMS = ("库存周转率", "周转率")
_INVENTORY_TERMS = ("库存", "当前库存", "库存数量", "当前数量", "现存", "结存", "inventory", "stock")
_TURNOVER_WORST_TERMS = ("最差", "最低", "最慢", "慢", "倒数", "垫底", "最小")
_INVENTORY_IDENTIFIER_PATTERN = (
    r"(?:`?[A-Za-z_][\w$]*`?\s*\.\s*)?"
    r"`?(?:number|quantity|qty|stock|inventory|current_stock|current_inventory|"
    r"库存|当前库存|库存数量|当前数量|现存|结存)`?"
)
_INVENTORY_POSITIVE_PATTERNS = (
    re.compile(rf"{_INVENTORY_IDENTIFIER_PATTERN}\s*>\s*0(?:\.0+)?\b", re.IGNORECASE),
    re.compile(rf"0(?:\.0+)?\s*<\s*{_INVENTORY_IDENTIFIER_PATTERN}", re.IGNORECASE),
)
_INVENTORY_TOKEN_PATTERN = re.compile(_INVENTORY_IDENTIFIER_PATTERN, re.IGNORECASE)
_TURNOVER_ALIAS_PATTERN = re.compile(r"(周转率|turnover|turnover_rate|rate|率)", re.IGNORECASE)
_ORDER_BY_PATTERN = re.compile(
    r"\border\s+by\s+(?P<expr>.*?)(?:\blimit\b|\boffset\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_sql_for_table_parse(sql: str) -> str:
    sql_clean = re.sub(r"--.*$", "", sql or "", flags=re.MULTILINE)
    sql_clean = re.sub(r"/\*.*?\*/", "", sql_clean, flags=re.DOTALL)
    sql_clean = re.sub(r"'(?:''|[^'])*'", "''", sql_clean)
    sql_clean = re.sub(r'"(?:""|[^"])*"', '""', sql_clean)
    return sql_clean


def _normalize_table_identifier(raw: str) -> str:
    table = str(raw or "").strip().rstrip(";")
    if not table or table.startswith("("):
        return ""
    table = re.sub(r"\s+", "", table)
    parts = [p for p in table.split(".") if p]
    table = parts[-1] if parts else table
    return re.sub(r'^[`\["]+|[`\]"]+$', "", table).strip().lower()


def _parse_sql_table_refs(sql: str) -> Set[str]:
    """Best-effort parser for table references used by generated read queries."""
    sql_clean = _strip_sql_for_table_parse(sql)
    cte_names = {
        _normalize_table_identifier(match.group(1))
        for match in _CTE_PATTERN.finditer(sql_clean)
    }
    tables: Set[str] = set()
    for match in _TABLE_REF_PATTERN.findall(sql_clean):
        for item in str(match or "").split(","):
            table = _normalize_table_identifier(item)
            if table and table not in cte_names and table not in {"select", "where"}:
                tables.add(table)
    return tables


def _validate_single_select_sql(sql: str) -> Optional[str]:
    try:
        import sqlglot

        statements = sqlglot.parse(sql or "", read="mysql")
    except Exception as exc:  # noqa: BLE001
        return f"SQL 安全解析失败，已拒绝执行: {exc}"
    if len(statements) != 1:
        return "拒绝执行多语句 SQL"
    if statements[0].__class__.__name__.lower() != "select":
        return "只允许执行 SELECT 查询"
    return None


def _get_database_table_names(engine) -> Set[str]:
    inspector = inspect(engine)
    names = {str(name).lower() for name in inspector.get_table_names()}
    try:
        names.update(str(name).lower() for name in inspector.get_view_names())
    except Exception as exc:  # noqa: BLE001
        logger.debug("SqlWorker: 获取视图列表失败，忽略: %s", exc)
    return names


async def _resolve_workspace_id_for_user(user_id: str) -> Optional[str]:
    try:
        from app.core.db.database import get_async_db_context
        from app.models.auth.rbac import UserModel
        from sqlalchemy import select

        async with get_async_db_context() as db:
            result = await db.execute(
                select(UserModel.workspace_id).where(UserModel.id == user_id)
            )
            return result.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SqlWorker: 获取 workspace_id 失败: %s", exc)
        return None


async def _get_db_config_for_user(user_id: str, workspace_id: Optional[str]):
    config = await get_user_db_config_async(user_id)
    if (not config or not config.is_active) and workspace_id:
        config = await get_workspace_db_config_async(workspace_id)
    if (not config or not config.is_active) and workspace_id:
        config = await get_workspace_admin_db_config_async(workspace_id)
    return config


async def _requires_semantic_guard(user_id: str, workspace_id: Optional[str], semantic_datasource: Any) -> bool:
    # Every tenant user, including workspace owners, must traverse the semantic
    # authorization compiler.  Role names and the historical wildcard role are
    # never a reason to return to the unfiltered SQL path.
    return True


def _member_semantic_error_type(error_type: Optional[str]) -> str:
    if error_type == "permission_rewrite_required":
        return "permission_rewrite_required"
    if error_type in {"permission_denied", "sensitive_object"}:
        return "permission_denied"
    return "semantic_unavailable"


def _format_table_constraint_prompt(question: str, table_names: Set[str]) -> str:
    if not table_names:
        return question
    sorted_tables = sorted(table_names)
    table_list = ", ".join(sorted_tables[:300])
    if len(sorted_tables) > 300:
        table_list += f"\n（其余 {len(sorted_tables) - 300} 张表已省略；不要使用未列出的表。）"
    return f"""{question}

【数据库真实表约束】
当前连接的数据库只允许使用以下真实存在的表或视图：
{table_list}

请只基于上面列出的真实表生成 SQL。不要猜测、翻译、编造或引用未列出的表名；如果问题过于模糊，优先选择最匹配的真实表。
不要生成 `SELECT '某表名' AS 表名/数据表/来源表/table_name/source_table` 这类来源说明字段；数据来源由系统解析 SQL 的 FROM/JOIN 自动给出。"""


def _find_missing_tables(sql: str, existing_tables: Set[str]) -> list[str]:
    if not existing_tables:
        return []
    referenced = _parse_sql_table_refs(sql)
    return sorted(table for table in referenced if table not in existing_tables)


def _is_pseudo_source_column(column: str) -> bool:
    normalized = str(column or "").strip().strip("`\"[]").lower()
    return normalized in {name.lower() for name in _PSEUDO_SOURCE_COLUMNS}


def _remove_pseudo_source_columns(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> tuple[list[str], list[list[Any]], list[str]]:
    """Remove model-generated source/table-name columns from SQL output."""
    clean_columns: list[str] = []
    keep_indexes: list[int] = []
    removed_columns: list[str] = []
    for idx, column in enumerate(columns):
        text = str(column or "")
        if _is_pseudo_source_column(text):
            removed_columns.append(text)
            continue
        keep_indexes.append(idx)
        clean_columns.append(text)

    clean_rows: list[list[Any]] = []
    for row in rows:
        clean_rows.append([
            row[idx] if idx < len(row) else None
            for idx in keep_indexes
        ])
    return clean_columns, clean_rows, removed_columns


def _build_result_text(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    row_count: int,
    ambiguity_warning: Optional[str] = None,
    diagnostics: Optional[Sequence[dict[str, Any]]] = None,
) -> str:
    if columns:
        result_text = " | ".join(str(col) for col in columns) + "\n"
        result_text += "-" * 50 + "\n"
        for row in rows[:50]:
            result_text += " | ".join(str(v) for v in row) + "\n"
        if row_count > 50:
            result_text += f"... 共 {row_count} 行\n"
    else:
        result_text = "SQL 结果只包含模型生成的表来源说明列，已移除；实际引用表见上方。\n"

    if ambiguity_warning:
        result_text += f"\n⚠️ 数据歧义提示: {ambiguity_warning}\n"
    return append_diagnostics_text(result_text, diagnostics or [])


def _redact_pseudo_source_literals(sql: str) -> str:
    """Hide table/source string literals from SQL shown to the synthesizer."""
    def _replace(match: re.Match) -> str:
        alias = match.group("alias")
        if _is_pseudo_source_column(alias):
            return f"'[已移除的表来源说明]' AS {alias}"
        return match.group(0)

    return _PSEUDO_SOURCE_ALIAS_PATTERN.sub(_replace, sql or "")


def _is_repairable_sql_execution_error(error: str, sql: str = "") -> bool:
    text = f"{error or ''}\n{sql or ''}".lower()
    return "full outer join" in text or "only_full_group_by" in text


def _build_sql_execution_repair_prompt(
    *,
    question: str,
    sql: str,
    error: str,
    referenced_tables: Sequence[str],
) -> str:
    return f"""{question}

【上一版 SQL 执行失败】
SQL:
{sql}

错误:
{error}

已确认可用表:
{", ".join(referenced_tables) or "仅使用原 SQL 已引用表"}

请重新生成一条 MySQL 兼容的只读 SELECT SQL：
1. 如果使用了 FULL OUTER JOIN，必须改写为 MySQL 兼容写法，例如 LEFT JOIN UNION RIGHT JOIN。
2. 如果触发 only_full_group_by，必须补齐 GROUP BY，或改写 SELECT / ORDER BY 中的聚合与非聚合字段。
3. 不要新增原 SQL 之外的数据表。
4. 必须保留 LIMIT。
5. 只返回 SQL，不要解释，不要 markdown。"""


def _extract_sql_from_text(text: str) -> str:
    value = str(text or "").strip()
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)```", value, re.I)
    if match:
        value = match.group(1).strip()
    value = re.sub(r"^\s*SQL\s*:\s*", "", value, flags=re.I).strip()
    return value.rstrip(";")


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(part for part in parts if part).strip()
    return str(content or "").strip()


def _build_recent_dialogue_for_sql(messages: Any, *, max_messages: int = 6) -> str:
    if not isinstance(messages, list) or not messages:
        return "无"

    lines: list[str] = []
    for message in messages[-max_messages:]:
        msg_type = (message.__class__.__name__ or "").lower()
        role_attr = str(getattr(message, "type", "") or "").lower()
        if "human" in msg_type or role_attr == "human":
            role = "用户"
        elif "ai" in msg_type or role_attr in {"ai", "assistant"}:
            role = "助手"
        else:
            continue

        text_value = _extract_message_text(getattr(message, "content", ""))
        if text_value:
            lines.append(f"- [{role}] {text_value[:260]}")
    return "\n".join(lines) if lines else "无"


def _extract_recent_sql_context(execution_results: Any, *, max_items: int = 2) -> str:
    if not isinstance(execution_results, list) or not execution_results:
        return "无"

    lines: list[str] = []
    for item in reversed(execution_results):
        if not isinstance(item, dict) or item.get("error"):
            continue
        if str(item.get("worker") or "") != "sql_worker":
            continue
        result_text = str(item.get("result") or "")
        if not result_text:
            continue
        sql_match = re.search(r"SQL:\s*(.*?)(?:\n\n改写后的问题:|\Z)", result_text, re.DOTALL)
        rewrite_match = re.search(r"改写后的问题:\s*(.*?)(?:\n实际引用表:|\Z)", result_text, re.DOTALL)
        ref_match = re.search(r"实际引用表:\s*(.*?)(?:\n数据引用:|\Z)", result_text, re.DOTALL)
        if rewrite_match:
            lines.append(f"- 上轮改写问题: {rewrite_match.group(1).strip()[:220]}")
        if ref_match:
            lines.append(f"- 上轮引用表: {ref_match.group(1).strip()[:220]}")
        if sql_match:
            sql_preview = re.sub(r"\s+", " ", sql_match.group(1).strip())
            lines.append(f"- 上轮 SQL: {sql_preview[:420]}")
        if len(lines) >= max_items * 3:
            break
    return "\n".join(lines) if lines else "无"


def _build_focus_context_for_sql(current_focus_result: Any) -> str:
    if not isinstance(current_focus_result, dict) or not current_focus_result:
        return "无"
    parts: list[str] = []
    query = str(current_focus_result.get("query") or "").strip()
    summary = str(current_focus_result.get("summary") or "").strip()
    columns = current_focus_result.get("columns") or []
    preview = str(current_focus_result.get("preview_text") or "").strip()
    if query:
        parts.append(f"上一轮焦点问题: {query[:220]}")
    if summary:
        parts.append(f"上一轮焦点结果: {summary[:220]}")
    if columns:
        parts.append("上一轮结果字段: " + ", ".join(str(col) for col in columns[:16]))
    if preview:
        parts.append(f"上一轮结果预览: {preview[:360]}")
    return "\n".join(parts) if parts else "无"


def _is_inventory_turnover_worst_query(text_value: str) -> bool:
    text_value = str(text_value or "").lower()
    has_turnover = any(term.lower() in text_value for term in _TURNOVER_TERMS)
    has_inventory = any(term.lower() in text_value for term in _INVENTORY_TERMS)
    has_worst = any(term.lower() in text_value for term in _TURNOVER_WORST_TERMS)
    return has_turnover and has_inventory and has_worst


def _append_inventory_turnover_rules(task_text: str) -> str:
    if not _is_inventory_turnover_worst_query(task_text):
        return task_text
    rules = """

【库存周转率业务口径 - 必须遵守】
1. 库存周转率 = 指定时间范围内出库总量 / 当前库存。
2. 当前库存 <= 0 的商品视为周转最好或售罄，不参与“周转率最差/最低/最慢”排行。
3. 查询“周转率最差”时必须同时满足：当前库存 > 0，并按“库存周转率”升序排序取前 N。
4. “库存不为0”只是筛选条件，不能把排序指标改成“当前库存最少”。
5. 多个筛选条件必须同时保留，例如“近180天”“库存不为0”“周转率最差”缺一不可。
"""
    return f"{task_text.rstrip()}\n{rules.strip()}"


def build_sql_task_context(
    *,
    task_description: str,
    messages: Any = None,
    summary: str = "",
    current_focus_result: Any = None,
    execution_results: Any = None,
) -> str:
    """Build the natural-language task sent to NL2SQL, preserving follow-up context."""
    recent_dialogue = _build_recent_dialogue_for_sql(messages)
    focus_context = _build_focus_context_for_sql(current_focus_result)
    recent_sql_context = _extract_recent_sql_context(execution_results)
    summary = str(summary or "").strip()

    has_context = any(
        value and value != "无"
        for value in (recent_dialogue, focus_context, recent_sql_context, summary)
    )
    if not has_context:
        return _append_inventory_turnover_rules(task_description)

    task = f"""用户当前问题:
{task_description}

请先把当前问题理解为一次可能带有追问、省略和新增筛选条件的数据查询。生成 SQL 时必须优先满足“用户当前问题”，并结合下列上下文补全省略的信息；如果当前问题新增了筛选条件，只叠加条件，不要丢掉上一轮的时间范围、指标公式、关联表或排序指标。

历史摘要:
{summary or '无'}

最近对话:
{recent_dialogue}

当前焦点结果:
{focus_context}

最近 SQL 查询上下文:
{recent_sql_context}

完整 SQL 查询需求:
{task_description}
"""
    return _append_inventory_turnover_rules(task)


def _extract_order_by_clause(sql: str) -> str:
    match = _ORDER_BY_PATTERN.search(sql or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group("expr") or "").strip()


def _first_order_item(order_clause: str) -> str:
    if not order_clause:
        return ""
    return order_clause.split(",", 1)[0].strip()


def validate_inventory_turnover_sql(task_description: str, sql: str) -> list[str]:
    """Return validation issues for inventory-turnover worst-ranking SQL."""
    if not _is_inventory_turnover_worst_query(task_description):
        return []

    sql_text = str(sql or "")
    sql_compact = re.sub(r"\s+", " ", sql_text)
    order_clause = _extract_order_by_clause(sql_compact)
    first_order_item = _first_order_item(order_clause)
    issues: list[str] = []

    has_positive_inventory_filter = any(
        pattern.search(sql_compact) for pattern in _INVENTORY_POSITIVE_PATTERNS
    )
    if not has_positive_inventory_filter:
        issues.append("缺少当前库存 > 0 过滤；库存为0的商品不能进入周转率最差排行。")

    if "/" not in sql_compact:
        issues.append("未发现出库量 / 当前库存形式的周转率计算。")

    if not order_clause:
        issues.append("缺少 ORDER BY，无法保证返回周转率最差的商品。")
    else:
        order_has_turnover = bool(_TURNOVER_ALIAS_PATTERN.search(order_clause) or "/" in order_clause)
        order_has_inventory = bool(_INVENTORY_TOKEN_PATTERN.search(order_clause))
        if not order_has_turnover:
            issues.append("ORDER BY 未按库存周转率排序。")
        if order_has_inventory and not order_has_turnover:
            issues.append("排序疑似使用库存数量，不能把“库存不为0”理解成按库存最少排序。")
        if re.search(r"\bdesc\b", first_order_item, flags=re.IGNORECASE):
            issues.append("周转率最差应按库存周转率升序排序，不能降序。")

    return issues


class SqlWorker:
    """
    SQL 取数工智能体
    
    使用 XiYan GBI 作为 NL2SQL 引擎
    本地执行生成的 SQL
    """
    
    async def execute_task(
        self,
        task_description: str,
        user_id: str,
        session_id: str = "",
        parent_step_id: str = "",
        round_index: int = 0,  # [P1] 轮次索引，用于 df_key 语义化命名
        messages: Optional[list] = None,
        summary: str = "",
        current_focus_result: Optional[dict] = None,
        execution_results: Optional[list] = None,
        run_mode: Literal["auto", "semantic_only", "legacy_only"] = "auto",
        expected_limit: Optional[int] = None,
        semantic_clarification: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        执行取数任务
        
        Args:
            task_description: 任务描述 (自然语言问题)
            user_id: 用户ID
            session_id: 会话sID (用于 SSE 推送)
            parent_step_id: 父步骤ID (用于子步骤嵌套显示)
            round_index: 轮次索引 (用于 df_key 命名)
            
        Returns:
            执行结果 dict，包含 sql, data, artifacts 等
        """
        
        effective_task_description = build_sql_task_context(
            task_description=task_description,
            messages=messages or [],
            summary=summary,
            current_focus_result=current_focus_result or {},
            execution_results=execution_results or [],
        )
        logger.info(f"SqlWorker: 开始执行, user_id={user_id}, task={task_description[:50]}...")

        workspace_id = await _resolve_workspace_id_for_user(user_id)
        semantic_attempted = False
        semantic_error_type: Optional[str] = None
        semantic_service = get_semantic_query_service()
        semantic_datasource = None
        try:
            semantic_datasource = await semantic_service.get_active_datasource(workspace_id or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SqlWorker: 读取语义运行模式失败: %s", exc)

        runtime_mode = getattr(semantic_datasource, "runtime_mode", None) if semantic_datasource else "disabled"
        if semantic_datasource and not runtime_mode:
            runtime_mode = (
                "disabled"
                if not semantic_datasource.semantic_sql_enabled
                else "shadow" if semantic_datasource.semantic_sql_fallback_enabled else "trusted"
            )
        semantic_guard_required = await _requires_semantic_guard(
            user_id,
            workspace_id,
            semantic_datasource,
        )

        if semantic_guard_required and run_mode == "legacy_only":
            return {
                "success": False,
                "error": "普通用户必须通过语义模型权限校验后才能进行数据库问答，不能使用旧 SQL 链路。",
                "error_type": "permission_denied",
                "suggestion": "请联系管理员在语义模型中配置可问答的数据范围。",
                "from_semantic": True,
                "semantic_fallback_blocked": True,
            }

        if semantic_guard_required and not semantic_datasource:
            message = "当前工作区尚未配置可用语义模型，普通用户不能直接进行数据库问答。请联系管理员配置语义模型和可见范围。"
            if session_id:
                await emit_step_update(
                    session_id=session_id,
                    step_id="semantic-query",
                    status="error",
                    label=message[:50],
                    parent_step_id=parent_step_id or None,
                )
            return {
                "success": False,
                "error": message,
                "error_type": "semantic_unavailable",
                "suggestion": "请联系管理员在语义模型中启用可问答的表和字段权限。",
                "from_semantic": True,
                "semantic_fallback_blocked": True,
            }

        if run_mode == "auto" and runtime_mode == "shadow" and workspace_id and not semantic_guard_required:
            correlation_id = uuid.uuid4().hex

            async def _run_semantic_shadow():
                try:
                    result = await semantic_service.execute_semantic_query(
                        question=effective_task_description,
                        user_id=user_id,
                        workspace_id=workspace_id or "",
                        session_id=session_id,
                        force_enabled=True,
                        expected_limit=expected_limit,
                        semantic_clarification=semantic_clarification,
                    )
                    return {
                        "success": True,
                        "sql": result.sql,
                        "row_count": result.row_count,
                        "execution_time_ms": result.execution_time_ms,
                        "error_type": None,
                        "columns": list(result.columns or []),
                        "_compare_data": list(result.data or [])[:1000],
                    }
                except SemanticQueryError as exc:
                    return {"success": False, "sql": None, "row_count": 0, "execution_time_ms": 0, "error_type": exc.error_type, "error": exc.message}
                except Exception as exc:  # noqa: BLE001
                    return {"success": False, "sql": None, "row_count": 0, "execution_time_ms": 0, "error_type": "semantic_unexpected_error", "error": str(exc)}

            semantic_shadow, legacy_result = await asyncio.gather(
                _run_semantic_shadow(),
                self.execute_task(
                    task_description=task_description,
                    user_id=user_id,
                    session_id=session_id,
                    parent_step_id=parent_step_id,
                    round_index=round_index,
                    messages=messages,
                    summary=summary,
                    current_focus_result=current_focus_result,
                    execution_results=execution_results,
                    run_mode="legacy_only",
                    expected_limit=expected_limit,
                ),
            )
            legacy_summary = {
                "success": bool(legacy_result.get("success")),
                "sql": legacy_result.get("sql"),
                "row_count": int(legacy_result.get("row_count") or 0),
                "columns": list(legacy_result.get("columns") or []),
                "error_type": legacy_result.get("error_type"),
                "error": legacy_result.get("error"),
            }
            comparison = compare_execution_results(
                {**semantic_shadow, "data": semantic_shadow.get("_compare_data") or []},
                {**legacy_summary, "data": list(legacy_result.get("data") or [])[:1000]},
            )
            semantic_stored = {key: value for key, value in semantic_shadow.items() if key != "_compare_data"}
            await semantic_service.record_query_run(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                question=task_description,
                semantic_enabled=True,
                fallback_used=False,
                status="shadow_compared",
                error_type=semantic_shadow.get("error_type"),
                intent=None,
                plan=None,
                sql=semantic_shadow.get("sql"),
                legacy_sql=legacy_summary.get("sql"),
                referenced_tables=list(legacy_result.get("referenced_tables") or []),
                row_count=legacy_summary["row_count"],
                execution_time_ms=max(int(semantic_shadow.get("execution_time_ms") or 0), int(legacy_result.get("execution_time_ms") or 0)),
                runtime_mode="shadow",
                correlation_id=correlation_id,
                semantic_result=semantic_stored,
                legacy_result=legacy_summary,
                comparison=comparison,
                returned_chain="legacy",
            )
            legacy_result["semantic_shadow"] = {"correlation_id": correlation_id, "comparison": comparison}
            return legacy_result

        try:
            if run_mode != "legacy_only" and semantic_datasource and (
                runtime_mode == "trusted" or run_mode == "semantic_only" or semantic_guard_required
            ):
                semantic_attempted = True
                await emit_step_update(
                    session_id=session_id,
                    step_id="semantic-query",
                    status="running",
                    label="语义问数: 正在解析查询计划...",
                    parent_step_id=parent_step_id or None,
                ) if session_id else None
                semantic_result = await semantic_service.execute_semantic_query(
                    question=effective_task_description,
                    user_id=user_id,
                    workspace_id=workspace_id or "",
                    session_id=session_id,
                    force_enabled=run_mode == "semantic_only" or semantic_guard_required,
                    expected_limit=expected_limit,
                    semantic_clarification=semantic_clarification,
                )
                tables_name = "_".join(semantic_result.referenced_tables[:2]) if semantic_result.referenced_tables else "query"
                tables_name = tables_name.replace(".", "_").replace("-", "_")[:30]
                df_key = f"sql_{tables_name}_r{round_index}"
                df = pd.DataFrame(semantic_result.data)
                semantic_access = (
                    (semantic_result.plan or {}).get("user_access", {}).get("semantic_access", {})
                )
                stamp_semantic_dataframe(
                    df,
                    user_id=user_id,
                    workspace_id=workspace_id or "",
                    authorization_revision=int(semantic_access.get("authorization_revision") or 0),
                    authorization_valid_until=semantic_access.get("authorization_valid_until"),
                )
                await emit_step_update(
                    session_id=session_id,
                    step_id="semantic-query",
                    status="done",
                    label=f"语义问数: 返回 {semantic_result.row_count} 行",
                    parent_step_id=parent_step_id or None,
                ) if session_id else None
                return {
                    "success": True,
                    "sql": semantic_result.sql,
                    "rewrite": "semantic_query",
                    "selected_tables": semantic_result.referenced_tables,
                    "referenced_tables": semantic_result.referenced_tables,
                    "xiyan_selected_tables": [],
                    "data": semantic_result.data,
                    "columns": semantic_result.columns,
                    "row_count": semantic_result.row_count,
                    "result_text": semantic_result.result_text,
                    "df_key": df_key,
                    "artifacts": {df_key: df},
                    "ambiguity_warning": None,
                    "from_example": False,
                    "from_semantic": True,
                    "semantic_intent": semantic_result.intent,
                    "semantic_plan": semantic_result.plan,
                    "semantic_run_id": semantic_result.run_id,
                    "diagnostics": semantic_result.diagnostics,
                    "removed_pseudo_source_columns": [],
                }
        except SemanticQueryError as exc:
            semantic_error_type = exc.error_type
            logger.warning(
                "SqlWorker: 语义链路失败 error_type=%s safe_to_fallback=%s message=%s",
                exc.error_type,
                exc.safe_to_fallback,
                exc.message,
            )
            if session_id:
                await emit_step_update(
                    session_id=session_id,
                    step_id="semantic-query",
                    status="error",
                    label=f"语义问数: {exc.message[:50]}",
                    parent_step_id=parent_step_id or None,
                )
            datasource = None
            try:
                datasource = await semantic_service.get_active_datasource(workspace_id or "")
            except Exception:
                datasource = None
            fallback_enabled = bool(datasource and datasource.semantic_sql_fallback_enabled)
            if semantic_guard_required or run_mode == "semantic_only" or not exc.safe_to_fallback or not fallback_enabled:
                public_error_type = _member_semantic_error_type(exc.error_type) if semantic_guard_required else exc.error_type
                failure_result = {
                    "success": False,
                    "error": exc.message,
                    "error_type": public_error_type,
                    "suggestion": "请联系管理员检查语义模型、权限或只读数据库账号配置。",
                    "from_semantic": True,
                    "semantic_fallback_blocked": True,
                }
                clarification = exc.details.get("clarification") if isinstance(exc.details, dict) else None
                if clarification:
                    failure_result["semantic_clarification"] = clarification
                return failure_result
            logger.info("SqlWorker: 语义链路允许回退，继续旧 XiYan/SQL 示例链路")
        except Exception as exc:  # noqa: BLE001
            semantic_error_type = "semantic_unexpected_error"
            logger.warning("SqlWorker: 语义链路异常，将回退旧链路: %s", exc)

        if semantic_guard_required and semantic_error_type == "semantic_unexpected_error":
            message = "语义问数链路异常，普通用户不能回退到自由 SQL。请联系管理员检查语义模型配置。"
            if session_id:
                await emit_step_update(
                    session_id=session_id,
                    step_id="semantic-query",
                    status="error",
                    label=message[:50],
                    parent_step_id=parent_step_id or None,
                )
            return {
                "success": False,
                "error": message,
                "error_type": "semantic_unavailable",
                "suggestion": "请联系管理员检查语义模型、权限和只读数据库账号配置。",
                "from_semantic": True,
                "semantic_fallback_blocked": True,
            }

        if run_mode == "semantic_only":
            return {
                "success": False,
                "error": "语义评估链路不可用：未找到可用语义数据源或语义模型目录。",
                "error_type": semantic_error_type or "semantic_unavailable",
                "from_semantic": True,
                "semantic_fallback_blocked": True,
            }
        
        # 1. 获取用户数据库引擎
        try:
            engine = await get_user_engine(user_id)
            if not engine:
                logger.warning(f"SqlWorker: 用户 {user_id} 未配置数据库连接")
                return {
                    "success": False,
                    "error": "用户未配置数据库连接，请先在数据库配置页面连接数据库"
                }
            logger.info("SqlWorker: 获取数据库引擎成功")
        except Exception as e:
            logger.error(f"SqlWorker: 获取数据库引擎失败: {e}")
            return {
                "success": False,
                "error": f"获取数据库引擎失败: {str(e)}"
            }

        db_config = None
        try:
            db_config = await _get_db_config_for_user(user_id, workspace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SqlWorker: 获取数据库配置失败，旧链路将使用普通引擎执行: %s", exc)

        try:
            existing_tables = _get_database_table_names(engine)
            logger.info("SqlWorker: 当前数据库真实表/视图数量=%d", len(existing_tables))
        except Exception as e:
            existing_tables = set()
            logger.warning("SqlWorker: 获取真实表清单失败，将仅依赖数据库执行错误兜底: %s", e)
        
        # 3. 定义 SSE 步骤回调
        async def on_step(name: str, status: str, detail: str):
            if session_id:
                try:
                    await emit_step_update(
                        session_id=session_id,
                        step_id=f"xiyan-{name}",
                        status=status,
                        label=f"{name}: {detail[:50]}..." if len(detail) > 50 else f"{name}: {detail}",
                        parent_step_id=parent_step_id or None,
                    )
                except Exception as e:
                    logger.warning(f"SSE 推送失败: {e}")
        
        # 4. 优先尝试 SQL 示例向量匹配
        sql = None
        use_example = False
        
        try:
            from app.models.config.sql_example_embeddings import search_sql_examples_by_similarity
            from app.core.db.database import get_async_db_context
            from app.models.auth.rbac import UserModel
            from sqlalchemy import select
            
            # 获取用户工作空间 ID（从用户表读取）
            workspace_id = None
            try:
                async with get_async_db_context() as db:
                    result = await db.execute(
                        select(UserModel.workspace_id).where(UserModel.id == user_id)
                    )
                    workspace_id = result.scalar_one_or_none()
            except Exception as e:
                logger.warning(f"SqlWorker: 获取 workspace_id 失败: {e}")
            
            if not workspace_id:
                workspace_id = user_id  # 兜底兼容旧逻辑
            
            await on_step("SQL示例匹配", "running", "正在匹配已配置的 SQL 示例...")
            
            matched_examples = await search_sql_examples_by_similarity(
                question=effective_task_description,
                workspace_id=workspace_id,
                n_results=1
            )
            
            if matched_examples:
                example, similarity = matched_examples[0]
                sql = example.sql
                use_example = True
                logger.info(f"SqlWorker: 匹配到 SQL 示例 (相似度: {similarity:.2f}), 跳过 XiYan")
                await on_step("SQL示例匹配", "done", f"找到相似问题，直接使用示例 SQL (相似度: {similarity*100:.1f}%)")
            else:
                logger.info("SqlWorker: 未匹配到 SQL 示例，将调用 XiYan")
                await on_step("SQL示例匹配", "done", "未找到匹配的示例，将调用 XiYan 生成")
                
        except Exception as e:
            logger.warning(f"SqlWorker: SQL 示例匹配失败: {e}, 将 fallback 到 XiYan")
            await on_step("SQL示例匹配", "done", f"匹配失败，将调用 XiYan")
        
        # 5. Fallback: 调用 XiYan 生成 SQL
        xiyan_result = None
        async def _generate_with_xiyan(prompt_text: str):
            xiyan = get_xiyan_skill()
            constrained_task = _format_table_constraint_prompt(prompt_text, existing_tables)
            return await xiyan.generate_sql(constrained_task, step_callback=on_step)

        if not use_example:
            xiyan_result = await _generate_with_xiyan(effective_task_description)
            if not xiyan_result.success:
                error_msg = f"SQL 生成失败: {xiyan_result.error}"
                logger.error(f"SqlWorker: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "suggestion": "请尝试重新描述您的问题，或检查数据库表是否存在。"
                }
            sql = xiyan_result.sql
            logger.info(f"SqlWorker: XiYan 生成 SQL = {sql[:100]}...")

        turnover_issues = validate_inventory_turnover_sql(effective_task_description, sql or "")
        if turnover_issues:
            logger.warning(
                "SqlWorker: 库存周转率 SQL 校验未通过，from_example=%s issues=%s sql=%s",
                use_example,
                turnover_issues,
                (sql or "")[:500],
            )
            await on_step("SQL口径校验", "running", "正在修复库存周转率排序和库存过滤口径...")
            repair_prompt = f"""{effective_task_description}

【上一次 SQL 未通过库存周转率口径校验】
问题:
{chr(10).join(f'- {issue}' for issue in turnover_issues)}

请重新生成 SQL，必须修复上述问题。"""
            xiyan_result = await _generate_with_xiyan(repair_prompt)
            use_example = False
            if not xiyan_result.success:
                error_msg = f"SQL 生成失败: {xiyan_result.error}"
                logger.error(f"SqlWorker: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "suggestion": "请尝试重新描述您的问题，或检查数据库表是否存在。"
                }
            sql = xiyan_result.sql
            retry_issues = validate_inventory_turnover_sql(effective_task_description, sql or "")
            if retry_issues:
                error_msg = "SQL 未满足库存周转率业务口径: " + "；".join(retry_issues)
                logger.warning("SqlWorker: %s; sql=%s", error_msg, (sql or "")[:500])
                await on_step("SQL口径校验", "error", error_msg)
                return {
                    "success": False,
                    "sql": sql,
                    "error": error_msg,
                    "suggestion": "库存周转率最差排行必须排除当前库存为0的商品，并按出库量/当前库存升序排序。",
                    "from_example": use_example,
                }
            await on_step("SQL口径校验", "done", "库存周转率口径校验通过")
        elif _is_inventory_turnover_worst_query(effective_task_description):
            await on_step("SQL口径校验", "done", "库存周转率口径校验通过")

        unsafe_reason = _validate_single_select_sql(sql or "")
        if unsafe_reason:
            logger.warning("SqlWorker: SQL 安全校验失败: %s; sql=%s", unsafe_reason, (sql or "")[:500])
            await on_step("SQL安全校验", "error", unsafe_reason)
            return {
                "success": False,
                "sql": sql,
                "error": unsafe_reason,
                "error_type": "unsafe_sql",
                "suggestion": "系统只允许执行单条 SELECT 查询，请重新描述只读数据查询需求。",
                "from_example": use_example,
            }

        missing_tables = _find_missing_tables(sql or "", existing_tables)
        if missing_tables:
            error_msg = (
                "SQL 生成引用了当前数据库中不存在的数据表: "
                + ", ".join(missing_tables)
            )
            logger.warning(
                "SqlWorker: %s; sql=%s; existing_sample=%s",
                error_msg,
                (sql or "")[:500],
                sorted(existing_tables)[:20],
            )
            await on_step("表存在校验", "error", error_msg)
            return {
                "success": False,
                "sql": sql,
                "error": error_msg,
                "missing_tables": missing_tables,
                "available_tables_sample": sorted(existing_tables)[:50],
                "suggestion": "请换一种说法，或明确要查询的业务表/字段；系统不会执行引用不存在表的 SQL。",
                "from_example": use_example,
            }

        referenced_tables = sorted(_parse_sql_table_refs(sql or ""))
        if not referenced_tables:
            error_msg = "SQL 生成结果无法解析出实际引用的数据表，已拒绝生成带表引用的答案。"
            logger.warning("SqlWorker: %s sql=%s", error_msg, (sql or "")[:500])
            await on_step("表存在校验", "error", error_msg)
            return {
                "success": False,
                "sql": sql,
                "error": error_msg,
                "suggestion": "请明确要查询的业务数据范围；系统只能基于 SQL 实际读取的表进行回答。",
                "from_example": use_example,
            }
        
        logger.info("SqlWorker: 旧 SQL 表级权限已停用，运行时不再读取 UserTablePermission")
        
        # 4. 推送执行 SQL 状态
        await on_step("执行SQL", "running", "正在执行 SQL...")
        
        # 5. 本地执行 SQL
        try:
            if db_config and db_config.has_readonly_config():
                executor = ReadOnlyExecutor(user_id, db_config.get_readonly_connection_url())
                readonly_result = executor.execute_query(sql, timeout_sec=5, max_rows=10000)
                if readonly_result.error:
                    raise RuntimeError(readonly_result.error)
                rows = readonly_result.rows
                columns = list(readonly_result.columns)
            else:
                logger.warning("SqlWorker: 用户/工作区未配置只读账号，旧链路暂使用原连接执行")
                with engine.connect() as conn:
                    db_result = conn.execute(text(sql))
                    rows = db_result.fetchall()
                    columns = list(db_result.keys())

            if len(rows) == 0:
                clean_columns, _, removed_columns = _remove_pseudo_source_columns(columns, [])
                logger.info("SqlWorker: 执行成功，无结果返回")
                await on_step("执行SQL", "done", "执行成功，无结果")
                if semantic_attempted and workspace_id:
                    await semantic_service.record_query_run(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=session_id,
                        question=effective_task_description,
                        semantic_enabled=True,
                        fallback_used=True,
                        status="success",
                        error_type=semantic_error_type,
                        intent=None,
                        plan={"fallback_reason": semantic_error_type},
                        sql=sql,
                        referenced_tables=referenced_tables,
                        row_count=0,
                        execution_time_ms=0,
                        legacy_sql=sql,
                    )
                return {
                    "success": True,
                    "sql": sql,
                    "rewrite": xiyan_result.rewrite if xiyan_result else "",
                    "selected_tables": referenced_tables,
                    "referenced_tables": referenced_tables,
                    "xiyan_selected_tables": xiyan_result.selected_tables if xiyan_result else [],
                    "data": [],
                    "columns": clean_columns,
                    "row_count": 0,
                    "message": "SQL 执行成功，无结果返回",
                    "from_example": use_example,
                    "removed_pseudo_source_columns": removed_columns,
                }

            clean_columns, clean_rows, removed_columns = _remove_pseudo_source_columns(
                columns,
                rows,
            )
            if removed_columns:
                logger.info(
                    "SqlWorker: 已移除模型生成的表来源说明列: %s; actual_tables=%s",
                    removed_columns,
                    referenced_tables,
                )

            # 格式化结果
            data = [dict(zip(clean_columns, row)) for row in clean_rows]

            # [3.3 数据歧义检测] Level 2 歧义：检测数据层面的模糊性
            ambiguity_warning = await self._detect_data_ambiguity(
                task_description, data, clean_columns
            )
            diagnostics = analyze_sql_result(
                question=effective_task_description,
                columns=clean_columns,
                rows=clean_rows,
                expected_limit=expected_limit,
            )

            # 构建文本结果 (供 Synthesizer 使用)
            result_text = _build_result_text(
                clean_columns,
                clean_rows,
                row_count=len(rows),
                ambiguity_warning=ambiguity_warning,
                diagnostics=diagnostics,
            )

            logger.info(f"SqlWorker: 执行成功，返回 {len(rows)} 行")
            await on_step("执行SQL", "done", f"返回 {len(rows)} 行")

            # [P1] 语义化 df_key: sql_{tables}_r{round_index}
            tables_name = "_".join(selected_tables[:2]) if selected_tables else "query"
            # 清理表名中的特殊字符
            tables_name = tables_name.replace(".", "_").replace("-", "_")[:30]
            df_key = f"sql_{tables_name}_r{round_index}"

            # 将结果转换为 DataFrame
            df = pd.DataFrame(data)

            # 直接在结果中返回 artifacts，不修改 self.memory_dfs
            artifacts = {df_key: df}
            if semantic_attempted and workspace_id:
                await semantic_service.record_query_run(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    question=effective_task_description,
                    semantic_enabled=True,
                    fallback_used=True,
                    status="success",
                    error_type=semantic_error_type,
                    intent=None,
                    plan={"fallback_reason": semantic_error_type},
                    sql=sql,
                    referenced_tables=referenced_tables,
                    row_count=len(rows),
                    execution_time_ms=0,
                    legacy_sql=sql,
                )

            # 更新返回结果，包含 df_key 和 artifacts
            return {
                "success": True,
                "sql": sql,
                "rewrite": xiyan_result.rewrite if xiyan_result else "",
                "selected_tables": referenced_tables,
                "referenced_tables": referenced_tables,
                "xiyan_selected_tables": xiyan_result.selected_tables if xiyan_result else [],
                "data": data,
                "columns": clean_columns,
                "row_count": len(rows),
                "result_text": result_text,
                "df_key": df_key,
                "artifacts": artifacts, # [NEW] 显式返回 artifacts
                "ambiguity_warning": ambiguity_warning,  # [3.3] 歧义警告
                "diagnostics": diagnostics,
                "from_example": use_example,  # [NEW] 标记是否来自示例
                "removed_pseudo_source_columns": removed_columns,
            }
                
        except Exception as e:
            logger.error(f"SqlWorker: SQL 执行失败: {e}")
            if _is_repairable_sql_execution_error(str(e), sql or ""):
                await on_step("执行SQL", "running", "检测到 MySQL 方言/分组错误，正在自动修复 SQL...")
                try:
                    repair_prompt = _build_sql_execution_repair_prompt(
                        question=effective_task_description,
                        sql=sql or "",
                        error=str(e),
                        referenced_tables=referenced_tables,
                    )
                    repaired_result = await _generate_with_xiyan(repair_prompt)
                    if repaired_result.success:
                        repaired_sql = _extract_sql_from_text(repaired_result.sql)
                        unsafe_reason = _validate_single_select_sql(repaired_sql)
                        repaired_tables = sorted(_parse_sql_table_refs(repaired_sql))
                        new_tables = set(repaired_tables) - set(referenced_tables)
                        if unsafe_reason:
                            raise RuntimeError(unsafe_reason)
                        if new_tables:
                            raise RuntimeError("修复 SQL 引用了原查询之外的数据表: " + ", ".join(sorted(new_tables)))
                        if _find_missing_tables(repaired_sql, existing_tables):
                            raise RuntimeError("修复 SQL 引用了不存在的数据表")

                        if db_config and db_config.has_readonly_config():
                            executor = ReadOnlyExecutor(user_id, db_config.get_readonly_connection_url())
                            retry_result = executor.execute_query(repaired_sql, timeout_sec=5, max_rows=10000)
                            if retry_result.error:
                                raise RuntimeError(retry_result.error)
                            retry_rows = retry_result.rows
                            retry_columns = list(retry_result.columns)
                        else:
                            with engine.connect() as conn:
                                db_result = conn.execute(text(repaired_sql))
                                retry_rows = db_result.fetchall()
                                retry_columns = list(db_result.keys())

                        clean_columns, clean_rows, removed_columns = _remove_pseudo_source_columns(
                            retry_columns,
                            retry_rows,
                        )
                        data = [dict(zip(clean_columns, row)) for row in clean_rows]
                        diagnostics = analyze_sql_result(
                            question=effective_task_description,
                            columns=clean_columns,
                            rows=clean_rows,
                            expected_limit=expected_limit,
                        )
                        result_text = _build_result_text(
                            clean_columns,
                            clean_rows,
                            row_count=len(retry_rows),
                            ambiguity_warning=None,
                            diagnostics=diagnostics,
                        )
                        tables_name = "_".join(repaired_tables[:2]) if repaired_tables else "query"
                        tables_name = tables_name.replace(".", "_").replace("-", "_")[:30]
                        df_key = f"sql_{tables_name}_r{round_index}"
                        await on_step("执行SQL", "done", f"自动修复后返回 {len(retry_rows)} 行")
                        return {
                            "success": True,
                            "sql": repaired_sql,
                            "original_sql": sql,
                            "sql_repaired": True,
                            "repair_error": str(e),
                            "rewrite": repaired_result.rewrite if repaired_result else "",
                            "selected_tables": repaired_tables,
                            "referenced_tables": repaired_tables,
                            "xiyan_selected_tables": repaired_result.selected_tables if repaired_result else [],
                            "data": data,
                            "columns": clean_columns,
                            "row_count": len(retry_rows),
                            "result_text": result_text,
                            "df_key": df_key,
                            "artifacts": {df_key: pd.DataFrame(data)},
                            "ambiguity_warning": None,
                            "diagnostics": diagnostics,
                            "from_example": False,
                            "removed_pseudo_source_columns": removed_columns,
                        }
                except Exception as repair_exc:  # noqa: BLE001
                    logger.warning("SqlWorker: SQL 自动修复失败: %s", repair_exc)
            await on_step("执行SQL", "error", str(e))
            
            # 返回错误字典（不再挂起询问用户）
            return {
                "success": False,
                "sql": sql,
                "error": f"SQL 执行失败: {str(e)}",
                "error_type": "sql_execution_failed",
            }
    
    async def _detect_data_ambiguity(
        self, 
        task_description: str, 
        data: list, 
        columns: list
    ) -> Optional[str]:
        """
        [3.3 数据歧义检测] Level 2 歧义：检测数据层面的模糊性
        
        检测场景：
        1. 多义词匹配：如查询"苹果"返回多个不同类型的苹果
        2. 相似名称：如查询"王经理"匹配到多个王姓经理
        3. 异常数据量：返回结果过多或过少
        
        Returns:
            歧义警告信息，无歧义返回 None
        """
        if not data or len(data) == 0:
            return None
        
        warnings = []
        
        # 检测1: 名称类字段的多义性
        name_columns = [c for c in columns if any(
            kw in c.lower() for kw in ['name', 'title', '名称', '名字', '产品', '设备', '客户']
        )]
        
        for col in name_columns:
            values = [row.get(col) for row in data if row.get(col)]
            unique_values = list(set(values))
            
            # 如果查询返回多个不同的名称值，可能是多义词
            if len(unique_values) > 1 and len(unique_values) <= 10:
                # 检查是否是多义词情况（同一关键词匹配到不同结果）
                query_keywords = task_description.lower().split()
                matched_values = []
                
                for val in unique_values:
                    val_str = str(val).lower()
                    for kw in query_keywords:
                        if kw in val_str and len(kw) >= 2:
                            matched_values.append(val)
                            break
                
                if len(matched_values) > 1:
                    warnings.append(
                        f"检测到多个匹配项({col}): {', '.join(str(v) for v in matched_values[:5])}"
                    )
        
        # 检测2: 结果数量异常
        if len(data) > 1000:
            warnings.append(f"返回结果较多({len(data)}行)，建议添加筛选条件")
        elif len(data) == 1:
            # 单条结果不一定是歧义，但可以是提示
            pass
        
        # 检测3: 检查是否有明显的分类字段可能需要澄清
        category_columns = [c for c in columns if any(
            kw in c.lower() for kw in ['type', 'category', '类型', '分类', '状态']
        )]
        
        for col in category_columns:
            values = [row.get(col) for row in data if row.get(col)]
            unique_values = list(set(values))
            
            if len(unique_values) > 1 and len(unique_values) <= 5:
                warnings.append(
                    f"结果包含多种{col}: {', '.join(str(v) for v in unique_values)}"
                )
        
        if warnings:
            return "; ".join(warnings)
        
        return None
    
    def format_result_for_synthesizer(self, result: Dict[str, Any]) -> str:
        """
        将执行结果格式化为 Synthesizer 可用的文本
        
        Args:
            result: execute_task 返回的结果
            
        Returns:
            格式化的文本
        """
        if not result.get("success"):
            return f"错误: {result.get('error', '未知错误')}\n\n{result.get('suggestion', '')}"

        referenced_tables = result.get("referenced_tables") or result.get("selected_tables") or []
        referenced_text = ", ".join(str(t) for t in referenced_tables) or "未知"
        
        display_sql = _redact_pseudo_source_literals(str(result.get("sql", "N/A")))
        output = f"""SQL: {display_sql}

改写后的问题: {result.get('rewrite', 'N/A')}
实际引用表: {referenced_text}
数据引用: [{result.get('df_key', 'N/A')}] (共 {result.get('row_count', 0)} 行)
表来源约束: 回答中只能把“实际引用表”作为数据表来源，不要引用 SQL 结果行中的表名/来源列、XiYan 选择器结果、临时 DataFrame 名或历史推断。

结果预览:
{result.get('result_text', '无数据')}"""
        
        return output


# 单例实例
_sql_worker: Optional[SqlWorker] = None


def get_sql_worker() -> SqlWorker:
    """获取 SqlWorker 单例"""
    global _sql_worker
    if _sql_worker is None:
        _sql_worker = SqlWorker()
    return _sql_worker
