"""LangChain应用主文件：整合工具、LLM、链和Flask路由"""

import os
import re
import json
import time
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

# LangChain核心组件
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableParallel, RunnableLambda

# 自定义组件
from langchain_tools import get_all_tools
from langchain_llm import get_custom_chat_model

# 导入现有功能以重用代码
from e import (
    conversation_history, 
    SYSTEM_PROMPT, 
    DETAILED_REPORT_PROMPT,
    generate_final_jql,
    fetch_tos_issues,
    cluster_common_issues,
    _log
)

# 配置
JIRA_URL = os.getenv("JIRA_URL", "http://jira.transsion.com")
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD", "")

# LLM实例
llm = get_custom_chat_model(temperature=0.7, streaming=False)


def create_analysis_chain():
    """创建分析链"""
    # 基础提示模板
    system_template = SYSTEM_PROMPT
    
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_template),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanMessage(content="{user_query}")
    ])
    
    # 创建链
    chain = prompt | llm | StrOutputParser()
    
    return chain


def create_intent_chain():
    """创建意图识别链"""
    intent_prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="""你是一个意图识别助手。请分析用户的查询，判断其意图属于以下哪一类：
        1. 项目风险分析：用户询问某个项目的风险、问题、Bug等
        2. 共性问题分析：用户询问tOS库的共性问题、模块聚类等
        3. 一般问答：其他问题
        
        请以JSON格式返回结果，格式为：{"intent": "project_risk" 或 "common_issue" 或 "general_qa", "confidence": 0.8, "project_key": "X6840 或 None"}"""),
        HumanMessage(content="{user_query}")
    ])
    
    # 从ChatResult中提取内容的函数
    def extract_content(chat_result):
        """从ChatResult中提取消息内容"""
        # 调试信息
        # print(f"DEBUG extract_content type: {type(chat_result)}")
        
        # chat_result可能是ChatResult或ChatGeneration
        from langchain_core.outputs import ChatResult, ChatGeneration
        
        # 尝试多种提取方式
        try:
            # 如果是ChatResult
            if isinstance(chat_result, ChatResult):
                if chat_result.generations:
                    generation = chat_result.generations[0]
                    if isinstance(generation, ChatGeneration):
                        return generation.message.content
                    elif hasattr(generation, 'message') and hasattr(generation.message, 'content'):
                        return generation.message.content
                    elif hasattr(generation, 'text'):
                        return generation.text
            
            # 如果是ChatGeneration
            elif isinstance(chat_result, ChatGeneration):
                if hasattr(chat_result, 'message') and hasattr(chat_result.message, 'content'):
                    return chat_result.message.content
                elif hasattr(chat_result, 'text'):
                    return chat_result.text
            
            # 如果有content属性
            elif hasattr(chat_result, 'content'):
                return chat_result.content
            
            # 如果有text属性
            elif hasattr(chat_result, 'text'):
                return chat_result.text
            
            # 如果是字典
            elif isinstance(chat_result, dict):
                # 尝试常见键
                if 'content' in chat_result:
                    return chat_result['content']
                elif 'text' in chat_result:
                    return chat_result['text']
                elif 'message' in chat_result:
                    msg = chat_result['message']
                    if isinstance(msg, dict) and 'content' in msg:
                        return msg['content']
        except Exception as e:
            # 提取失败，返回字符串表示
            pass
        
        # 最后返回字符串表示
        return str(chat_result)
    
    # 创建链：prompt -> llm -> 提取内容 -> 解析JSON
    chain = intent_prompt | llm | RunnableLambda(extract_content) | JsonOutputParser()
    return chain


