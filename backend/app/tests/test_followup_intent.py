from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.supervisor.focus_result import (
    FocusResultSnapshot,
    build_direct_answer_payload,
    dump_focus_result,
    resolve_focus_result,
)
from app.supervisor.nodes.common import summarizer_node
from app.supervisor.nodes.intent_classifier import intent_classifier_node
from app.supervisor.nodes.synthesizer import _handle_direct_response
from app.services.chat_service import ChatService


def _build_focus_result() -> dict:
    snapshot = FocusResultSnapshot(
        key="df_1_sql_order_r1",
        source_kind="dataframe",
        source_worker="sql_worker",
        round_index=1,
        row_count=2,
        column_count=3,
        columns=["销售单号", "客户名称", "来料数量"],
        preview_rows=[
            {"销售单号": "SO001", "客户名称": "客户A", "来料数量": 12},
            {"销售单号": "SO002", "客户名称": "客户B", "来料数量": 8},
        ],
        preview_text='[{"销售单号":"SO001","客户名称":"客户A","来料数量":12}]',
        summary="订单来料汇总，共 2 行。",
        query="查询订单的来料汇总数据",
    )
    return dump_focus_result(snapshot)


def _build_sql_source_focus_result() -> dict:
    snapshot = FocusResultSnapshot(
        key="df_1_sql_inventory_turnover_r1",
        source_kind="dataframe",
        source_worker="sql_worker",
        round_index=1,
        row_count=3,
        column_count=4,
        columns=["货号", "当前库存", "近180天出库总量", "库存周转率"],
        preview_rows=[
            {"货号": "N701", "当前库存": 5013230, "近180天出库总量": 0, "库存周转率": 0},
        ],
        preview_text='[{"货号":"N701","当前库存":5013230,"近180天出库总量":0,"库存周转率":0}]',
        summary="库存周转率排行，共 3 行。",
        query="近180天内库存周转率最差的三个商品",
        source_tables=["clean_jf_out_store_detail", "clean_jf_now_details"],
        source_sql=(
            "SELECT n.item_no AS 货号, n.number AS 当前库存, SUM(o.out_qty) AS 近180天出库总量, "
            "SUM(o.out_qty) / n.number AS 库存周转率 "
            "FROM clean_jf_out_store_detail o JOIN clean_jf_now_details n ON o.item_no = n.item_no "
            "WHERE n.number > 0 GROUP BY n.item_no, n.number ORDER BY 库存周转率 ASC LIMIT 3"
        ),
        source_rewrite="查询近180天库存周转率最差的三个商品",
    )
    return dump_focus_result(snapshot)


def _build_settings() -> SimpleNamespace:
    return SimpleNamespace(
        supervisor=SimpleNamespace(chitchat_keywords_list=["你好", "谢谢"]),
        llm=SimpleNamespace(fast_model="test-fast-model"),
    )


def _build_state(user_query: str, *, current_focus_result: dict | None = None, user_context: dict | None = None) -> dict:
    return {
        "execution_mode": "auto",
        "user_query": user_query,
        "summary": "上一轮已经查询订单来料汇总，共 17 行。",
        "user_context": user_context or {"user_id": "u-1", "workspace_id": "default"},
        "messages": [
            HumanMessage(content="查询订单的来料汇总数据"),
            AIMessage(content="已返回 17 行订单来料汇总数据。"),
            HumanMessage(content=user_query),
        ],
        "current_focus_result": current_focus_result or {},
    }


class _StubUserContext:
    def __init__(self, *, user_id: str = "u-1", workspace_id: str = "default") -> None:
        self.user_id = user_id
        self.workspace_id = workspace_id

    def model_dump(self) -> dict:
        return {"user_id": self.user_id, "workspace_id": self.workspace_id}


async def _stream_chunks(*chunks: str):
    for chunk in chunks:
        yield chunk


