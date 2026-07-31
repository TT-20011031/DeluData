"""Shared defaults and fallback content for experience services."""

from __future__ import annotations

VOICE_PROFILE_DEFAULTS = {
    "child_voice": "child",
    "adult_voice": "female_gentle",
    "elder_voice": "male_broadcast",
    "female_voice": "female_gentle",
    "male_voice": "male_broadcast",
    "default_voice": "robot",
    "gender_confidence_threshold": 0.75,
}

WAKEWORD_CONFIG_DEFAULTS = {
    "wakeword": "小莎小莎",
    "enabled": True,
    "sensitivity": 0.65,
}

REWARD_POLICY_DEFAULTS = {
    "required_correct_count": 3,
    "period_hours": 24,
    "max_claims_per_period": 1,
    "is_active": True,
}

REWARD_MESSAGE_COUPON_UNAVAILABLE = "恭喜达标！当前暂无可用优惠券，请联系工作人员。"
COUPON_CODE_PREFIX = "SC"

QUIZ_DIFFICULTY_LABELS = {
    1: "简单",
    2: "较易",
    3: "中等",
    4: "较难",
    5: "困难",
}

QUIZ_GENERATION_EXTRA_REQUIREMENTS_TEMPLATE = (
    "补充要求：主题围绕“{topic}”；难度等级为 {difficulty}（{difficulty_label}）；"
    "请只输出 JSON，不要输出 Markdown 代码块或额外说明。"
)

QUIZ_GENERATION_DEFAULT_QUESTION = {
    "question_text": "科技馆里常见的互动装置主要是为了什么？",
    "options": [
        "A. 仅仅装饰",
        "B. 让参观者动手体验科学",
        "C. 只给工作人员使用",
        "D. 只是拍照背景",
    ],
    "answer_key": "B",
    "explanation": "互动装置的核心目标是让参观者通过动手和观察理解科学原理。",
}

STARTUP_PROFILE_FALLBACK_PERSONA_TEXT = "准备好一起解锁有趣的科学现象吧。"
