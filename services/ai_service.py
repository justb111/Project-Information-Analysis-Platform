"""
AI服务模块
提供AI分析功能的统一接口
"""

import os
import sys

# 添加项目根目录到路径，以便导入其他模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    # 从llm.chains模块导入分析函数
    from llm.chains import analyze_with_langchain
    
    # 直接重新导出，保持兼容性
    __all__ = ["analyze_with_langchain"]
    
except ImportError as e:
    print(f"[WARNING] services.ai_service: 无法导入llm.chains: {e}")
    print("[WARNING] services.ai_service: 将使用简化实现")
    
    # 简化实现（仅用于测试）
    def analyze_with_langchain(user_query: str, jira_data: str, sse_callback=None, ai_config: dict = None, timeout: int = 120) -> str:
        """
        简化版的AI分析函数（占位符）
        """
        print(f"[SIMPLIFIED] ai_service.analyze_with_langchain called with query: {user_query[:50]}...")
        
        # 返回模拟的AI分析结果
        return f"""【模拟AI分析结果 - 来自ai_service】
用户问题：{user_query}

这是来自services.ai_service模块的简化实现。请确保：
1. llm.chains模块已正确配置
2. LangChain依赖已安装
3. AI服务配置正确

当前数据长度：{len(jira_data)}字符"""
    
    __all__ = ["analyze_with_langchain"]