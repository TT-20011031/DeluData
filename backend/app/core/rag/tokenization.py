"""Tokenization utilities for Chinese + English retrieval."""

from __future__ import annotations

import logging
import re
from typing import Iterable, List

try:
    import jieba
except Exception:  # pragma: no cover - optional dependency fallback
    jieba = None


logger = logging.getLogger(__name__)

_CJK_RE = re.compile("[\u4e00-\u9fff]+")
_EN_NUM_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_JIEBA_MISSING_WARNED = False


def _tokenize_cjk_fallback(sequence: str) -> List[str]:
    """Fallback CJK segmentation without external dictionaries."""
    seq = (sequence or "").strip()
    if not seq:
        return []
    if len(seq) <= 2:
        return [seq]

    # Overlapping bi-grams keep enough lexical signal for BM25.
    tokens = [seq[i : i + 2] for i in range(len(seq) - 1)]
    # Keep short full phrase as a backup exact-match token.
    if len(seq) <= 8:
        tokens.append(seq)
    return tokens


def tokenize_mixed_text(text: str, use_jieba: bool = True) -> List[str]:
    """Tokenize text for BM25 in mixed Chinese/English scenarios."""
    global _JIEBA_MISSING_WARNED

    if not text:
        return []

    normalized = str(text).strip().lower()
    if not normalized:
        return []

    pieces: List[str]
    if use_jieba and jieba is not None:
        pieces = [p.strip() for p in jieba.lcut(normalized, cut_all=False) if p and p.strip()]
    else:
        pieces = re.split(r"\s+", normalized)
        if use_jieba and jieba is None and not _JIEBA_MISSING_WARNED:
            logger.warning("[Tokenization] jieba not installed, using CJK fallback tokenizer")
            _JIEBA_MISSING_WARNED = True

    tokens: List[str] = []
    for piece in pieces:
        cjk_sequences = _CJK_RE.findall(piece)
        if cjk_sequences:
            if use_jieba and jieba is not None:
                tokens.extend(cjk_sequences)
            else:
                for seq in cjk_sequences:
                    tokens.extend(_tokenize_cjk_fallback(seq))

        en_tokens = _EN_NUM_RE.findall(piece)
        if en_tokens:
            tokens.extend(en_tokens)

    if tokens:
        return tokens

    # Fallback for unusual punctuation-only splits.
    fallback_tokens: List[str] = _EN_NUM_RE.findall(normalized)
    for seq in _CJK_RE.findall(normalized):
        fallback_tokens.extend(_tokenize_cjk_fallback(seq))
    return fallback_tokens


def tokenize_corpus(texts: Iterable[str], use_jieba: bool = True) -> List[List[str]]:
    return [tokenize_mixed_text(text, use_jieba=use_jieba) for text in texts]
