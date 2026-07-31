"""
Prompt 管理器

从 YAML 文件加载提示词，实现 Prompt 与业务逻辑解耦
"""
import os
import yaml
from typing import Dict, Any, Optional
from functools import lru_cache
from pathlib import Path

# 提示词目录 (文件位于 app/core/llm/, prompts 位于 backend/prompts)
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"


class PromptManager:
    """
    提示词管理器
    
    从 YAML 文件加载提示词模板，支持变量替换
    """
    
    def __init__(self, prompts_dir: Optional[Path] = None):
        """
        初始化提示词管理器
        
        Args:
            prompts_dir: 提示词目录路径，默认为 backend/prompts
        """
        self.prompts_dir = prompts_dir or PROMPTS_DIR
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_all_prompts()
    
    def _load_all_prompts(self) -> None:
        """加载所有提示词文件"""
        if not self.prompts_dir.exists():
            os.makedirs(self.prompts_dir, exist_ok=True)
            return
            
        for yaml_file in self.prompts_dir.glob("*.yaml"):
            module_name = yaml_file.stem
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    self._cache[module_name] = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Warning: Failed to load {yaml_file}: {e}")
    
    def get(
        self, 
        key: str, 
        module: Optional[str] = None,
        **variables
    ) -> str:
        """
        获取提示词
        
        Args:
            key: 提示词 key，格式为 "module.prompt_name.type" 或 "prompt_name"
            module: 模块名（可选，如果 key 中包含模块名则忽略）
            **variables: 模板变量
            
        Returns:
            格式化后的提示词字符串
            
        Example:
            >>> pm = PromptManager()
            >>> pm.get("supervisor.supervisor_main.system")
            >>> pm.get("sql_generate.user", module="sql_worker", task_description="查询销售额")
        """
        # 解析 key
        parts = key.split(".")
        
        if len(parts) == 3:
            # 完整路径: module.prompt_name.type
            module_name, prompt_name, prompt_type = parts
        elif len(parts) == 2:
            # 部分路径: prompt_name.type
            if module:
                module_name = module
                prompt_name, prompt_type = parts
            else:
                # 尝试从第一部分推断模块
                module_name = parts[0]
                prompt_name = parts[0]
                prompt_type = parts[1]
        elif len(parts) == 1:
            # 只有 prompt_name，尝试查找
            prompt_name = parts[0]
            prompt_type = "system"
            module_name = module or self._find_module_for_prompt(prompt_name)
        else:
            raise ValueError(f"Invalid prompt key format: {key}")
        
        # 获取提示词
        if module_name not in self._cache:
            raise KeyError(f"Module not found: {module_name}")
            
        module_prompts = self._cache[module_name]
        
        if prompt_name not in module_prompts:
            raise KeyError(f"Prompt not found: {prompt_name} in module {module_name}")
            
        prompt_config = module_prompts[prompt_name]
        
        if isinstance(prompt_config, str):
            template = prompt_config
        elif isinstance(prompt_config, dict):
            template = prompt_config.get(prompt_type, "")
        else:
            template = str(prompt_config)
        
        # 变量替换
        if variables:
            try:
                template = template.format(**variables)
            except KeyError as e:
                # 部分变量未提供，保留原始占位符
                for var, value in variables.items():
                    template = template.replace(f"{{{var}}}", str(value))
        
        return template
    
    def _find_module_for_prompt(self, prompt_name: str) -> str:
        """在所有模块中查找包含指定 prompt 的模块"""
        for module_name, prompts in self._cache.items():
            if prompt_name in prompts:
                return module_name
        raise KeyError(f"Prompt not found in any module: {prompt_name}")
    
    def reload(self) -> None:
        """重新加载所有提示词"""
        self._cache.clear()
        self._load_all_prompts()
    
    def list_modules(self) -> list:
        """列出所有模块"""
        return list(self._cache.keys())
    
    def list_prompts(self, module: str) -> list:
        """列出模块中的所有提示词"""
        if module not in self._cache:
            return []
        return list(self._cache[module].keys())


# 单例实例
@lru_cache(maxsize=1)
def get_prompt_manager() -> PromptManager:
    """获取提示词管理器单例"""
    return PromptManager()


# 便捷函数
def get_prompt(key: str, **variables) -> str:
    """
    获取提示词的便捷函数
    
    Example:
        >>> from app.core.llm.prompt_manager import get_prompt
        >>> prompt = get_prompt("supervisor.supervisor_main.system")
    """
    return get_prompt_manager().get(key, **variables)
