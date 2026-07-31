import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.common.context import UserContext
import app.services.skill_retriever as skill_retriever_module


class _ExplodingSearchService:
    async def get_skill(self, skill_id, user_context=None):
        if skill_id == "skill-1":
            return SimpleNamespace(
                id="skill-1",
                title="销售周报",
                description="生成销售周报",
                workspace_id="ws-1",
                steps=[
                    {
                        "step": 1,
                        "action": "生成销售周报",
                        "tool": "office_worker",
                        "output_filename": "sales.xlsx",
                    }
                ],
            )
        return None

    def format_skill_for_prompt(self, skill):
        return f"### {skill.title}"

    async def search_skills(self, **kwargs):
        raise AssertionError("显式 skill_id 模式下不应再走语义检索")


@pytest.mark.asyncio
async def test_retrieve_skill_for_query_uses_explicit_skill_id(monkeypatch):
    monkeypatch.setattr(skill_retriever_module, "get_skill_service", lambda: _ExplodingSearchService())
    user_context = UserContext(user_id="u-1", workspace_id="ws-1")

    skill_id, skill_name, skill_context, skill_steps = await skill_retriever_module.retrieve_skill_for_query(
        query="执行",
        user_context=user_context,
        skill_id="skill-1",
    )

    assert skill_id == "skill-1"
    assert skill_name == "销售周报"
    assert skill_context == "### 销售周报"
    assert skill_steps[0]["output_filename"] == "sales.xlsx"


@pytest.mark.asyncio
async def test_retrieve_skill_for_query_raises_on_missing_explicit_skill(monkeypatch):
    monkeypatch.setattr(skill_retriever_module, "get_skill_service", lambda: _ExplodingSearchService())
    user_context = UserContext(user_id="u-1", workspace_id="ws-1")

    with pytest.raises(ValueError, match="不存在或已删除"):
        await skill_retriever_module.retrieve_skill_for_query(
            query="执行",
            user_context=user_context,
            skill_id="missing-skill",
        )


@pytest.mark.asyncio
async def test_retrieve_skill_for_query_raises_on_cross_workspace_skill(monkeypatch):
    class _CrossWorkspaceService(_ExplodingSearchService):
        async def get_skill(self, skill_id, user_context=None):
            return SimpleNamespace(
                id="skill-1",
                title="销售周报",
                description="生成销售周报",
                workspace_id="ws-2",
                steps=[],
            )

    monkeypatch.setattr(skill_retriever_module, "get_skill_service", lambda: _CrossWorkspaceService())
    user_context = UserContext(user_id="u-1", workspace_id="ws-1")

    with pytest.raises(ValueError, match="无权限访问"):
        await skill_retriever_module.retrieve_skill_for_query(
            query="执行",
            user_context=user_context,
            skill_id="skill-1",
        )
