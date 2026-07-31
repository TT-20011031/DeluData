"""
Supervisor Graph 构建

组装 StateGraph，添加节点和边，编译图
"""
import logging
from functools import lru_cache

from langgraph.graph import END, StateGraph

from app.core.db.checkpointer import MySQLSaver
from app.supervisor.edges import (
    route_after_executor,
    route_after_intent,
    route_after_resume,
    route_after_router_agent,
    should_continue_after_plan,
    should_continue_after_review,
)
from app.supervisor.nodes import (
    direct_execute_node,
    executor_node,
    human_review_node,
    intent_classifier_node,
    knowledge_router_node,
    planner_node,
    router_agent_node,
    summarizer_node,
    suspend_node,
    synthesizer_node,
)
from app.supervisor.state import SupervisorState

logger = logging.getLogger(__name__)


def create_supervisor_graph():
    """创建 Supervisor Graph（Worker 自主闭环版）"""
    workflow = StateGraph(SupervisorState)

    workflow.add_node("intent_classifier", intent_classifier_node)
    workflow.add_node("knowledge_router", knowledge_router_node)  # [M3.1] Wiki-First 路由
    workflow.add_node("planner", planner_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("router_agent", router_agent_node)
    workflow.add_node("suspend", suspend_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("summarizer", summarizer_node)
    workflow.add_node("direct_execute", direct_execute_node)

    workflow.set_entry_point("intent_classifier")

    workflow.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {
            "knowledge_router": "knowledge_router",  # [M3.1] tool_use 先经路由
            "synthesizer": "synthesizer",
            "direct_execute": "direct_execute",
        },
    )

    # [M3.1] knowledge_router 仅决定路径，决策完成后无条件进入 planner
    workflow.add_edge("knowledge_router", "planner")

    workflow.add_edge("direct_execute", "synthesizer")

    workflow.add_conditional_edges(
        "planner",
        should_continue_after_plan,
        {
            "suspend": "suspend",
            "human_review": "human_review",
            "executor": "executor",
            "synthesizer": "synthesizer",
            "end": END,
        },
    )

    workflow.add_conditional_edges(
        "human_review",
        should_continue_after_review,
        {
            "executor": "executor",
            "end": END,
        },
    )

    workflow.add_conditional_edges(
        "executor",
        route_after_executor,
        {
            "suspend": "suspend",
            "router_agent": "router_agent",
            "executor": "executor",
            "synthesizer": "synthesizer",
        },
    )

    workflow.add_conditional_edges(
        "router_agent",
        route_after_router_agent,
        {
            "executor": "executor",
            "suspend": "suspend",
            "synthesizer": "synthesizer",
        },
    )

    workflow.add_conditional_edges(
        "suspend",
        route_after_resume,
        {
            "end": END,
            "planner": "planner",
            "executor": "executor",
        },
    )

    workflow.add_edge("synthesizer", "summarizer")
    workflow.add_edge("summarizer", END)

    checkpointer = MySQLSaver()
    return workflow.compile(checkpointer=checkpointer, interrupt_before=["human_review"])


@lru_cache(maxsize=1)
def get_supervisor_graph():
    """获取 Supervisor Graph 单例（线程安全）"""
    return create_supervisor_graph()
