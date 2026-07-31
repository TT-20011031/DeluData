"""[M4.2] Wiki 配额运行时校验单元测试。

覆盖三层：
1. WikiService._apply_outcome: 配额边界（未达 / 触达截断 / 已超）+ 单页 outgoing_links 截断
2. WikiService.get_quota_status: 三类返回结构（空 / 正常 / 接近上限）
3. GET /api/wiki/quota: 路由层透传

不依赖真实 DB，使用 _SeqSession 按调用顺序返回预设结果。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ============ 共用 fakes ============


class _SeqSession:
    """按调用顺序返回预设 execute result 的 fake AsyncSession。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self._idx = 0
        self.adds: list[Any] = []

    def add(self, obj: Any) -> None:
        self.adds.append(obj)

    async def flush(self) -> None:  # noqa: D401
        pass

    async def execute(self, stmt: Any) -> Any:  # noqa: ANN401
        if self._idx >= len(self._results):
            raise AssertionError(
                f"_SeqSession 调用 execute 超出预设次数 ({self._idx} >= {len(self._results)})"
            )
        item = self._results[self._idx]
        self._idx += 1
        return item


def _make_count_result(value: int) -> MagicMock:
    """模拟 select(func.count(...)) 的返回。"""
    m = MagicMock()
    m.scalar_one.return_value = value
    m.scalar.return_value = value
    return m


def _make_rows_result(rows: list[tuple]) -> MagicMock:
    """模拟 select(WikiPage.slug)... 的 .all() 返回。"""
    m = MagicMock()
    m.all.return_value = list(rows)
    return m


class _FakeScope:
    def __init__(self, session: _SeqSession) -> None:
        self._session = session

    async def __aenter__(self) -> _SeqSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


def _make_compiled(slug: str, *, outgoing: int = 0, existing_id: str = ""):
    """构造一个 _apply_outcome 视角下足够鸭子类型的 CompiledPage。

    用 SimpleNamespace 而非真实 dataclass，避免触发 app.core.wiki.compiler
    里链式 import chromadb 等沉重依赖。
    """

    return SimpleNamespace(
        operation="create",
        slug=slug,
        title=slug.title(),
        summary=f"summary of {slug}",
        domain="general",
        markdown_body=f"# {slug}\n\nbody",
        existing_page_id=existing_id or None,
        base_version=None,
        outgoing_links=[
            {"target_slug": f"target-{i}", "link_type": "related"}
            for i in range(outgoing)
        ],
        contradicts=[],
        evidence_chunk_ids=[],
        source_file_id="",
        ops_log="",
        compile_meta={},
    )


def _make_outcome(pages: list) -> Any:
    """轻量 CompileOutcome 替身，仅暴露 _apply_outcome 实际访问到的字段。"""

    return SimpleNamespace(
        pages=list(pages),
        skipped_candidates=[],
        error_messages=[],
        elapsed_seconds=0.0,
        total_candidates=len(pages),
    )


# ============ _apply_outcome 配额校验 ============


