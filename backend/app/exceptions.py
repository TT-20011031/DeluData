"""
DeluData 智能问数系统 - 自定义异常

定义业务相关的异常类，用于控制流转
"""


class ExecutionError(Exception):
    """
    执行错误 - 可恢复的执行失败
    
    当 Worker 执行失败但可以通过用户提供更多信息恢复时使用
    """
    def __init__(
        self, 
        message: str, 
        suggestions: list = None,
        recoverable: bool = True
    ):
        self.message = message
        self.suggestions = suggestions or []
        self.recoverable = recoverable
        super().__init__(message)
    
    def to_dict(self) -> dict:
        return {
            "type": "execution_error",
            "message": self.message,
            "suggestions": self.suggestions,
            "recoverable": self.recoverable
        }
