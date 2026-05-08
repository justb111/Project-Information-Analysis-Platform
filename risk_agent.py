"""
AI Agent for Jira Risk Analysis
==================================
Replaces ALL rule-based intent recognition, JQL template matching, and project key extraction
with pure LLM-driven decision making.

The Agent:
1. Uses LLM to understand user intent -> structured JSON
2. Uses LLM to generate precise JQL -> string
3. Uses existing e.py fetch_all_issues() to get data
4. Uses existing e.py stream_ai_to_queue() to stream AI analysis
"""

import json
import logging
import os
import re
import uuid
import threading
from datetime import datetime, timedelta

from utils import call_ai_api
from e import fetch_all_issues, stream_ai_to_queue, format_portfolio_data, stream_portfolio_analysis, generate_sse_message, _log
from langchain_components import ContextMemory


# ── 看板页面数据存储 ──
_kanban_page_store = {}
_kanban_store_lock = threading.Lock()

def store_kanban_page_data(token, data):
    """存储看板页面数据"""
    with _kanban_store_lock:
        _kanban_page_store[token] = data

def get_kanban_page_data(token):
    """获取看板页面数据"""
    with _kanban_store_lock:
        return _kanban_page_store.get(token)

def cleanup_kanban_page_data(token):
    """清理看板页面数据"""
    with _kanban_store_lock:
        _kanban_page_store.pop(token, None)