@pytest.mark.asyncio
class TestApplyOutcomeQuota:
    async def _run_apply_outcome(
        self,
        monkeypatch,
        *,
        compiled_pages: list,
        current_count: int,
        existing_slugs: list[str],
        max_pages: int = 500,
        max_links: int = 50,
    ) -> dict:
        """共用 helper：mock _apply_single_page、settings、执行 _apply_outcome 并返回结果。"""
        from app.services import wiki_service as svc_module
        from app.config import get_settings

        # 调整配额阈值
        settings = get_settings()
        monkeypatch.setattr(settings.wiki, "max_pages_per_workspace", max_pages)
        monkeypatch.setattr(settings.wiki, "max_links_per_page", max_links)

        # mock _apply_single_page：根据 compiled.existing_page_id / slug 是否存在返回 create/update
        async def _fake_apply_single(self, session, *, workspace_id, compiled, triggered_by):
            op = "update" if (
                compiled.existing_page_id or compiled.slug in set(existing_slugs)
            ) else "create"
            return {
                "operation": op,
                "page_id": f"page-{compiled.slug}",
                "slug": compiled.slug,
                "version": 1,
                "link_stats": {"created": len(compiled.outgoing_links)},
                "conflicts_added": 0,
            }

        monkeypatch.setattr(svc_module.WikiService, "_apply_single_page", _fake_apply_single)

        # 顺序：第一次 execute = count；第二次 = slug rows
        session = _SeqSession(
            results=[
                _make_count_result(current_count),
                _make_rows_result([(s,) for s in existing_slugs]),
            ]
        )

        outcome = _make_outcome(compiled_pages)
        service = svc_module.WikiService()
        result = await service._apply_outcome(
            session=session,  # type: ignore[arg-type]
            workspace_id="ws-1",
            outcome=outcome,
            triggered_by="test",
        )
        result["_outcome_errors"] = list(outcome.error_messages)
        return result

    async def test_未达上限_全部正常落库(self, monkeypatch):
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[
                _make_compiled("a"),
                _make_compiled("b"),
                _make_compiled("c"),
            ],
            current_count=10,
            existing_slugs=[],
            max_pages=500,
        )
        assert result["created"] == 3
        assert result["updated"] == 0
        assert result["quota_breach"]["pages_dropped"] == 0
        assert result["quota_breach"]["links_dropped"] == 0

    async def test_触达上限_新建被截断(self, monkeypatch):
        """current=499，max=500，3 个新建只有 1 个能通过。"""
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[
                _make_compiled("a"),
                _make_compiled("b"),
                _make_compiled("c"),
            ],
            current_count=499,
            existing_slugs=[],
            max_pages=500,
        )
        assert result["created"] == 1
        assert result["quota_breach"]["pages_dropped"] == 2
        assert result["quota_breach"]["current_pages_before"] == 499
        # outcome.error_messages 应记录两条跳过日志
        assert sum(1 for m in result["_outcome_errors"] if "max_pages_per_workspace" in m) == 2

    async def test_已超上限_全部被截断(self, monkeypatch):
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[
                _make_compiled("a"),
                _make_compiled("b"),
            ],
            current_count=500,
            existing_slugs=[],
            max_pages=500,
        )
        assert result["created"] == 0
        assert result["quota_breach"]["pages_dropped"] == 2

    async def test_update_不占配额(self, monkeypatch):
        """已存在的 slug 走 update，即使配额已满也不被截断。"""
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[
                _make_compiled("existing-a"),
                _make_compiled("existing-b"),
            ],
            current_count=500,
            existing_slugs=["existing-a", "existing-b"],
            max_pages=500,
        )
        assert result["updated"] == 2
        assert result["created"] == 0
        assert result["quota_breach"]["pages_dropped"] == 0

    async def test_outgoing_links_超限被截断(self, monkeypatch):
        """单页出链 80 条，max_links=50，应截断为 50 并记录 30。"""
        compiled = _make_compiled("a", outgoing=80)
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[compiled],
            current_count=10,
            existing_slugs=[],
            max_pages=500,
            max_links=50,
        )
        assert result["created"] == 1
        assert len(compiled.outgoing_links) == 50  # 已截断
        assert result["quota_breach"]["links_dropped"] == 30
        assert result["quota_breach"]["max_links_per_page"] == 50

    async def test_混合_create_update_部分截断(self, monkeypatch):
        """current=498，max=500，存在 b/c，传入 a/b/c/d/e → 创建仅 a/d；e 被截断。"""
        result = await self._run_apply_outcome(
            monkeypatch,
            compiled_pages=[
                _make_compiled("a"),
                _make_compiled("b"),
                _make_compiled("c"),
                _make_compiled("d"),
                _make_compiled("e"),
            ],
            current_count=498,
            existing_slugs=["b", "c"],
            max_pages=500,
        )
        # b/c 走 update，a/d 创建占满 499/500，e 被截断
        assert result["updated"] == 2
        assert result["created"] == 2
        assert result["quota_breach"]["pages_dropped"] == 1


