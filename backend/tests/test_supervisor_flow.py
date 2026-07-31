"""
Supervisor Flow 测试

验证 Plan -> Confirm -> Execute 完整状态机
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
import json

# 测试导入
import sys
sys.path.insert(0, '.')

from app.entrypoints.admin_main import app


# Mock 的固定 Plan 数据
MOCK_PLAN_RESPONSE = {
    "plan_summary": "测试任务计划",
    "task_plan": [
        {"step_id": "1", "description": "查询销售数据", "worker": "sql_worker", "status": "pending", "result": None},
        {"step_id": "2", "description": "分析数据趋势", "worker": "analyst", "status": "pending", "result": None},
        {"step_id": "3", "description": "生成报表", "worker": "python_worker", "status": "pending", "result": None},
    ],
    "plan_status": "draft",
    "error": None,
}


@pytest.fixture
def mock_supervisor_graph():
    """Mock SupervisorGraph"""
    with patch('app.supervisor.get_supervisor_graph') as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value=MOCK_PLAN_RESPONSE)
        mock_get_graph.return_value = mock_graph
        yield mock_graph


@pytest.fixture
def mock_auth():
    """Mock 认证"""
    with patch('app.api.chat.get_user_context') as mock_get_user:
        from app.models.context import UserContext
        mock_get_user.return_value = UserContext(
            user_id="test_user",
            workspace_id="test_workspace",
            allowed_tables=["*"],
            role="admin"
        )
        yield mock_get_user


@pytest.mark.asyncio
async def test_chat_start_returns_plan(mock_supervisor_graph, mock_auth):
    """
    测试 POST /chat/start 返回任务计划
    
    - 调用 /chat/start 应返回包含 plan 的响应
    - 状态应为 "draft"（等待用户确认）
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/chat/start",
            json={"message": "上个月的销售额是多少？"},
            headers={"Authorization": "Bearer test_token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # 验证返回结构
        assert "session_id" in data
        assert "plan_id" in data
        assert "steps" in data
        assert "status" in data
        
        # 验证状态为 draft（waiting_for_confirmation）
        assert data["status"] == "draft"
        
        # 验证 plan 包含步骤
        assert len(data["steps"]) == 3
        assert data["steps"][0]["worker"] == "sql_worker"
        
        print(f"✓ /chat/start 返回计划成功: {len(data['steps'])} 个步骤")
        return data


@pytest.mark.asyncio
async def test_chat_confirm_executes_plan(mock_supervisor_graph, mock_auth):
    """
    测试 POST /chat/confirm 执行计划
    
    - 先调用 /chat/start 获取 session_id
    - 再调用 /chat/confirm 确认执行
    - 状态应变为 "executing" 或 "completed"
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        # Step 1: 创建 plan
        start_response = await client.post(
            "/api/chat/start",
            json={"message": "分析客户分布"},
            headers={"Authorization": "Bearer test_token"}
        )
        
        assert start_response.status_code == 200
        start_data = start_response.json()
        session_id = start_data["session_id"]
        plan_id = start_data["plan_id"]
        
        print(f"✓ 获取 session_id: {session_id}")
        
        # Step 2: 修改计划并确认
        modified_steps = start_data["steps"]
        # 模拟用户修改：删除第3步
        modified_steps = modified_steps[:2]
        
        confirm_response = await client.post(
            "/api/chat/confirm",
            json={
                "session_id": session_id,
                "plan_id": plan_id,
                "modified_steps": modified_steps
            },
            headers={"Authorization": "Bearer test_token"}
        )
        
        assert confirm_response.status_code == 200
        confirm_data = confirm_response.json()
        
        # 验证状态
        assert confirm_data["status"] in ["executing", "completed"]
        print(f"✓ /chat/confirm 返回状态: {confirm_data['status']}")


@pytest.mark.asyncio  
async def test_full_supervisor_flow(mock_supervisor_graph, mock_auth):
    """
    完整流程测试: Start -> Confirm
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        # 1. 发送消息，获取计划
        start_resp = await client.post(
            "/api/chat/start",
            json={"message": "生成本季度业绩报表"},
            headers={"Authorization": "Bearer test_token"}
        )
        
        assert start_resp.status_code == 200
        plan = start_resp.json()
        
        assert plan["status"] == "draft"
        assert len(plan["steps"]) >= 1
        
        print(f"✓ 步骤1: 收到 {len(plan['steps'])} 步计划")
        
        # 2. 确认执行
        confirm_resp = await client.post(
            "/api/chat/confirm",
            json={
                "session_id": plan["session_id"],
                "plan_id": plan["plan_id"],
            },
            headers={"Authorization": "Bearer test_token"}
        )
        
        assert confirm_resp.status_code == 200
        result = confirm_resp.json()
        
        assert result["status"] in ["executing", "completed"]
        print(f"✓ 步骤2: 计划执行状态 = {result['status']}")
        
        print("\n✅ Supervisor 完整流程测试通过!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
