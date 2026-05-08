"""LangChain组件：LLM、Prompt、Memory、Chain和StreamingHandler（基于langchain_app.py模式）"""

import os
import sys
import re
from typing import Any, Dict, List, Union, Optional
from datetime import datetime
import json
import queue
import threading
import httpx  # 添加httpx支持，用于自定义HTTP客户端

# 加载环境变量（确保在导入其他模块之前加载）
from dotenv import load_dotenv
# 计算项目根目录路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(project_root, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"[langchain_components] 已加载环境变量文件: {env_path}")
else:
    print(f"[langchain_components] 警告: 未找到环境变量文件: {env_path}")
    # 尝试从当前目录加载
    load_dotenv()
    
# 检查关键环境变量
required_env_vars = ['AI_API_KEY', 'AI_BASE_URL', 'AI_MODEL']
for var in required_env_vars:
    value = os.getenv(var)
    if not value:
        print(f"[langchain_components] 警告: 环境变量 {var} 未设置")
    else:
        print(f"[langchain_components] {var}: {'*' * min(8, len(value))}... (长度: {len(value)})")

# LangChain核心组件（使用与langchain_app.py相同的导入）
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult, ChatResult, ChatGeneration
from langchain_openai import ChatOpenAI


class StreamingHandler(BaseCallbackHandler):
    """流式回调处理器，将LLM输出的token通过SSE发送"""
    
    def __init__(self, sse_callback=None):
        """
        初始化StreamingHandler
        
        Args:
            sse_callback: 可调用函数，接收(event_type, data)参数，用于发送SSE事件
        """
        self.sse_callback = sse_callback
        self.buffer = []
        self.sentence_delimiters = {'。', '！', '？', '\n', '.', '!', '?'}
        
    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """当LLM生成新token时调用"""
        if self.sse_callback:
            # 将token添加到缓冲区
            self.buffer.append(token)
            
            # 检查是否到达句子边界或缓冲区长度达到10个token
            if (len(self.buffer) >= 10 or 
                any(delimiter in token for delimiter in self.sentence_delimiters)):
                # 发送缓冲区内容
                text = ''.join(self.buffer)
                self.sse_callback('answer', text)
                self.buffer.clear()
            else:
                # 单个token发送（用于即时反馈）
                self.sse_callback('answer', token)
    
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """当LLM生成结束时调用，发送剩余缓冲区内容"""
        if self.buffer and self.sse_callback:
            text = ''.join(self.buffer)
            self.sse_callback('answer', text)
            self.buffer.clear()


class SimpleMemory:
    """简单的内存类，模拟ConversationBufferWindowMemory（基于langchain_app.py）"""
    
    def __init__(self, history=None, k=5):
        """
        初始化简单内存
        
        Args:
            history: 初始历史记录列表，格式为[{"role": "user", "content": "..."}, ...]
            k: 保留的历史记录条数
        """
        self.k = k
        self.chat_history = []
        
        if history:
            # 转换历史记录格式
            for msg in history[-k:]:  # 只加载最近k条消息
                if msg.get("role") == "user":
                    self.chat_history.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    self.chat_history.append(AIMessage(content=msg.get("content", "")))
    
    def load_memory_variables(self, inputs):
        """加载内存变量"""
        return {"history": self.chat_history}
    
    def save_context(self, inputs, outputs):
        """保存上下文（这里不需要，因为我们在外部存储历史）"""
        pass
    
    def clear(self):
        """清空内存"""
        self.chat_history.clear()


