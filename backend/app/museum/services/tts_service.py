"""
火山引擎 TTS 服务

兼容性模块：保持 Museum 原有 API 不变，内部使用公用模块实现。

遵循零技术债策略 (Zero Tech Debt)：
- Museum 模块改为使用 core/voice 公用模块
- 保持原有 API 接口向后兼容
- 原有的连接池、连接类保持兼容
"""

from typing import Optional, AsyncIterator

from app.core.voice.tts import (
    VolcengineTTSService,
    TTSConnection as _TTSConnection,
    TTSConnectionPool as _TTSConnectionPool,
    TTSChunk,
    get_tts_connection_pool as _get_tts_connection_pool,
)

# 兼容性导出：保持原有类名
TTSConnection = _TTSConnection
TTSConnectionPool = _TTSConnectionPool

# [Zero Tech Debt] 类型别名，不重定义，保持与 Core 模块类型一致
# 旧代码 TTSAudioChunk 现在是 TTSChunk 的别名
TTSAudioChunk = TTSChunk


def get_tts_connection_pool() -> _TTSConnectionPool:
    """
    获取 TTS 连接池单例
    
    兼容原有 API
    """
    return _get_tts_connection_pool()