def create_jql_chain():
    """创建JQL生成链"""
    jql_prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="""你是一个JQL生成助手。请根据用户的查询和项目键生成合适的JQL查询语句。
        
        重要说明：
        1. tOS项目有多个版本，必须精确匹配版本号：tOS16.1、tOS16.2、tOS16.3是三个不同的项目
        2. 当用户查询"tOS16.3的项目风险"时，项目键是"tOS16.3"，而不是"tOS16"
        3. 如果用户指定了具体版本，必须使用精确版本号
        
        示例：
        用户查询："X6840项目的bug"
        项目键："X6840"
        生成的JQL："project = X6840 AND type = Bug ORDER BY created DESC"
        
        用户查询："tOS16.3的未解决bug"
        项目键："tOS16.3"
        生成的JQL："project = tOS16.3 AND resolution is empty AND type = Bug ORDER BY priority DESC"
        
        用户查询："tOS16.1最近7天的问题"
        项目键："tOS16.1"
        生成的JQL："project = tOS16.1 AND created >= -7d ORDER BY created DESC"
        
        返回纯JQL语句，不要包含任何解释。"""),
        HumanMessage(content="用户查询: {user_query}\n项目键: {project_key}")
    ])
    
    # 从ChatResult中提取内容的函数
    def extract_content(chat_result):
        """从ChatResult中提取消息内容"""
        # chat_result可能是ChatResult或ChatGeneration
        from langchain_core.outputs import ChatResult, ChatGeneration
        if isinstance(chat_result, ChatResult):
            # 提取第一个generation的消息内容
            if chat_result.generations:
                generation = chat_result.generations[0]
                if isinstance(generation, ChatGeneration):
                    return generation.message.content
        # 如果已经是字符串或其他类型，直接返回
        return str(chat_result)
    
    chain = jql_prompt | llm | RunnableLambda(extract_content) | StrOutputParser()
    return chain


def create_memory(conversation_id: str):
    """为指定会话创建内存（简化版，不使用ConversationBufferMemory）"""
    if conversation_id not in conversation_history:
        conversation_history[conversation_id] = []
    
    # 创建简单的内存对象
    class SimpleMemory:
        def __init__(self, history):
            self.chat_history = []
            # 转换历史记录格式
            for msg in history[-10:]:  # 只加载最近10条消息
                if msg["role"] == "user":
                    self.chat_history.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    self.chat_history.append(AIMessage(content=msg["content"]))
        
        def load_memory_variables(self, inputs):
            return {"chat_history": self.chat_history}
        
        def save_context(self, inputs, outputs):
            # 这里不需要保存，因为我们在conversation_history中保存
            pass
    
    history = conversation_history[conversation_id]
    return SimpleMemory(history)


