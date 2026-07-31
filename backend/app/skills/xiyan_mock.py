"""
DeluData 智能问数系统 - XiYan Mock 模块

🎭 模拟 XiYan GBI 的 SSE 事件流，用于测试前端
设置环境变量 XIYAN_MOCK=true 启用

后续清理时直接删除此文件，并移除 xiyan_skill.py 中的相关引用即可
"""
import asyncio
import logging
from typing import Optional, Callable, Any, List

from app.skills.xiyan_skill import XiYanResult, XiYanStep

logger = logging.getLogger(__name__)


async def generate_sql_mock(
    question: str,
    step_callback: Optional[Callable] = None,
    safe_callback: Optional[Callable] = None,
    parent_step_id: Optional[str] = None  # 新增：接收父步骤 ID
) -> XiYanResult:
    """
    🎭 模拟模式：返回模拟的 XiYan SSE 事件流
    
    用于测试前端 SSE 事件处理，避免消耗真实 API 调用次数
    
    Args:
        question: 用户问题
        step_callback: 步骤回调函数 (name, status, detail)
        safe_callback: 安全调用回调的函数 (来自 XiYanSkill._safe_callback)
        parent_step_id: 父步骤 ID，用于前端将子步骤挂载到正确的主任务下
    """
    steps: List[XiYanStep] = []
    
    async def emit(name: str, status: str, detail: str):
        """发送步骤事件 (自动添加 xiyan- 前缀)"""
        if step_callback:
            # 构造符合前端要求的 ID: xiyan-{name}
            mock_step_id = f"xiyan-{name}"
            
            if safe_callback:
                # safe_callback 签名: (callback, name, status, detail)
                # 但我们需要传递完整的 step_id 和 parent_step_id
                # 这里直接调用 emit_step_update
                from app.api.events import emit_step_update
                # 从 step_callback 的闭包获取 session_id
                # 注意：原 step_callback 是 on_step，签名为 (name, status, detail)
                # 我们需要绕过它，直接使用 emit_step_update
                pass  # 见下方实现
            
            # 直接使用回调，让 sql_worker 的 on_step 处理
            # on_step 会自动添加 xiyan- 前缀
            if asyncio.iscoroutinefunction(step_callback):
                await step_callback(name, status, detail)
            else:
                step_callback(name, status, detail)
    
    # 模拟步骤 1: 问题改写
    await emit("问题改写", "running", "分析用户问题...")
    await asyncio.sleep(0.5)  # 模拟延迟
    
    rewrite = f"查询{question}的相关数据"
    steps.append(XiYanStep("问题改写", "done", rewrite))
    await emit("问题改写", "done", rewrite)
    
    # 模拟步骤 2: 数据表选取
    await emit("数据表选取", "running", "分析相关数据表...")
    await asyncio.sleep(0.5)
    
    selected_tables = ["clean_incoming_material_acceptance_detail"]
    table_names = ", ".join(selected_tables)
    steps.append(XiYanStep("数据表选取", "done", table_names))
    await emit("数据表选取", "done", table_names)
    
    # 模拟步骤 3: SQL生成
    await emit("SQL生成", "running", "生成 SQL 语句...")
    await asyncio.sleep(0.5)
    
    mock_sql = """SELECT 
    a.incoming_date AS '制单日期',
    a.source_code AS '订单单号',
    a.spec AS '规格',
    a.customer_po AS '客户PO',
    a.customer_name AS '客户名称',
    a.received_number AS '实收数量',
    a.gross_weight AS '毛重',
    a.net_weight AS '净重',
    a.remark AS '备注'
FROM clean_incoming_material_acceptance_detail a
WHERE a.incoming_date >= DATE_SUB(CURDATE(), INTERVAL 1 MONTH)
ORDER BY a.incoming_date DESC"""
    
    steps.append(XiYanStep("SQL生成", "done", mock_sql[:100] + "..."))
    await emit("SQL生成", "done", "SQL 生成完毕")
    
    return XiYanResult(
        success=True,
        sql=mock_sql,
        rewrite=rewrite,
        selected_tables=selected_tables,
        steps=steps
    )
