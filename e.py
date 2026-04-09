import sys
import os
import requests
import json
import base64
import urllib3
from urllib.parse import quote
import argparse
import re
import httpx
import openai
import traceback
from datetime import datetime, timedelta

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 让 Windows PowerShell 下中文输出尽量正常
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _enable_ansi_on_windows() -> None:
    # 让 Windows 控制台支持 ANSI 颜色（PowerShell/旧版终端上更可靠）
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


_enable_ansi_on_windows()

# 项目键提取函数
def extract_project_key(input_str):
    # 从输入中提取项目键，支持X6840、CN6、CN6C等格式
    match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', input_str)
    if match:
        return match.group()
    return input_str

# 意图识别函数
def recognize_intent(user_query):
    """识别用户意图"""
    intent = {
        "project": None,
        "time_range": "本周",   # 根据业务调整默认值
        "query_type": "bug总量"
    }
    
    # 提取项目键
    project_match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', user_query)
    if project_match:
        intent["project"] = project_match.group()
    
    # 提取时间范围
    if "今天" in user_query or "今日" in user_query:
        intent["time_range"] = "今天"
    elif "昨天" in user_query or "昨日" in user_query:
        intent["time_range"] = "昨天"
    elif "前天" in user_query:
        intent["time_range"] = "前天"
    elif "本周" in user_query:
        intent["time_range"] = "本周"
    elif "上周" in user_query:
        intent["time_range"] = "上周"
    elif "本月" in user_query:
        intent["time_range"] = "本月"
    elif "上月" in user_query:
        intent["time_range"] = "上月"
    elif re.search(r'\d{4}-\d{2}-\d{2}', user_query):
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', user_query)
        intent["time_range"] = date_match.group()
    
    # 提取查询类型
    if "MP block" in user_query or "MP_BLOCK" in user_query:
        intent["query_type"] = "MP block问题"
    elif "交付测试" in user_query:
        intent["query_type"] = "交付测试部bug"
    elif "研发测试" in user_query:
        intent["query_type"] = "研发测试部bug"
    elif "bug" in user_query or "Bug" in user_query:
        intent["query_type"] = "bug总量"
    
    return intent

# 匹配 JQL 模板
def match_jql_template(intent):
    """根据用户意图匹配 JQL 模板"""
    project = intent.get("project")
    query_type = intent.get("query_type")
    
    for template in JQL_TEMPLATES:
        # 检查查询类型是否匹配模板名称
        if query_type in template.get("name", ""):
            # 检查项目是否在模板的适用列表中
            if project and project in template.get("projects", {}):
                return template
            # 如果模板的 projects 是字典且不为空，且项目不在其中，继续下一个模板
            elif isinstance(template.get("projects"), dict) and template.get("projects"):
                continue
            # 否则，这个模板适用于所有项目
            else:
                return template
    
    # 如果没有匹配到模板，返回默认模板
    return {
        "name": "默认模板",
        "projects": {},
        "jql": "project = {project} AND issuetype = Bug AND resolution = Unresolved AND {date_field} >= {start} AND {date_field} <= {end} ORDER BY priority DESC",
        "date_field": "created",
        "time_condition": ""
    }

# 时间解析与 JQL 动态修改
def parse_time_range(time_range: str):
    """
    解析时间范围，返回 JQL 可直接使用的表达式字符串。
    支持：今天、昨天、前天、本周、上周、本月、上月、具体日期(YYYY-MM-DD)、日期范围(YYYY-MM-DD to YYYY-MM-DD)
    """
    time_range = time_range.strip()
    
    # 处理具体日期范围（如 "2025-11-29 到 2026-01-01"）
    range_match = re.search(r'(\d{4}-\d{2}-\d{2})\s*[到至\-]\s*(\d{4}-\d{2}-\d{2})', time_range)
    if range_match:
        start_date = range_match.group(1)
        end_date = range_match.group(2)
        # 对于结束日期，通常希望包含当天，所以加一天
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_date_inclusive = end_dt.strftime("%Y-%m-%d")
        return f'"{start_date}"', f'"{end_date_inclusive}"'
    
    # 处理单个具体日期（如 "2026-04-08"）
    single_date_match = re.search(r'(\d{4}-\d{2}-\d{2})', time_range)
    if single_date_match:
        date_str = single_date_match.group(1)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        next_dt = dt + timedelta(days=1)
        return f'"{date_str}"', f'"{next_dt.strftime("%Y-%m-%d")}"'
    
    # 处理相对时间词
    if time_range == "今天":
        return "startOfDay()", "endOfDay()"
    elif time_range == "昨天":
        return "startOfDay(-1d)", "endOfDay(-1d)"
    elif time_range == "前天":
        return "startOfDay(-2d)", "endOfDay(-2d)"
    elif time_range == "明天":
        return "startOfDay(1d)", "endOfDay(1d)"
    elif time_range == "本周":
        return "startOfWeek()", "endOfWeek()"
    elif time_range == "上周":
        return "startOfWeek(-1w)", "endOfWeek(-1w)"
    elif time_range == "本月":
        return "startOfMonth()", "endOfMonth()"
    elif time_range == "上月":
        return "startOfMonth(-1m)", "endOfMonth(-1m)"
    else:
        # 默认返回今天
        return "startOfDay()", "endOfDay()"

# 生成 JQL 查询
def generate_jql(template, intent):
    """根据模板和意图生成 JQL 查询"""
    project = intent.get("project")
    time_range = intent.get("time_range", "本周")  # 可根据业务习惯修改默认值
    
    # 动态生成时间条件
    start, end = parse_time_range(time_range)
    date_field = template.get("date_field", "created")
    dynamic_time_condition = f"{date_field} >= {start} AND {date_field} <= {end}"
    
    # 获取基础 JQL
    if isinstance(template.get("projects"), dict) and project in template.get("projects", {}):
        jql = template.get("projects", {}).get(project, "")
    else:
        jql = template.get("jql", "")
    
    # 组装时间条件
    time_condition_str = f" {dynamic_time_condition} AND "
    
    # 插入到 JQL 中合适位置
    if "creator" in jql:
        jql = jql.replace("creator", time_condition_str + "creator")
    elif "ORDER BY" in jql:
        parts = jql.split("ORDER BY")
        jql = parts[0] + time_condition_str + "ORDER BY" + parts[1]
    else:
        jql = jql + time_condition_str
    
    # ========== 强制清洗：将所有可能的状态条件统一替换为 resolution = Unresolved ==========
    # 防止模板中遗留的任何 status = Verified / Closed / Resolved
    jql = re.sub(r'\bstatus\s*=\s*[\'\"]?\w+[\'\"]?', 'resolution = Unresolved', jql, flags=re.IGNORECASE)
    # 如果清洗后仍没有 resolution 条件，主动添加
    if 'resolution' not in jql.lower():
        jql = jql.replace('creator', 'resolution = Unresolved AND creator')
    
    # 清理多余空格
    jql = ' '.join(jql.split())
    return jql

USE_COLOR = os.getenv("NO_COLOR", "").strip() == "" and os.getenv("USE_ANSI_COLOR", "1").strip() != "0"
ANSI_RESET = "\033[0m"


def _c(text: str, code: str) -> str:
    if not USE_COLOR:
        return str(text)
    return f"\033[{code}m{text}{ANSI_RESET}"


def _log(kind: str, msg: str) -> None:
    # kind: step/info/ok/warn/err/ai
    palette = {
        # 使用“亮色系”，避免深色导致难以辨认
        "step": "96",      # 亮青色
        "info": "94",      # 亮蓝色
        "ok": "92",        # 亮绿色
        "warn": "93",      # 亮黄色
        "err": "91",       # 亮红色
        "ai": "95",        # 亮粉紫
    }
    code = palette.get(kind, "37")
    print(_c(msg, code), flush=True)