def _build_chat_service_settings(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        sandbox=SimpleNamespace(base_dir=str(tmp_path)),
        llm=SimpleNamespace(
            final_reply_model_flash="flash-model",
            final_reply_model_plus="plus-model",
            final_reply_model_default_key="flash",
            final_reply_model="flash-model",
        ),
    )


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_fallback_requires_exact_intent_token(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.side_effect = RuntimeError("schema failed")
    mock_llm.chat.return_value = "不是 direct_answer，而是 tool_use，因为需要导出文件。"
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("把这份结果导出成 Excel", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_routes_followup_to_direct_answer(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {
        "intent_type": "direct_answer",
        "is_followup_to_existing_result": True,
        "has_sufficient_session_context": True,
        "requires_new_tool": False,
        "reason": "用户在继续追问当前结果，现有结果足够回答。",
    }
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("看一下数据都是什么", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "direct_answer", "skip_planner": True}
    prompt_text = mock_llm.generate_structured.call_args.kwargs["messages"][0]["content"]
    assert "当前正在讨论的结果" in prompt_text
    assert "df_1_sql_order_r1" in prompt_text
    assert "销售单号" in prompt_text


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_routes_new_query_to_tool_use(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {
        "intent_type": "tool_use",
        "is_followup_to_existing_result": True,
        "has_sufficient_session_context": False,
        "requires_new_tool": True,
        "reason": "用户要求重新筛选本周数据，需要新的执行动作。",
    }
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("再查一下本周的来料汇总", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_routes_to_tool_use_when_context_is_not_enough(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {
        "intent_type": "direct_answer",
        "is_followup_to_existing_result": True,
        "has_sufficient_session_context": False,
        "requires_new_tool": False,
        "reason": "当前结果只有摘要，无法直接回答用户要的细节。",
    }
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("第五列所有值有哪些", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_allows_summary_only_direct_answer(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {
        "intent_type": "direct_answer",
        "is_followup_to_existing_result": False,
        "has_sufficient_session_context": True,
        "requires_new_tool": False,
        "reason": "问题可以仅基于已有对话摘要回答。",
    }
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("你刚刚说一共多少行", current_focus_result={})
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "direct_answer", "skip_planner": True}


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_short_circuits_chitchat(
    mock_get_settings,
    mock_get_async_llm,
):
    mock_get_settings.return_value = _build_settings()
    state = _build_state("你好")

    result = await intent_classifier_node(state)

    assert result == {"intent_type": "chitchat", "skip_planner": True}
    mock_get_async_llm.assert_not_called()


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_routes_source_followup_to_direct_answer(
    mock_get_settings,
    mock_get_async_llm,
):
    mock_get_settings.return_value = _build_settings()
    state = _build_state(
        "当前库存信息出自哪张表",
        current_focus_result=_build_sql_source_focus_result(),
    )

    result = await intent_classifier_node(state)

    assert result == {"intent_type": "direct_answer", "skip_planner": True}
    mock_get_async_llm.assert_not_called()


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_short_circuits_uploaded_asset(
    mock_get_settings,
    mock_get_async_llm,
):
    mock_get_settings.return_value = _build_settings()
    state = _build_state(
        "帮我看一下这个文件",
        user_context={
            "user_id": "u-1",
            "workspace_id": "default",
            "file_path": "sandbox/input.xlsx",
        },
    )

    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}
    mock_get_async_llm.assert_not_called()


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_requires_new_tool_takes_precedence_over_direct_answer_label(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=False)
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {
        "intent_type": "direct_answer",
        "is_followup_to_existing_result": True,
        "has_sufficient_session_context": True,
        "requires_new_tool": True,
        "reason": "用户要求导出当前结果，需要新的执行动作。",
    }
    mock_get_async_llm.return_value = mock_llm

    state = _build_state("把这份结果导出成 Excel", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_respects_always_confirm_preference(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_user_config,
):
    mock_get_settings.return_value = _build_settings()
    mock_get_user_config.return_value = SimpleNamespace(always_confirm=True)

    state = _build_state("这些字段是什么意思", current_focus_result=_build_focus_result())
    result = await intent_classifier_node(state)

    assert result == {"intent_type": "tool_use", "skip_planner": False}
    mock_get_async_llm.assert_not_called()


@pytest.mark.asyncio
@patch("app.supervisor.nodes.intent_classifier.get_async_llm")
@patch("app.supervisor.nodes.intent_classifier.get_settings")
async def test_intent_classifier_short_circuits_explicit_execution_mode(
    mock_get_settings,
    mock_get_async_llm,
):
    mock_get_settings.return_value = _build_settings()
    state = _build_state("直接查订单数据", current_focus_result=_build_focus_result())
    state["execution_mode"] = "sql_only"

    result = await intent_classifier_node(state)

    assert result == {"intent_type": "direct_execution", "skip_planner": False}
    mock_get_async_llm.assert_not_called()


def test_resolve_focus_result_builds_snapshot_from_new_dataframe():
    pending_artifacts = {
        "df_1_sql_order_r2": pd.DataFrame(
            {
                "销售单号": ["SO003", "SO004"],
                "客户名称": ["客户C", "客户D"],
                "来料数量": [5, 9],
            }
        )
    }

    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={},
        pending_artifacts=pending_artifacts,
        execution_results=[
            {
                "step_id": "1",
                "worker": "sql_worker",
                "result": "数据引用: [df_1_sql_order_r2] (共 2 行)",
            }
        ],
        user_query="查询最新订单来料汇总",
        round_index=2,
    )

    assert focus_result["key"] == "df_1_sql_order_r2"
    assert focus_result["row_count"] == 2
    assert focus_result["column_count"] == 3
    assert focus_result["columns"] == ["销售单号", "客户名称", "来料数量"]


def test_resolve_focus_result_persists_sql_source_metadata():
    pending_artifacts = {
        "df_1_sql_inventory_turnover_r1": pd.DataFrame(
            {
                "货号": ["N701"],
                "当前库存": [5013230],
                "近180天出库总量": [0],
                "库存周转率": [0],
            }
        )
    }

    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={},
        pending_artifacts=pending_artifacts,
        execution_results=[
            {
                "step_id": "1",
                "worker": "sql_worker",
                "result": (
                    "SQL: SELECT n.item_no AS 货号, n.number AS 当前库存 "
                    "FROM clean_jf_out_store_detail o JOIN clean_jf_now_details n ON o.item_no = n.item_no\n\n"
                    "改写后的问题: 查询近180天库存周转率最差的三个商品\n"
                    "实际引用表: clean_jf_out_store_detail, clean_jf_now_details\n"
                    "数据引用: [df_1_sql_inventory_turnover_r1] (共 1 行)"
                ),
            }
        ],
        user_query="近180天内库存周转率最差的三个商品",
        round_index=1,
    )

    assert focus_result["source_tables"] == ["clean_jf_out_store_detail", "clean_jf_now_details"]
    assert "n.number AS 当前库存" in focus_result["source_sql"]
    assert focus_result["source_rewrite"] == "查询近180天库存周转率最差的三个商品"


def test_resolve_focus_result_prefers_explicitly_referenced_dataframe():
    pending_artifacts = {
        "df_1_sql_order_r2": pd.DataFrame(
            {
                "销售单号": ["SO003", "SO004"],
                "客户名称": ["客户C", "客户D"],
                "来料数量": [5, 9],
            }
        ),
        "df_2_sql_other_r2": pd.DataFrame(
            {
                "销售单号": ["SO888"],
                "客户名称": ["客户X"],
                "来料数量": [1],
            }
        ),
    }

    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={},
        pending_artifacts=pending_artifacts,
        execution_results=[
            {
                "step_id": "1",
                "worker": "sql_worker",
                "result": "数据引用: [df_1_sql_order_r2] (共 2 行)",
            }
        ],
        user_query="查询最新订单来料汇总",
        round_index=2,
    )

    assert focus_result["key"] == "df_1_sql_order_r2"
    assert focus_result["row_count"] == 2


def test_resolve_focus_result_prefers_new_pending_result_over_existing_focus():
    existing_focus = _build_focus_result()
    pending_artifacts = {
        "df_2_sql_order_r3": pd.DataFrame(
            {
                "销售单号": ["SO009"],
                "客户名称": ["客户Z"],
                "来料数量": [99],
            }
        )
    }

    focus_result = resolve_focus_result(
        current_focus_raw=existing_focus,
        memory_dfs={
            "df_1_sql_order_r1": pd.DataFrame(
                {
                    "销售单号": ["SO001"],
                    "客户名称": ["客户A"],
                    "来料数量": [12],
                }
            )
        },
        pending_artifacts=pending_artifacts,
        execution_results=[
            {
                "step_id": "2",
                "worker": "sql_worker",
                "result": "数据引用: [df_2_sql_order_r3] (共 1 行)",
            }
        ],
        user_query="查询新的订单来料汇总",
        round_index=3,
    )

    assert focus_result["key"] == "df_2_sql_order_r3"
    assert focus_result["summary"] == "数据引用: [df_2_sql_order_r3] (共 1 行)"


def test_resolve_focus_result_ignores_auxiliary_file_artifacts():
    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={},
        pending_artifacts={
            "df_1_file_name": "input.xlsx",
            "df_1_file_path": "sandbox/input.xlsx",
            "df_1_file_type": "Excel",
            "df_1_columns": ["销售单号", "客户名称", "来料数量"],
            "df_1_row_count": 17,
            "df_1_preview_rows": 5,
        },
        execution_results=[
            {
                "step_id": "1",
                "worker": "inspect_file_worker",
                "result": "已预览 Excel 文件，共 17 行",
            }
        ],
        user_query="帮我看一下这个文件",
        round_index=1,
    )

    assert focus_result == {}


def test_resolve_focus_result_falls_back_to_latest_memory_result():
    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={
            "template_preview": {"title": "ignore"},
            "df_1_sql_order_r1": pd.DataFrame(
                {
                    "销售单号": ["SO001"],
                    "客户名称": ["客户A"],
                    "来料数量": [12],
                }
            ),
            "df_3_sql_order_r5": pd.DataFrame(
                {
                    "销售单号": ["SO010"],
                    "客户名称": ["客户K"],
                    "来料数量": [20],
                }
            ),
        },
        pending_artifacts={},
        execution_results=[],
        user_query="这些数据再解释一下",
        round_index=5,
    )

    assert focus_result["key"] == "df_3_sql_order_r5"
    assert focus_result["columns"] == ["销售单号", "客户名称", "来料数量"]


def test_resolve_focus_result_prefers_meta_selected_data_source():
    focus_result = resolve_focus_result(
        current_focus_raw={},
        memory_dfs={
            "df_1_sales_r1": pd.DataFrame({"销售额": [100]}),
            "df_2_inventory_r1": pd.DataFrame({"库存": [9]}),
        },
        pending_artifacts={},
        execution_results=[
            {
                "step_id": "3",
                "worker": "chart_worker",
                "result": "已生成数据可视化图表（共 1 条数据）。",
                "quality_signal": {"verdict": "pass", "reason_code": "chart_generated"},
                "meta": {"selected_data_source": "df_1_sales_r1"},
            }
        ],
        user_query="把销售数据画成图",
        round_index=1,
    )

    assert focus_result["key"] == "df_1_sales_r1"
    assert focus_result["columns"] == ["销售额"]


def test_build_direct_answer_payload_includes_focus_result_preview():
    payload = build_direct_answer_payload(
        summary="上一轮已经查询订单来料汇总，共 17 行。",
        recent_dialogue="- [助手] 已返回 17 行订单来料汇总数据。",
        user_query="这些字段都是什么意思",
        focus_result=FocusResultSnapshot.model_validate(_build_focus_result()),
        memory_dfs={
            "df_1_sql_order_r1": pd.DataFrame(
                {
                    "销售单号": ["SO001", "SO002"],
                    "客户名称": ["客户A", "客户B"],
                    "来料数量": [12, 8],
                }
            )
        },
    )

    assert "最近对话" in payload
    assert "当前正在讨论的结果" in payload
    assert "销售单号" in payload
    assert "客户名称" in payload
    assert "SO001" in payload


def test_build_direct_answer_payload_includes_sql_source_metadata():
    payload = build_direct_answer_payload(
        summary="上一轮查询了近180天库存周转率。",
        recent_dialogue="- [用户] 当前库存信息出自哪张表",
        user_query="当前库存信息出自哪张表",
        focus_result=FocusResultSnapshot.model_validate(_build_sql_source_focus_result()),
        memory_dfs={},
    )

    assert "实际引用表" in payload
    assert "clean_jf_now_details" in payload
    assert "来源SQL" in payload
    assert "n.number AS 当前库存" in payload


def test_build_direct_answer_payload_uses_snapshot_preview_when_memory_missing():
    payload = build_direct_answer_payload(
        summary="上一轮已经查询订单来料汇总，共 17 行。",
        recent_dialogue="无",
        user_query="这些数据都是什么",
        focus_result=FocusResultSnapshot.model_validate(_build_focus_result()),
        memory_dfs={},
    )

    assert '{"销售单号":"SO001"' in payload
    assert "当前结果详情" in payload


@pytest.mark.asyncio
@patch("app.supervisor.nodes.synthesizer.emit_message_end", new_callable=AsyncMock)
@patch("app.supervisor.nodes.synthesizer.emit_message_chunk", new_callable=AsyncMock)
@patch("app.supervisor.nodes.synthesizer.get_async_llm")
@patch("app.supervisor.nodes.synthesizer.get_settings")
async def test_handle_direct_response_includes_recent_dialogue_in_prompt(
    mock_get_settings,
    mock_get_async_llm,
    mock_emit_chunk,
    mock_emit_end,
):
    mock_get_settings.return_value = _build_settings()
    mock_llm = SimpleNamespace(chat_stream=Mock(return_value=_stream_chunks("字段说明")))
    mock_get_async_llm.return_value = mock_llm

    state = {
        "summary": "上一轮已经查询订单来料汇总，共 17 行。",
        "user_query": "第二个字段是什么意思",
        "intent_type": "direct_answer",
        "messages": [
            HumanMessage(content="查询订单的来料汇总数据"),
            AIMessage(content="字段依次是销售单号、客户名称、来料数量。"),
            HumanMessage(content="第二个字段是什么意思"),
        ],
        "current_focus_result": _build_focus_result(),
        "memory_dfs": {
            "df_1_sql_order_r1": pd.DataFrame(
                {
                    "销售单号": ["SO001"],
                    "客户名称": ["客户A"],
                    "来料数量": [12],
                }
            )
        },
    }

    result = await _handle_direct_response(state)

    assert result["final_answer"] == "字段说明"
    prompt_messages = mock_llm.chat_stream.call_args.args[0]
    assert "最近对话" in prompt_messages[1]["content"]
    assert "字段依次是销售单号、客户名称、来料数量。" in prompt_messages[1]["content"]
    mock_emit_chunk.assert_not_awaited()
    mock_emit_end.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("graph_result", "expected_message"),
    [
        (
            {
                "task_plan": [],
                "final_answer": "直接回答完成",
                "intent_type": "direct_answer",
                "round_index": 1,
            },
            "直接回答完成",
        ),
        (
            {
                "task_plan": [{"step_id": "1", "worker": "sql_worker"}],
                "plan_summary": "",
                "final_answer": "直连执行完成",
                "is_direct_execution": True,
                "round_index": 1,
            },
            "直连执行完成",
        ),
    ],
)
@patch("app.services.chat_service.emit_message_end", new_callable=AsyncMock)
@patch("app.services.chat_service.get_settings")
@patch("app.services.skill_retriever.prepare_skill_state")
@patch("app.services.skill_retriever.retrieve_skill_for_query", new_callable=AsyncMock)
@patch("app.templates_config.synthesizer_templates.resolve_template_prompt", new_callable=AsyncMock)
@patch("app.models.config.user_agent_config.get_user_agent_config_async", new_callable=AsyncMock)
@patch("app.services.workspace_readiness_service.WorkspaceReadinessService")
@patch("app.supervisor.get_supervisor_graph")
async def test_start_new_session_success_does_not_emit_message_end(
    mock_get_graph,
    mock_readiness_service_cls,
    mock_get_user_agent_config,
    mock_resolve_template_prompt,
    mock_retrieve_skill,
    mock_prepare_skill_state,
    mock_get_settings,
    mock_emit_message_end,
    graph_result,
    expected_message,
    tmp_path,
):
    mock_get_settings.return_value = _build_chat_service_settings(tmp_path)
    mock_get_user_agent_config.return_value = SimpleNamespace(
        execution_mode=SimpleNamespace(value="auto"),
        synthesizer_template="default",
        synthesizer_custom_prompt="",
    )
    mock_resolve_template_prompt.return_value = ""
    mock_retrieve_skill.return_value = (None, None, "", [])
    mock_prepare_skill_state.return_value = {}

    mock_readiness_service = mock_readiness_service_cls.return_value
    mock_readiness_service.get_workspace_readiness = AsyncMock(
        return_value=SimpleNamespace(has_db=False, has_knowledge=False, reasons=[])
    )

    graph = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(values={}, next=[])),
        ainvoke=AsyncMock(return_value=graph_result),
    )
    mock_get_graph.return_value = graph

    result = await ChatService().start_new_session(
        message="帮我处理一下",
        user_context=_StubUserContext(),
    )

    assert result["status"] == "completed"
    assert result["message"] == expected_message
    mock_emit_message_end.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.services.chat_service.emit_message_end", new_callable=AsyncMock)
