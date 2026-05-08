#!/usr/bin/env python3
# 测试智能Agent的核心功能

import sys
import os
import httpx
from openai import OpenAI

# 避免e.py中的模块级代码执行
# AI_API_KEY 请通过 .env 文件或环境变量设置
os.environ['AI_MODEL'] = 'gpt-5.4'

# 重定向命令行参数，避免argparse解析错误
sys.argv = [sys.argv[0]]

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 初始化必要的客户端
AI_BASE_URL = "https://hk-intra-paas.transsion.com/tranai-proxy/v1"
AI_API_KEY = os.getenv("AI_API_KEY")
X_USER_NO = os.getenv("X_USER_NO", "18654794")

# 自定义httpx客户端
class CustomClient:
    def __init__(self):
        def add_custom_headers(request):
            request.headers["Authorization"] = f"Bearer {AI_API_KEY}"
            request.headers["X-USER-NO"] = X_USER_NO
        
        self.client = httpx.Client(
            event_hooks={"request": [add_custom_headers]},
            timeout=httpx.Timeout(120.0, connect=10.0)
        )

# 导入必要的模块
from e import IntelligentAgent, get_current_time, jql_search, fetch_jira_issues, analyze_risks

# 测试智能Agent
def test_agent():
    print("测试智能Agent...")
    
    # 测试时间感知工具
    print("\n测试时间感知工具:")
    current_time = get_current_time()
    print(f"当前时间: {current_time}")
    
    # 测试JQL搜索工具
    print("\n测试JQL搜索工具:")
    user_query = "分析今日交付测试部未解决的MP block问题"
    jql = jql_search(user_query)
    print(f"生成的JQL: {jql}")
    
    # 初始化智能Agent
    print("\n初始化智能Agent...")
    agent = IntelligentAgent()
    
    # 测试查询
    print(f"\n用户查询: {user_query}")
    
    try:
        # 运行Agent
        result = agent.run(user_query)
        
        # 打印结果
        print("\n=== 思考过程 ===")
        print(result['thinking'])
        print("\n=== 最终答案 ===")
        print(result['answer'])
        
        print("\n测试成功！")
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_agent()