# ============ get_quota_status ============


@pytest.mark.asyncio
class TestGetQuotaStatus:
    def _patch_db(self, monkeypatch, *, count: int, max_outgoing: int):
        """让 service 内部 db_manager.session_scope() 返回预设序列结果。"""
        from app.services import wiki_service as svc_module

        session = _SeqSession(
            results=[
                _make_count_result(count),
                _make_count_result(max_outgoing),
            ]
        )
        fake_mgr = MagicMock()
        fake_mgr.session_scope = lambda: _FakeScope(session)
        monkeypatch.setattr(svc_module, "get_async_db_manager", lambda: fake_mgr)
        # set_current_workspace 不需要做任何事
        monkeypatch.setattr(svc_module, "set_current_workspace", lambda *_a, **_kw: None)
        monkeypatch.setattr(svc_module, "get_current_workspace", lambda: "")

    async def _run(self, monkeypatch, *, max_pages=500, max_links=50, count, observed):
        from app.services.wiki_service import WikiService
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings.wiki, "max_pages_per_workspace", max_pages)
        monkeypatch.setattr(settings.wiki, "max_links_per_page", max_links)
        self._patch_db(monkeypatch, count=count, max_outgoing=observed)
        return await WikiService().get_quota_status(workspace_id="ws-1")

    async def test_空工作区_全零(self, monkeypatch):
        result = await self._run(monkeypatch, count=0, observed=0)
        assert result["pages"]["current"] == 0
        assert result["pages"]["limit"] == 500
        assert result["pages"]["usage_pct"] == 0.0
        assert result["links"]["max_outgoing_observed"] == 0
        assert result["near_limit"] is False

    async def test_正常使用_未达_90pct(self, monkeypatch):
        result = await self._run(monkeypatch, count=100, observed=20)
        assert result["pages"]["current"] == 100
        assert result["pages"]["usage_pct"] == 0.2
        assert result["near_limit"] is False

    async def test_pages_接近上限_near_limit_true(self, monkeypatch):
        result = await self._run(monkeypatch, count=460, observed=10)
        assert result["pages"]["usage_pct"] >= 0.9
        assert result["near_limit"] is True

    async def test_links_接近上限_也置_near_limit(self, monkeypatch):
        result = await self._run(monkeypatch, count=10, observed=46)
        assert result["near_limit"] is True
        assert result["links"]["max_outgoing_observed"] == 46


# ============ GET /api/wiki/quota 路由集成 ============


@pytest.mark.asyncio
class TestQuotaRouteIntegration:
    async def test_路由调用_service_并返回(self, monkeypatch):
        """验证 router endpoint 调 service.get_quota_status 并透传结果。"""
        # 注意：app.api.wiki.__init__ 把同名属性 `router` 重绑定到 APIRouter 实例，
        # 普通 `import app.api.wiki.router as m` 取到的是被遮蔽后的 APIRouter 对象。
        # 这里用 importlib 显式拿到子模块对象。
        import importlib

        router_module = importlib.import_module("app.api.wiki.router")

        async def _fake_get_quota_status(self, *, workspace_id):
            return {
                "workspace_id": workspace_id,
                "max_pages_per_workspace": 500,
                "max_links_per_page": 50,
                "pages": {"current": 123, "limit": 500, "usage_pct": 0.246},
                "links": {"max_per_page": 50, "max_outgoing_observed": 12},
                "near_limit": False,
            }

        monkeypatch.setattr(
            router_module.WikiService, "get_quota_status", _fake_get_quota_status
        )

        # 直接调路由函数，避免拉起 FastAPI app
        fake_user = MagicMock()
        fake_user.workspace_id = "ws-1"
        result = await router_module.get_wiki_quota_status(current_user=fake_user)
        assert result["workspace_id"] == "ws-1"
        assert result["pages"]["current"] == 123
        assert result["near_limit"] is False
