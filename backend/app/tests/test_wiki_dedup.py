"""[编译质量] WikiCompiler._deduplicate_candidates 单元测试。

覆盖 6 个场景：
1. 完全相同的 slug → 已由 _merge_candidates 处理（验证不对输入做多余操作）
2. slug 编辑距离 <=2 的 typo → 模糊合并
3. 标题词元高度重叠 → 模糊合并
4. 完全不相关的候选项 → 不合并
5. 三个候选项，两个可合并、一个独立 → 正确分组
6. 空列表 → 无报错直接返回

不依赖 DB，直接使用 CandidateEntity dataclass。
"""
from __future__ import annotations

from app.core.wiki.compiler import CandidateEntity, WikiCompiler


class TestDeduplicateCandidates:
    def test_identical_slug_passthrough(self):
        """同 slug 已由 _merge_candidates 合并；这里验证 _deduplicate 对单例不做修改。"""
        c = CandidateEntity(slug="knowledge-router", title="Knowledge Router", brief="A router for knowledge")
        result = WikiCompiler._deduplicate_candidates([c])
        assert len(result) == 1
        assert result[0].slug == "knowledge-router"
        assert result[0].merged_from_count == 1

    def test_slug_typo_merged(self):
        """slug 编辑距离相近（typo/连字符变体）→ 合并。"""
        c1 = CandidateEntity(slug="knowledge-router", title="Knowledge Router", brief="Route knowledge")
        c2 = CandidateEntity(slug="knowledge-routr", title="Knowledge Router", brief="Route knowledge")
        result = WikiCompiler._deduplicate_candidates([c1, c2])
        assert len(result) == 1
        merged = result[0]
        assert merged.slug in ("knowledge-router", "knowledge-routr")
        assert merged.merged_from_count == 2
        assert merged.title == "Knowledge Router"

    def test_title_token_overlap_merged(self):
        """slug 不同但标题词元高度重叠 → 合并。"""
        c1 = CandidateEntity(slug="router-config", title="Router Configuration Guide System",
                             brief="Config for router")
        c2 = CandidateEntity(slug="router-setup", title="Router Configuration Guide",
                             brief="Configuration setup guide")
        # Jaccard: {router,configuration,guide,system} & {router,configuration,guide}
        #        = 3 / 4 = 0.75 >= 0.70
        result = WikiCompiler._deduplicate_candidates([c1, c2])
        assert len(result) == 1
        assert result[0].merged_from_count == 2

    def test_distinct_not_merged(self):
        """完全不同的概念 → 不合并。"""
        c1 = CandidateEntity(slug="knowledge-router", title="Knowledge Router", brief="Route knowledge")
        c2 = CandidateEntity(slug="customer-churn", title="Customer Churn Rate", brief="Churn metric")
        result = WikiCompiler._deduplicate_candidates([c1, c2])
        assert len(result) == 2
        slugs = {c.slug for c in result}
        assert slugs == {"knowledge-router", "customer-churn"}

    def test_three_candidates_two_merge(self):
        """三选二场景：两个相似 + 一个独立 → 正确分组。"""
        c1 = CandidateEntity(slug="travel-policy", title="Travel Policy", brief="Policy for travel")
        c2 = CandidateEntity(slug="travel-policy-v2", title="Travel Policy", brief="Travel policy details")
        c3 = CandidateEntity(slug="expense-limit", title="Expense Limit", brief="Expense cap")
        result = WikiCompiler._deduplicate_candidates([c1, c2, c3])
        assert len(result) == 2
        merged_slugs = {c.slug for c in result}
        # travel-policy + travel-policy-v2 合并为一条
        assert "expense-limit" in merged_slugs
        merged = next(c for c in result if c.slug != "expense-limit")
        assert merged.merged_from_count == 2
        # 独立的那条保持 merged_from_count=1
        single = next(c for c in result if c.slug == "expense-limit")
        assert single.merged_from_count == 1

    def test_empty_list(self):
        """空列表 → 返回空列表，不报错。"""
        result = WikiCompiler._deduplicate_candidates([])
        assert result == []
