from abc import ABC, abstractmethod
from typing import Dict, List, Any, Generator, Optional


class BaseAgent(ABC):
    def __init__(self, name: str, system_prompt: str, tools: Optional[List[Any]] = None):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []

    @abstractmethod
    def process(self, user_query: str, context: Dict[str, Any], **kwargs) -> Generator[str, None, None]:
        """返回生成器，yield SSE事件
        
        Args:
            user_query: 用户查询字符串
            context: 上下文字典，包含conversation_id、project_key等信息
            **kwargs: 其他参数
            
        Yields:
            SSE格式的事件字符串
        """
        pass