@patch("app.services.chat_service.event_queue.publish", new_callable=AsyncMock)
@patch("app.supervisor.get_supervisor_graph")
async def test_confirm_and_execute_success_does_not_emit_message_end(
    mock_get_graph,
    mock_publish,
    mock_emit_message_end,
):
    graph = SimpleNamespace(
        aupdate_state=AsyncMock(),
        aget_state=AsyncMock(
            return_value=SimpleNamespace(
                values={"task_plan": [], "round_index": 2, "plan_status": "confirmed"},
                next=[],
            )
        ),
        ainvoke=AsyncMock(return_value={"final_answer": "执行完成", "plan_status": "completed"}),
    )
    mock_get_graph.return_value = graph

    await ChatService().confirm_and_execute(
        session_id="sid-1",
        plan_id="plan-1",
        user_context=_StubUserContext(),
    )

    mock_emit_message_end.assert_not_awaited()
    assert mock_publish.await_count == 2


@pytest.mark.asyncio
@patch("app.services.chat_service.emit_message_end", new_callable=AsyncMock)
@patch("app.services.chat_service.event_queue.publish", new_callable=AsyncMock)
@patch("app.supervisor.get_supervisor_graph")
async def test_resume_session_success_does_not_emit_message_end(
    mock_get_graph,
    mock_publish,
    mock_emit_message_end,
):
    graph = SimpleNamespace(
        aupdate_state=AsyncMock(),
        ainvoke=AsyncMock(
            return_value={
                "final_answer": "恢复执行完成",
                "plan_status": "completed",
                "task_plan": [],
                "plan_summary": "",
                "plan_id": "plan-1",
            }
        ),
    )
    mock_get_graph.return_value = graph

    await ChatService().resume_session(
        session_id="sid-1",
        plan_id="plan-1",
        user_input="继续",
        user_context=_StubUserContext(),
    )

    mock_emit_message_end.assert_not_awaited()
    assert mock_publish.await_count == 1


