"""
Ingestion progress callback helpers.

职责：
- 统一进度回调调用规范
- 提供线程到事件循环的安全桥接
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str, int, Optional[dict[str, Any]]], Any]]


def emit_progress(
    progress_callback: ProgressCallback,
    stage: str,
    progress: int,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    if progress_callback is None:
        return
    try:
        result = progress_callback(stage, max(0, min(100, int(progress))), detail or {})
        if asyncio.iscoroutine(result):
            # 同线程上下文下可直接创建任务。
            asyncio.create_task(result)
    except RuntimeError:
        # 非事件循环线程下若返回协程，交由线程代理处理。
        logger.debug("emit_progress runtime context unavailable: stage=%s", stage)
    except Exception as exc:
        logger.debug("emit_progress failed: stage=%s err=%s", stage, exc)


@dataclass
class ThreadsafeProgressProxy:
    """
    将线程内进度事件安全投递回主事件循环。
    """

    callback: ProgressCallback
    loop: asyncio.AbstractEventLoop

    def __call__(
        self,
        stage: str,
        progress: int,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        if self.callback is None:
            return
        try:
            result = self.callback(stage, max(0, min(100, int(progress))), detail or {})
            if asyncio.iscoroutine(result):
                asyncio.run_coroutine_threadsafe(result, self.loop)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("threadsafe progress dispatch failed: stage=%s err=%s", stage, exc)