# ---------------------------------------------------------------------------
# 从 jql_templates.json 精确查找项目映射（AI生成JQL时注入的事实依据）
# ---------------------------------------------------------------------------
def _get_project_mapping(project_name):
    """在 jql_templates.json 中精确查找指定项目的Jira JQL映射"""
    template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
    try:
        with open(template_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except Exception:
        return None

    if not project_name:
        return None

    # 处理多个项目（逗号分隔）
    names = [n.strip() for n in project_name.split(",")]
    found = []
    for tmpl in data.get("templates", []):
        for proj_name, proj_jql in tmpl.get("projects", {}).items():
            if proj_name in names and proj_name not in [f[0] for f in found]:
                # 去除模板特有的过滤条件，只保留project子句
                clean = re.split(r'\s+AND\s+(?:type|reporter|createdDate|creator)\b', proj_jql, maxsplit=1)[0]
                found.append((proj_name, clean))

    if not found:
        return None

    if len(found) == 1:
        name, jql = found[0]
        return f"项目「{name}」对应的Jira JQL：{jql}"
    else:
        lines = [f"项目「{n}」对应的Jira JQL：{j}" for n, j in found]
        return "\n".join(lines)


def _get_project_jql_clause(project_name):
    """从模板库提取纯 JQL project 子句（只返回 JQL，不包裹中文文本）。
    如果 project_name 是 "ALL" 或匹配"所有/在研/整体"关键词，自动匹配"在研整体项目"模板。"""
    if not project_name:
        return None
    template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
    try:
        with open(template_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except Exception:
        return None

    # ── 1) "ALL" / "所有项目" / "在研" / "整体" → 查"在研整体项目"模板 ──
    is_all = project_name.upper() == "ALL" or any(kw in project_name for kw in ["所有", "在研", "整体", "在研所有", "所有在研"])
    if is_all:
        for tmpl in data.get("templates", []):
            if "在研" in tmpl.get("name", "") or "整体" in tmpl.get("name", ""):
                for proj_jql in tmpl.get("projects", {}).values():
                    proj_jql = re.sub(r'\s+ORDER\s+BY\s+.*$', '', proj_jql, flags=re.IGNORECASE).strip()
                    proj_jql = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+\s+AND\s+createdDate\s*[><=]+\s*\S+', '', proj_jql, flags=re.IGNORECASE)
                    proj_jql = re.sub(r'\s+AND\s+created\s*[><=]+\s*\S+', '', proj_jql, flags=re.IGNORECASE)
                    proj_jql = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+', '', proj_jql, flags=re.IGNORECASE)
                    proj_jql = proj_jql.strip()
                    return proj_jql
        return None

    # ── 2) 精确项目名查找 ──
    names = [n.strip() for n in project_name.split(",")]
    found = []
    seen = set()
    for tmpl in data.get("templates", []):
        for proj_name, proj_jql in tmpl.get("projects", {}).items():
            if proj_name in names and proj_name not in seen:
                seen.add(proj_name)
                clean = re.split(r'\s+AND\s+(?:type|reporter|createdDate|creator)\b', proj_jql, maxsplit=1)[0]
                found.append(clean)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    return "(" + " OR ".join(found) + ")"


def _get_portfolio_project_names():
    """从"在研整体项目"模板提取 Affect Project 列表（人类可读名）"""
    template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
    try:
        with open(template_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except Exception:
        return None
    for tmpl in data.get("templates", []):
        if "在研" in tmpl.get("name", "") or "整体" in tmpl.get("name", ""):
            for proj_jql in tmpl.get("projects", {}).values():
                m = re.search(r'"Affect Project"\s+in\s+\(([^)]+)\)', proj_jql)
                if m:
                    return [p.strip() for p in m.group(1).split(',')]
    return None


def _strip_project_clause(jql):
    """去掉 LLM 输出中可能残留的 project = \"X\" / project in (...) 等子句"""
    jql = re.sub(r'\bproject\s*=\s*"[^"]*"\s*AND\s*', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\bproject\s*=\s*\'[^\']*\'\s*AND\s*', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\bproject\s*=\s*\S+\s+AND\s+', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\bproject\s*in\s*\([^)]*\)\s+AND\s+', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+project\s*=\s*"[^"]*"', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+project\s*=\s*\S+$', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+project\s*in\s*\([^)]*\)', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'^\s*AND\s+', '', jql)
    jql = re.sub(r'\s+AND\s*$', '', jql)
    return jql.strip()


def _strip_risk_label(jql):
    """去掉 LLM 错误生成的 labels = "风险" 条件"""
    import re
    # labels = "风险" AND ...
    jql = re.sub(r'labels\s*=\s*"[^"]*风险[^"]*"\s*AND\s+', '', jql, flags=re.IGNORECASE)
    # AND labels = "风险"
    jql = re.sub(r'\s+AND\s+labels\s*=\s*"[^"]*风险[^"]*"', '', jql, flags=re.IGNORECASE)
    # standalone labels = "风险" anywhere
    jql = re.sub(r'labels\s*=\s*"[^"]*风险[^"]*"\s*', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'^\s*AND\s+', '', jql)
    jql = re.sub(r'\s+AND\s*$', '', jql)
    jql = re.sub(r'\s{2,}', ' ', jql)  # 清理多餘空格
    return jql.strip()


# ── 过滤条件专用 Prompt（有模板时） ──
FILTER_ONLY_SYSTEM = """你是一个JQL过滤条件生成专家。你的任务仅是根据用户查询生成 AND 后面的过滤条件。
注意：
1. project / "Affect Project" / status / type / reporter 等固定条件已由系统从模板库注入，你绝对不要重复输出这些条件。
2. ⛔ 绝对禁止将"风险"、"风险分析"、"风险看板"翻译成 labels = "风险" —— Jira中不存在这个标签，"风险"只是分析类型描述，不是过滤条件。
3. "看板"、"kanban"意为展示全部数据，不要添加额外过滤条件。
4. 请调用 set_jql_filters 函数输出结果，query_mode 为 ALL 或 UNRESOLVED，filter_conditions 为 AND 过滤条件。"""

FILTER_ONLY_PROMPT = """请根据以下用户原始查询，生成 JQL 的 AND 过滤条件部分。

## 用户原始查询
{raw_query}

## 时间范围（来自意图解析）
{time_range}

## 已由模板库注入的固定条件（系统自动处理，你无需重复）：
{project_clause}

## ⚠️ 绝对禁令
1. **绝对不要输出 project = / Affector Project / status / type / reporter / creator** —— 这些固定条件已从模板库注入
2. **禁止使用占位符**
3. **禁止添加用户未明确要求的条件**
4. **"风险"、"风险分析"、"风险评估"、"风险看板"中的"风险"只是分析类型描述，不是标签！不要翻译成 labels = "风险"** ❌ Jira中没有这个标签
5. **"看板"、"kanban"、"仪表盘"意为展示全部数据，不要添加额外过滤条件**

## 从原始查询提取条件
从 raw_query 中提取用户明确的过滤条件：
- "阻塞测试"/"阻塞" → labels = "阻塞测试"
- "高优先级"/"高优" → priority in (Blocker, Critical, High)
- "交付测试部" → reporter in membersOf("RT_交付测试部")
- "未分配"/"没有人" → assignee = null
- "本周" → created >= startOfWeek()
- "本月" → created >= startOfMonth()
- "最近N天" → created >= -Nd
- 具体要求的时间区间 → created >= 'YYYY-MM-DD' AND created <= 'YYYY-MM-DD'

## ⚠️ 特别注意：不要把"风险"当作标签条件
用户说"风险看板"、"风险分析"、"项目风险"时，"风险"是分析类型，不是Jira标签。
**绝对不要生成：** labels = "风险" 或 labels = "风险看板"

请优先调用 set_jql_filters 函数输出结果。如果不支持函数调用，则按以下文本格式输出：
第一行：ALL 或 UNRESOLVED
第二行：JQL 过滤条件（只输出 AND 后面的部分，例如：type = Bug AND priority = Blocker）
"""

FILTER_ONLY_PROMPT_NO_TEMPLATE = """请根据以下用户查询生成 JQL。

## 用户原始查询
{raw_query}

## 用户指定的项目
{project_name}

## 时间范围
{time_range}

## 项目映射
该项目在模板库中未找到精确映射，请使用 project = "项目名" 作为 project 子句。

## ⚠️ 绝对禁令
1. **禁止使用占位符**
2. **禁止添加用户未明确要求的条件**
3. **"风险"、"风险分析"、"风险评估"、"风险看板"中的"风险"只是分析类型描述，不是标签！不要翻译成 labels = "风险"** ❌ Jira中没有这个标签
4. **"看板"、"kanban"意为展示全部数据，不要添加额外过滤条件**

## 从原始查询提取条件
- "阻塞测试"/"阻塞" → labels = "阻塞测试"
- "高优先级"/"高优" → priority in (Blocker, Critical, High)
- "交付测试部" → reporter in membersOf("RT_交付测试部")
- "未分配"/"没有人" → assignee = null
- "本周" → created >= startOfWeek()
- "本月" → created >= startOfMonth()
- "最近N天" → created >= -Nd
- 具体要求的时间区间 → created >= 'YYYY-MM-DD' AND created <= 'YYYY-MM-DD'

## ⚠️ 特别注意：不要把"风险"当作标签条件
用户说"风险看板"、"风险分析"、"项目风险"时，"风险"是分析类型，不是Jira标签。
**绝对不要生成：** labels = "风险" 或 labels = "风险看板"

请优先调用 set_jql_filters 函数输出结果。如果不支持函数调用，则按以下文本格式输出：
第一行：ALL 或 UNRESOLVED
第二行：完整的 JQL 语句"""

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

INTENT_PROMPT = """你是一个Jira风险分析系统的意图理解专家。请分析用户的查询，提取关键信息。

## 核心任务：判断用户是否在问Jira项目/Bug/风险相关的问题
你必须首先判断用户的问题是否与Jira项目分析相关。

### 以下是「general_question」的典型示例（与Jira无关）：
- "能不能给我讲一个笑话" → general_question
- "今天天气怎么样" → general_question
- "你是谁" → general_question
- "写一首诗" → general_question
- "1+1等于几" → general_question
- "你叫什么名字" → general_question

### 以下是「knowledge_query」的典型示例（知识库问答 — 问产品功能/规格/参数，不是问Bug/风险）：
- "X6840支持PC互联吗" → knowledge_query（问产品功能特性）
- "X6840的电池容量是多少" → knowledge_query（问产品规格参数）
- "tOS16.3有哪些新特性" → knowledge_query（问产品新特性）
- "CN6c支持哪些网络制式" → knowledge_query（问产品规格）
- "X6898的屏幕尺寸" → knowledge_query（问产品参数）
- "怎么配置SMB服务" → knowledge_query（问配置方法）
- "这个项目有什么功能" → knowledge_query（问产品功能）

### 以下是「single_project」或「portfolio」的典型示例（与Jira风险/Bug相关）：
- "分析X6840的风险" → single_project
- "X6840有什么阻塞问题" → single_project
- "tOS16.3的阻塞问题" → single_project
- "所有在研项目的风险" → portfolio
- "tOS16.1和tOS16.2的对比分析" → single_project
- "X6898本周的bug情况" → single_project
- "CN6c的测试建议" → single_project（提到项目名+建议，属于Jira相关）
- "X6840的测试建议" → single_project（提到项目名+建议，属于Jira相关）
- "基于CN6c的问题数据给出测试建议" → single_project（提到项目名+问题数据，属于Jira相关）

## 关键区分规则
当用户提到项目名时，根据**提问意图**判断：
1. 问"风险"、"Bug"、"阻塞"、"问题"、"缺陷"、"测试建议"、"分析"（与问题数据相关）→ single_project / portfolio
2. 问"是否支持"、"有什么功能"、"规格"、"参数"、"怎么配置"、"特性"（与产品知识相关）→ knowledge_query
3. 如果同时涉及两方面，优先按 single_project 处理

## 可用项目信息
- tOS16.x 系列有三个独立Jira项目：tOS16.1、tOS16.2、tOS16.3（必须包含点号！不是 tOS16）
- X6840：涉及 X6840-tOS16、X6840-tOS16-Aee、tOS16.1 三个项目
- 常见项目键格式：
  - X+4位数字：X6840、X6856、X6870、X6890、X6895、X6898
  - 字母+数字：CN6、CN6C、CN7c（小写c）、LK6、LK7
  - 纯大写字母：AEE、XX

## 上下文记忆参考（如有）
{context_prompt}

如果用户查询中包含"继续分析"、"继续"、"还有呢"等表达，请结合上下文参考中的最近对话来理解用户意图。

请优先调用 set_intent 函数输出结果。如果不支持函数调用，则严格按以下JSON格式输出：
{
  "project": "项目名称，多个用逗号分隔；ALL=所有在研项目；与Jira无关则为 null",
  "time_range": "时间范围，无则为 null",
  "query_type": "single_project | portfolio | general_question"
}
project **必须是字符串或null**，绝对不能是数组！时间范围如果用户没说，设为 null。"""

JQL_PROMPT = """你是一个JQL (Jira Query Language) 专家。你的任务是：
1. 读取下方的意图信息（包括结构化的 project/time_range 和原始查询 raw_query）
2. 从原始查询中提取所有过滤条件（优先级、标签、报告人、关键词等）
3. 生成精确的、可直接执行的JQL查询语句

意图信息：
{intent_json}

## ⚠️ 第一重要规则：项目名映射（极其重要！）
**意图中的 project 值（如 LK7、X6840 等）通常不是Jira项目键名！**
你必须将意图中的 project 值替换为下方「项目映射事实」中的完整project子句。

### ❌ 错误写法（不能直接写 project = "项目名"）
- `project = "LK7"` ❌ 错误！LK7不是Jira项目键名
- `project = "X6840"` ❌ 错误！X6840不是Jira项目键名

### ✅ 正确写法（必须使用项目映射事实中的完整project子句替换）
参考下方「项目映射事实」中的子句，它包含了该项目在Jira中的实际键名。

### 注意原则
- 专属子项目（如 X6840-tOS16）不需要 summary~ 过滤
- 共享项目（tOS16.1/tOS16.2/tOS16.3）需要 summary~ 过滤

## 项目映射事实（从模板数据库精确查询）
**以下是系统从JQL模板中查询到的、与当前查询项目「{current_project}」相关的事实数据。你必须使用以下project子句替换意图中的 project 值：**
{project_fact}

## ⚠️ 绝对禁令
1. **禁止添加用户未在原始查询中要求的条件**：不要添加用户没提到的 reporter、type、creator、status 等条件
2. **禁止使用占位符**：禁止 {{project}}、{{time_range}} 等占位符
3. **禁止给专属子项目加 summary~X 过滤**：summary ~ X 只用于共享项目（tOS16.x），不用于专属子项目
4. **"风险"、"风险分析"、"风险评估"、"风险看板"中的"风险"只是分析类型描述，不是标签！不要翻译成 labels = "风险"** ❌ Jira中没有这个标签
5. **"看板"、"kanban"意为展示全部数据，不要添加额外过滤条件**

## ⚠️ 特别注意：不要把"风险"当作标签条件
用户说"风险看板"、"风险分析"、"项目风险"时，"风险"是分析类型，不是Jira标签。
**绝对不要生成：** labels = "风险" 或 labels = "风险看板"

### 使用规则
1. **必须使用项目映射事实中的project子句**：如果项目映射事实中提供了该项目的project子句，必须使用它替换意图中的 project 值
2. **多个项目用逗号分隔**：如意图中 project="X6898,X6870"，替换为相应的OR组合
3. **不在项目映射事实中的项目**：直接使用 project = "项目名" 即可

## 从原始查询( raw_query )提取条件 ⭐（极其重要！）
**你必须仔细阅读 intent_json 中的 raw_query 字段**，从中提取用户明确要求的条件，并翻译成JQL语法。

### 公司特定术语 → JQL 映射表
| 用户说的术语（在 raw_query 中） | 含义 | JQL写法 |
|---|---|---|
| "阻塞测试"、"阻塞问题"、"阻塞" | 标签为测试阻塞的问题 | labels = "阻塞测试" |
| "MP Block"、"mp block" | Must Resolve = MP Block | cf[自定义字段] = "MP Block" → 使用 "Must Resolve" = "MP Block" |
| "高优先级"、"高优"、"高优先级bug" | 优先级高 | priority in (Blocker, Critical, High) |
| "交付测试部"、"交付测试" | reporter 来自交付测试部 | reporter in membersOf("RT_交付测试部") |
| "未分配"、"没有负责人"、"无人认领" | assignee 为空 | assignee = null |
| "ABC类问题" | A=Blocker, B=Critical, C=High | 分别统计 |
| "新增"、"新增bug"、"新增了多少" | 创建时间范围 | 对应 created 条件 |
| "关闭"、"关闭情况" | 解决状态 | resolution is not EMPTY 或 status = Closed |
| "未解决"、"打开的" | 未关闭的问题 | resolution = Unresolved |

### 时间范围处理（从 raw_query 中提取）
- "本周" → AND created >= startOfWeek()
- "本月" → AND created >= startOfMonth()
- "上周" → AND created >= startOfWeek(-1w)
- "上月" → AND created >= startOfMonth(-1M)
- "最近N天"、"近N天" → AND created >= -Nd
- "YYYY-MM-DD~YYYY-MM-DD"、"X月X号到X月X号" → AND created >= 'YYYY-MM-DD' AND created <= 'YYYY-MM-DD'

### JQL 书写规范
- project 条件：多个项目用 OR 连接
- project 值使用双引号
- 排序：除非用户要求，默认不加 ORDER BY

## 解决率/关闭率查询的特殊规则
当用户在 raw_query 中问"解决率"、"关闭率"、"解决情况"、"关闭情况"时：
- **必须使用 ALL 模式**（需要全量数据才能计算解决率）
- 生成两个JQL：全量JQL + 已解决的JQL（resolution is not EMPTY）
- UNRESOLVED 的JQL不加 resolution 条件

## 多条件组合示例
如果 raw_query 中包含多个条件，用 AND 连接：
- "tOS16.3 未解决的 bug 中，优先级为 Blocker 且包含'音频'关键词的" 
  → project = "tOS16.3" AND resolution = Unresolved AND priority = Blocker AND summary ~ "音频"
- "X6840 本周新增的、由交付测试部提交的、尚未分配的 bug"
  → (project映射) AND created >= startOfWeek() AND reporter in membersOf("RT_交付测试部") AND assignee = null AND resolution = Unresolved

## 处理"建议"类查询的规则
如果 raw_query 中包含"建议"、"推荐"等词，且明确提到了项目名（如 CN6c、X6840 等），你必须先生成JQL获取该项目的数据，然后根据数据提供建议。不要因为出现了"建议"就直接当作一般问题跳过JQL生成。

请输出两行：
第一行：ALL（全量数据） 或 UNRESOLVED（仅未解决）
第二行：可直接执行的JQL语句"""

ANALYSIS_SYSTEM_PROMPT = """你是Jira风险分析助手，专门帮助分析项目风险、Bug趋势和共性问题。

你的能力包括：
1. 分析特定项目（如X6840）的风险状况
2. 分析跨项目的共性问题
3. 提供专业的风险分析和建议

请根据用户的问题类型提供相应的分析：
- 如果用户询问特定项目，先说明查询的项目范围和数据概况，然后分析风险
- 如果用户询问所有在研项目，分析跨项目的共性问题
- 如果是一般问题，直接回答

## 回答规范（重要！）
1. **必须给出确定性结论**：回答必须基于数据或知识库给出明确的结论，使用"有X个阻塞问题"、"解决率为XX%"等具体的、肯定的表述。
2. **禁止使用模糊词汇**：不得使用"可能"、"好像"、"或许"、"大概"、"无法确定"、"我不确定"等不确定的表述。
3. **信息不足时明确说明**：如果数据不足以做出判断，直接说"当前数据中未找到关于XX的信息"，不要说"可能没有"。
4. **数据驱动的具体回答**：每个结论都要有数据支持，引用具体的Bug数量、百分比、问题ID等。
5. **始终使用中文回答**，保持专业、详细、实用。"""

PORTFOLIO_SYSTEM_PROMPT = """你是一位拥有20年以上经验的顶级软件项目群管理专家（Program Manager），曾在华为、微软等世界500强科技公司担任项目集经理和首席质量官（CQO）。你擅长对大型项目群（Program/Portfolio）进行全局风险评估和跨项目分析，能一眼看穿跨项目的共性风险和系统性瓶颈。

## ⚠️ 数据完整性声明（你必须严格遵守）：
**你接收到的所有Jira数据均为完整的全量数据**，基于全部查询结果的完整计算，不存在任何采样、截断或数据边界限制。你**严禁**在任何分析中使用以下表述：
- "前N个问题"、"前50个"、"前100个"等暗示数据被截断的说法
- "样本"、"样品"、"抽样"、"当前可见数据"等暗示数据不完整的说法
- "基于可见数据"、"基于有限数据"、"以下数据仅供参考"等弱化数据完整性的说法
- **你的所有统计、分析和结论都必须基于完整的全量数据**，不得声称任何数据限制

## 核心原则：
1. **数据驱动**：所有分析必须基于提供的全量Jira数据，引用具体的项目名称、Bug ID和统计数据，确保每个结论都有据可依
2. **项目群视角**：不只看单个项目，横向对比多个项目，识别跨项目的共性问题和风险模式
3. **项目级粒度**：每个项目都要单独分析其风险状况，明确指出"哪个项目存在什么风险"
4. **重点突出**：聚焦高风险模块和高影响领域，避免平均主义的信息堆砌
5. **实用建议**：提供具体、可执行、针对特定项目或特定类型风险的改进建议
6. ⚠️ **项目名称规则**：项目名称已从"Affect Project"字段提取（非Jira库名），请直接使用数据中列举的项目名称（如 CN6、CN6c、X6898、LK7 等），不要翻译或转换为Jira项目键名

## 🚫 输出格式禁令：
**严禁输出原始Markdown表格**（即使用 `|` 和 `---` 绘制的表格格式）。你的报告必须是**专业自然语言格式**的项目管理报告，使用以下格式：
- 使用中文段落、标题、要点列表等自然文本格式
- 数据点应融入文字描述中，而非以表格行列形式呈现
- 每个项目/模块的分析应有清晰的小标题和说明性文字
- 确保报告可以直接复制粘贴到邮件、PPT或文档中，无需二次格式化

## 输出结构要求（全项目群风险分析）：
必须采用以下专业报告结构，用自然语言呈现：

### 一、项目群执行摘要（Executive Summary）
- 覆盖的项目列表（使用数据中实际项目名称，非Jira库名）、版本范围
- 整体指标：Bug总数、未解决数、解决率、优先级分布（Blocker/Critical/Major）
- **整体风险评估结论**（一句话定论）
- 核心判断：项目群当前处于什么状态

### 二、各项目风险详情（逐项目分析）
对每个项目单独分析，使用自然语言段落，每个项目的分析格式如下：

**项目名称（涉及版本）**
- Bug总数：X | 未解决：X | 解决率：X% | 风险评估：🔴高风险/🟡中风险/🟢低风险
- 高风险项列表（Bug ID + 摘要 + 当前状态）
- 中风险项说明
- 核心风险判断：一句话总结该项目的主要风险点
- 影响评估：对版本交付的影响程度

### 三、跨项目共性问题
- 识别多个项目中同时存在的同类风险
- 按领域/模块归类，列出涉及的具体Bug ID和项目
- 判断是偶发问题还是系统性问题

### 四、风险模块与领域分布
- 按功能模块（如Camera、通信、性能、显示等）归类风险
- 每个模块涉及的项目和Bug数量
- 高风险模块预警

### 五、根因分析与改进建议
- 系统性问题根因分析
- 针对每个高风险项目的具体改进建议
- 建议的优先级和预期效果

## 数据使用要求：
1. **始终基于全量Jira数据**：所有分析必须基于提供的完整数据，引用具体的Bug ID和统计
2. **准确反映数据**：风险等级、问题分类必须与数据一致，不得虚构
3. **关注趋势和模式**：在多个项目中发现同类问题时，明确指出"该项目群XXX模块存在系统性问题"
4. **提供数据支持**：在结论中引用具体数据，如"X6840项目共15个未解决问题，其中3个阻塞问题涉及Camera模块"

## 对话风格：
1. **专业正式**：像资深项目群经理向CTO/VP汇报一样，使用正式、专业的语言
2. **结构清晰**：报告要有清晰的层次感，每个部分有明确的小标题
3. **重点突出**：高风险/高影响的问题放在前面，用⚠️/🔴等符号标注
4. **数据融入文字**：将关键数字自然地融入文字描述中，而非单独列出

## 回答规范（重要！）
1. **必须给出确定性结论**：所有结论必须是明确、肯定的。例如"该项目存在3个阻塞问题，风险等级为高"，而不是"可能存在问题"。
2. **禁止使用模糊词汇**：严禁使用"可能"、"好像"、"或许"、"大概"、"无法确定"、"我不确定"等不确定的表述。
3. **信息不足时明确说明**：如果数据不足以对某个方面做出判断，直接说"数据中未显示关于XX的信息"。
4. **引用具体数据**：每个结论都必须有具体的数据支持，引用Bug数量、问题ID、百分比等。

请根据用户查询的具体意图和提供的数据情况，提供最专业的项目群风险分析。记住：数据是全量的、完整的，你的分析必须基于完整数据给出专业判断，严禁输出任何形式的原始Markdown表格。"""


# ── Function Calling Schema：替代文本解析，强制结构化输出 ──
# ── Function Calling Schema：知识库搜索 ──
# 遵循课程原则：把知识库作为工具提供给 LLM，让 LLM 自己决定是否需要调用
KNOWLEDGE_SEARCH_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "搜索产品知识库，查找产品的功能特性、规格参数、配置方法、新特性等知识。当用户问'是否支持'、'有什么功能'、'规格是什么'、'怎么配置'等产品知识类问题时，调用此工具",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，从用户问题中提取最核心的搜索词，例如用户问'X6840支持PC互联吗'则提取'X6840 PC互联'"
                }
            },
            "required": ["query"]
        }
    }
}