class ContextMemory:
    """上下文记忆管理器 - 追踪项目名、查询类型、用户偏好等

    在单个会话中有效，无需外部数据库。支持：
    - 项目名称追踪
    - 查询类型追踪
    - 代词指代解析
    - 模糊意图检测
    - 记忆重置
    """

    # 代词/指代表达式映射
    PRONOUN_PATTERNS = [
        (r'它', 'project'),
        (r'这个项目', 'project'),
        (r'那个项目', 'project'),
        (r'该项目', 'project'),
        (r'此项目', 'project'),
        (r'当前项目', 'project'),
        (r'该\s*项目', 'project'),
    ]

    # 继续/进一步分析关键词
    CONTINUE_PATTERNS = [
        r'继续',
        r'继续分析',
        r'接着说',
        r'还有呢',
        r'然后呢',
        r'再\s*[看查分说]',
        r'继续\s*[看查分说]',
    ]

    # 重置关键词
    RESET_PATTERNS = [
        r'换\s*一\s*个\s*项目',
        r'换项目',
        r'不讨论这个',
        r'不说[了那这]',
        r'切换项目',
        r'重置.*对话',
        r'新[的]?[话题对话]',
    ]

    # 模糊查询检测 - 查询类型缺失
    VAGUE_TYPE_PATTERNS = [
        r'数据',
        r'信息',
        r'情况',
        r'内容',
        r'资料',
    ]

    def __init__(self, k=10):
        self.project_name = None
        self.query_type = None
        self.last_result_summary = None
        self.user_preferences = {}
        self.chat_history = []
        self.k = k
        self._last_intent_raw = None

    # ---- 序列化 ----

    def to_dict(self):
        """序列化为字典，用于跨请求传递"""
        return {
            "project_name": self.project_name,
            "query_type": self.query_type,
            "last_result_summary": self.last_result_summary,
            "user_preferences": self.user_preferences,
            "chat_history": self.chat_history[-self.k:] if self.chat_history else [],
        }

    @classmethod
    def from_dict(cls, data: dict):
        """从字典恢复上下文记忆"""
        mem = cls()
        if data:
            mem.project_name = data.get("project_name")
            mem.query_type = data.get("query_type")
            mem.last_result_summary = data.get("last_result_summary")
            mem.user_preferences = data.get("user_preferences", {})
            mem.chat_history = data.get("chat_history", [])
        return mem

    # ---- 记忆更新 ----

    def update_after_query(self, intent: dict, analysis_summary: str = None):
        """根据新的查询和结果更新记忆"""
        if intent:
            if intent.get("project") and intent["project"] != "ALL":
                self.project_name = intent["project"]
            if intent.get("query_type"):
                self.query_type = intent["query_type"]
            self._last_intent_raw = intent.get("raw_query", "")
        if analysis_summary:
            self.last_result_summary = analysis_summary[:500]

    def reset(self):
        """重置记忆（用户说'换一个项目'时调用）"""
        self.project_name = None
        self.query_type = None
        self.last_result_summary = None
        # 不清除 chat_history，保留对话上下文用于引用
        # 但清除项目相关的记忆

    # ---- 代词解析 ----

    def resolve_pronouns(self, user_query: str) -> (str, bool):
        """解析用户查询中的代词和指代表达式

        Returns:
            (resolved_query, was_modified): 解析后的查询和是否被修改
        """
        if not user_query:
            return user_query, False

        modified = False
        resolved = user_query

        # 处理"继续"类表达
        for pattern in self.CONTINUE_PATTERNS:
            if re.search(pattern, user_query):
                if self.query_type and self.project_name:
                    resolved = re.sub(
                        pattern,
                        f"{self.project_name}的{self._query_type_to_chinese()}",
                        resolved
                    )
                    modified = True
                    break
                elif self.query_type:
                    resolved = re.sub(
                        pattern,
                        f"{self._query_type_to_chinese()}",
                        resolved
                    )
                    modified = True
                    break

        # 处理代词 → 项目名
        if self.project_name:
            for pattern, field in self.PRONOUN_PATTERNS:
                if re.search(pattern, resolved) and field == 'project':
                    resolved = re.sub(pattern, self.project_name, resolved)
                    modified = True

        return resolved, modified

    def _query_type_to_chinese(self) -> str:
        """将查询类型转为中文"""
        mapping = {
            "single_project": "项目风险",
            "portfolio": "项目组合风险",
            "knowledge_query": "配置信息",
            "general_question": "问题",
        }
        return mapping.get(self.query_type, "信息")

    # ---- 模糊查询检测 ----

    def is_vague_query(self, user_query: str) -> bool:
        """判断用户查询是否模糊（缺少明确指向）

        检测规则：
        1. 有项目名但无查询类型（没有说配置/风险/进度等）
        2. 使用了'数据'、'信息'等模糊词
        3. 无项目名且无记忆
        """
        if not user_query:
            return True

        # 如果查询中包含明确意图词，不算模糊
        explicit_patterns = [
            r'配置', r'风险', r'进度', r'阻塞', r'bug', r'Bug',
            r'问题', r'分析', r'查询', r'查看', r'统计',
            r'测试建议', r'建议', r'功能', r'特性', r'参数',
            r'规格', r'支持',
        ]
        has_explicit = any(re.search(p, user_query) for p in explicit_patterns)

        # 有明确意图词 → 不算模糊
        if has_explicit:
            return False

        # 使用模糊词 → 可能模糊
        has_vague = any(re.search(p, user_query) for p in self.VAGUE_TYPE_PATTERNS)

        # 有项目名 + 模糊词 → 模糊
        if has_vague and (self._extract_project_ref(user_query) or self.project_name):
            return True

        # 无项目名 + 无记忆 + 无明确意图 → 模糊
        if not self._extract_project_ref(user_query) and not self.project_name and not has_explicit:
            return True

        return False

    def _extract_project_ref(self, query: str) -> bool:
        """检查查询中是否引用了项目（粗略检测）"""
        patterns = [
            r'X\d{4}', r'tOS\d+(\.\d+)?', r'CN\d+c?', r'LK\d+',
            r'AEE', r'XX',
        ]
        return any(re.search(p, query) for p in patterns)

    def get_clarification_question(self, user_query: str) -> str:
        """生成模糊意图的澄清问题"""
        if not self._extract_project_ref(user_query) and not self.project_name:
            return "请提供项目名称（如 X6840、tOS16.3），以及你想查询的内容（配置、风险还是进度）。"

        if self.project_name and not self._extract_project_ref(user_query):
            return f"你提到的「数据/信息」是指 {self.project_name} 的配置信息，还是 Jira 中的风险数据？请说明。"

        return "你提到的「数据/信息」具体是指配置信息，还是 Jira 中的风险数据？请选择。"

    # ---- 记忆重置检测 ----

    def should_reset_memory(self, user_query: str) -> bool:
        """检测用户是否要求更换话题/项目"""
        return any(re.search(p, user_query) for p in self.RESET_PATTERNS)

    # ---- 对话历史管理 ----

    def add_turn(self, user_msg: str, assistant_msg: str):
        """添加一轮对话"""
        self.chat_history.append({"role": "user", "content": user_msg})
        self.chat_history.append({"role": "assistant", "content": assistant_msg})
        # 保留最近 k 轮
        if len(self.chat_history) > self.k * 2:
            self.chat_history = self.chat_history[-(self.k * 2):]

    def get_recent_history(self, n: int = 5) -> list:
        """获取最近 n 轮对话历史"""
        return self.chat_history[-(n * 2):] if self.chat_history else []

    # ---- 上下文注入 ----

    def build_context_prompt(self) -> str:
        """构建上下文提示词片段，注入到LLM Prompt中"""
        parts = []
        if self.project_name:
            parts.append(f"当前对话上下文中的项目：{self.project_name}")
        if self.query_type:
            type_name = self._query_type_to_chinese()
            parts.append(f"用户上次查询类型：{type_name}")
        if self.last_result_summary:
            parts.append(f"上次查询结果摘要：{self.last_result_summary[:200]}")
        if self.chat_history:
            last_qa = self.chat_history[-2:] if len(self.chat_history) >= 2 else self.chat_history
            context_lines = []
            for msg in last_qa:
                role = "用户" if msg["role"] == "user" else "助手"
                content = msg["content"][:150]
                context_lines.append(f"{role}: {content}")
            parts.append("最近对话：\n" + "\n".join(context_lines))
        if parts:
            return "\n".join(parts)
        return ""


