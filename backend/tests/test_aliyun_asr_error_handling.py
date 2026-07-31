from app.core.voice.asr.aliyun_asr import AliyunASRService
from app.core.voice.asr.base import ASRErrorCode


def test_free_trial_expired_is_auth_error() -> None:
    error_code = AliyunASRService._classify_provider_error(
        40000010,
        "Gateway:FREE_TRIAL_EXPIRED:The free trial has expired!",
    )

    assert error_code == ASRErrorCode.AUTH_ERROR


def test_provider_rate_limit_is_rate_limit_error() -> None:
    error_code = AliyunASRService._classify_provider_error(
        None,
        "QPS limit exceeded",
    )

    assert error_code == ASRErrorCode.RATE_LIMIT