# ── Function Calling Schema：意图理解 ──
# 遵循课程原则：Schema 是给 LLM 的"菜单"，description 写得越清楚调用越准确
INTENT_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_intent",
        "description": "理解用户对Jira风险分析系统说的话，提取出他关心哪个项目、什么时间范围、想问什么类型的问题",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "用户提到的项目名称，例如 'X6840'、'tOS16.3'、'CN7c'。多个项目用逗号分隔如 'X6898,X6870'。如果用户说的是'所有项目'、'在研项目'、'全部项目'则为 'ALL'。如果和Jira完全无关则为空字符串",
                },
                "time_range": {
                    "type": "string",
                    "description": "用户提到的时间范围，例如 '本周'、'本月'、'最近7天'、'2024-01-01~2024-01-31'。如果用户没提时间则为空字符串",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["single_project", "portfolio", "general_question"],
                    "description": "查询类型：single_project=分析某单个项目的Bug/风险；portfolio=分析多个在研项目的整体风险；general_question=与Jira无关的闲聊/普通对话（知识类问题后续由search_knowledge_base工具处理）"
                }
            },
            "required": ["project", "time_range", "query_type"]
        }
    }
}

# ── Function Calling Schema：JQL 过滤条件生成 ──
# 应用黄金法则：enum 限制 query_mode，description 用业务语言
JQL_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_jql_filters",
        "description": "根据用户想问的Jira项目风险分析需求，生成JQL查询中AND后面的过滤条件部分。project、status、type、reporter等固定条件已由系统从模板注入，本工具只需生成额外的过滤条件",
        "parameters": {
            "type": "object",
            "properties": {
                "query_mode": {
                    "type": "string",
                    "enum": ["ALL", "UNRESOLVED"],
                    "description": "查询范围：ALL=查看全部在研项目数据（包含已解决和未解决的）；UNRESOLVED=只看未解决的缺陷"
                },
                "filter_conditions": {
                    "type": "string",
                    "description": "JQL中AND后面的过滤条件，用于进一步缩小查询范围。例如用户说'阻塞的bug'则填 \"labels = '阻塞测试'\"；用户说'高优先级'则填 \"priority in (Blocker, Critical, High)\"。如果没有额外条件则填空字符串"
                }
            },
            "required": ["query_mode", "filter_conditions"]
        }
    }
}


