"""
DeluData 智能问数系统 - 技能基类

定义所有 Skill 的公共接口和基础功能
"""
from abc import ABC, abstractmethod
from typing import Any, Optional
import logging

from app.models.common.context import UserContext


class BaseSkill(ABC):
    """
    技能基类
    
    所有 L4 能力层的技能都应继承此类
    
    设计原则：
    1. 无状态：技能本身不保存状态
    2. 原子化：每个技能只做一件事
    3. 可复用：技能可被多个 Agent 调用
    """
    
    def __init__(self, name: str):
        """
        初始化技能
        
        Args:
            name: 技能名称，用于日志和追踪
        """
        self.name = name
        self.logger = logging.getLogger(f"skill.{name}")
    
    @abstractmethod
    async def execute(self, *args, **kwargs) -> Any:
        """
        执行技能
        
        子类必须实现此方法
        """
        pass
    
    def log_execution(self, action: str, details: Optional[str] = None):
        """记录技能执行日志"""
        msg = f"[{self.name}] {action}"
        if details:
            msg += f": {details}"
        self.logger.info(msg)
    
    def log_error(self, error: Exception, context: Optional[str] = None):
        """记录错误日志"""
        msg = f"[{self.name}] Error"
        if context:
            msg += f" in {context}"
        self.logger.error(f"{msg}: {error}", exc_info=True)


class SecureSkill(BaseSkill):
    """
    需要权限校验的技能基类
    
    继承此类的技能会在执行前进行权限校验
    """
    
    def validate_user_context(self, user_context: UserContext) -> bool:
        """
        验证用户上下文有效性
        
        Args:
            user_context: 用户上下文
            
        Returns:
            是否有效
        """
        if not user_context:
            self.log_error(ValueError("用户上下文不能为空"))
            return False
        if not user_context.user_id:
            self.log_error(ValueError("用户ID不能为空"))
            return False
        return True
