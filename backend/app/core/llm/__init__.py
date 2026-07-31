# LLM (Large Language Model) module
# 使用惰性导入避免循环依赖，外部应直接从子模块导入
# 例如: from app.core.llm.async_llm import get_async_llm

__all__ = [
    "llm",
    "async_llm", 
    "async_embedding",
    "vlm_service",
    "prompt_manager",
]