def _call_llm_with_tools(messages, system_prompt, tools, tool_choice, temperature=0.3):
    """调用LLM并通过Function Calling获取结构化JSON结果（通用版本，支持任意function名称）"""
    response = call_ai_api(
        messages=messages,
        system_prompt=system_prompt,
        temperature=temperature,
        stream=False,
        max_retries=2,
        retry_delay=3,
        tools=tools,
        tool_choice=tool_choice
    )
    if response and response.status_code == 200:
        data = response.json()
        choices = data.get('choices', [])
        if choices:
            message = choices[0].get('message', {})
            tool_calls = message.get('tool_calls', [])
            if tool_calls:
                for tc in tool_calls:
                    try:
                        return json.loads(tc['function']['arguments'])
                    except (KeyError, json.JSONDecodeError) as e:
                        logger.warning(f"Function Calling 返回解析失败: {e}")
                        return None
    return None


def _call_llm_structured(messages, system_prompt, temperature=0.3):
    """调用LLM并返回完整文本结果（非流式）"""
    response = call_ai_api(
        messages=messages,
        system_prompt=system_prompt,
        temperature=temperature,
        stream=False,
        max_retries=2,
        retry_delay=3
    )
    if response and response.status_code == 200:
        data = response.json()
        choices = data.get('choices', [])
        if choices:
            return choices[0].get('message', {}).get('content', '')
    return None