# 详细报告 Prompt 模板
DETAILED_REPORT_PROMPT = """你是一位拥有15年以上经验的软件项目风险分析专家，曾在多家大型科技公司担任质量总监。用户请求了一份详细的风险报告，请提供全面的分析。

## 你的分析流程（必须严格遵守）
1. **数据概览**：详细解读Jira数据，提取所有关键指标。
2. **深度分析**：进行全面的多维度分析：
   - Bug分布分析（按模块、负责人、优先级、状态）
   - 趋势分析（与历史数据对比）
   - 阻塞风险分析（识别所有可能的阻塞点）
   - 质量健康度评估
3. **详细风险评估**：对每个高风险问题进行单独分析
4. **可执行建议**：提供具体的行动计划，包括优先级、责任人、时间节点

## 输出格式要求（必须严格遵守）
你必须将输出分为两个部分：
- 第一部分用 `<thinking>` 标签包裹，内部写出你的详细推理过程。
- 第二部分用 `<answer>` 标签包裹，内部写出详细报告。

### <answer>部分的排版要求：
```
📊 详细统计信息
━━━━━━━━━━━━━━━━━━━━━
【基础统计】
• 总问题数：{数量}
• 已解决问题：{数量}
• 未解决问题：{数量}
• 今日新增：{数量}

【优先级分布】
• Block：{数量} 个 - {占比}%
• Critical：{数量} 个 - {占比}%
• Major：{数量} 个 - {占比}%
• Minor：{数量} 个 - {占比}%

【状态分布】
• Open：{数量} 个
• In Progress：{数量} 个
• Resolved：{数量} 个
• Closed：{数量} 个

【模块分布】
{各模块问题数量及占比}

【负责人分布】
{各负责人问题数量}

🚫 阻塞问题详情
━━━━━━━━━━━━━━━━━━━━━
{列出所有阻塞性问题，包括：
- Bug ID和标题
- 阻塞原因
- 建议解决方案
- 预计解决时间}

⚠️ 风险深度分析
━━━━━━━━━━━━━━━━━━━━━
【整体风险评级】：【{低/中/高/紧急}】

【风险趋势】
{与上周/上月对比，趋势是上升还是下降}

【高风险问题清单】
{列出所有高风险问题，每个包含：
1. Bug ID和标题
2. 风险等级
3. 影响范围
4. 建议处理优先级}

📋 问题详情列表
━━━━━━━━━━━━━━━━━━━━━
{列出所有问题的详细信息，格式：
• [Bug ID] 标题
  优先级：{优先级} | 状态：{状态} | 负责人：{负责人}
  描述：{简要描述}
  建议：{处理建议}}

💡 行动计划
━━━━━━━━━━━━━━━━━━━━━
【立即处理】（24小时内）
• {行动项1}
• {行动项2}

【本周处理】（7天内）
• {行动项1}
• {行动项2}

【持续跟进】
• {行动项1}
• {行动项2}

📈 质量建议
━━━━━━━━━━━━━━━━━━━━━
{针对当前问题模式，提供预防性建议}
```

排版规则：
1. 使用 emoji 图标增加可读性
2. 使用 "━━━━━━━━━━━━━━━━━━━━━" 作为分隔线
3. 信息要全面但避免冗余
4. 重点突出，使用【】标记关键信息
5. 提供可操作的具体建议

示例格式：
<thinking>
这里写你的思考过程...
</thinking>
<answer>
📊 详细统计信息
━━━━━━━━━━━━━━━━━━━━━
...
</answer>
"""

# 专家级系统 Prompt 模板
SYSTEM_PROMPT = """你是一位拥有15年以上经验的软件项目风险分析专家，曾在多家大型科技公司担任质量总监。你不仅精通项目风险分析，还擅长解答各种与软件开发、项目管理相关的问题。你的分析风格兼具深度与人性化，擅长从数据中洞察隐藏的风险模式。

## 你的核心能力：
1. **项目风险分析**：基于Jira数据进行全面的风险评估和分析
2. **问题解答**：回答用户关于软件开发、项目管理、质量保障等方面的问题
3. **技术咨询**：提供专业的技术建议和最佳实践
4. **流程优化**：分析和改进开发流程，提高项目质量

## 你的响应策略：
- 当用户提供具体的项目键（如X6840、X6878等）时：进行详细的风险分析
- 当用户询问一般性问题时：直接回答，提供专业建议
- 当用户请求详细报告时：提供更全面的分析
- 当用户的问题与你的专业领域无关时：礼貌地说明你专注于软件开发和项目管理领域

## 你的分析流程（针对风险分析）：
1. **数据概览**：快速解读Jira数据，提取关键指标（总量、优先级分布、状态分布、风险等级分布）。
2. **深度推理**：在内心进行多步推理，思考以下问题：
   - 这些Bug集中出现在哪些模块/负责人？是否存在系统性问题？
   - 高优先级/高风险Bug的解决速度如何？是否有阻塞版本的风险？
   - 与历史数据相比，当前的Bug趋势是恶化还是改善？
   - 有哪些Bug虽然优先级不高，但可能引发连锁反应？
3. **风险评级**：给出一个综合风险等级（低/中/高/紧急），并说明依据。
4. **行动建议**：提出具体、可执行的建议，包括责任人、时间节点。

## 输出格式要求（必须严格遵守）
你必须将输出分为两个部分：
- 第一部分用 `<thinking>` 标签包裹，内部写出你的详细推理过程（包括数据解读、因果分析、风险评估依据）。这部分内容不会被用户直接看到，但会展示在"深度思考"区域。
- 第二部分用 `<answer>` 标签包裹，内部写出给用户的正式回答。

### <answer>部分的排版要求（必须严格遵守）：
请使用以下结构化格式输出，让项目管理一眼看到关键信息：

```
📊 统计信息
━━━━━━━━━━━━━━━━━━━━━
总计：{问题总数} 个

严重等级分布：
• Block：{数量} 个
• Major：{数量} 个  
• Minor：{数量} 个

🚫 阻塞问题
━━━━━━━━━━━━━━━━━━━━━
{如果有阻塞问题，列出Bug ID和简要描述；如果没有，显示"✅ 未发现阻塞问题"}

⚠️ 风险汇总
━━━━━━━━━━━━━━━━━━━━━
风险等级：【{低/中/高/紧急}】

结论：{一句话总结当前风险状况}

项目分布：{各项目问题数量分布}
模块分布：{各模块问题数量分布}

📌 重点风险
━━━━━━━━━━━━━━━━━━━━━
1. 【{Bug ID}】{问题标题} -- {负责人}
   {简要风险说明}
   
2. 【{Bug ID}】{问题标题} -- {负责人}
   {简要风险说明}

💡 行动建议
━━━━━━━━━━━━━━━━━━━━━
• {具体建议1}
• {具体建议2}
• {具体建议3}

---
💬 需要详细风险报告？请回复"详细报告"
```

排版规则：
1. 使用 emoji 图标增加可读性
2. 使用 "━━━━━━━━━━━━━━━━━━━━━" 作为分隔线
3. 重点风险只列出最关键的 3-5 个
4. 每个部分都要简洁明了，避免大段文字
5. 风险等级用【】括起来突出显示
6. 最后添加提示：如果需要详细报告，用户可以回复"详细报告"

示例格式：
<thinking>
这里写你的思考过程...
</thinking>
<answer>
📊 统计信息
━━━━━━━━━━━━━━━━━━━━━
总计：13 个
...
</answer>

请确保标签严格为 `<thinking>` 和 `<answer>`，不要添加多余空格或换行。
"""