class LangChainAnalyzer:
    """LangChain分析器主类"""
    
    def __init__(self, jira_username: Optional[str] = None, jira_password: Optional[str] = None, jira_url: Optional[str] = None):
        """初始化分析器，支持自定义Jira凭据"""
        # 保存凭据
        self.jira_username = jira_username or JIRA_USERNAME
        self.jira_password = jira_password or JIRA_PASSWORD
        self.jira_url = jira_url or JIRA_URL
        
        # 使用自定义凭据创建工具
        self.tools = self._create_tools_with_credentials()
        self.intent_chain = create_intent_chain()
        self.jql_chain = create_jql_chain()
        self.general_chain = create_analysis_chain()
        
        # 创建带有工具的代理
        self.agent = self._create_agent()
    
    def _create_tools_with_credentials(self):
        """使用自定义凭据创建工具集"""
        from langchain_tools import JiraQueryTool, JQLGenerationTool, CommonIssueAnalysisTool, RiskAnalysisTool
        
        # 使用自定义凭据创建JiraQueryTool
        jira_tool = JiraQueryTool(
            username=self.jira_username,
            password=self.jira_password,
            url=self.jira_url
        )
        
        # 其他工具使用默认配置
        return [
            jira_tool,
            JQLGenerationTool(),
            CommonIssueAnalysisTool(),
            RiskAnalysisTool()
        ]
    
    def _create_agent(self):
        """创建简单的工具调用器（占位符）"""
        # 在LangChain 1.2.0中，代理API已更改
        # 由于我们有自己的意图识别和路由逻辑，这里返回一个简单的工具调用器
        class SimpleToolInvoker:
            def __init__(self, tools):
                self.tools = tools
            
            def invoke(self, input_text):
                # 简单的工具调用逻辑
                return f"使用工具处理: {input_text}"
        
        return SimpleToolInvoker(self.tools)
    
    def analyze_intent(self, user_query: str, project_key: str = "") -> Dict[str, Any]:
        """分析用户意图"""
        try:
            result = self.intent_chain.invoke({
                "user_query": user_query
            })
            _log("info", f"意图识别原始结果: {result}")
            
            # 提取项目键
            if not result.get("project_key") and project_key:
                # 支持带点号的版本号，如tOS16.3，以及纯字母项目键如XX
                match = re.search(r'[A-Za-z]+\d+(?:\.\d+)?|X?\d{4}(?:-[a-zA-Z0-9]+)?|[A-Z]{2,}', project_key)
                if match:
                    extracted_key = match.group()
                    if re.match(r'^\d+$', extracted_key):
                        extracted_key = 'X' + extracted_key
                    result["project_key"] = extracted_key
            
            return result
        except Exception as e:
            _log("error", f"意图分析失败: {e}, user_query={user_query}, project_key={project_key}")
            return {"intent": "general_qa", "confidence": 0.5, "project_key": project_key}
    
    def generate_jql(self, user_query: str, project_key: str) -> str:
        """生成JQL语句"""
        try:
            jql = self.jql_chain.invoke({
                "user_query": user_query,
                "project_key": project_key
            })
            
            # 清理JQL
            jql = jql.strip()
            if jql.startswith('"') and jql.endswith('"'):
                jql = jql[1:-1]
            
            # 检查版本特异性：如果查询的是具体tOS版本，但JQL包含了所有版本，则使用模板匹配后备
            specific_tos_versions = ['tOS16.1', 'tOS16.2', 'tOS16.3']
            all_versions_in_jql = all(version in jql for version in specific_tos_versions)
            
            if project_key in specific_tos_versions and all_versions_in_jql:
                _log("warning", f"AI生成的JQL包含所有tOS16版本，但查询的是具体版本 {project_key}，使用模板匹配后备")
                # 使用模板匹配的后备方案
                try:
                    from e import generate_final_jql, extract_project_key, recognize_intent, match_jql_template
                    # 提取项目键和识别意图
                    extracted_key = extract_project_key(user_query) or project_key
                    intent = recognize_intent(user_query)
                    intent['project'] = extracted_key  # 确保使用正确的项目键
                    template = match_jql_template(intent)
                    
                    if template and 'projects' in template and extracted_key in template['projects']:
                        template_jql = template['projects'][extracted_key]
                        _log("info", f"使用模板匹配的JQL: {template_jql[:100]}...")
                        return template_jql
                except Exception as template_error:
                    _log("error", f"模板匹配后备失败: {template_error}")
            
            # 修复编码问题：RT_交付测试部中的空格
            if "RT_" in jql and "交付测试" in jql:
                import re
                # 修复RT_交付测试 部中的空格
                jql = re.sub(r'RT_交付测试\s+部', 'RT_交付测试部', jql)
                _log("debug", f"修复JQL编码空格")
            
            _log("info", f"生成的JQL: {jql[:100]}...")
            return jql
        except Exception as e:
            _log("error", f"JQL生成失败: {e}")
            # 使用现有函数作为后备
            try:
                from e import generate_final_jql
                return generate_final_jql(user_query)
            except:
                return f'project = {project_key} AND type = Bug ORDER BY created DESC'
    
    def analyze_project_risk(self, user_query: str, project_key: str, conversation_id: str) -> str:
        """分析项目风险"""
        try:
            _log("debug", f"开始分析项目风险: query={user_query}, project={project_key}")
            _log("debug", f"user_query原始值: '{user_query}', 类型: {type(user_query)}, 长度: {len(user_query)}")
            
            # 生成JQL
            jql = self.generate_jql(user_query, project_key)
            _log("debug", f"生成的JQL: {jql}")
            
            # 获取Jira数据
            jira_tool = self.tools[0]  # JiraQueryTool
            _log("debug", f"开始查询Jira数据，最大结果数: 100")
            _log("debug", f"Jira查询工具类型: {type(jira_tool)}")
            _log("debug", f"Jira查询工具属性: {dir(jira_tool)}")
            _log("debug", f"JQL: {jql}")
            try:
                issues = jira_tool._run(jql=jql, max_results=100)
                _log("debug", f"获取到Jira问题数量: {len(issues) if issues else 0}")
                if issues:
                    _log("debug", f"第一个问题key: {issues[0].get('key', '未知')}")
                else:
                    _log("warn", f"Jira查询返回空结果，可能原因: 1) JQL无效 2) 凭据无效 3) 网络问题")
            except Exception as e:
                _log("error", f"Jira查询异常: {e}")
                import traceback
                _log("error", f"异常详情: {traceback.format_exc()}")
                issues = []
            
            if not issues:
                _log("warn", f"在项目 {project_key} 中未找到匹配的问题，JQL: {jql}")
                # 尝试使用简单的JQL作为后备
                simple_jql = f'project = {project_key} AND type = Bug ORDER BY created DESC'
                _log("info", f"尝试后备JQL: {simple_jql}")
                try:
                    issues = jira_tool._run(jql=simple_jql, max_results=50)
                    _log("debug", f"后备查询结果数量: {len(issues) if issues else 0}")
                except Exception as e2:
                    _log("error", f"后备Jira查询也失败: {e2}")
                    issues = []
            
            if not issues:
                return f"在项目 {project_key} 中未找到匹配的问题。"
            
            # 分析风险
            risk_tool = self.tools[3]  # RiskAnalysisTool
            _log("debug", f"开始风险分析")
            risk_result = risk_tool._run(issues=issues, project_key=project_key)
            _log("debug", f"风险分析完成，结果包含 {len(risk_result) if isinstance(risk_result, dict) else '未知'} 个键")
            
            # 生成分析报告，根据用户意图调整输出详细程度
            analysis_prompt = f"""基于以下Jira风险分析数据，为用户提供专业的风险分析：

## 数据分析结果
```json
{json.dumps(risk_result, indent=2, ensure_ascii=False)}
```

## 用户查询
{user_query}

## 项目标识
{project_key}

## 分析要求
1. **数据驱动分析**：所有结论必须基于上述Jira数据，引用具体的问题ID和统计数据
2. **意图导向输出**：根据用户查询的意图调整输出的详细程度、格式和内容
3. **自然专业对话**：使用自然、专业的语言，像专家顾问一样提供分析，避免机械的模板式回答
4. **重点突出**：只关注最关键的问题和建议，避免信息过载
5. **实用建议**：提供具体、可执行的建议，明确责任人和时间预期

## 输出风格指导
- **对于整体风险分析**（如"分析X6840的项目风险"）：提供风险概览、主要问题、风险等级、建议措施，可以使用适当的结构但不必严格遵守固定格式
- **对于单领域查询**（如"X6840的稳定性问题"）：直接列出该领域的关键问题ID、简要描述和核心结论，使用简洁的列表格式
- **对于简单查询**（如"X6840有多少个未解决bug"）：直接给出数据和简要分析
- **自然语言优先**：使用自然流畅的中文，适当使用emoji增强可读性（如🎯📊🔴🟡📝📈）
- **数据支持**：所有结论必须有数据支持，引用具体的Jira问题ID和统计数据

请基于上述Jira数据，以专业顾问的身份提供风险分析。记住：数据是基础，意图是导向，自然对话是目标。"""
            _log("info", f"分析提示词预览: {analysis_prompt[:1000]}...")
            # 调试：检查提示词中是否包含未替换的占位符
            if '{user_query}' in analysis_prompt:
                _log("error", f"警告：分析提示词中包含未替换的占位符 '{{user_query}}'")
                _log("debug", f"user_query值: '{user_query}'")
            # 记录分析提示词的关键部分，确保变量替换正确
            _log("debug", f"分析提示词中user_query替换检查: '{user_query}' 是否在提示词中: {user_query in analysis_prompt}")
            _log("debug", f"分析提示词中project_key替换检查: '{project_key}' 是否在提示词中: {project_key in analysis_prompt}")
            _log("debug", f"分析提示词中是否包含JSON数据: {'risk_result' in analysis_prompt}")
            
            response = llm.invoke([HumanMessage(content=analysis_prompt)])
            _log("debug", f"AI响应类型: {type(response)}, 是否有content属性: {hasattr(response, 'content')}")
            response_content = response.content if hasattr(response, 'content') else str(response)
            _log("debug", f"AI响应内容预览: {response_content[:500]}...")
            _log("info", f"AI响应内容 (前1000字符): {response_content[:1000]}")
            # 检查响应中是否包含占位符
            if '{user_query}' in response_content:
                _log("error", f"AI响应中包含占位符 '{{user_query}}'")
                _log("debug", f"完整响应: {response_content}")
            return response_content
            
        except Exception as e:
            _log("error", f"项目风险分析失败: {e}")
            return f"项目风险分析失败: {str(e)}"
    
    def analyze_common_issues(self, user_query: str, time_range: str = "7天") -> str:
        """分析共性问题"""
        try:
            # 使用共性问题分析工具
            common_issue_tool = self.tools[2]  # CommonIssueAnalysisTool
            result = common_issue_tool._run(time_range=time_range)
            
            # 生成分析报告，根据用户意图调整输出详细程度
            analysis_prompt = f"""基于以下共性问题分析结果，为用户提供分析报告：

共性问题分析结果:
{json.dumps(result, indent=2, ensure_ascii=False)}

用户查询: {user_query}
时间范围: {time_range}

**输出要求：**
1. **根据用户查询的意图和范围调整输出的详细程度和结构**
2. 如果用户询问的是特定模块、特定类型或单领域的共性问题，只输出该领域的关键问题、ID和核心结论，不要提供完整的结构化报告
3. 如果用户询问的是整体分析，可以输出完整报告，但重点突出关键结论和数据
4. 始终以用户意图为中心，只输出与用户查询最相关的信息和数据
5. **对于单领域查询**：直接列出该领域的共性问题ID、简要描述和关键结论，不要使用结构化格式
6. **对于整体查询**：可以输出结构化报告，但重点突出关键数据和结论，避免冗长描述
7. 确保输出简洁、聚焦，只包含关键信息和必要数据

请根据用户查询的意图和范围，提供最合适的共性问题分析报告。"""
            
            response = llm.invoke([HumanMessage(content=analysis_prompt)])
            return response.content if hasattr(response, 'content') else str(response)
            
        except Exception as e:
            _log("error", f"共性问题分析失败: {e}")
            return f"共性问题分析失败: {str(e)}"
    
    def general_qa(self, user_query: str, conversation_id: str) -> str:
        """一般问答"""
        try:
            # 获取会话内存
            memory = create_memory(conversation_id)
            
            # 准备聊天历史
            chat_history = memory.load_memory_variables({}).get("chat_history", [])
            
            # 调用通用链
            _log("info", f"general_qa调用: user_query={user_query}, chat_history长度={len(chat_history)}")
            response = self.general_chain.invoke({
                "user_query": user_query,
                "chat_history": chat_history
            })
            
            # 更新内存
            memory.save_context({"input": user_query}, {"output": response})
            
            # 更新全局历史记录
            if conversation_id in conversation_history:
                conversation_history[conversation_id].append({"role": "user", "content": user_query})
                conversation_history[conversation_id].append({"role": "assistant", "content": response})
            
            return response
        except Exception as e:
            _log("error", f"一般问答失败: {e}")
            return f"问答失败: {str(e)}"
    
    def analyze_stream(self, user_query: str, project_key: str, conversation_id: str, detailed_report: bool = False) -> str:
        """流式分析（简化版）"""
        # 分析意图
        intent_result = self.analyze_intent(user_query, project_key)
        intent = intent_result.get("intent", "general_qa")
        project_key = intent_result.get("project_key", project_key)
        
        _log("info", f"识别意图: {intent}, 项目键: {project_key}")
        
        # 根据意图路由
        if intent == "project_risk" and project_key:
            return self.analyze_project_risk(user_query, project_key, conversation_id)
        elif intent == "common_issue":
            # 尝试从查询中提取时间范围
            time_range = "7天"
            if "30天" in user_query or "一个月" in user_query:
                time_range = "30天"
            elif "全部" in user_query or "所有" in user_query:
                time_range = "全部"
            
            return self.analyze_common_issues(user_query, time_range)
        else:
            return self.general_qa(user_query, conversation_id)