# ========== 借鉴旧的修复方案：自定义 httpx 客户端，添加需要的请求头 ==========
def add_custom_headers(request: httpx.Request) -> None:
    """在每次请求前添加自定义头"""
    # 使用服务器共享身份，确保所有用户都能访问AI服务
    # 从环境变量读取，但确保值只包含ASCII字符，避免编码问题
    server_user_no = os.getenv("X_USER_NO", "JIRA_RISK_SERVER")
    server_user_name_raw = os.getenv("X_USER_NAME", "JiraRiskAnalysisServer")
    server_user_dept_name_raw = os.getenv("X_USER_DEPT_NAME", "PublicAnalysisService")
    
    # 确保headers值安全（支持中文等UTF-8字符，与utils.py的encode_header一致）
    def safe_ascii(value: str) -> str:
        if not value:
            return value
        try:
            return value.encode('utf-8').decode('latin-1')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return "JiraRiskAnalysisServer"
    
    server_user_name = safe_ascii(server_user_name_raw)
    server_user_dept_name = safe_ascii(server_user_dept_name_raw)
    
    # 记录headers值（调试用）
    print(f"[DEBUG] add_custom_headers: X_USER_NO={repr(server_user_no)}, "
          f"X_USER_NAME raw={repr(server_user_name_raw)} -> safe={repr(server_user_name)}, "
          f"X_USER_DEPT_NAME raw={repr(server_user_dept_name_raw)} -> safe={repr(server_user_dept_name)}")
    
    # 注意：AI_API_KEY通过ChatOpenAI的api_key参数传递，不需要在这里重复设置Authorization头
    # 避免重复设置Authorization头，防止与ChatOpenAI的内部认证机制冲突
    # 只记录AI_API_KEY用于调试
    ai_api_key = os.getenv("AI_API_KEY")
    if ai_api_key:
        masked_key = ai_api_key[:8] + "..." + ai_api_key[-4:] if len(ai_api_key) > 12 else "***"
        print(f"[DEBUG] add_custom_headers: AI_API_KEY exists (masked: {masked_key})")
    
    # 设置headers（使用安全值）
    request.headers["X-USER-NO"] = server_user_no
    request.headers["X-USER-NAME"] = server_user_name
    request.headers["X-USER-DEPT-NAME"] = server_user_dept_name
    
    # 记录最终设置的headers
    print(f"[DEBUG] add_custom_headers: 设置的headers - X-USER-NO: {repr(request.headers.get('X-USER-NO'))}, "
          f"X-USER-NAME: {repr(request.headers.get('X-USER-NAME'))}, "
          f"X-USER-DEPT-NAME: {repr(request.headers.get('X-USER-DEPT-NAME'))}")

