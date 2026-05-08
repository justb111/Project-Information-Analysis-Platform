"""LangChain LLM包装器：包装现有的AI服务调用"""

import os
import json
from typing import Any, Dict, Iterator, List, Optional, Union
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.llms import LLM
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult, Generation
from langchain_core.runnables import RunnableConfig
import httpx

# 导入现有的AI调用函数
from utils import call_ai_api

# 环境变量
AI_API_KEY = os.getenv("AI_API_KEY", "sk_0f04e27baf7fd49de98314bc793b943e2514b72afaf9f67af8676a2")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.4")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://hk-intra-paas.transsion.com/tranai-proxy/v1")

# 服务器共享身份信息
SERVER_USER_NO = "JIRA_RISK_SERVER"
SERVER_USER_NAME = "Jira风险分析服务器"
SERVER_USER_DEPT_NAME = "公共分析服务"


class CustomAILLM(LLM):
    """自定义AI LLM包装器，包装现有的call_ai_api函数"""
    
    model_name: str = AI_MODEL
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    streaming: bool = False
    
    @property
    def _llm_type(self) -> str:
        return "custom_tranai_llm"
    
    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """同步调用AI服务"""
        # 构建消息列表
        messages = [{"role": "user", "content": prompt}]
        
        # 调用现有的AI API函数
        response = call_ai_api(
            messages=messages,
            system_prompt=None,
            temperature=self.temperature,
            stream=False,
            max_retries=3,
            retry_delay=5
        )
        
        if response and response.status_code == 200:
            try:
                response_data = response.json()
                choices = response_data.get('choices', [])
                if choices and len(choices) > 0:
                    message = choices[0].get('message', {})
                    content = message.get('content', '')
                    if content:
                        return content
                    else:
                        return "AI响应内容为空"
                else:
                    return "AI响应中没有choices数据"
            except Exception as e:
                return f"解析AI响应失败: {str(e)}"
        elif response:
            return f"AI服务调用失败，状态码: {response.status_code}"
        else:
            return "AI服务调用失败"
    
    async def _acall(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """异步调用AI服务"""
        # 简单实现，直接调用同步版本
        return self._call(prompt, stop, run_manager, **kwargs)


class CustomAIChatModel(BaseChatModel):
    """自定义AI聊天模型，支持消息格式和流式响应"""
    
    model_name: str = AI_MODEL
    temperature: float = 0.7
    streaming: bool = False
    
    @property
    def _llm_type(self) -> str:
        return "custom_tranai_chat_model"
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应"""
        # 转换消息格式
        openai_messages = []
        for message in messages:
            if isinstance(message, HumanMessage):
                openai_messages.append({"role": "user", "content": message.content})
            elif isinstance(message, SystemMessage):
                openai_messages.append({"role": "system", "content": message.content})
            elif isinstance(message, AIMessage):
                openai_messages.append({"role": "assistant", "content": message.content})
            else:
                # 默认作为用户消息
                openai_messages.append({"role": "user", "content": message.content})
        
        # 检查是否有系统消息
        system_prompt = None
        if openai_messages and openai_messages[0]["role"] == "system":
            system_prompt = openai_messages[0]["content"]
            openai_messages = openai_messages[1:]
        
        # 调用现有的AI API函数
        response = call_ai_api(
            messages=openai_messages,
            system_prompt=system_prompt,
            temperature=self.temperature,
            stream=False,
            max_retries=3,
            retry_delay=5
        )
        
        if response and response.status_code == 200:
            try:
                response_data = response.json()
                choices = response_data.get('choices', [])
                if choices and len(choices) > 0:
                    message = choices[0].get('message', {})
                    content = message.get('content', '')
                    if content:
                        # 创建ChatResult
                        generation = ChatGeneration(
                            message=AIMessage(content=content),
                            generation_info={}
                        )
                        return ChatResult(generations=[generation])
                    else:
                        # 返回错误消息
                        generation = ChatGeneration(
                            message=AIMessage(content="AI响应内容为空"),
                            generation_info={"error": True}
                        )
                        return ChatResult(generations=[generation])
                else:
                    # 返回错误消息
                    generation = ChatGeneration(
                        message=AIMessage(content="AI响应中没有choices数据"),
                        generation_info={"error": True}
                    )
                    return ChatResult(generations=[generation])
            except Exception as e:
                # 返回错误消息
                generation = ChatGeneration(
                    message=AIMessage(content=f"解析AI响应失败: {str(e)}"),
                    generation_info={"error": True}
                )
                return ChatResult(generations=[generation])
        elif response:
            # 返回错误消息
            generation = ChatGeneration(
                message=AIMessage(content=f"AI服务调用失败，状态码: {response.status_code}"),
                generation_info={"error": True}
            )
            return ChatResult(generations=[generation])
        else:
            # 返回错误消息
            generation = ChatGeneration(
                message=AIMessage(content="AI服务调用失败"),
                generation_info={"error": True}
            )
            return ChatResult(generations=[generation])
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步生成聊天响应"""
        # 简单实现，直接调用同步版本
        return self._generate(messages, stop, run_manager, **kwargs)
    
    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGeneration]:
        """流式生成聊天响应"""
        # 转换消息格式
        openai_messages = []
        for message in messages:
            if isinstance(message, HumanMessage):
                openai_messages.append({"role": "user", "content": message.content})
            elif isinstance(message, SystemMessage):
                openai_messages.append({"role": "system", "content": message.content})
            elif isinstance(message, AIMessage):
                openai_messages.append({"role": "assistant", "content": message.content})
            else:
                openai_messages.append({"role": "user", "content": message.content})
        
        # 检查是否有系统消息
        system_prompt = None
        if openai_messages and openai_messages[0]["role"] == "system":
            system_prompt = openai_messages[0]["content"]
            openai_messages = openai_messages[1:]
        
        # 调用现有的AI API函数，启用流式
        try:
            response = call_ai_api(
                messages=openai_messages,
                system_prompt=system_prompt,
                temperature=self.temperature,
                stream=True,
                max_retries=3,
                retry_delay=5
            )
            
            if response:
                # 模拟流式响应
                yield ChatGeneration(
                    message=AIMessage(content=response),
                    generation_info={}
                )
            else:
                yield ChatGeneration(
                    message=AIMessage(content="AI服务调用失败"),
                    generation_info={"error": True}
                )
        except Exception as e:
            yield ChatGeneration(
                message=AIMessage(content=f"AI服务异常: {str(e)}"),
                generation_info={"error": True}
            )
    
    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "CustomAIChatModel":
        """绑定工具（简化实现）"""
        # 返回自身，表示支持工具调用
        return self
    
    @property
    def bind_tools(self):
        """工具绑定属性"""
        return lambda tools, **kwargs: self


# LLM实例工厂函数
def get_custom_llm(temperature: float = 0.7, streaming: bool = False) -> CustomAILLM:
    """获取自定义LLM实例"""
    return CustomAILLM(
        model_name=AI_MODEL,
        temperature=temperature,
        streaming=streaming
    )


def get_custom_chat_model(temperature: float = 0.7, streaming: bool = False) -> CustomAIChatModel:
    """获取自定义聊天模型实例"""
    return CustomAIChatModel(
        model_name=AI_MODEL,
        temperature=temperature,
        streaming=streaming
    )