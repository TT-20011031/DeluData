import pytest
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# === Early Mocking to bypass Config/DB initialization ===
import logging
mock_db_module = MagicMock()
mock_db_module.get_async_db_context = MagicMock()
sys.modules["app.core.db.database"] = mock_db_module

mock_core_config = MagicMock()
sys.modules["app.core.config"] = mock_core_config

mock_logger_mod = MagicMock()
mock_logger_mod.logger = logging.getLogger("test")
sys.modules["app.core.utils.logger"] = mock_logger_mod

mock_museum_config = MagicMock()
mock_settings = MagicMock()
mock_settings.guide_llm_model = "test-model"
mock_settings.guide_workspace_id = "ws-123"
mock_settings.guide_default_top_k = 3
mock_settings.guide_system_prompt = "test-prompt"
mock_settings.llm_base_url = "http://test"
mock_settings.image_base_url = "http://test:8000"
mock_museum_config.get_museum_settings.return_value = mock_settings
sys.modules["app.museum.config"] = mock_museum_config

# Mock Museum DB Models (Crucial for SQLAlchemy select)
mock_museum_db = MagicMock()
class MockArtifact:
    name = MagicMock()
    is_highlight = MagicMock()
    click_count = MagicMock()
    @classmethod
    def __getattr__(cls, name): return MagicMock()

class MockProduct:
    id = MagicMock()
    name = MagicMock()
    price = MagicMock()
    is_global = MagicMock()
    status = MagicMock()
    target_crowd = MagicMock()
    image_urls = MagicMock()
    @classmethod
    def __getattr__(cls, name): return MagicMock()

mock_museum_db.MuseumArtifact = MockArtifact
mock_museum_db.MuseumProduct = MockProduct
mock_museum_db.ProductStatus = MagicMock()
sys.modules["app.museum.db"] = mock_museum_db

mock_prompt_adjuster_mod = MagicMock()
mock_prompt_adjuster_mod.PromptAdjuster = MagicMock
mock_prompt_adjuster_mod.AdjustmentResult = MagicMock
sys.modules["app.museum.services.prompt_adjuster"] = mock_prompt_adjuster_mod

mock_person_recognizer_mod = MagicMock()
mock_person_recognizer_mod.PersonRecognizer = MagicMock
mock_person_recognizer_mod.PersonAnalysisResult = MagicMock
sys.modules["app.museum.services.person_recognizer"] = mock_person_recognizer_mod
# ========================================================

from app.museum.services.query_rewriter import QueryRewriterService
from app.museum.services.catalog_service import CatalogService
from app.museum.services.guide_service import GuideService, SSEEmitter
from app.museum.models import PersonType

@pytest.mark.asyncio
async def test_query_rewriter_logic():
    """测试查询改写逻辑 (Mock LLM)"""
    service = QueryRewriterService()
    
    # Mock OpenAI client
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"rewritten_query": "司母戊鼎有多重？", "intent": "specific_query"}'
    
    service.client.chat.completions.create = AsyncMock(return_value=mock_response)
    
    result = await service.rewrite_query("它有多重？", [{"role": "user", "content": "介绍下司母戊鼎"}])
    
    assert result["rewritten_query"] == "司母戊鼎有多重？"
    assert result["intent"] == "specific_query"
    
    # Test Topic Switch (Mock)
    mock_response.choices[0].message.content = '{"rewritten_query": "厕所在哪", "intent": "topic_switch"}'
    result_switch = await service.rewrite_query("厕所在哪", [{"role": "user", "content": "介绍下司母戊鼎"}])
    assert result_switch["rewritten_query"] == "厕所在哪"
    assert result_switch["intent"] == "topic_switch"

@pytest.mark.asyncio
async def test_catalog_seeding():
    """测试目录 Seeding (Mock DB)"""
    service = CatalogService() # This will use mock_museum_db due to sys.modules
    
    # Mock DB execution
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = ["司母戊鼎", "四羊方尊"]
    mock_session.execute = AsyncMock(return_value=mock_result)
    
    # Correctly mock async context manager:
    mock_ctx_manager = AsyncMock()
    mock_ctx_manager.__aenter__.return_value = mock_session
    mock_ctx_manager.__aexit__.return_value = None
    
    # Patch WHERE it is used (inside catalog_service)
    # Patching 'select' and 'desc' to avoid SQLAlchemy ArgumentError with Mocks
    with patch("app.museum.services.catalog_service.get_async_db_context", return_value=mock_ctx_manager), \
         patch("app.museum.services.catalog_service.select", return_value=MagicMock()) as mock_select, \
         patch("app.museum.services.catalog_service.desc", return_value=MagicMock()):
        
        seeds = await service.get_catalog_seeds(limit=2)
        assert len(seeds) == 2
        assert "司母戊鼎" in seeds
        # Verify it was called
        assert mock_select.called

@pytest.mark.asyncio
async def test_guide_image_parsing():
    """测试 GuideService 的图片标签解析逻辑"""
    # Create GuideService (will use mocks from sys.modules)
    service = GuideService()
    
    # Mock LLM stream
    mock_chunks = [
        MagicMock(choices=[MagicMock(delta=MagicMock(content="这里有"))]),
        MagicMock(choices=[MagicMock(delta=MagicMock(content="一张图 [[IMAGE"))]),
        MagicMock(choices=[MagicMock(delta=MagicMock(content=":group/123.jpg]] 请看"))]),
        MagicMock(choices=[MagicMock(delta=MagicMock(content="。"))]),
    ]
    
    mock_client = AsyncMock()
    # Mock the stream response
    mock_client.chat.completions.create = AsyncMock(return_value=AsyncIterator(mock_chunks))
    
    # Set environment variable for API Key to avoid ValueError
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
         patch("openai.AsyncOpenAI", return_value=mock_client):
        
        emitter = AsyncMock()
        with patch("app.museum.services.guide_service.get_tts_connection_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = AsyncMock()
            
            # 准备 fallback images
            fallback_images = [{"id": "group/123.jpg", "url": "http://img.com/1.jpg"}]
            
            await service._generate_response(
                context="test",
                emitter=emitter,
                enable_tts=False,
                person_type="通用访客",
                session_id="123",
                fallback_images=fallback_images
            )
            
            # Verify SEARCH_IMAGES emitted
            calls = emitter.emit.call_args_list
            print(f"DEBUG: Emitter calls: {calls}")
            
            search_image_calls = [c for c in calls if c.args[0] == "SEARCH_IMAGES"]
            
            # Check for error if assertion fails
            if len(search_image_calls) == 0:
                error_calls = [c for c in calls if c.args[0] == "ERROR" or (c.args[0] == "MESSAGE_END" and c.args[1].get("error"))]
                if error_calls:
                    pytest.fail(f"Service returned error: {error_calls}")
                    
            assert len(search_image_calls) > 0, "Should have emitted SEARCH_IMAGES"
            
            # Verify MESSAGE_CHUNK contains text but NOT tags
            content_sent = "".join([c.args[1].get("content", "") for c in calls if c.args[0] == "MESSAGE_CHUNK"])
            assert "[[IMAGE" not in content_sent
            assert "group/123.jpg" not in content_sent
            assert "请看" in content_sent

class AsyncIterator:
    def __init__(self, items):
        self.items = items
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_query_rewriter_logic())
    print("test_query_rewriter_logic: PASSED")
    asyncio.run(test_catalog_seeding())
    print("test_catalog_seeding: PASSED")
    asyncio.run(test_guide_image_parsing())
    print("test_guide_image_parsing: PASSED")
    print("\nAll tests passed successfully!")

