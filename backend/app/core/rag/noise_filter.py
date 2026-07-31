"""Noise chunk detection used during ingestion."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence


_VALID_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SHORT_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,8}")


@dataclass
class NoiseDecision:
    is_noise: bool
    reason: str
    metrics: Dict[str, float]


class NoiseFilter:
    """Heuristic chunk-level noise classifier."""

    def __init__(
        self,
        marker_density_threshold: float = 0.02,
        min_readable_ratio: float = 0.2,
        repeat_ratio_threshold: float = 0.45,
        watermark_markers: Sequence[str] | None = None,
    ) -> None:
        self.marker_density_threshold = max(0.0, float(marker_density_threshold))
        self.min_readable_ratio = max(0.0, min(float(min_readable_ratio), 1.0))
        self.repeat_ratio_threshold = max(0.0, min(float(repeat_ratio_threshold), 1.0))
        self.watermark_markers = tuple(
            marker.lower()
            for marker in (watermark_markers or ["www.bzfxw.com", "bzfxw.com"])
            if marker
        )

    def evaluate(self, text: str) -> NoiseDecision:
        normalized = (text or "").strip().lower()
        if not normalized:
            return NoiseDecision(True, "empty", {"readable_ratio": 0.0, "marker_density": 0.0, "repeat_ratio": 1.0})

        compact = "".join(normalized.split())
        compact_len = max(1, len(compact))

        marker_hits = sum(compact.count(marker) for marker in self.watermark_markers)
        marker_density = marker_hits / compact_len

        valid_chars = len(_VALID_CHAR_RE.findall(compact))
        readable_ratio = valid_chars / compact_len

        repeat_ratio = self._repeat_ratio(compact)

        metrics = {
            "readable_ratio": round(readable_ratio, 4),
            "marker_density": round(marker_density, 4),
            "repeat_ratio": round(repeat_ratio, 4),
        }

        if marker_hits >= 2 and marker_density >= self.marker_density_threshold:
            return NoiseDecision(True, "watermark_density", metrics)
        if marker_density >= self.marker_density_threshold and readable_ratio < max(self.min_readable_ratio, 0.35):
            return NoiseDecision(True, "watermark_density", metrics)
        if readable_ratio < self.min_readable_ratio:
            return NoiseDecision(True, "low_readable_ratio", metrics)
        if repeat_ratio >= self.repeat_ratio_threshold:
            return NoiseDecision(True, "high_repeat_ratio", metrics)
        return NoiseDecision(False, "ok", metrics)

    @staticmethod
    def _repeat_ratio(text: str) -> float:
        tokens: List[str] = _SHORT_TOKEN_RE.findall(text)
        if len(tokens) < 8:
            return 0.0
        counts = Counter(tokens)
        top_count = max(counts.values()) if counts else 0
        return top_count / max(1, len(tokens))