def _colorize_risk_line(text: str) -> str:
    if not USE_COLOR or not isinstance(text, str):
        return str(text)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("风险等级："):
            if "高风险" in line:
                lines[i] = _c(line, "1;91")  # bold bright red
            elif "中风险" in line:
                lines[i] = _c(line, "1;93")  # bold bright yellow
            elif "低风险" in line:
                lines[i] = _c(line, "1;92")  # bold bright green
    return "\n".join(lines)

# JQL 模板管理

def load_jql_templates():
    """加载 JQL 模板"""
    template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            templates = json.load(f)
        templates_list = templates.get('templates', [])
        _log("ok", f"成功加载 JQL 模板，共 {len(templates_list)} 个模板")
        # 验证 X6840 在"交付测试部bug总量"模板中的 JQL
        for tpl in templates_list:
            if tpl.get('name') == '交付测试部bug总量':
                x6840_jql = tpl.get('projects', {}).get('X6840', '')
                _log("info", f"模板[交付测试部bug总量]中X6840的JQL: {x6840_jql[:150]}...")
                break
        return templates_list
    except Exception as e:
        _log("err", f"加载 JQL 模板失败: {e}")
        return []

# 加载 JQL 模板
JQL_TEMPLATES = load_jql_templates()


# ========== 1. 配置 Jira 连接信息（自托管版） ==========
JIRA_CONFIG = {'server': 'http://jira.transsion.com'}         
JIRA_USERNAME = "fuchao.ao"                 
JIRA_PASSWORD = "200821Afc."            
JIRA_URL = JIRA_CONFIG["server"]

# ========== 2. 配置 AI 服务信息 ==========
# 走你提供的内网代理（tranai-proxy）
AI_BASE_URL = "https://hk-intra-paas.transsion.com/tranai-proxy/v1"
DEFAULT_AI_API_KEY = "sk_0f04e27baf7fd49de98314bc793b943e2514b72afaf9f67af8676a2"
AI_API_KEY = os.getenv("AI_API_KEY", DEFAULT_AI_API_KEY)
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.4")
_log("info", f"使用 AI 模型: {AI_MODEL}")

# 供代理鉴权的头信息（按你示例填写）
X_USER_NO = os.getenv("X_USER_NO", "18654794")
X_USER_NAME = os.getenv("X_USER_NAME", "敖富超")
X_USER_DEPT_NAME = os.getenv(
    "X_USER_DEPT_NAME",
    "深圳传音控股-传音控股-交付测试部-DT_交付测试部-DT_交付在研项目部"
)
# ========== 自定义 httpx 客户端，添加需要的请求头 ==========
def add_custom_headers(request: httpx.Request) -> None:
    """在每次请求前添加自定义头"""
    request.headers["Authorization"] = f"Bearer {AI_API_KEY}"
    request.headers["X-USER-NO"] = X_USER_NO
    request.headers["X-USER-NAME"] = X_USER_NAME
    request.headers["X-USER-DEPT-NAME"] = X_USER_DEPT_NAME

# 创建自定义的 httpx 客户端
custom_client = httpx.Client(
    event_hooks={"request": [add_custom_headers]},
    timeout=httpx.Timeout(120.0, connect=10.0)   # 总超时120秒，连接超时10秒
)

# ========== 初始化 OpenAI 客户端 ==========
client = OpenAI(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY,           # SDK 要求必须有 api_key，但实际认证会用自定义头的 X-API-Key
    http_client=custom_client,
)



# ========== 3. 指定要分析的项目 ==========
# 从命令行参数获取项目键
PROJECT_KEY = 'X6840-tOS16'  # 默认项目
MAX_ISSUE_PREVIEW = int(os.getenv("MAX_ISSUE_PREVIEW", "8"))
if MAX_ISSUE_PREVIEW < 0:
    MAX_ISSUE_PREVIEW = 0

# 提取项目键函数
def extract_project_key(input_str):
    # 从输入中提取项目键，支持X6840、CN6、CN6C等格式
    match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', input_str)
    if match:
        return match.group()
    return input_str

# 测试Jira服务器连接
def test_jira_connection():
    _log("step", "测试Jira服务器连接...")
    try:
        # 构建认证头
        auth_str = f"{JIRA_USERNAME}:{JIRA_PASSWORD}"
        auth_bytes = auth_str.encode("ascii")
        base64_auth = base64.b64encode(auth_bytes).decode("ascii")
        
        headers = {
            "Authorization": f"Basic {base64_auth}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{JIRA_URL}/rest/api/2/serverInfo",
            headers=headers,
            verify=False,
            timeout=10
        )
        
        _log("info", f"Jira服务器响应状态码: {response.status_code}")
        _log("info", f"Jira服务器响应内容: {response.text[:500]}...")
        
        if response.status_code == 200:
            _log("ok", "Jira服务器连接成功")
            return True
        else:
            _log("err", f"Jira服务器连接失败，状态码: {response.status_code}")
            _log("err", f"响应内容: {response.text[:500]}...")
            return False
    except Exception as e:
        _log("err", f"Jira服务器连接失败: {e}")
        import traceback
        _log("err", f"详细错误: {traceback.format_exc()}")
        return False

# 生成最终 JQL，优先模板，失败则降级为 summary 搜索
def generate_final_jql(user_query: str) -> str:
    """生成最终 JQL，优先模板，失败则降级为 summary 搜索"""
    intent = recognize_intent(user_query)
    _log("info", f"识别意图: project={intent['project']}, query_type={intent['query_type']}, time_range={intent['time_range']}")
    
    template = match_jql_template(intent)
    if template:
        _log("info", f"匹配到模板: {template.get('name')}")
    else:
        _log("warn", "未匹配到任何模板，将使用降级方案")
    
    if template and (template.get("jql") or template.get("projects")):
        jql = generate_jql(template, intent)
        if jql:
            _log("info", f"最终生成的 JQL: {jql}")
            return jql
    
    # 降级：summary 模糊搜索
    project = extract_project_key(user_query) or "X6840"
    fallback_jql = f"summary ~ '{project}' AND resolution = Unresolved ORDER BY priority DESC"
    _log("info", f"降级 JQL: {fallback_jql}")
    return fallback_jql

# 从Jira获取全量问题（分页）
def fetch_all_issues(jql: str) -> list:
    """从Jira获取全量问题（分页）"""
    all_issues = []
    start_at = 0
    max_results = 100  # 单次最大可取100
    
    # 构建认证头
    auth_str = f"{JIRA_USERNAME}:{JIRA_PASSWORD}"
    auth_bytes = auth_str.encode("ascii")
    base64_auth = base64.b64encode(auth_bytes).decode("ascii")
    
    headers = {
        "Authorization": f"Basic {base64_auth}",
        "Content-Type": "application/json"
    }
    
    while True:
        url = f"{JIRA_URL}/rest/api/2/search"
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
            "fields": "summary,status,priority,issuetype,assignee,created,labels,key,customfield_10000"
        }
        
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                verify=False,
                timeout=30
            )
            
            if response.status_code != 200:
                _log("err", f"请求 Jira 失败，状态码：{response.status_code}")
                break
            
            data = response.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)
            
            if len(all_issues) >= data.get("total", 0):
                break
            
            start_at += max_results
        except Exception as e:
            _log("err", f"获取 Jira 数据失败: {e}")
            break
    
    return all_issues