# 创建自定义的 httpx 客户端（借鉴旧的超时设置）
custom_client = httpx.Client(
    event_hooks={"request": [add_custom_headers]},
    timeout=httpx.Timeout(300.0, connect=10.0)   # 总超时300秒，连接超时10秒
)


def create_llm_from_config(ai_config: dict = None):
    """
    根据AI配置创建LLM实例
    
    Args:
        ai_config: AI配置字典，包含api_key、base_url、model等字段
        
    Returns:
        配置好的ChatOpenAI实例
    """
    # 从配置或环境变量中提取参数
    if ai_config:
        api_key = ai_config.get('api_key', os.getenv("AI_API_KEY"))
        base_url = ai_config.get('base_url', os.getenv("AI_BASE_URL"))
        model = ai_config.get('model', os.getenv("AI_MODEL", "gpt-5.4"))
    else:
        # 使用环境变量配置
        api_key = os.getenv("AI_API_KEY")
        base_url = os.getenv("AI_BASE_URL")
        model = os.getenv("AI_MODEL", "gpt-5.4")
    
    # 检查必要的配置
    if not api_key:
        raise ValueError("AI_API_KEY未设置，请检查环境变量配置")
    if not base_url:
        raise ValueError("AI_BASE_URL未设置，请检查环境变量配置")
    
    # 决定是否使用自定义HTTP客户端
    # 如果用户提供了自己的base_url（可能不是公司代理），则使用默认HTTP客户端
    # 否则使用自定义客户端（包含代理headers）
    use_custom_client = True
    default_base_url = os.getenv("AI_BASE_URL")
    if base_url and default_base_url and base_url != default_base_url:
        # 用户提供了不同的base_url，可能不需要公司代理的headers
        use_custom_client = False
    
    # 创建HTTP客户端
    if use_custom_client:
        http_client = custom_client
    else:
        # 使用默认HTTP客户端，不添加自定义headers
        http_client = httpx.Client(
            timeout=httpx.Timeout(300.0, connect=10.0)
        )
    
    print(f"[DEBUG] create_llm_from_config: 创建LLM, model={model}, base_url={base_url[:30]}..., api_key_length={len(api_key)}")
    
    # 创建LLM实例
    return ChatOpenAI(
        model=model,
        temperature=0.7,
        streaming=False,
        api_key=api_key,
        base_url=base_url,
        request_timeout=300,
        max_retries=3,
        max_tokens=2000,  # 限制token使用，用户要求节约成本
        http_client=http_client
    )