def test_resolve_focus_result_keeps_existing_focus_when_no_new_data():
    existing_focus = _build_focus_result()

    focus_result = resolve_focus_result(
        current_focus_raw=existing_focus,
        memory_dfs={},
        pending_artifacts={},
        execution_results=[],
        user_query="这些字段分别是什么意思",
        round_index=4,
    )

    assert focus_result["key"] == existing_focus["key"]
    assert focus_result["row_count"] == existing_focus["row_count"]


def test_resolve_focus_result_keeps_existing_focus_when_new_artifacts_are_auxiliary():
    existing_focus = _build_focus_result()

    focus_result = resolve_focus_result(
        current_focus_raw=existing_focus,
        memory_dfs={
            "df_1_sql_order_r1": pd.DataFrame(
                {
                    "销售单号": ["SO001"],
                    "客户名称": ["客户A"],
                    "来料数量": [12],
                }
            ),
            "df_9_sql_other_r9": pd.DataFrame(
                {
                    "销售单号": ["SO999"],
                    "客户名称": ["客户Y"],
                    "来料数量": [1],
                }
            ),
        },
        pending_artifacts={
            "df_2_output_files": ["report.xlsx"],
            "df_2_code": "print(1)",
            "df_2_template_context": {"title": "月报", "fields": ["客户", "金额"]},
        },
        execution_results=[
            {
                "step_id": "2",
                "worker": "office_worker",
                "result": "已生成报表文件",
            }
        ],
        user_query="把它导出成 Excel",
        round_index=2,
    )

    assert focus_result["key"] == existing_focus["key"]
    assert focus_result["summary"] == existing_focus["summary"]