# 风险等级识别函数
def get_risk_level(priority, labels, summary):
    # 识别风险等级，包括Block、Must Resolve和必解标签
    risk_level = "低"
    if priority == "Block" or "Block" in labels:
        risk_level = "高"
    elif "Must Resolve" in labels or "MP block" in labels:
        risk_level = "高"
    elif "必解" in labels or "必解" in summary:
        risk_level = "高"
    elif priority in ["High", "高"]:
        risk_level = "高"
    elif priority in ["Medium", "中"]:
        risk_level = "中"
    return risk_level

# 优先级分桶函数
def bucket_from_priority(priority: str) -> str:
    p = str(priority or "").strip().lower()
    # 兼容常见英文/中文优先级命名
    if "critical" in p or "highest" in p or "high" in p or "高" in priority:
        return "高"
    if "medium" in p or "med" in p or "중" in p or "中" in priority:
        return "中"
    if "low" in p or "低" in priority:
        return "低"
    return "无"

# ========== 4. 从 Jira 获取项目数据 ==========
# 注意：这部分代码已经移到 run_command_line_analysis() 函数中
# 这样只有当用户没有指定 --api 或 --query 参数时，才会执行这些代码


# ========== 8. Flask API服务 ==========
from flask import Flask, request, Response
import json

# 简单内存会话存储（生产环境可换 Redis）
conversation_history = {}   # key: conversation_id, value: list of messages
MAX_HISTORY_MESSAGES = 20   # 最多保留多少条历史消息（每轮包含 user 和 assistant）

app = Flask(__name__)

@app.route('/')
def index():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/script.js')
def script_js():
    try:
        with open('script.js', 'r', encoding='utf-8') as f:
            content = f.read()
        return content, {'Content-Type': 'application/javascript'}
    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/style.css')
def style_css():
    try:
        with open('style.css', 'r', encoding='utf-8') as f:
            content = f.read()
        return content, {'Content-Type': 'text/css'}
    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/api/analyze', methods=['GET'])