# 创建系统提示词
SYSTEM_PROMPT = """你是拥有15年以上经验的资深软件项目管理专家，专注于技术项目风险管理和质量保障。你以敏锐的商业洞察力、精准的风险识别和务实的解决方案而著称。

## ⚠️ 数据完整性声明（你必须严格遵守）：
**你接收到的所有Jira数据均为完整的全量数据**，基于全部查询结果的完整计算，不存在任何采样、截断或数据边界限制。你**严禁**在任何分析中使用以下表述：
- "前N个问题"、"前50个"、"前100个"等暗示数据被截断的说法
- "样本"、"样品"、"抽样"、"当前可见数据"等暗示数据不完整的说法
- "基于可见数据"、"基于有限数据"、"以下数据仅供参考"等弱化数据完整性的说法
- **你的所有统计、分析和结论都必须基于完整的全量数据**，不得声称任何数据限制

## 你的核心分析框架（PM专家视角）：
### 1. 执行摘要（30秒内可理解）
- **总体风险评级**：使用红/黄/绿灯系统（🔴红色/🟡黄色/🟢绿色）
- **核心发现**：1-2句话总结最关键的风险
- **对业务影响**：对交付时间、质量、成本的影响评估

### 2. 关键风险矩阵（按优先级排序）
- **P0级风险**（立即行动）：可能导致项目失败或重大延迟的问题
- **P1级风险**（本周解决）：对关键路径有显著影响的问题
- **P2级风险**（规划内解决）：需要关注但可规划解决的问题

### 3. 根本原因深度分析
- **技术债务**：代码质量、架构缺陷、技术选型问题
- **流程缺陷**：开发流程、测试流程、发布流程问题
- **资源约束**：人员技能、时间压力、工具限制
- **沟通协作**：团队协作、跨部门沟通、需求管理问题

### 4. 行动建议（SMART原则）
- **立即行动**（24小时内）：具体任务、负责人、完成标准
- **短期改进**（1周内）：流程优化、技术修复、资源调整
- **长期规划**（1个月内）：体系建设、能力提升、预防机制

## 输出格式要求：
### 对于项目风险分析（如"分析X6840的项目风险"）：
1. **【执行摘要】**（不超过5行）
   - 风险评级：[🔴红色/🟡黄色/🟢绿色]
   - 核心风险：[1-2个最关键的问题]
   - 影响评估：[对交付的影响程度]

2. **【关键风险矩阵】**
   - P0级（立即处理）：
     - [问题ID]：[问题描述] - [根本原因] - [预计影响]
   - P1级（本周解决）：
     - [问题ID]：[问题描述] - [根本原因] - [建议方案]

3. **【深度分析】**
   - 技术层面：[主要技术问题分析]
   - 流程层面：[关键流程缺陷]
   - 团队层面：[协作或技能缺口]

4. **【行动路线图】**
   - 本周重点：[具体可执行的任务清单]
   - 负责人：[明确的责任人]
   - 完成标准：[可衡量的验收标准]

### 对于特定查询（如"X6840的阻塞问题"）：
- 直接聚焦于该主题，使用简洁的专家分析
- 提供深度见解而不只是数据罗列

## 核心原则：
1. **聚焦重点**：只分析最关键的前20%问题（帕累托原则）
2. **数据驱动**：基于Jira数据，引用具体问题ID
3. **商业导向**：始终关联到业务目标和交付价值
4. **可操作性**：每个建议都必须有明确的执行路径
5. **简洁有力**：避免冗长描述，使用精炼的专业语言

## 重要提示：
- 根据数据量动态调整分析深度：数据少时深入分析每个问题，数据多时聚焦模式和高风险项
- 如果数据不足或质量差，明确说明局限性并提出改进数据质量的建议
- 避免使用模板化语言，每次分析都应根据项目特点提供定制化见解
- 回答总长度控制在合理范围内，保持信息密度和可读性"""

