from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from fastapi import HTTPException
from PIL import Image

from app.api import maintenance_records as module
from app.core.voice.asr.base import ASRErrorCode


class _FakeUploadFile:
    def __init__(self, data: bytes, *, content_type: str = "image/png", filename: str = "demo.png"):
        self._data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self) -> bytes:
        return self._data


def _png_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (160, 100), (210, 80, 60)).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_maintenance_image_validation_requires_images():
    with pytest.raises(HTTPException) as exc:
        await module._read_and_validate_images([])

    assert exc.value.status_code == 400
    assert exc.value.detail == "maintenance_images_required"


@pytest.mark.asyncio
async def test_maintenance_image_validation_rejects_too_many_images():
    images = [_FakeUploadFile(_png_bytes()) for _ in range(module.MAX_IMAGE_COUNT + 1)]

    with pytest.raises(HTTPException) as exc:
        await module._read_and_validate_images(images)

    assert exc.value.status_code == 400
    assert exc.value.detail == "maintenance_images_too_many"


@pytest.mark.asyncio
async def test_maintenance_image_validation_rejects_unsupported_type():
    images = [_FakeUploadFile(_png_bytes(), content_type="image/gif")]

    with pytest.raises(HTTPException) as exc:
        await module._read_and_validate_images(images)

    assert exc.value.status_code == 400
    assert exc.value.detail == "maintenance_image_type_unsupported"


@pytest.mark.asyncio
async def test_maintenance_image_validation_rejects_oversized_file():
    images = [_FakeUploadFile(b"x" * (module.MAX_IMAGE_BYTES + 1))]

    with pytest.raises(HTTPException) as exc:
        await module._read_and_validate_images(images)

    assert exc.value.status_code == 413
    assert exc.value.detail == "maintenance_image_too_large"


@pytest.mark.asyncio
async def test_maintenance_image_validation_rejects_invalid_image_bytes():
    images = [_FakeUploadFile(b"not an image")]

    with pytest.raises(HTTPException) as exc:
        await module._read_and_validate_images(images)

    assert exc.value.status_code == 400
    assert exc.value.detail == "maintenance_image_invalid"


def test_maintenance_pdf_contains_text_and_embedded_image(tmp_path):
    output = tmp_path / "maintenance.pdf"

    size = module._generate_pdf_sync(
        output_path=str(output),
        created_at=module.datetime(2026, 6, 17, 10, 30, 0),
        description="更换轴承并完成试运行。",
        image_payloads=[("现场照片.png", _png_bytes())],
    )

    assert size > 0
    with fitz.open(output) as doc:
        text = "\n".join(page.get_text() for page in doc)
        images = sum(len(page.get_images(full=True)) for page in doc)

    assert "维修记录" in text
    assert "公开维修采集页" in text
    assert "更换轴承并完成试运行" in text
    assert images >= 1


@pytest.mark.asyncio
async def test_public_rate_limit_returns_429_after_limit():
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.10"))
    action = f"unit-test-submit-{uuid4()}"

    await module._check_rate_limit(request, action=action, limit=1)
    with pytest.raises(HTTPException) as exc:
        await module._check_rate_limit(request, action=action, limit=1)

    assert exc.value.status_code == 429
    assert exc.value.detail == "maintenance_rate_limited"


@pytest.mark.asyncio
async def test_public_transcribe_returns_asr_failure(monkeypatch):
    class _FakeAudio:
        content_type = "audio/webm"

        async def read(self) -> bytes:
            return b"voice-bytes"

    class _FakeResult:
        success = False
        text = ""
        error_code = ASRErrorCode.AUTH_ERROR
        user_message = "语音识别服务额度到期或认证失败，请联系管理员处理"
        error_message = "FREE_TRIAL_EXPIRED"

    class _FakeASR:
        async def transcribe_file(self, audio_content: bytes, content_type: str):
            return _FakeResult()

    async def _noop_rate_limit(*args, **kwargs):
        return None

    monkeypatch.setattr(module, "_check_rate_limit", _noop_rate_limit)
    monkeypatch.setattr(module, "get_asr_service", lambda: _FakeASR())

    response = await module.transcribe_public_maintenance_audio(
        SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.11")),
        _FakeAudio(),
    )

    assert response.code == ASRErrorCode.AUTH_ERROR
    assert "额度到期" in response.msg
    assert response.error_detail == "FREE_TRIAL_EXPIRED"