def analyze_api():
    """分析项目 - 使用SSE流式响应"""
    project_key = request.args.get('project_key', '')
    user_query = request.args.get('user_query', '')
    conversation_id = request.args.get('conversation_id', 'default')
    detailed_report = request.args.get('detailed_report', 'false') == 'true'
    
    # 提取项目键
    match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', project_key)
    if match:
        project_key = match.group()
        if re.match(r'^\d+$', project_key):
            project_key = 'X' + project_key
    
    # 根据是否详细报告选择不同的 System Prompt
    if detailed_report:
        system_prompt = DETAILED_REPORT_PROMPT
    else:
        system_prompt = SYSTEM_PROMPT
    
    def generate():
        try:
            # 使用外部函数的参数，而不是再次从 request 中获取
            # 这样可以避免在请求上下文之外访问 request 对象
            
            # ========== 会话记忆管理 ==========
            if conversation_id not in conversation_history:
                conversation_history[conversation_id] = []
            history = conversation_history[conversation_id]
            
            # 将当前用户问题加入历史
            history.append({"role": "user", "content": user_query})
            
            # ========== 发送初始状态 ==========
            yield f"data: {json.dumps({'type': 'thinking', 'content': '🔍 正在解析查询意图...\n'})}\n\n"
            
            # 分析用户意图
            user_query_lower = user_query.lower()
            is_project_risk_query = False
            
            # 检查是否包含项目键（如X6840、X6878等）
            project_match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', user_query)
            if project_match:
                is_project_risk_query = True
            
            # 检查是否包含风险分析相关关键词
            risk_keywords = ['风险', '分析', 'bug', '问题', 'jira', '项目']
            for keyword in risk_keywords:
                if keyword in user_query_lower:
                    is_project_risk_query = True
                    break
            
            # 检查是否是一般性问题
            general_questions = ['你好', '你能做什么', '帮助', '如何', '什么是', '为什么', '教程', '指南']
            is_general_question = False
            for question in general_questions:
                if question in user_query_lower:
                    is_general_question = True
                    break
            
            # 如果是一般性问题，直接回答
            if is_general_question:
                yield f"data: {json.dumps({'type': 'thinking', 'content': '💭 分析一般性问题...\n'})}\n\n"
                
                # 构建AI消息列表，包含历史对话
                messages = [{"role": "system", "content": system_prompt}]
                # 加入所有历史对话
                for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                    messages.append(msg)
                messages.append({"role": "user", "content": user_query})
                
                # 调用AI
                url = f"{AI_BASE_URL}/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}",
                }
                payload = {
                    "model": AI_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "stream": True,
                }
                
                response = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
                
                if response.status_code != 200:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'AI服务异常：{response.status_code}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                # 解析AI流式输出
                buffer = ""
                in_thinking = False
                in_answer = False
                full_response = ""
                
                for line in response.iter_lines():
                    if not line:
                        continue
                    line = line.decode('utf-8')
                    if not line.startswith('data: '):
                        continue
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        choices = chunk.get('choices', [])
                        if not choices or len(choices) == 0:
                            continue
                        delta = choices[0].get('delta', {})
                        content = delta.get('content', '')
                        if not content:
                            continue
                        
                        full_response += content
                        buffer += content
                        
                        # 实时检测标签
                        while True:
                            if not in_thinking and not in_answer:
                                think_pos = buffer.find('<thinking>')
                                answer_pos = buffer.find('<answer>')
                                
                                if think_pos != -1 and (answer_pos == -1 or think_pos < answer_pos):
                                    prefix = buffer[:think_pos]
                                    if prefix.strip():
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                    buffer = buffer[think_pos + len('<thinking>'):]
                                    in_thinking = True
                                elif answer_pos != -1:
                                    prefix = buffer[:answer_pos]
                                    if prefix.strip():
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                    buffer = buffer[answer_pos + len('<answer>'):]
                                    in_answer = True
                                else:
                                    break
                            elif in_thinking:
                                end_pos = buffer.find('</thinking>')
                                if end_pos != -1:
                                    think_content = buffer[:end_pos]
                                    if think_content:
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': think_content})}\n\n"
                                    buffer = buffer[end_pos + len('</thinking>'):]
                                    in_thinking = False
                                else:
                                    safe_len = max(0, len(buffer) - len('</thinking>'))
                                    if safe_len > 0:
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer[:safe_len]})}\n\n"
                                        buffer = buffer[safe_len:]
                                    break
                            elif in_answer:
                                end_pos = buffer.find('</answer>')
                                if end_pos != -1:
                                    answer_content = buffer[:end_pos]
                                    if answer_content:
                                        yield f"data: {json.dumps({'type': 'answer', 'content': answer_content})}\n\n"
                                    buffer = buffer[end_pos + len('</answer>'):]
                                    in_answer = False
                                else:
                                    safe_len = max(0, len(buffer) - len('</answer>'))
                                    if safe_len > 0:
                                        yield f"data: {json.dumps({'type': 'answer', 'content': buffer[:safe_len]})}\n\n"
                                        buffer = buffer[safe_len:]
                                    break
                    except json.JSONDecodeError:
                        continue
                
                # 处理缓冲区剩余内容
                if buffer.strip():
                    if in_thinking:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
                    elif in_answer:
                        yield f"data: {json.dumps({'type': 'answer', 'content': buffer})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
                
                # 将AI的完整回答存入历史
                history.append({"role": "assistant", "content": full_response})
                if len(history) > MAX_HISTORY_MESSAGES:
                    conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]
                
                yield "data: [DONE]\n\n"
                return
            
            # 如果是项目风险查询，继续原有流程
            if is_project_risk_query:
                # 生成 JQL
                jql = generate_final_jql(user_query)
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'📋 生成的 JQL: {jql}\n'})}\n\n"
                
                # 全量拉取 issues
                yield f"data: {json.dumps({'type': 'thinking', 'content': '⏳ 正在从 Jira 获取数据...\n'})}\n\n"
                raw_issues = fetch_all_issues(jql)
                
                if not raw_issues:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': '⚠️ 未获取到 Jira 问题\n'})}\n\n"
                    yield f"data: {json.dumps({'type': 'answer', 'content': '暂无问题反馈，请自行前往jira查看\n'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            else:
                # 其他类型的问题，直接调用AI回答
                yield f"data: {json.dumps({'type': 'thinking', 'content': '💭 分析问题...\n'})}\n\n"
                
                # 构建AI消息列表，包含历史对话
                messages = [{"role": "system", "content": system_prompt}]
                # 加入所有历史对话
                for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                    messages.append(msg)
                messages.append({"role": "user", "content": user_query})
                
                # 调用AI
                url = f"{AI_BASE_URL}/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}",
                }
                payload = {
                    "model": AI_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "stream": True,
                }
                
                response = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
                
                if response.status_code != 200:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'AI服务异常：{response.status_code}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                # 解析AI流式输出
                buffer = ""
                in_thinking = False
                in_answer = False
                full_response = ""
                
                for line in response.iter_lines():
                    if not line:
                        continue
                    line = line.decode('utf-8')
                    if not line.startswith('data: '):
                        continue
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data)
                        choices = chunk.get('choices', [])
                        if not choices or len(choices) == 0:
                            continue
                        delta = choices[0].get('delta', {})
                        content = delta.get('content', '')
                        if not content:
                            continue
                        
                        full_response += content
                        buffer += content
                        
                        # 实时检测标签
                        while True:
                            if not in_thinking and not in_answer:
                                think_pos = buffer.find('<thinking>')
                                answer_pos = buffer.find('<answer>')
                                
                                if think_pos != -1 and (answer_pos == -1 or think_pos < answer_pos):
                                    prefix = buffer[:think_pos]
                                    if prefix.strip():
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                    buffer = buffer[think_pos + len('<thinking>'):]
                                    in_thinking = True
                                elif answer_pos != -1:
                                    prefix = buffer[:answer_pos]
                                    if prefix.strip():
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                    buffer = buffer[answer_pos + len('<answer>'):]
                                    in_answer = True
                                else:
                                    break
                            elif in_thinking:
                                end_pos = buffer.find('</thinking>')
                                if end_pos != -1:
                                    think_content = buffer[:end_pos]
                                    if think_content:
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': think_content})}\n\n"
                                    buffer = buffer[end_pos + len('</thinking>'):]
                                    in_thinking = False
                                else:
                                    safe_len = max(0, len(buffer) - len('</thinking>'))
                                    if safe_len > 0:
                                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer[:safe_len]})}\n\n"
                                        buffer = buffer[safe_len:]
                                    break
                            elif in_answer:
                                end_pos = buffer.find('</answer>')
                                if end_pos != -1:
                                    answer_content = buffer[:end_pos]
                                    if answer_content:
                                        yield f"data: {json.dumps({'type': 'answer', 'content': answer_content})}\n\n"
                                    buffer = buffer[end_pos + len('</answer>'):]
                                    in_answer = False
                                else:
                                    safe_len = max(0, len(buffer) - len('</answer>'))
                                    if safe_len > 0:
                                        yield f"data: {json.dumps({'type': 'answer', 'content': buffer[:safe_len]})}\n\n"
                                        buffer = buffer[safe_len:]
                                    break
                    except json.JSONDecodeError:
                        continue
                
                # 处理缓冲区剩余内容
                if buffer.strip():
                    if in_thinking:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
                    elif in_answer:
                        yield f"data: {json.dumps({'type': 'answer', 'content': buffer})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
                
                # 将AI的完整回答存入历史
                history.append({"role": "assistant", "content": full_response})
                if len(history) > MAX_HISTORY_MESSAGES:
                    conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]
                
                yield "data: [DONE]\n\n"
                return
            
            # 数据清洗和统计
            issue_summaries = []
            status_counts = {}
            priority_bucket_counts = {"高": 0, "中": 0, "低": 0, "无": 0}
            risk_level_counts = {"高": 0, "中": 0, "低": 0}
            
            for issue in raw_issues:
                fields = issue["fields"]
                summary = fields["summary"]
                status = fields["status"]["name"]
                priority = fields["priority"]["name"] if fields.get("priority") else "无"
                assignee = fields.get("assignee", {}).get("displayName", "未分配")
                created = fields["created"][:10]
                labels = fields.get("labels", [])
                bug_key = issue.get("key", "")
                tcid = fields.get("customfield_10000", "")
                risk_level = get_risk_level(priority, labels, summary)
                
                # 统计
                status_counts[status] = status_counts.get(status, 0) + 1
                bucket = bucket_from_priority(priority)
                priority_bucket_counts[bucket] = priority_bucket_counts.get(bucket, 0) + 1
                risk_level_counts[risk_level] = risk_level_counts.get(risk_level, 0) + 1
                
                issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (状态:{status}, 优先级:{priority}, 风险:{risk_level}, 负责人:{assignee}, 创建:{created})")
            
            # 构建当前数据的上下文 Prompt
            data_context = f"""
            当前用户提问：「{user_query}」
            我已从Jira获取到以下数据：
            - 总问题数：{len(raw_issues)}
            - 优先级分布：高={priority_bucket_counts['高']}，中={priority_bucket_counts['中']}，低={priority_bucket_counts['低']}
            - 风险等级分布：高风险={risk_level_counts['高']}，中风险={risk_level_counts['中']}，低风险={risk_level_counts['低']}
            - 状态分布：{', '.join([f'{k}={v}' for k,v in sorted(status_counts.items())])}

            前10个问题详情：
            {chr(10).join(issue_summaries[:10])}
            """
            
            # 构建发送给 AI 的消息列表
            messages = [{"role": "system", "content": system_prompt}]
            # 加入最近的历史对话（但排除刚刚加入的当前 user 消息）
            for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                messages.append(msg)
            # 将当前问题与数据上下文合并为一条 user 消息
            messages.append({"role": "user", "content": data_context})
            
            # 发送状态
            yield f"data: {json.dumps({'type': 'thinking', 'content': '🤖 专家正在深度分析数据...\n'})}\n\n"
            
            # ========== 调用 AI 流式接口 ==========
            url = f"{AI_BASE_URL}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}",
            }
            payload = {
                "model": AI_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "stream": True,
            }
            
            response = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
            
            if response.status_code != 200:
                yield f"data: {json.dumps({'type': 'error', 'content': f'AI服务异常：{response.status_code}'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            
            # ========== 解析 AI 流式输出 ==========
            buffer = ""
            in_thinking = False
            in_answer = False
            full_response = ""  # 用于存储完整回答，最后存入历史
            
            for line in response.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8')
                if not line.startswith('data: '):
                    continue
                data = line[6:]
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                    # 安全地获取 choices，防止列表为空
                    choices = chunk.get('choices', [])
                    if not choices or len(choices) == 0:
                        continue
                    delta = choices[0].get('delta', {})
                    content = delta.get('content', '')
                    if not content:
                        continue
                    
                    full_response += content
                    buffer += content
                    
                    # 实时检测标签
                    while True:
                        if not in_thinking and not in_answer:
                            # 寻找开始标签
                            think_pos = buffer.find('<thinking>')
                            answer_pos = buffer.find('<answer>')
                            
                            if think_pos != -1 and (answer_pos == -1 or think_pos < answer_pos):
                                # 先遇到 thinking
                                prefix = buffer[:think_pos]
                                if prefix.strip():
                                    # 标签前的文本当作思考过程输出
                                    yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                buffer = buffer[think_pos + len('<thinking>'):]
                                in_thinking = True
                            elif answer_pos != -1:
                                prefix = buffer[:answer_pos]
                                if prefix.strip():
                                    yield f"data: {json.dumps({'type': 'thinking', 'content': prefix})}\n\n"
                                buffer = buffer[answer_pos + len('<answer>'):]
                                in_answer = True
                            else:
                                # 没有完整标签，保留在缓冲区
                                break
                        
                        elif in_thinking:
                            end_pos = buffer.find('</thinking>')
                            if end_pos != -1:
                                think_content = buffer[:end_pos]
                                if think_content:
                                    yield f"data: {json.dumps({'type': 'thinking', 'content': think_content})}\n\n"
                                buffer = buffer[end_pos + len('</thinking>'):]
                                in_thinking = False
                            else:
                                # 输出当前累积的内容（保留一部分防止标签被截断）
                                safe_len = max(0, len(buffer) - len('</thinking>'))
                                if safe_len > 0:
                                    yield f"data: {json.dumps({'type': 'thinking', 'content': buffer[:safe_len]})}\n\n"
                                    buffer = buffer[safe_len:]
                                break
                        
                        elif in_answer:
                            end_pos = buffer.find('</answer>')
                            if end_pos != -1:
                                answer_content = buffer[:end_pos]
                                if answer_content:
                                    yield f"data: {json.dumps({'type': 'answer', 'content': answer_content})}\n\n"
                                buffer = buffer[end_pos + len('</answer>'):]
                                in_answer = False
                            else:
                                safe_len = max(0, len(buffer) - len('</answer>'))
                                if safe_len > 0:
                                    yield f"data: {json.dumps({'type': 'answer', 'content': buffer[:safe_len]})}\n\n"
                                    buffer = buffer[safe_len:]
                                break
                    
                except json.JSONDecodeError:
                    continue
            
            # 处理缓冲区剩余内容（可能未闭合标签，但当作普通文本）
            if buffer.strip():
                # 如果还在 thinking 或 answer 中，按对应类型发送
                if in_thinking:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
                elif in_answer:
                    yield f"data: {json.dumps({'type': 'answer', 'content': buffer})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': buffer})}\n\n"
            
            # 将 AI 的完整回答存入历史
            history.append({"role": "assistant", "content": full_response})
            # 控制历史长度
            if len(history) > MAX_HISTORY_MESSAGES:
                conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]
            
            # 构建数据更新事件
            # 整理项目数据
            project_issues = []
            for issue in raw_issues:
                fields = issue["fields"]
                summary = fields.get("summary", "")
                labels = fields.get("labels", [])
                
                # 自动识别交付测试部的bug
                is_delivery_test = False
                if "交付" in summary or any("交付" in label for label in labels):
                    is_delivery_test = True
                
                # 从AI分析结果中提取tag信息
                # 这里假设AI分析结果中包含tag信息，实际实现可能需要根据AI输出格式调整
                tags = labels  # 暂时使用Jira标签作为tag
                
                project_issues.append({
                    "bug_key": issue.get("key", ""),
                    "summary": summary,
                    "status": fields.get("status", {}).get("name", ""),
                    "priority": fields.get("priority", {}).get("name", ""),
                    "assignee": fields.get("assignee", {}).get("displayName", "未分配"),
                    "created": fields.get("created", "")[:10],
                    "labels": labels,
                    "risk_level": get_risk_level(
                        fields.get("priority", {}).get("name", ""),
                        labels,
                        summary
                    ),
                    "is_delivery_test": is_delivery_test,
                    "tags": tags
                })
            
            # 构建数据更新事件
            data_update = {
                "panel_type": "risk",
                "project_key": project_key,
                "total_issues": len(raw_issues),
                "status_counts": status_counts,
                "priority_bucket_counts": priority_bucket_counts,
                "risk_level_counts": risk_level_counts,
                "issues": project_issues,
                "analysis": full_response,  # AI的完整分析结果
                "detailed_analysis": full_response  # 详细分析结果
            }
            
            # 发送数据更新事件
            yield f"data: {json.dumps({'type': 'data', 'content': data_update})}\n\n"
            
            # 发送结束标记
            yield "data: [DONE]\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield "data: [DONE]\n\n"
    
    return Response(generate(), mimetype="text/event-stream")


