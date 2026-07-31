"""
Supervisor Nodes 模块

包含所有节点函数的实现
"""
from app.supervisor.nodes.planner import planner_node
from app.supervisor.nodes.executor import executor_node, execute_worker_task
from app.supervisor.nodes.synthesizer import synthesizer_node
from app.supervisor.nodes.router import router_agent_node
from app.supervisor.nodes.common import human_review_node, suspend_node, summarizer_node
from app.supervisor.nodes.intent_classifier import intent_classifier_node
from app.supervisor.nodes.direct_execute import direct_execute_node  # [NEW] 直连执行节点
from app.supervisor.nodes.knowledge_router import knowledge_router_node  # [M3.1] Wiki-First 路由

__all__ = [
    "intent_classifier_node",
    "planner_node",
    "executor_node",
    "execute_worker_task",
    "synthesizer_node",
    "router_agent_node",
    "human_review_node",
    "suspend_node",
    "summarizer_node",
    "direct_execute_node",  # [NEW]
    "knowledge_router_node",  # [M3.1]
]

