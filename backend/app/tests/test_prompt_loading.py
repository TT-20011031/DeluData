from app.core.llm.prompt_manager import get_prompt
import pytest

def test_load_prompts():
    # Test loading supervisor planner prompt
    planner_prompt = get_prompt("supervisor.supervisor_planner.system")
    assert "DeluData" in planner_prompt
    assert "steps" in planner_prompt
    
    # Test loading supervisor router prompt
    router_prompt = get_prompt("supervisor.supervisor_router.system")
    assert "DeluData" in router_prompt
    assert "next_worker" in router_prompt
    
    # Test loading finish prompt
    finish_prompt = get_prompt("finish.finish_main.system")
    assert "DeluData" in finish_prompt
    assert "简体中文" in finish_prompt
    
    print("Prompts loaded successfully!")

if __name__ == "__main__":
    test_load_prompts()