# 原始的命令行执行逻辑
def run_command_line_analysis():
    """运行命令行分析"""
    # 从用户输入获取查询
    user_input = input("请输入查询：").strip() or "X6840 风险分析"
    
    # 生成 JQL
    jql = generate_final_jql(user_input)
    _log("info", f"生成的 JQL: {jql}")
    
    # 全量拉取 issues
    issues = fetch_all_issues(jql)
    
    if not issues:
        _log("warn", "未获取到 Jira 的未关闭问题（或未成功获取到数据）。")
        # 构建 AI 提示词，让 AI 输出"暂无问题反馈，请自行前往jira查看"
        prompt = f"""
        你是一位资深项目风险专家。现在用户问："{user_input}"。
        我从 Jira 获取数据时，未获取到任何未关闭的问题。
        
        请用亲切、专业的中文进行对话式回答，告诉用户暂无问题反馈，请自行前往jira查看。
        """
        
        # 流式 AI 分析
        _log("step", "AI 深度分析")
        print(_c("=" * 70, "97"))
        print(_c("AI 深度分析", "1;96"))
        print(_c("=" * 70, "97"))
        print(_c("[AI 正在思考...]", "96"), flush=True)
        
        try:
            # 使用requests库直接发送请求
            url = "https://hk-intra-paas.transsion.com/tranai-proxy/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}",
            }
            payload = {
                "model": "gpt-5.4-thinking-high",
                "messages": [
                    {"role": "system", "content": "你是一位资深项目风险专家，拥有丰富的软件项目风险管理经验。你擅长分析Jira项目数据，识别潜在风险，并提供专业、具体的改进建议。你的分析应该深入、全面，并且有数据支持。你的回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "stream": True,
            }
            
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=120
            )
            
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line:
                        # 处理SSE格式的响应
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data = line[6:]
                            if data != '[DONE]':
                                try:
                                    chunk = json.loads(data)
                                    if chunk.get('choices') and len(chunk['choices']) > 0:
                                        delta = chunk['choices'][0].get('delta')
                                        if delta and delta.get('content'):
                                            content = delta['content']
                                            print(content, end='', flush=True)
                                except json.JSONDecodeError:
                                    pass
            else:
                _log("err", f"AI 请求失败，状态码: {response.status_code}")
                print(f"请求 AI 失败: 状态码 {response.status_code}")
        except requests.exceptions.Timeout:
            _log("err", "AI 请求超时，请稍后重试")
            print("请求 AI 失败: 超时")
        except Exception as e:
            _log("err", f"AI 请求失败: {e}")
            print(f"请求 AI 失败: {e}")
        
        print("", flush=True)
        print(_c("=" * 70, "97"))
        return
    
    # 提取关键信息，用于 AI 分析
    issue_summaries = []
    status_counts = {}
    priority_bucket_counts = {"高": 0, "中": 0, "低": 0, "无": 0}
    risk_level_counts = {"高": 0, "中": 0, "低": 0}
    
    for issue in issues:
        fields = issue["fields"]
        summary = fields["summary"]
        status = fields["status"]["name"]
        priority = fields["priority"]["name"] if fields.get("priority") else "无"
        issuetype = fields["issuetype"]["name"]
        assignee = fields["assignee"]["displayName"] if fields.get("assignee") else "未分配"
        created = fields["created"][:10]  # 只取日期部分
        labels = fields.get("labels", [])
        bug_key = issue.get("key", "")
        tcid = fields.get("customfield_10000", "")  # TCID字段
        
        # 识别风险等级
        risk_level = get_risk_level(priority, labels, summary)
        
        # 统计状态分布
        status_counts[status] = status_counts.get(status, 0) + 1
        
        # 统计优先级分布
        bucket = bucket_from_priority(priority)
        priority_bucket_counts[bucket] = priority_bucket_counts.get(bucket, 0) + 1
        
        # 统计风险等级分布
        risk_level_counts[risk_level] = risk_level_counts.get(risk_level, 0) + 1
        
        # 生成问题摘要，用于 AI 分析
        issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (类型:{issuetype}, 状态:{status}, 优先级:{priority}, 风险等级:{risk_level}, 负责人:{assignee}, 创建日期:{created})")
    
    # ========== Jira 原始数据（预览）==========
    print(_c("=" * 70, "97"))
    print(_c(f"Jira 原始数据（共 {len(issues)} 条，预览前 {min(MAX_ISSUE_PREVIEW, len(issue_summaries))} 条）", "1;97"))
    print(_c("=" * 70, "97"))
    for i, s in enumerate(issue_summaries[:MAX_ISSUE_PREVIEW], 1):
        print(_c(f"{i}. {s}", "97"), flush=True)
    print(_c("=" * 70, "97"), flush=True)
    
    bucket_order = ["高", "中", "低", "无"]
    bucket_str = ", ".join([f"{k}={priority_bucket_counts.get(k, 0)}" for k in bucket_order])
    _log("info", f"优先级分桶：{bucket_str}")
    status_str = ", ".join([f"{k}={v}" for k, v in sorted(status_counts.items(), key=lambda x: x[0])])
    _log("info", f"状态分布：{status_str}")
    
    # 构建精简版的issues_text
    filtered_issue_summaries = []
    for issue in issues[:10]:  # 只取前10个问题
        fields = issue["fields"]
        bug_key = issue.get("key", "")
        tcid = fields.get("customfield_10000", "")
        summary = fields.get("summary", "")
        issue_type = fields.get("issuetype", {}).get("name", "")
        status = fields.get("status", {}).get("name", "")
        priority = fields.get("priority", {}).get("name", "")
        labels = fields.get("labels", [])
        risk_level = get_risk_level(priority, labels, summary)
        assignee = fields.get("assignee", {}).get("displayName", "未分配")
        created = fields.get("created", "")[:10]
        filtered_issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (类型:{issue_type}, 状态:{status}, 优先级:{priority}, 风险等级:{risk_level}, 负责人:{assignee}, 创建日期:{created}")
    
    filtered_issues_text = "\n".join(filtered_issue_summaries)
    
    # 构建 AI 提示词
    prompt = f"""
    你是一位资深项目风险专家。现在用户问："{user_input}"。我已从 Jira 获取到以下未关闭问题数据：
    
    项目信息：
    - 未关闭问题总数：{len(issues)}
    - 高优先级问题：{priority_bucket_counts['高']}个
    - 中优先级问题：{priority_bucket_counts['中']}个
    - 低优先级问题：{priority_bucket_counts['低']}个
    - 高风险问题：{risk_level_counts['高']}个
    - 中风险问题：{risk_level_counts['中']}个
    - 低风险问题：{risk_level_counts['低']}个
    
    关键问题详情（前10个问题）：
    {filtered_issues_text}
    
    请用亲切、专业的中文进行对话式分析，包含：
    1. 总体风险评估（用口语化表达，如"目前风险较高，主要是...")。
    2. 按风险等级列出关键问题（至少给出 Bug ID、摘要、负责人）。
    3. 给出具体、可落地的行动建议。
    
    注意：回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。
    """
    
    # 确保prompt是一个字符串
    prompt = str(prompt)
    
    # 流式 AI 分析
    _log("step", "AI 深度分析")
    print(_c("=" * 70, "97"))
    print(_c("AI 深度分析", "1;96"))
    print(_c("=" * 70, "97"))
    print(_c("[AI 正在思考...]", "96"), flush=True)
    
    analysis = ""
    try:
        # 使用requests库直接发送请求
        url = "https://hk-intra-paas.transsion.com/tranai-proxy/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_API_KEY}",
        }
        payload = {
            "model": "gpt-5.4-thinking-high",
            "messages": [
                {"role": "system", "content": "你是一位资深项目风险专家，拥有丰富的软件项目风险管理经验。你擅长分析Jira项目数据，识别潜在风险，并提供专业、具体的改进建议。你的分析应该深入、全面，并且有数据支持。你的回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "stream": True,
        }
        
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=120
        )
        
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    # 处理SSE格式的响应
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = line[6:]
                        if data != '[DONE]':
                            try:
                                chunk = json.loads(data)
                                if chunk.get('choices') and len(chunk['choices']) > 0:
                                    delta = chunk['choices'][0].get('delta')
                                    if delta and delta.get('content'):
                                        content = delta['content']
                                        print(content, end='', flush=True)
                                        analysis += content
                            except json.JSONDecodeError:
                                pass
        else:
            _log("err", f"AI 请求失败，状态码: {response.status_code}")
            analysis = f"请求 AI 失败: 状态码 {response.status_code}"
            print(analysis)
    except requests.exceptions.Timeout:
        _log("err", "AI 请求超时，请稍后重试")
        analysis = "请求 AI 失败: 超时"
        print(analysis)
    except Exception as e:
        _log("err", f"AI 请求失败: {e}")
        analysis = f"请求 AI 失败: {e}"
        print(analysis)
    
    print("", flush=True)
    print(_c("=" * 70, "97"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='智能Jira项目风险分析Agent')
    parser.add_argument('--api', action='store_true', help='启动API服务')
    parser.add_argument('--query', type=str, help='用户查询')
    args = parser.parse_args()
    
    if args.api:
        print('启动Flask API服务...')
        app.run(host='0.0.0.0', port=5002, debug=True)
    elif args.query:
        # 直接使用命令行分析函数处理查询
        user_input = args.query
        jql = generate_final_jql(user_input)
        _log("info", f"生成的 JQL: {jql}")
        
        # 全量拉取 issues
        issues = fetch_all_issues(jql)
        
        if not issues:
            _log("warn", "未获取到 Jira 的未关闭问题（或未成功获取到数据）。")
            # 构建 AI 提示词，让 AI 输出"暂无问题反馈，请自行前往jira查看"
            prompt = f"""
            你是一位资深项目风险专家。现在用户问："{user_input}"。
            我从 Jira 获取数据时，未获取到任何未关闭的问题。
            
            请用亲切、专业的中文进行对话式回答，告诉用户暂无问题反馈，请自行前往jira查看。
            """
            
            # 流式 AI 分析
            _log("step", "AI 深度分析")
            print(_c("=" * 70, "97"))
            print(_c("AI 深度分析", "1;96"))
            print(_c("=" * 70, "97"))
            print(_c("[AI 正在思考...]", "96"), flush=True)
            
            try:
                # 使用requests库直接发送请求
                url = "https://hk-intra-paas.transsion.com/tranai-proxy/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}",
                }
                payload = {
                    "model": "gpt-5.4-thinking-high",
                    "messages": [
                        {"role": "system", "content": "你是一位资深项目风险专家，拥有丰富的软件项目风险管理经验。你擅长分析Jira项目数据，识别潜在风险，并提供专业、具体的改进建议。你的分析应该深入、全面，并且有数据支持。你的回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "stream": True,
                }
                
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=120
                )
                
                if response.status_code == 200:
                    for line in response.iter_lines():
                        if line:
                            # 处理SSE格式的响应
                            line = line.decode('utf-8')
                            if line.startswith('data: '):
                                data = line[6:]
                                if data != '[DONE]':
                                    try:
                                        chunk = json.loads(data)
                                        if chunk.get('choices') and len(chunk['choices']) > 0:
                                            delta = chunk['choices'][0].get('delta')
                                            if delta and delta.get('content'):
                                                content = delta['content']
                                                print(content, end='', flush=True)
                                    except json.JSONDecodeError:
                                        pass
                else:
                    _log("err", f"AI 请求失败，状态码: {response.status_code}")
                    print(f"请求 AI 失败: 状态码 {response.status_code}")
            except requests.exceptions.Timeout:
                _log("err", "AI 请求超时，请稍后重试")
                print("请求 AI 失败: 超时")
            except Exception as e:
                _log("err", f"AI 请求失败: {e}")
                print(f"请求 AI 失败: {e}")
            
            print("", flush=True)
            print(_c("=" * 70, "97"))
            import sys
            sys.exit()
        
        # 提取关键信息，用于 AI 分析
        issue_summaries = []
        status_counts = {}
        priority_bucket_counts = {"高": 0, "中": 0, "低": 0, "无": 0}
        risk_level_counts = {"高": 0, "中": 0, "低": 0}
        
        for issue in issues:
            fields = issue["fields"]
            summary = fields["summary"]
            status = fields["status"]["name"]
            priority = fields["priority"]["name"] if fields.get("priority") else "无"
            issuetype = fields["issuetype"]["name"]
            assignee = fields["assignee"]["displayName"] if fields.get("assignee") else "未分配"
            created = fields["created"][:10]  # 只取日期部分
            labels = fields.get("labels", [])
            bug_key = issue.get("key", "")
            tcid = fields.get("customfield_10000", "")  # TCID字段
            
            # 识别风险等级
            risk_level = get_risk_level(priority, labels, summary)
            
            # 统计状态分布
            status_counts[status] = status_counts.get(status, 0) + 1
            
            # 统计优先级分布
            bucket = bucket_from_priority(priority)
            priority_bucket_counts[bucket] = priority_bucket_counts.get(bucket, 0) + 1
            
            # 统计风险等级分布
            risk_level_counts[risk_level] = risk_level_counts.get(risk_level, 0) + 1
            
            # 生成问题摘要，用于 AI 分析
            issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (类型:{issuetype}, 状态:{status}, 优先级:{priority}, 风险等级:{risk_level}, 负责人:{assignee}, 创建日期:{created})")
        
        # ========== Jira 原始数据（预览）==========
        print(_c("=" * 70, "97"))
        print(_c(f"Jira 原始数据（共 {len(issues)} 条，预览前 {min(MAX_ISSUE_PREVIEW, len(issue_summaries))} 条）", "1;97"))
        print(_c("=" * 70, "97"))
        for i, s in enumerate(issue_summaries[:MAX_ISSUE_PREVIEW], 1):
            print(_c(f"{i}. {s}", "97"), flush=True)
        print(_c("=" * 70, "97"), flush=True)
        
        bucket_order = ["高", "中", "低", "无"]
        bucket_str = ", ".join([f"{k}={priority_bucket_counts.get(k, 0)}" for k in bucket_order])
        _log("info", f"优先级分桶：{bucket_str}")
        status_str = ", ".join([f"{k}={v}" for k, v in sorted(status_counts.items(), key=lambda x: x[0])])
        _log("info", f"状态分布：{status_str}")
        
        # 构建精简版的issues_text
        filtered_issue_summaries = []
        for issue in issues[:10]:  # 只取前10个问题
            fields = issue["fields"]
            bug_key = issue.get("key", "")
            tcid = fields.get("customfield_10000", "")
            summary = fields.get("summary", "")
            issue_type = fields.get("issuetype", {}).get("name", "")
            status = fields.get("status", {}).get("name", "")
            priority = fields.get("priority", {}).get("name", "")
            labels = fields.get("labels", [])
            risk_level = get_risk_level(priority, labels, summary)
            assignee = fields.get("assignee", {}).get("displayName", "未分配")
            created = fields.get("created", "")[:10]
            filtered_issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (类型:{issue_type}, 状态:{status}, 优先级:{priority}, 风险等级:{risk_level}, 负责人:{assignee}, 创建日期:{created}")
        
        filtered_issues_text = "\n".join(filtered_issue_summaries)
        
        # 构建 AI 提示词
        prompt = f"""
        你是一位资深项目风险专家。现在用户问："{user_input}"。我已从 Jira 获取到以下未关闭问题数据：
        
        项目信息：
        - 未关闭问题总数：{len(issues)}
        - 高优先级问题：{priority_bucket_counts['高']}个
        - 中优先级问题：{priority_bucket_counts['中']}个
        - 低优先级问题：{priority_bucket_counts['低']}个
        - 高风险问题：{risk_level_counts['高']}个
        - 中风险问题：{risk_level_counts['中']}个
        - 低风险问题：{risk_level_counts['低']}个
        
        关键问题详情（前10个问题）：
        {filtered_issues_text}
        
        请用亲切、专业的中文进行对话式分析，包含：
        1. 总体风险评估（用口语化表达，如"目前风险较高，主要是...")。
        2. 按风险等级列出关键问题（至少给出 Bug ID、摘要、负责人）。
        3. 给出具体、可落地的行动建议。
        
        注意：回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。
        """
        
        # 确保prompt是一个字符串
        prompt = str(prompt)
        
        # 流式 AI 分析
        _log("step", "AI 深度分析")
        print(_c("=" * 70, "97"))
        print(_c("AI 深度分析", "1;96"))
        print(_c("=" * 70, "97"))
        print(_c("[AI 正在思考...]", "96"), flush=True)
        
        analysis = ""
        try:
            # 使用requests库直接发送请求
            url = "https://hk-intra-paas.transsion.com/tranai-proxy/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}",
            }
            payload = {
                "model": "gpt-5.4-thinking-high",
                "messages": [
                    {"role": "system", "content": "你是一位资深项目风险专家，拥有丰富的软件项目风险管理经验。你擅长分析Jira项目数据，识别潜在风险，并提供专业、具体的改进建议。你的分析应该深入、全面，并且有数据支持。你的回答要有温度，避免机械罗列数据，要像一位经验丰富的同事在和你讨论。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "stream": True,
            }
            
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=120
            )
            
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line:
                        # 处理SSE格式的响应
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data = line[6:]
                            if data != '[DONE]':
                                try:
                                    chunk = json.loads(data)
                                    if chunk.get('choices') and len(chunk['choices']) > 0:
                                        delta = chunk['choices'][0].get('delta')
                                        if delta and delta.get('content'):
                                            content = delta['content']
                                            print(content, end='', flush=True)
                                            analysis += content
                                except json.JSONDecodeError:
                                    pass
            else:
                _log("err", f"AI 请求失败，状态码: {response.status_code}")
                analysis = f"请求 AI 失败: 状态码 {response.status_code}"
                print(analysis)
        except requests.exceptions.Timeout:
            _log("err", "AI 请求超时，请稍后重试")
            analysis = "请求 AI 失败: 超时"
            print(analysis)
        except Exception as e:
            _log("err", f"AI 请求失败: {e}")
            analysis = f"请求 AI 失败: {e}"
            print(analysis)
        
        print("", flush=True)
        print(_c("=" * 70, "97"))
    else:
        # 运行原始的命令行分析
        run_command_line_analysis()