# 全局分析器实例（默认凭据）
_default_analyzer = None
# 按凭据存储的分析器实例
_credentialed_analyzers = {}
import hashlib

def get_analyzer(jira_username: Optional[str] = None, jira_password: Optional[str] = None, jira_url: Optional[str] = None) -> LangChainAnalyzer:
    """获取分析器实例，支持自定义凭据"""
    global _default_analyzer, _credentialed_analyzers
    
    # 如果没有提供自定义凭据，返回默认分析器（向后兼容）
    if jira_username is None and jira_password is None and jira_url is None:
        if _default_analyzer is None:
            _default_analyzer = LangChainAnalyzer()
        return _default_analyzer
    
    # 使用凭据创建唯一键
    cred_key = hashlib.md5(f"{jira_username}:{jira_password}:{jira_url}".encode()).hexdigest()
    
    # 检查是否已存在该凭据的分析器
    if cred_key not in _credentialed_analyzers:
        _credentialed_analyzers[cred_key] = LangChainAnalyzer(
            jira_username=jira_username,
            jira_password=jira_password,
            jira_url=jira_url
        )
        _log("info", f"创建新的分析器实例，凭据键: {cred_key[:8]}...")
    
    return _credentialed_analyzers[cred_key]


def test_langchain_integration():
    """测试LangChain集成"""
    analyzer = get_analyzer()
    
    print("测试1: 意图识别")
    intent = analyzer.analyze_intent("X6840项目的风险分析")
    print(f"  结果: {intent}")
    
    print("\n测试2: JQL生成")
    jql = analyzer.generate_jql("X6840的bug", "X6840")
    print(f"  结果: {jql}")
    
    print("\n测试3: 一般问答")
    response = analyzer.general_qa("你好，介绍一下这个系统", "test_session")
    print(f"  结果: {response[:100]}...")
    
    print("\nLangChain集成测试完成！")


if __name__ == "__main__":
    test_langchain_integration()