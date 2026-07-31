import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd
from app.agents.state import AgentState, Task, ExecutionResult, QueryResult
from app.agents.supervisor import Supervisor, supervisor_node
from app.agents.sql_worker import SqlWorker, sql_worker_node
from app.skills.python_skill import PythonSkill, python_worker_node
from app.models.common.context import UserContext

# Mock UserContext
@pytest.fixture
def mock_user_context():
    return UserContext(
        user_id="test_user",
        tenant_id="test_tenant",
        username="tester",
        roles=["admin"],
        allowed_tables=["test_table"]
    )

@pytest.fixture
def mock_state(mock_user_context):
    return AgentState(
        user_context=mock_user_context,
        messages=[],
        plan=[],
        memory_dfs={},
        artifacts={},
        error_trace=None,
        current_step="supervisor",
        thinking_logs=[]
    )

# --- Test PythonSkill ---
@pytest.mark.asyncio
async def test_python_skill_security():
    skill = PythonSkill()
    
    # Test valid code
    code = "result = 1 + 1"
    res = await skill.exec_python(code, {})
    assert res.success
    assert res.output == 2
    
    # Test forbidden import
    code = "import os"
    res = await skill.exec_python(code, {})
    assert not res.success
    assert "禁止导入模块" in res.error

@pytest.mark.asyncio
async def test_python_skill_dataframe():
    skill = PythonSkill()
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    
    code = "result = df['a'].sum()"
    res = await skill.exec_python(code, {"df": df})
    assert res.success
    assert res.output == 3

# --- Test Supervisor Node ---
@pytest.mark.asyncio
@patch("app.agents.supervisor.get_llm_client")
async def test_supervisor_node(mock_get_llm, mock_state):
    # Mock LLM response for decision
    mock_llm_instance = AsyncMock()
    mock_llm_instance.generate_json.return_value = {
        "thinking": "Need data from DB",
        "next_worker": "sql_worker",
        "task_description": "Query sales data",
        "message_to_user": "Checking database..."
    }
    mock_get_llm.return_value = mock_llm_instance
    
    config = {"configurable": {"session_id": "test_session"}}
    new_state = await supervisor_node(mock_state, config)
    
    assert new_state["current_step"] == "sql_worker"
    assert len(new_state["plan"]) == 1
    assert new_state["plan"][0]["type"] == "sql"
    assert new_state["plan"][0]["status"] == "pending"

# --- Test SqlWorker Node ---
@pytest.mark.asyncio
@patch("app.agents.sql_worker.get_llm_client")
@patch("app.agents.sql_worker.get_db_manager")
@patch("app.agents.sql_worker.SqlSkill")
@patch("app.agents.sql_worker.SchemaSkill")
async def test_sql_worker_node(mock_schema_cls, mock_sql_cls, mock_get_db, mock_get_llm, mock_state):
    # Setup state with a pending task
    task = Task(id="task_1", type="sql", description="Get sales", status="pending", result=None)
    mock_state["plan"] = [task]
    
    # Mock SchemaSkill
    mock_schema_instance = AsyncMock()
    # Mock schema object
    mock_schema_obj = MagicMock()
    mock_schema_obj.to_sql_ddl.return_value = "CREATE TABLE sales..."
    mock_schema_instance.search_related_tables.return_value = [mock_schema_obj]
    mock_schema_cls.return_value = mock_schema_instance
    
    # Mock SqlSkill
    mock_sql_instance = AsyncMock()
    mock_sql_instance.execute_safe_query.return_value = QueryResult(
        success=True,
        data=pd.DataFrame({"id": [1], "amount": [100]}),
        sql="SELECT * FROM sales",
        row_count=1
    )
    mock_sql_cls.return_value = mock_sql_instance
    
    # Mock LLM for SQL generation
    mock_llm_instance = AsyncMock()
    mock_llm_instance.chat.return_value = "SELECT * FROM sales"
    mock_get_llm.return_value = mock_llm_instance
    
    config = {"configurable": {"session_id": "test_session"}}
    new_state = await sql_worker_node(mock_state, config)
    
    # Verify task completion
    assert new_state["plan"][0]["status"] == "completed"
    assert "df_1" in new_state["memory_dfs"]
    assert not new_state["memory_dfs"]["df_1"].empty

# --- Test PythonWorker Node ---
@pytest.mark.asyncio
@patch("app.agents.data_agent.get_llm_client") # Note: python_worker_node is in data_agent.py? No, wait. 
# Checking file list: python_skill.py is in skills. 
# I previously viewed `data_agent.py` but failed. I assumed `python_worker_node` was there.
# Checking `python_skill.py` content again... wait. 
# In Step 29, the view_file output for `python_skill.py` ENDS with `async def python_worker_node`.
# So `python_worker_node` is actually defined in `app/skills/python_skill.py`.
# Wait, that's unusual design (node inside skill file), but okay.
# Let me double check the import path in the test.
async def test_python_worker_node_mock(mock_state):
    # I need to patch `app.skills.python_skill.get_llm_client` because the node is defined there.
    with patch("app.skills.python_skill.get_llm_client") as mock_get_llm:
        # User defined python_worker_node in app/skills/python_skill.py
        from app.skills.python_skill import python_worker_node
        
        # Setup state with pending python task
        # And some data in memory
        df = pd.DataFrame({"a": [1, 2]})
        mock_state["memory_dfs"] = {"df_1": df}
        task = Task(id="task_2", type="python", description="Calculate sum of a", status="pending", result=None)
        mock_state["plan"] = [task]
        
        # Mock LLM to return code
        mock_llm_instance = AsyncMock()
        mock_llm_instance.chat.return_value = "```python\nresult = df_1['a'].sum()\n```"
        mock_get_llm.return_value = mock_llm_instance
        
        config = {"configurable": {"session_id": "test_session"}}
        new_state = await python_worker_node(mock_state, config)
        
        assert new_state["plan"][0]["status"] == "completed"
        # The result of sum is 3, but it's not stored in memory_dfs unless it's a dataframe
        # The task result should have the output
        assert new_state["plan"][0]["result"]["output"] == 3