# 创建提示词模板（使用字符串模板确保变量替换可靠）
prompt_template_str = SYSTEM_PROMPT + "\n\n用户问题：{user_query}\n\n真实Jira数据：{jira_data}"
prompt_template = PromptTemplate.from_template(prompt_template_str)


def create_analysis_chain(streaming_callback=None, ai_config: dict = None):
    """
    创建分析链（使用PromptTemplate确保变量替换可靠）
    
    Args:
        streaming_callback: 用于流式输出的回调函数（暂时不使用）
        ai_config: AI配置字典，包含api_key、base_url、model等字段
        
    Returns:
        可运行的链
    """
    # 创建LLM（根据AI配置）
    streaming_llm = create_llm_from_config(ai_config)
    
    # 如果streaming_callback为True，可能需要设置streaming=True，但当前先保持关闭
    # 未来可以在这里根据streaming_callback调整LLM的streaming参数
    
    # 创建简单的链：prompt -> llm -> StrOutputParser()
    # 使用PromptTemplate确保变量替换可靠
    chain = prompt_template | streaming_llm | StrOutputParser()
    
    return chain


def format_jira_data(issues: List[Dict], max_issues: int = None) -> str:
    """
    将Jira问题列表格式化为文本字符串

    Args:
        issues: Jira问题列表
        max_issues: 最大详细显示数量（不限制统计范围），None表示不限制

    Returns:
        格式化后的文本
    """
    if not issues:
        return "未查询到相关Jira数据。"

    total = len(issues)

    # 全量统计（遍历所有问题，不受max_issues限制）
    status_counts = {}
    priority_counts = {}
    type_counts = {}

    for issue in issues:
        fields = issue.get('fields', {})
        status = fields.get('status', {}).get('name', '未知状态')
        priority = fields.get('priority', {}).get('name', '未知优先级')
        issue_type = fields.get('issuetype', {}).get('name', '未知类型')

        status_counts[status] = status_counts.get(status, 0) + 1
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        type_counts[issue_type] = type_counts.get(issue_type, 0) + 1

    jira_data = f"共查询到 {total} 个问题（以下为全量数据的完整统计）：\n\n"

    # 添加统计摘要
    jira_data += "【全量数据统计】\n"
    jira_data += f"- 状态分布: {', '.join([f'{k}:{v}' for k, v in sorted(status_counts.items())])}\n"
    jira_data += f"- 优先级分布: {', '.join([f'{k}:{v}' for k, v in sorted(priority_counts.items())])}\n"
    jira_data += f"- 类型分布: {', '.join([f'{k}:{v}' for k, v in sorted(type_counts.items())])}\n\n"
    jira_data += "（以上统计基于全量数据的完整计算，包含所有问题）\n\n"

    # 详细问题列表（可以限制显示数量但注明全量）
    if max_issues is None:
        limited_issues = issues
        detail_label = f"共{total}个"
    else:
        limited_issues = issues[:max_issues]
        detail_label = f"前{len(limited_issues)}个（共{total}个）"

    if max_issues is None or total <= max_issues:
        jira_data += f"【详细问题列表】（全量{total}个问题完整列出）\n"
    else:
        jira_data += f"【详细问题列表】（显示{detail_label}，统计数据仍基于全量{total}个问题）\n"

    for i, issue in enumerate(limited_issues, 1):
        fields = issue.get('fields', {})
        summary = fields.get('summary', '无标题')
        status = fields.get('status', {}).get('name', '未知状态')
        priority = fields.get('priority', {}).get('name', '未知优先级')
        issue_type = fields.get('issuetype', {}).get('name', '未知类型')
        assignee = fields.get('assignee', {}).get('displayName', '未分配') if fields.get('assignee') else '未分配'

        jira_data += f"{i}. {issue['key']}: {summary}\n"
        jira_data += f"   状态: {status}, 优先级: {priority}, 类型: {issue_type}, 负责人: {assignee}\n\n"

    if max_issues is not None and total > max_issues:
        jira_data += f"（还有 {total - max_issues} 个问题未在列表中详细列出，但上述统计数据已包含全部{total}个问题的完整信息）\n"

    jira_data += f"⚠️ 注意：以上所有统计和分析均基于全部{total}个问题的完整数据，不存在数据边界限制。\n"

    return jira_data


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
    import time
    
    print(f"[AI分析开始] 用户查询: {repr(user_query)}")
    print(f"[AI分析开始] Jira数据长度: {len(jira_data)} 字符")
    
    # 重试配置
    max_retries = 2
    retry_delay = 5  # 秒
    
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[AI分析] 尝试第 {attempt} 次（最多 {max_retries} 次）")
            
            # 创建链（使用AI配置）
            chain = create_analysis_chain(streaming_callback=None, ai_config=ai_config)
            
            # 准备输入
            inputs = {
                "user_query": user_query,
                "jira_data": jira_data
            }
            
            # 调用链，设置超时配置
            config = {"run_name": "analysis"}
            if timeout:
                config["timeout"] = timeout
            
            start_time = time.time()
            response = chain.invoke(inputs, config=config)
            elapsed_time = time.time() - start_time
            
            print(f"[AI分析成功] 第 {attempt} 次尝试成功，耗时 {elapsed_time:.2f} 秒")
            print(f"[AI分析成功] 响应长度: {len(response)} 字符")
            print(f"[AI分析成功] 响应内容前200字符: {repr(response[:200])}")
            
            # 检查响应是否为空
            if not response or len(response.strip()) == 0:
                print(f"[AI分析警告] 响应为空或仅包含空白字符，尝试第 {attempt} 次")
                # 如果是最后一次尝试，返回有意义的错误消息
                if attempt == max_retries:
                    return "AI分析失败: AI服务返回了空响应，可能是服务配置问题或网络异常"
                # 否则继续重试
                continue
            
            return response
            
        except TimeoutError as e:
            error_msg = f"AI分析超时（{timeout}秒），请检查网络连接或AI服务状态"
            print(f"[AI分析超时] 第 {attempt} 次尝试: {error_msg}")
            
            if attempt < max_retries:
                print(f"[AI分析] {retry_delay}秒后重试...")
                time.sleep(retry_delay)
                continue
            else:
                return f"AI分析失败: 多次尝试均超时（{timeout}秒），请检查AI服务连通性"
                
        except Exception as e:
            # 记录详细错误信息
            error_type = type(e).__name__
            error_detail = str(e)
            
            print(f"[AI分析异常] 第 {attempt} 次尝试 - 错误类型: {error_type}, 详情: {error_detail}")
            
            # 判断是否为可重试错误（网络相关错误）
            is_retryable = any(keyword in error_detail.lower() for keyword in [
                'connection', 'timeout', 'network', 'socket', 'refused', 'unreachable'
            ])
            
            if is_retryable and attempt < max_retries:
                print(f"[AI分析] 检测到可重试错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
                continue
            else:
                # 不可重试错误或已达最大重试次数
                error_msg = f"AI分析失败: {error_type}: {error_detail[:200]}"
                return error_msg
    
    # 理论上不会执行到这里，但为了安全
    return "AI分析失败: 未知错误"