@pytest.mark.asyncio
@patch("app.supervisor.nodes.common.get_prompt")
@patch("app.supervisor.nodes.common.get_async_llm")
@patch("app.config.get_settings")
async def test_summarizer_keeps_focused_dataframe(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_prompt,
):
    mock_get_settings.return_value = SimpleNamespace(app=SimpleNamespace(keep_count=2))
    mock_get_prompt.return_value = "请压缩摘要"
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = "新的摘要"
    mock_get_async_llm.return_value = mock_llm

    state = {
        "messages": [
            HumanMessage(content="第一轮查了订单数据", id="m1"),
            AIMessage(content="已返回结果", id="m2"),
            HumanMessage(content="这些字段是什么意思", id="m3"),
            AIMessage(content="我来解释字段含义", id="m4"),
        ],
        "summary": "旧摘要",
        "memory_dfs": {
            "df_1_sql_order_r1": pd.DataFrame({"销售单号": ["SO001"]}),
            "df_2_unused_r2": pd.DataFrame({"无关字段": ["X"]}),
        },
        "current_focus_result": _build_focus_result(),
        "active_assets": [],
        "round_index": 3,
    }

    result = await summarizer_node(state)

    assert "df_1_sql_order_r1" in result["memory_dfs"]
    assert "df_2_unused_r2" not in result["memory_dfs"]


@pytest.mark.asyncio
@patch("app.supervisor.nodes.common.get_prompt")
@patch("app.supervisor.nodes.common.get_async_llm")
@patch("app.config.get_settings")
async def test_summarizer_clears_invalid_focus_result(
    mock_get_settings,
    mock_get_async_llm,
    mock_get_prompt,
):
    mock_get_settings.return_value = SimpleNamespace(app=SimpleNamespace(keep_count=2))
    mock_get_prompt.return_value = "请压缩摘要"
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = "新的摘要"
    mock_get_async_llm.return_value = mock_llm

    state = {
        "messages": [
            HumanMessage(content="第一轮查了订单数据", id="m1"),
            AIMessage(content="已返回结果", id="m2"),
            HumanMessage(content="后面继续追问", id="m3"),
            AIMessage(content="继续回答", id="m4"),
        ],
        "summary": "旧摘要",
        "memory_dfs": {
            "df_1_sql_order_r1": pd.DataFrame({"销售单号": ["SO001"]}),
        },
        "current_focus_result": {"bad": "snapshot"},
        "active_assets": [],
        "round_index": 3,
    }

    result = await summarizer_node(state)

    assert result["current_focus_result"] == {}
