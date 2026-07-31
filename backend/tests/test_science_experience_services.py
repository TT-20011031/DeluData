import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.path.insert(0, ".")

BASE_DIR = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    spec = spec_from_file_location(module_name, BASE_DIR / relative_path)
    module = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


scope_service = _load_module("app/experience/services/scope_service.py", "test_scope_service")
startup_profile_service = _load_module(
    "app/experience/services/startup_profile_service.py",
    "test_startup_profile_service",
)

build_effective_doc_scope = scope_service.build_effective_doc_scope
normalize_device_scope = scope_service.normalize_device_scope
summarize_effective_doc_scope = scope_service.summarize_effective_doc_scope
StartupProfileService = startup_profile_service.StartupProfileService
ExperienceSettings = _load_module("app/config.py", "test_app_config").ExperienceSettings


def test_normalize_device_scope_keeps_only_active_mode_list():
    scope = normalize_device_scope(
        {
            "mode": "files",
            "dept_ids": ["d1", "d2"],
            "file_ids": ["f1", "f1", "f2", ""],
        }
    )

    assert scope == {
        "mode": "files",
        "dept_ids": [],
        "file_ids": ["f1", "f2"],
    }


def test_build_effective_doc_scope_only_narrows_workspace_scope():
    effective = build_effective_doc_scope(
        {
            "file_ids": ["wf1", "wf2"],
            "visibilities": ["public"],
            "dept_ids": ["wd1", "wd2"],
        },
        {
            "mode": "dept",
            "dept_ids": ["wd2", "wd3"],
            "file_ids": [],
        },
    )

    assert effective == {
        "mode": "dept",
        "file_ids": ["wf1", "wf2"],
        "visibilities": ["public"],
        "dept_ids": ["wd2"],
    }

    summary = summarize_effective_doc_scope(effective)
    assert summary["mode"] == "dept"
    assert summary["file_count"] == 2
    assert summary["dept_count"] == 1
    assert summary["visibility_count"] == 1


def test_startup_profile_service_normalize_returns_new_intro_fields():
    service = StartupProfileService()

    normalized = service._normalize(
        {
            "age_group": "adult",
            "age_confidence": 0.82,
            "gender": "female",
            "gender_confidence": 0.73,
            "outfit_tags": ["实验风", "实验风", "彩色外套"],
            "vibe_tags": ["活泼", "好奇", "活泼"],
        },
        {
            "welcome_text": "彩色外套朋友，想问科学问题，还是来一题挑战？",
            "welcome_emotion": "playful",
        }
    )

    assert normalized["age_group"] == "adult"
    assert normalized["age_confidence"] == 0.82
    assert normalized["gender"] == "female"
    assert normalized["gender_confidence"] == 0.73
    assert normalized["outfit_tags"] == ["实验风", "彩色外套"]
    assert normalized["vibe_tags"] == ["活泼", "好奇"]
    assert normalized["feature_tags"] == ["实验风", "彩色外套", "活泼", "好奇"][:5]
    assert normalized["persona_text"]
    assert normalized["welcome_text"] == "彩色外套朋友，想问科学问题，还是来一题挑战？"
    assert normalized["welcome_emotion"] == "playful"
    assert normalized["tts_speaking_style"]


def test_startup_profile_service_fallback_contains_intro_contract():
    service = StartupProfileService()

    fallback = service._fallback("startup_profile_failed")

    assert fallback["age_group"] == "unknown"
    assert fallback["gender"] == "unknown"
    assert fallback["outfit_tags"] == []
    assert fallback["vibe_tags"] == []
    assert fallback["feature_tags"] == []
    assert fallback["persona_text"]
    assert fallback["welcome_text"]
    assert fallback["welcome_emotion"]
    assert fallback["tts_speaking_style"]
    assert fallback["fallback"] is True


def test_startup_profile_service_personalized_welcome_invites_question_or_quiz():
    service = StartupProfileService()

    welcome = service._build_personalized_welcome(
        age_group="adult",
        feature_tags=["彩色外套", "好奇"],
    )

    assert service.WELCOME_MIN_CHARS <= len(welcome) <= service.WELCOME_MAX_CHARS
    assert "问" in welcome
    assert "挑战" in welcome or "答题" in welcome


def test_experience_settings_prompts_include_guidance_and_asr_tolerance():
    settings = ExperienceSettings()

    assert "18~32" in settings.profile_welcome_prompt
    assert "语音提问" in settings.profile_welcome_prompt
    assert "答题挑战" in settings.profile_welcome_prompt
    assert "语音识别" in settings.answer_system_prompt
    assert "同音" in settings.answer_prompt_template
    assert "揣摩" in settings.answer_empty_context_prompt
