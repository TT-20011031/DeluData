import sys

sys.path.insert(0, ".")

from app.services.skill_step_defaults import apply_skill_step_defaults  # noqa: E402


def test_apply_skill_step_defaults_injects_doc_scope_and_template():
    mapped_steps = [
        {"worker": "doc_worker", "params": {}},
        {"worker": "office_worker", "params": {}},
    ]
    llm_steps = [
        {"worker": "doc_worker", "description": "查询知识"},
        {"worker": "office_worker", "description": "生成文档"},
    ]
    skill_steps = [
        {
            "tool": "doc_worker",
            "doc_scope": {"folder_ids": ["folder_a"], "include_subfolders": True},
        },
        {
            "tool": "office_worker",
            "template_id": 12,
            "template_mode": "draft",
        },
    ]

    merged = apply_skill_step_defaults(mapped_steps, llm_steps, skill_steps)
    assert merged[0]["params"]["doc_scope"] == {
        "folder_ids": ["folder_a"],
        "file_ids": [],
        "include_subfolders": True,
    }
    assert merged[1]["params"]["template_id"] == 12
    assert merged[1]["params"]["template_mode"] == "draft"


def test_apply_skill_step_defaults_does_not_override_existing_params():
    mapped_steps = [{"worker": "office_worker", "params": {"template_id": 99}}]
    llm_steps = [{"worker": "office_worker"}]
    skill_steps = [{"tool": "office_worker", "template_id": 12}]

    merged = apply_skill_step_defaults(mapped_steps, llm_steps, skill_steps)
    assert merged[0]["params"]["template_id"] == 99


def test_apply_skill_step_defaults_respects_llm_worker():
    mapped_steps = [{"worker": "sql_worker", "params": {}}]
    llm_steps = [{"worker": "sql_worker"}]
    skill_steps = [{"tool": "doc_worker"}]

    merged = apply_skill_step_defaults(mapped_steps, llm_steps, skill_steps)
    assert merged[0]["worker"] == "sql_worker"