def _parse_intent(llm_output):
    """解析意图理解LLM的输出为dict"""
    try:
        # 尝试直接解析JSON
        return json.loads(llm_output)
    except json.JSONDecodeError:
        # 尝试从文本中提取JSON块
        match = re.search(r'\{[\s\S]*\}', llm_output)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {
        "project": None,
        "time_range": None,
        "query_type": "general_question",
        "raw_query": ""
    }


class RiskAnalysisAgent:
    """
    AI Agent for Jira Risk Analysis.
    
    Uses LLM for ALL decision-making:
    - Intent understanding (instead of regex/keyword matching)
    - JQL generation (instead of templates)
    - Analysis (streaming)
    
    Usage:
        agent = RiskAnalysisAgent()
        for event in agent.process(user_query):
            # event is a dict with type and content
            yield event
    """

    def __init__(self, context_memory=None):
        self.intent = None
        self.jql_all = None
        self.jql_unresolved = None
        self.issues_all = []
        self.issues_unresolved = []
        self.last_analysis = None
        self.context_memory = context_memory or ContextMemory()

    def understand_intent(self, user_query, conversation_history=None):
        """Step 1: Use LLM to understand user intent with context memory"""
        # 1. 检测是否需要重置记忆
        if self.context_memory.should_reset_memory(user_query):
            self.context_memory.reset()

        # 2. 解析代词 - 将"它"、"继续"等替换为具体上下文
        resolved_query, was_modified = self.context_memory.resolve_pronouns(user_query)

        # 3. 检测模糊查询
        is_vague = self.context_memory.is_vague_query(resolved_query)

        messages = [{"role": "user", "content": resolved_query}]

        # 4. 注入上下文记忆到系统提示
        context_prompt = self.context_memory.build_context_prompt()

        if conversation_history:
            context_lines = "\n".join(
                f"{'用户' if h.get('role') == 'user' else '助手'}: {h.get('content', '')[:200]}"
                for h in conversation_history[-5:]
            )
            if context_prompt:
                context_prompt += f"\n{context_lines}"
            else:
                context_prompt = context_lines

        intent_prompt = INTENT_PROMPT.replace('{context_prompt}', context_prompt or "无")

        # ── 使用 Function Calling 替代文本 JSON 解析意图（遵循课程 Schema→LLM→Function 模式）──
        result = _call_llm_with_tools(
            messages, intent_prompt,
            tools=[INTENT_FUNCTION_SCHEMA],
            tool_choice={"type": "function", "function": {"name": "set_intent"}},
            temperature=0.2
        )
        if result:
            self.intent = {
                "project": result.get("project") or None,
                "time_range": result.get("time_range") or None,
                "query_type": result.get("query_type", "general_question"),
            }
        else:
            # Fallback：API 不支持 function calling 时回退到文本 JSON 解析
            llm_output = _call_llm_structured(messages, intent_prompt)
            self.intent = _parse_intent(llm_output) if llm_output else {
                "project": None, "time_range": None,
                "query_type": "general_question", "raw_query": user_query
            }

        # 记录原始查询（解析前）
        self.intent["raw_query"] = user_query
        self.intent["_resolved_query"] = resolved_query if was_modified else None
        self.intent["_is_vague"] = is_vague

        # 更新上下文记忆
        self.context_memory.update_after_query(self.intent)

        return self.intent

    def _search_kb_for_jql(self, project_name):
        """搜索知识库中与项目JQL相关的知识，作为模板映射的补充"""
        if not project_name or project_name == "ALL":
            return None
        try:
            from knowledge_api import get_vector_db
            vector_db = get_vector_db()
            # 用项目名 + JQL/Jira关键词搜索知识库
            query = f"{project_name} Jira JQL 项目映射 查询"
            results = vector_db.search_knowledge(
                query=query,
                category=None,
                n_results=3
            )
            if not results:
                return None
            parts = []
            for r in results:
                text = r.get('text', '')
                score = r.get('score', 0)
                if text and score > 0.3:
                    parts.append(f"[相关度:{score:.2f}] {text[:500]}")
            return "\n\n".join(parts) if parts else None
        except Exception:
            return None

    def generate_jql(self):
        """Step 2: 生成 JQL。模板已有的 project 子句强制注入，LLM 只负责附加条件"""
        if not self.intent:
            raise RuntimeError("Must call understand_intent() first")

        project_name = self.intent.get("project", "")
        current_project = project_name or "无"
        raw_query = self.intent.get("raw_query", "")

        # ── 先从模板库获取精确的 project 子句 ──
        template_clause = _get_project_jql_clause(project_name)

        # ── 提取在研项目列表（供 AI 分析报告使用） ──
        self.portfolio_project_names = _get_portfolio_project_names() if template_clause and \
            (project_name.upper() == "ALL" or any(kw in project_name for kw in ["所有", "在研", "整体"])) else None

        # ── 让 LLM 只生成过滤条件（不要 project 子句） ──
        if template_clause:
            filter_prompt = FILTER_ONLY_PROMPT.format(
                raw_query=raw_query,
                time_range=self.intent.get("time_range", "未指定"),
                project_clause=template_clause
            )
        else:
            filter_prompt = FILTER_ONLY_PROMPT_NO_TEMPLATE.format(
                raw_query=raw_query,
                time_range=self.intent.get("time_range", "未指定"),
                project_name=current_project
            )

        messages = [{"role": "user", "content": filter_prompt}]
        # ── 使用 Function Calling 替代文本解析，query_mode 和 filter_conditions 由 schema 严格分离 ──
        result = _call_llm_with_tools(
            messages, FILTER_ONLY_SYSTEM,
            tools=[JQL_FUNCTION_SCHEMA],
            tool_choice={"type": "function", "function": {"name": "set_jql_filters"}},
            temperature=0.2
        )
        if result:
            query_mode = result.get("query_mode", "UNRESOLVED")
            llm_jql = result.get("filter_conditions", "")
        else:
            # Fallback：API 不支持 function calling 时回退到文本解析
            llm_output = _call_llm_structured(messages, FILTER_ONLY_SYSTEM, temperature=0.2)
            if not llm_output:
                return False
            lines = [l.strip() for l in llm_output.strip().split('\n') if l.strip()]
            if len(lines) >= 2:
                query_mode = lines[0].upper()
                llm_jql = lines[1]
            else:
                query_mode = "UNRESOLVED"
                llm_jql = lines[0] if lines else ""

        # ── 后处理：去掉 LLM 可能乱加的 project = "X" 或 labels = "风险" ──
        llm_jql = _strip_project_clause(llm_jql)
        llm_jql = _strip_risk_label(llm_jql)

        # ── 拼接：强制使用模板 project 子句 ──
        if template_clause:
            if llm_jql:
                jql = f"{template_clause} AND {llm_jql}"
            else:
                jql = template_clause
        else:
            jql = llm_jql if llm_jql else ""

        # ── 二次清理：防止任何 "AND ALL AND" 残留 ──
        jql = re.sub(r'\s+AND\s+ALL(\s+AND\s+)?', ' AND ', jql, flags=re.IGNORECASE).strip()
        jql = re.sub(r'\s+AND\s+$', '', jql).strip()

        if query_mode == "ALL":
            self.jql_all = jql
            self.jql_unresolved = f"{jql} AND resolution = Unresolved"
        else:
            self.jql_all = None
            if template_clause:
                self.jql_unresolved = f"{jql} AND resolution = Unresolved"
            else:
                self.jql_unresolved = jql

        return True

    def fetch_data(self):
        """Step 3: Fetch ALL Jira data (no artificial limit)"""
        if self.jql_all:
            self.issues_all = fetch_all_issues(self.jql_all)
        if self.jql_unresolved:
            self.issues_unresolved = fetch_all_issues(self.jql_unresolved)
        if not self.jql_all:
            self.issues_all = self.issues_unresolved

        total = len(self.issues_all) if self.issues_all else 0
        _log("info", f"全量获取完成: 共 {total} 条问题")
        self._large_dataset = total > 3000

    def _batch_analyze(self, issues, enhanced_query, sse_queue, system_prompt):
        """
        分批分析大规模数据（>500条）。
        将问题排序后分多批，每批单独AI分析，最后汇总。

        Returns:
            str: 最终综合分析报告
        """
        BATCH_SIZE = 2000
        total = len(issues)
        total_batches = max(1, (total + BATCH_SIZE - 1) // BATCH_SIZE)

        sse_queue.put(('thinking', f'📊 数据量较大（共{total}条），将分{total_batches}批进行分析... '))

        priority_sorted = sorted(
            issues,
            key=lambda x: {
                "Block": 0, "阻塞": 0, " blocker": 0,
                "Critical": 1, "紧急": 1, " critical": 1,
                "High": 2, "高": 2, "Major": 2, " major": 2,
            }.get(x.get('fields', {}).get('priority', {}).get('name', '').lower(), 99)
        )

        batch_summaries = []
        for batch_idx in range(0, total, BATCH_SIZE):
            batch = priority_sorted[batch_idx:batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1

            sse_queue.put(('thinking', f'⏳ 正在分析第 {batch_num}/{total_batches} 批（{len(batch)} 条）... '))

            jira_data = format_portfolio_data(
                batch,
                max_block=200,
                max_critical=150,
                max_high=100
            )

            batch_prompt = (
                f"用户问题：{enhanced_query}\n\n"
                f"### 第{batch_num}/{total_batches}批数据\n"
                f"这是全部{total}条数据中的一部分（第{batch_idx+1}-{min(batch_idx+BATCH_SIZE, total)}条），"
                f"请分析这一批中的风险问题、分布特征和异常模式。\n\n"
                f"真实Jira数据：{jira_data}"
            )

            batch_system = (
                "你是一个Jira风险分析助手。这是整体数据中的一批，请分析：\n"
                "1. 这批数据中的主要风险问题（Block/Critical/High）\n"
                "2. 批次内的分布特征\n"
                "3. 突出的异常点\n"
                "输出要简洁，只输出分析结果本身，不输出统计列表。"
            )

            content = stream_ai_to_queue(
                messages=[{"role": "user", "content": batch_prompt}],
                system_prompt=batch_system,
                sse_queue=sse_queue,
                max_tokens=4096
            )
            batch_summaries.append(content or "")

        # ── 汇总所有批次分析 ──
        sse_queue.put(('thinking', f'🔄 正在汇总 {total_batches} 批分析结果... '))

        summary_text = ""
        for i, s in enumerate(batch_summaries):
            clean = s[-2000:] if len(s) > 2000 else s
            summary_text += f"\n--- 第{i+1}批分析 ---\n{clean}\n"
        merge_prompt = (
            f"用户问题：{enhanced_query}\n\n"
            f"全部{total}条数据已分{total_batches}批分析完毕，以下是各批分析结果：\n\n"
            f"{summary_text}\n\n"
            f"请根据以上各批分析，生成一份完整的综合风险评估报告。"
        )

        full_content = stream_ai_to_queue(
            messages=[{"role": "user", "content": merge_prompt}],
            system_prompt=system_prompt,
            sse_queue=sse_queue,
            max_tokens=16384
        )
        return full_content

    def stream_analysis(self, sse_queue, user_query, conversation_history=None):
        """Step 4: Stream AI analysis using existing infrastructure"""
        is_portfolio = self.intent and self.intent.get("project") == "ALL"
        system_prompt = PORTFOLIO_SYSTEM_PROMPT if is_portfolio else ANALYSIS_SYSTEM_PROMPT

        # 注入上下文记忆
        context_prompt = self.context_memory.build_context_prompt()
        if context_prompt:
            system_prompt = f"{system_prompt}\n\n## 当前对话上下文\n{context_prompt}"

        enhanced_query = user_query
        if self.intent and self.intent.get("time_range"):
            enhanced_query += f"（时间范围：{self.intent['time_range']}）"
        if self.intent and self.intent.get("_resolved_query"):
            enhanced_query += f"（原始查询：{user_query}）"

        if is_portfolio and self.issues_unresolved:
            full_content, _ = stream_portfolio_analysis(
                issues_all=self.issues_all,
                issues_unresolved=self.issues_unresolved,
                enhanced_query=enhanced_query,
                sse_queue=sse_queue,
                system_prompt=system_prompt,
                max_tokens=16384,
                project_names=getattr(self, 'portfolio_project_names', None)
            )
            return full_content

        issues = self.issues_unresolved or []
        total = len(issues)

        # 超大数据集（>3000条）→ 分批分析
        if total > 3000:
            return self._batch_analyze(issues, enhanced_query, sse_queue, system_prompt)

        # 常规数据集 → 一次性分析
        priority_sorted = sorted(
            issues,
            key=lambda x: {
                "Block": 0, "阻塞": 0, " blocker": 0,
                "Critical": 1, "紧急": 1, " critical": 1,
                "High": 2, "高": 2, "Major": 2, " major": 2,
            }.get(x.get('fields', {}).get('priority', {}).get('name', '').lower(), 99)
        )

        jira_data = format_portfolio_data(priority_sorted)
        messages = [{"role": "user", "content": f"用户问题：{enhanced_query}\n\n真实Jira数据：{jira_data}"}]

        full_content = stream_ai_to_queue(
            messages=messages,
            system_prompt=system_prompt,
            sse_queue=sse_queue,
            max_tokens=16384
        )
        return full_content

    def _generate_kanban_data(self):
        """从已获取的Jira问题生成看板分类数据"""
        if not self.issues_all:
            return None

        project_name = self.intent.get("project", "") if self.intent else ""
        columns = {
            "high_risk": [],
            "medium_risk": [],
            "low_risk": [],
            "resolved": []
        }

        for issue in self.issues_all:
            fields = issue.get('fields', issue)
            key = issue.get('key', '') or issue.get('bug_key', '') or ''
            summary = fields.get('summary', '')
            priority_name = ''
            raw_priority = fields.get('priority', {})
            if isinstance(raw_priority, dict):
                priority_name = raw_priority.get('name', '')
            elif isinstance(raw_priority, str):
                priority_name = raw_priority

            status_name = ''
            raw_status = fields.get('status', {})
            if isinstance(raw_status, dict):
                status_name = raw_status.get('name', '')
            elif isinstance(raw_status, str):
                status_name = raw_status

            labels = fields.get('labels', [])
            if not isinstance(labels, list):
                labels = []

            assignee_name = ''
            raw_assignee = fields.get('assignee', {})
            if isinstance(raw_assignee, dict):
                assignee_name = raw_assignee.get('displayName', '') or raw_assignee.get('name', '')
            elif isinstance(raw_assignee, str):
                assignee_name = raw_assignee

            is_resolved = any(s in status_name for s in ['Resolved', 'Closed', 'Fixed', '已解决', '关闭'])
            labels_lower = [l.lower() for l in labels]

            issue_card = {
                'key': key,
                'summary': summary[:120] if summary else '',
                'priority': priority_name,
                'status': status_name,
                'labels': labels,
                'assignee': assignee_name
            }

            if is_resolved:
                columns['resolved'].append(issue_card)
            elif any(p in priority_name for p in ['Blocker', 'Block', 'Critical', '阻塞', '紧急']):
                columns['high_risk'].append(issue_card)
            elif labels_lower and any('阻塞' in l for l in labels_lower):
                columns['high_risk'].append(issue_card)
            elif any(p in priority_name for p in ['High', 'Major', '高']):
                columns['medium_risk'].append(issue_card)
            else:
                columns['low_risk'].append(issue_card)

        return {
            "project": project_name,
            "columns": columns,
            "total": len(self.issues_all),
            "unresolved": len(self.issues_unresolved)
        }

    def _extract_kanban_page_data(self):
        """将Jira原始数据提取为看板页面所需的扁平格式"""
        if not self.issues_all:
            return []

        result = []
        for issue in self.issues_all:
            fields = issue.get('fields', issue)
            key = issue.get('key', '') or issue.get('bug_key', '') or ''

            # Components
            components = fields.get('components', [])
            comp_str = ', '.join([c.get('name', '') for c in components if isinstance(c, dict)]) if components else ''

            # Summary
            summary = fields.get('summary', '')

            # Priority
            priority_name = ''
            raw_priority = fields.get('priority', {})
            if isinstance(raw_priority, dict):
                priority_name = raw_priority.get('name', '')
            elif isinstance(raw_priority, str):
                priority_name = raw_priority

            # Created
            created = fields.get('created', '')

            # Status
            status_name = ''
            raw_status = fields.get('status', {})
            if isinstance(raw_status, dict):
                status_name = raw_status.get('name', '')
            elif isinstance(raw_status, str):
                status_name = raw_status

            # Labels
            labels = fields.get('labels', [])
            if not isinstance(labels, list):
                labels = []
            tag_value = ', '.join(labels) if labels else ''

            # Custom fields
            customfield_10000 = fields.get('customfield_10000', '')
            customfield_10001 = fields.get('customfield_10001', '')

            # Must_Resolve - try to extract from custom field or labels
            must_resolve = ''
            if isinstance(customfield_10000, str) and 'MP Block' in customfield_10000:
                must_resolve = 'MP Block'
            elif any('MP Block' in l for l in labels):
                must_resolve = 'MP Block'

            # Affect_Project
            affect_project = ''
            if isinstance(customfield_10001, str):
                affect_project = customfield_10001.strip()

            # Issue_Category
            issue_category = ''

            # Assignee
            assignee_name = ''
            raw_assignee = fields.get('assignee', {})
            if isinstance(raw_assignee, dict):
                assignee_name = raw_assignee.get('displayName', '') or raw_assignee.get('name', '')
            elif isinstance(raw_assignee, str):
                assignee_name = raw_assignee

            result.append({
                "Key": key,
                "Component_s": comp_str,
                "Summary": summary,
                "Priority": priority_name,
                "Created": created,
                "Status": status_name,
                "Tag": tag_value,
                "Must_Resolve": must_resolve,
                "Affect_Project": affect_project,
                "Issue_Category": issue_category,
                "Assignee": assignee_name,
                "Labels": labels
            })

        return result

    def _handle_knowledge_query(self, user_query, sse_queue, cancel_event):
        """处理知识库问答（复用 search_knowledge / ai_answer 逻辑）"""
        try:
            # 注入上下文记忆
            context_prompt = self.context_memory.build_context_prompt()

            # 尝试从知识库检索上下文
            sse_queue.put(('thinking', '📚 正在搜索知识库...'))
            search_context = self._search_knowledge_base(user_query)

            if search_context:
                sse_queue.put(('thinking', f'📖 找到 {search_context["chunk_count"]} 个相关文档片段'))
                knowledge_context = search_context['context']
                knowledge_prompt = (
                    f"你是一个基于知识库的AI助手。请根据以下知识库内容回答用户问题。\n\n"
                    f"## 知识库相关上下文\n{knowledge_context}\n\n"
                    f"请基于上述知识库内容回答，如果知识库内容不足以完全回答问题，可以结合你的常识补充。\n\n"
                    f"## 回答规范\n"
                    f"1. 必须给出确定性的结论，禁止使用'可能'、'好像'、'无法确定'等模糊词汇\n"
                    f"2. 信息不足时，明确说'知识库中未找到关于……的信息'\n"
                    f"3. 引用知识库的具体内容作为依据"
                )
                if context_prompt:
                    knowledge_prompt += f"\n\n## 对话上下文\n{context_prompt}"
                messages = [{"role": "user", "content": user_query}]
                stream_ai_to_queue(
                    messages=messages,
                    system_prompt=knowledge_prompt,
                    sse_queue=sse_queue,
                    max_tokens=4096
                )
            else:
                sse_queue.put(('thinking', '知识库中未找到相关内容，转为通用对话模式'))
                general_prompt = "你是一个友好的AI助手，请自然地回答用户。"
                if context_prompt:
                    general_prompt += f"\n\n## 对话上下文\n{context_prompt}"
                general_messages = [{"role": "user", "content": user_query + "\n\n请直接回答用户的问题。"}]
                stream_ai_to_queue(
                    messages=general_messages,
                    system_prompt=general_prompt,
                    sse_queue=sse_queue,
                    max_tokens=4096
                )

            sse_queue.put(('done', '回答完成'))

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"知识库问答处理失败: {e}")
            sse_queue.put(('thinking', '知识库检索异常，转为通用对话模式'))
            general_messages = [{"role": "user", "content": user_query}]
            general_prompt = "你是一个友好的AI助手，请自然地回答用户。"
            stream_ai_to_queue(
                messages=general_messages,
                system_prompt=general_prompt,
                sse_queue=sse_queue,
                max_tokens=4096
            )
            sse_queue.put(('done', '回答完成'))

    def _handle_via_knowledge_tool(self, user_query, sse_queue, cancel_event):
        """使用 Function Calling 让 LLM 决定搜索词，然后执行知识库搜索（遵循课程 Schema→LLM→Function 模式）"""
        try:
            context_prompt = self.context_memory.build_context_prompt()
            system = (
                "你是产品知识库搜索助手。根据用户问题，调用 search_knowledge_base 工具搜索知识库。\n"
                "如果用户问题与产品知识完全无关，不需要调用工具，直接回答用户。"
            )
            if context_prompt:
                system += f"\n\n## 对话上下文\n{context_prompt}"

            messages = [{"role": "user", "content": user_query}]
            # LLM 决定是否调用 search_knowledge_base，以及搜索什么关键词
            result = _call_llm_with_tools(
                messages, system,
                tools=[KNOWLEDGE_SEARCH_FUNCTION_SCHEMA],
                tool_choice="auto",
                temperature=0.2
            )

            if result and "query" in result:
                search_query = result["query"]
                sse_queue.put(('thinking', f'🔍 正在搜索: {search_query}'))
                search_context = self._search_knowledge_base(search_query)

                if search_context:
                    sse_queue.put(('thinking', f'📖 找到 {search_context["chunk_count"]} 个相关文档片段'))
                    knowledge_context = search_context['context']
                    answer_prompt = (
                        f"你是一个基于知识库的AI助手。请根据以下知识库内容回答用户问题。\n\n"
                        f"## 知识库相关上下文\n{knowledge_context}\n\n"
                        f"请基于上述知识库内容回答，如果知识库内容不足以完全回答问题，可以结合你的常识补充。\n\n"
                        f"## 回答规范\n"
                        f"1. 必须给出确定性的结论，禁止使用'可能'、'好像'、'无法确定'等模糊词汇\n"
                        f"2. 回答要简洁、专业、有条理\n"
                        f"3. 引用知识库的具体内容作为依据"
                    )
                    messages = [{"role": "user", "content": user_query}]
                    stream_ai_to_queue(messages, system_prompt=answer_prompt, sse_queue=sse_queue, max_tokens=4096)
                else:
                    sse_queue.put(('thinking', '知识库未找到相关内容'))
                    general_prompt = "知识库没有找到相关信息，请根据你的常识回答用户问题。回答要简洁、专业。"
                    stream_ai_to_queue(messages, system_prompt=general_prompt, sse_queue=sse_queue, max_tokens=4096)
            else:
                # FC 未返回结果（API 不支持或 LLM 未调用工具），直接搜索知识库
                sse_queue.put(('thinking', '📚 正在搜索知识库...'))
                search_context = self._search_knowledge_base(user_query)
                if search_context:
                    sse_queue.put(('thinking', f'📖 找到 {search_context["chunk_count"]} 个相关文档片段'))
                    answer_prompt = (
                        f"你是一个基于知识库的AI助手。请根据以下知识库内容回答用户问题。\n\n"
                        f"## 知识库相关上下文\n{search_context['context']}\n\n"
                        f"## 回答规范\n"
                        f"1. 必须给出确定性的结论\n"
                        f"2. 回答要简洁、专业、有条理\n"
                        f"3. 引用知识库的具体内容作为依据"
                    )
                    stream_ai_to_queue(messages, system_prompt=answer_prompt, sse_queue=sse_queue, max_tokens=4096)
                else:
                    stream_ai_to_queue(messages, system_prompt="请直接回答用户的问题。回答要简洁、专业。", sse_queue=sse_queue, max_tokens=4096)

            sse_queue.put(('done', '回答完成'))

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"知识库工具处理失败: {e}")
            sse_queue.put(('thinking', '知识库检索异常，转为通用对话模式'))
            general_messages = [{"role": "user", "content": user_query}]
            stream_ai_to_queue(messages=general_messages, system_prompt="你是一个友好的AI助手，请自然地回答用户。", sse_queue=sse_queue, max_tokens=4096)
            sse_queue.put(('done', '回答完成'))

    def _search_knowledge_base(self, query: str, max_chunks: int = 10):
        """搜索知识库，复用search_knowledge的底层逻辑"""
        try:
            from knowledge_api import get_vector_db
            from knowledge_models import get_knowledge_db, ContentChunk, KnowledgeFile, AILearningLog

            vector_db = get_vector_db()
            vector_results = vector_db.search_knowledge(
                query=query,
                category=None,
                n_results=max_chunks
            )

            if not vector_results:
                return None

            context_parts = []
            db = get_knowledge_db()
            with db.get_session() as session:
                for result in vector_results:
                    chunk_id = result['metadata'].get('chunk_id')
                    filename = result['metadata'].get('filename', '知识库文档')
                    chunk = session.query(ContentChunk).filter_by(id=chunk_id).first() if chunk_id else None
                    if chunk:
                        score = result['score']
                        source_doc = chunk.file.filename if chunk.file else filename
                        context_parts.append(
                            f"[文档: {source_doc}, "
                            f"相关度: {score:.2f}]\n{chunk.content_text}"
                        )
                    elif result['text']:
                        context_parts.append(
                            f"[文档: {filename}, "
                            f"相关度: {result['score']:.2f}]\n{result['text']}"
                        )

                if not context_parts:
                    return None

                context = "\n\n".join(context_parts)
                return {'context': context, 'chunk_count': len(context_parts)}

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"知识库搜索失败: {e}")
            return None

    def run(self, user_query, sse_queue, cancel_event, conversation_history=None):
        """
        Main entry point. Runs the full pipeline and puts SSE events into the queue.
        
        Args:
            user_query: Raw user query string
            sse_queue: Thread-safe queue for SSE events (tuple of event_type, data)
            cancel_event: threading.Event for cancellation
            conversation_history: Optional list of previous conversation turns
        """
        try:
            # Step 1: Understand intent (with context memory)
            sse_queue.put(('thinking', '🔍 正在解析查询意图...'))
            self.understand_intent(user_query, conversation_history)
            import logging
            logging.warning(f"[Agent Debug] intent解析结果: {json.dumps(self.intent, ensure_ascii=False)}")
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            # 检查模糊查询 - 需要返回澄清信息
            if self.intent.get("_is_vague"):
                clarification = self.context_memory.get_clarification_question(user_query)
                sse_queue.put(('answer', f'🤔 我需要确认一下：\n\n{clarification}'))
                sse_queue.put(('done', '请求确认'))
                return

            # ── 使用 Function Calling 让 LLM 决定：搜索知识库还是走 Jira 分析 ──
            if self.intent.get("query_type") == "general_question" or not self.intent.get("project"):
                sse_queue.put(('thinking', '📚 正在分析是否需要搜索知识库...'))
                self._handle_via_knowledge_tool(user_query, sse_queue, cancel_event)
                return

            # Step 2: Generate JQL
            sse_queue.put(('thinking', '📊 正在基于AI理解生成JQL查询...'))
            success = self.generate_jql()
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            if not success:
                sse_queue.put(('error', 'JQL生成失败，请重试'))
                return

            if self.jql_all:
                sse_queue.put(('jql', f"📋 生成的JQL（全量）: {self.jql_all}"))
            sse_queue.put(('jql', f"📋 生成的JQL（未解决）: {self.jql_unresolved}"))

            # Step 3: Fetch data
            sse_queue.put(('thinking', '📡 正在从Jira获取数据...'))
            self.fetch_data()
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            total_all = len(self.issues_all)
            total_unresolved = len(self.issues_unresolved)
            sse_queue.put(('thinking', f'📊 获取到 {total_all} 条问题（未解决 {total_unresolved} 条）'))
            sse_queue.put(('data', json.dumps({
                "total": total_all,
                "unresolved": total_unresolved,
                "project": self.intent.get("project"),
                "time_range": self.intent.get("time_range")
            }, ensure_ascii=False)))

            if total_unresolved == 0:
                answer_text = f'✅ 查询完成，共获取到 {total_all} 条问题，所有问题均已被解决，当前没有未解决的Bug。\n\n项目状态良好！'
                sse_queue.put(('answer', answer_text))
                # 生成看板数据（即使全部已解决也发送）
                kanban_data = self._generate_kanban_data()
                if kanban_data:
                    sse_queue.put(('kanban_data', json.dumps(kanban_data, ensure_ascii=False)))
                # 生成独立看板页面数据
                try:
                    page_issues = self._extract_kanban_page_data()
                    token = str(uuid.uuid4())
                    store_kanban_page_data(token, {
                        "issues": page_issues,
                        "project": self.intent.get("project", ""),
                        "jql_all": self.jql_all,
                        "jql_unresolved": self.jql_unresolved
                    })
                    sse_queue.put(('kanban_page_url', f'/kanban-page?token={token}'))
                except Exception as e:
                    logging.getLogger(__name__).warning(f"生成看板页面数据失败: {e}")
                sse_queue.put(('done', '分析完成'))
                self.context_memory.update_after_query(self.intent, answer_text)
                return

            # Step 4: Stream AI analysis
            sse_queue.put(('thinking', '🤖 专家正在深入分析数据...'))
            self.last_analysis = self.stream_analysis(sse_queue, user_query, conversation_history)
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            # 更新记忆（含分析摘要）
            if self.last_analysis:
                summary = self.last_analysis[:500]
                self.context_memory.update_after_query(self.intent, summary)

            # 生成看板数据并发送
            kanban_data = self._generate_kanban_data()
            if kanban_data:
                sse_queue.put(('kanban_data', json.dumps(kanban_data, ensure_ascii=False)))

            # 生成独立看板页面数据
            try:
                page_issues = self._extract_kanban_page_data()
                token = str(uuid.uuid4())
                store_kanban_page_data(token, {
                    "issues": page_issues,
                    "project": self.intent.get("project", ""),
                    "jql_all": self.jql_all,
                    "jql_unresolved": self.jql_unresolved
                })
                sse_queue.put(('kanban_page_url', f'/kanban-page?token={token}'))
            except Exception as e:
                logging.getLogger(__name__).warning(f"生成看板页面数据失败: {e}")

            sse_queue.put(('done', '分析完成'))

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            sse_queue.put(('error', f'分析过程出错: {str(e)}'))
            print(f"[RiskAgent] Error: {error_detail}")
