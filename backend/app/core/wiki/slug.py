"""Wiki slug 工具：规范化、生成、匹配。

slug 设计原则（Karpathy 风格）：
- 工作区内唯一（DB UNIQUE 约束）
- 可读：英文/数字/连字符；汉字保留为拼音首字母简写或 fallback 使用 hash 后缀
- 稳定：同一实体在不同时间点输出同一 slug，避免实体页"分裂"
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, Optional

# 允许通过的字符：ASCII 字母、数字、连字符
_ASCII_ALLOWED = re.compile(r"[a-z0-9-]+")
_NON_ALPHANUM = re.compile(r"[^a-z0-9\u4e00-\u9fa5]+", flags=re.UNICODE)


def normalize_slug(raw: str) -> str:
    """把 LLM 给出的原始 slug 标准化为合法形式。

    规则：
    1. NFKC 归一化（全角→半角）
    2. 转小写、去首尾空白
    3. 非字母/数字/汉字字符 → 连字符
    4. 折叠多重连字符
    5. 空字符串 → 空字符串（调用方需兜底）
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    text = _NON_ALPHANUM.sub("-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:120]  # 防止过长


def make_fallback_slug(title: str, salt: str = "") -> str:
    """当 LLM 没有提供 slug 时，根据 title + salt 生成稳定 fallback slug。

    用 8 位 sha1 摘要保证：
    - 同一 (title, salt) 永远相同
    - 不同 title 极低概率冲突
    """
    base = normalize_slug(title)
    digest_input = f"{base}|{salt}".encode("utf-8")
    suffix = hashlib.sha1(digest_input).hexdigest()[:8]
    if base and _ASCII_ALLOWED.fullmatch(base):
        return f"{base}-{suffix}"
    # 含汉字/非 ASCII 时，用 wiki- 前缀保证可作为 URL
    return f"wiki-{suffix}"


def candidate_slugs(title: str, aliases: Optional[Iterable[str]] = None) -> list[str]:
    """生成一组候选 slug（用于查重）：title + 全部 aliases 的标准化结果。

    返回去重后的列表，按出现顺序保留。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in [title or "", *(aliases or [])]:
        s = normalize_slug(raw)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
