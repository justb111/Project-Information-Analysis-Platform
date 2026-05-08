"""
LangChain链实现
提供AI分析功能
"""

import os
import sys
from typing import Dict, Any, Optional

# 添加项目根目录到路径，以便导入其他模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    # 从现有的langchain_components模块导入分析函数
    from langchain_components import analyze_with_langchain as _analyze_with_langchain
    from langchain_components import create_analysis_chain, create_llm_from_config, format_jira_data
    from langchain_components import StreamingHandler, SimpleMemory
    
    # 重新导出函数，保持兼容性
    def analyze_with_langchain(user_query: str, jira_data: str, sse_callback=None, ai_config: dict = None, timeout: int = 120) -> str:
        """
        使用LangChain进行分析（增强版：重试机制、详细日志、可靠超时）
        
        Args:
            user_query: 用户查询
            jira_data: 格式化后的Jira数据
            sse_callback: 可选的SSE回调函数（暂时不使用）
            ai_config: AI配置字典，包含api_key、base_url、model等字段
            timeout: 超时时间（秒），默认120秒（2分钟）
            
        Returns:
            AI分析结果或错误消息（以"AI分析失败:"开头的表示不可恢复错误）
        """
        # 直接调用现有的实现
        return _analyze_with_langchain(user_query, jira_data, sse_callback, ai_config, timeout)
    
    # 导出其他可能需要的函数
    __all__ = [
        "analyze_with_langchain",
        "create_analysis_chain",
        "create_llm_from_config",
        "format_jira_data",
        "StreamingHandler",
        "SimpleMemory"
    ]
    
except ImportError as e:
    print(f"[WARNING] llm.chains: 无法导入langchain_components: {e}")
    print("[WARNING] llm.chains: 将使用简化实现")
    
    # 简化实现（仅用于测试）
    def analyze_with_langchain(user_query: str, jira_data: str, sse_callback=None, ai_config: dict = None, timeout: int = 120) -> str:
        """
        简化版的LangChain分析函数（占位符）
        
        注意：这只是一个占位符实现，实际使用时应该安装langchain相关依赖
        并配置正确的AI服务。
        """
        print(f"[SIMPLIFIED] analyze_with_langchain called with query: {user_query[:50]}...")
        print(f"[SIMPLIFIED] jira_data length: {len(jira_data)} chars")
        
        # 返回模拟的AI分析结果
        return f"""【模拟AI分析结果】
用户问题：{user_query}

基于提供的Jira数据（共{len(jira_data)}字符），以下是分析：

1. **【执行摘要】**
   - 风险评级：🟡黄色（中等风险）
   - 核心风险：数据量有限，需要更多上下文进行深度分析
   - 影响评估：对项目交付有中等影响

2. **【建议】**
   - 请确保AI服务配置正确（检查.env文件中的AI_API_KEY、AI_BASE_URL、AI_MODEL）
   - 安装必要的依赖：pip install langchain-openai langchain-core
   - 检查网络连接，确保可以访问AI服务

注意：这是简化版实现，请配置完整的LangChain环境以获得真正的AI分析能力。"""
    
    # 导出简化函数
    __all__ = ["analyze_with_langchain"]