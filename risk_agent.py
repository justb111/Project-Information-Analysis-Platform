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
from domain_mapping import lookup_domain
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
# 从 jql_templates.json 提取项目名，用于本地精确匹配路由
# ---------------------------------------------------------------------------
_PROJECT_INDEX = None
_PROJECT_INDEX_LOCK = threading.Lock()

# 明确表达JIRA分析意图的关键词（命中任一 → HIGH confidence）
_ANALYSIS_KEYWORDS = [
    '风险', '阻塞', 'bug', 'Bug', 'BUG', '分析', '问题', '缺陷',
    '测试', '看板', 'kanban', '进度', '统计', '报告',
    'MP Block', 'mp block', '未解决', '未关闭', '新增',
    '解决', '关闭', '优先级', '测试建议', '质量',
]

def _load_project_index():
    """从 jql_templates.json 加载所有项目名，构建本地索引。

    项目名按长度降序排列（长名优先匹配），避免 tOS16 误匹配 tOS16.1。
    """
    global _PROJECT_INDEX
    if _PROJECT_INDEX is not None:
        return _PROJECT_INDEX

    with _PROJECT_INDEX_LOCK:
        if _PROJECT_INDEX is not None:
            return _PROJECT_INDEX

        template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
        project_names = set()
        try:
            with open(template_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            for tmpl in data.get("templates", []):
                for proj_name in tmpl.get("projects", {}):
                    if proj_name and proj_name not in ("所有项目", "整体项目"):
                        project_names.add(proj_name)
        except Exception:
            pass

        # 按长度降序，长名优先
        _PROJECT_INDEX = sorted(project_names, key=lambda x: (-len(x), x))
        return _PROJECT_INDEX


def _detect_projects_in_query(query):
    """在用户查询中检测已知项目名。

    Returns:
        list[str]: 匹配到的项目名列表（已去重，按在查询中出现顺序）
    """
    if not query:
        return []

    index = _load_project_index()
    if not index:
        return []

    matched = []
    matched_positions = set()

    for proj_name in index:
        # 在查询中查找项目名（全词匹配，不匹配子串的一部分）
        for m in re.finditer(re.escape(proj_name), query):
            pos = m.start()
            # 避免同一位置被多个项目匹配（长名已优先排在前面）
            if not any(abs(pos - p) < len(proj_name) for p in matched_positions):
                matched.append(proj_name)
                matched_positions.add(pos)
                break  # 一个项目只匹配一次

    return matched


def _has_analysis_intent(query):
    """检查查询中是否包含明确的分析类关键词"""
    if not query:
        return False
    return any(kw in query for kw in _ANALYSIS_KEYWORDS)


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
                # 只去除 summary ~ XXX 和日期限制（AI会按需添加），保留 type / creator / ORDER BY 等
                clean = re.sub(r'\s+AND\s+summary\s*~\s*"[^"]*"\s*', ' ', proj_jql, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+summary\s*~\s*\S+\s*', ' ', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+\s+AND\s+createdDate\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+created\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s{2,}', ' ', clean).strip()
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
                # 只去除 summary ~ XXX 和模板自带的日期限制（AI会按需添加），保留 type / creator / ORDER BY 等
                clean = re.sub(r'\s+AND\s+summary\s*~\s*"[^"]*"\s*', ' ', proj_jql, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+summary\s*~\s*\S+\s*', ' ', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+\s+AND\s+createdDate\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+created\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s+AND\s+createdDate\s*[><=]+\s*\S+', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'\s{2,}', ' ', clean).strip()
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
    jql = re.sub(r'labels\s*=\s*"[^"]*风险[^"]*"\s*AND\s+', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+labels\s*=\s*"[^"]*风险[^"]*"', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'labels\s*=\s*"[^"]*风险[^"]*"\s*', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'^\s*AND\s+', '', jql)
    jql = re.sub(r'\s+AND\s*$', '', jql)
    jql = re.sub(r'\s{2,}', ' ', jql)
    return jql.strip()


def _strip_summary_clause(jql, project_name):
    """去掉 LLM 添加的 summary ~ \"项目名\" 条件（项目已由模板子句覆盖）"""
    if not project_name:
        return jql
    # summary ~ "X6840" / summary ~ X6840 (无引号) 等
    jql = re.sub(r'\bsummary\s*~\s*"\s*' + re.escape(project_name) + r'\s*"\s*AND\s+', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\bsummary\s*~\s*' + re.escape(project_name) + r'\s*AND\s+', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+summary\s*~\s*"\s*' + re.escape(project_name) + r'\s*"', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'\s+AND\s+summary\s*~\s*' + re.escape(project_name) + r'\s*$', '', jql, flags=re.IGNORECASE)
    jql = re.sub(r'^\s*AND\s+', '', jql)
    jql = re.sub(r'\s+AND\s*$', '', jql)
    jql = re.sub(r'\s{2,}', ' ', jql)
    return jql.strip()


def _parse_time_condition(query):
    """从用户查询中提取时间过滤条件的 JQL 片段（规则降级用，不依赖 AI）"""
    if not query:
        return ""
    if re.search(r'本月|这个月', query):
        return "created >= startOfMonth()"
    if re.search(r'本周|这周', query):
        return "created >= startOfWeek()"
    if re.search(r'今日|今天', query):
        return "created >= startOfDay()"
    m = re.search(r'最近\s*(\d+)\s*天', query)
    if m:
        return f"created >= -{m.group(1)}d"
    m = re.search(r'近\s*(\d+)\s*天', query)
    if m:
        return f"created >= -{m.group(1)}d"
    if re.search(r'昨天|昨日', query):
        return "created >= -1d AND created < startOfDay()"
    if re.search(r'上周|上星期', query):
        return "created >= startOfWeek(-1w) AND created < startOfWeek()"
    if re.search(r'上个月|上月', query):
        return "created >= startOfMonth(-1M) AND created < startOfMonth()"
    return ""


def _generate_jql_fallback(project_name, template_clause, raw_query):
    """AI 不可用时，用规则解析生成 JQL"""
    jql_parts = []
    if template_clause:
        jql_parts.append(template_clause)
    elif project_name and project_name != "ALL":
        jql_parts.append(f'project = "{project_name}"')
    time_cond = _parse_time_condition(raw_query)
    if time_cond:
        jql_parts.append(time_cond)
    if not jql_parts:
        return ""
    return " AND ".join(jql_parts)


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
- "tOS16的项目风险" → portfolio（tOS16不带点号时泛指整个tOS16.x系列，属于项目风险分析）
- "分析tOS16的风险" → portfolio（同上）
- "tOS16的测试情况" → single_project
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
- tOS16.x 系列有三个独立Jira项目：tOS16.1、tOS16.2、tOS16.3；用户说"tOS16"（不带点号）泛指整个tOS16.x系列，视为项目风险分析
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

### Bug摘要格式识别知识（辅助JQL生成）
Jira中的Bug摘要通常遵循以下模板格式：
**【部门】【项目】【阶段】【模块】问题描述**
例如：【驱动组】【CN6】【SDV】【Camera】预览画面黑屏

其中：
- 【部门】= 负责团队（驱动组、系统组、测试组、算法组、硬件组等）
- 【项目】= 项目代号（CN6、X6898、LK7、X6840等）
- 【阶段】= 项目阶段（SDV、SIT、QTR、PVT、EVT、DVT等）
- 【模块】= 功能模块（Camera、Display、Audio等）

当用户按"模块"查询时，通常对应摘要中的【模块】部分或Jira Component字段。
当用户按"阶段"查询时，通常对应摘要中的【阶段】部分（如SDV、SIT等）。

### Must_Resolve字段知识
- Jira中通过 customfield_10000（Must_Resolve）标记是否为必解问题
- "MP Block" = 最高优先级必须解决的问题
- "Not MP Block" = 非必须解决
- 用户说"必解"、"MP Block"、"必须解决"时，对应 cf[10000] = "MP Block"

### JQL 书写规范
- project 条件：多个项目用 OR 连接
- project 值使用双引号
- 排序：除非用户要求，默认不加 ORDER BY

## 解决率/关闭率查询的说明
当用户在 raw_query 中问"解决率"、"关闭率"、"解决情况"、"关闭情况"时：
系统始终全量获取数据，AI会根据全量数据自动计算解决率和关闭情况，无需额外JQL条件。

## 多条件组合示例
如果 raw_query 中包含多个条件，用 AND 连接：
- "tOS16.3 优先级为 Blocker 且包含'音频'关键词的 bug"
  → project = "tOS16.3" AND priority = Blocker AND summary ~ "音频"

## 处理"建议"类查询的规则
如果 raw_query 中包含"建议"、"推荐"等词，且明确提到了项目名（如 CN6c、X6840 等），你必须先生成JQL获取该项目的数据，然后根据数据提供建议。不要因为出现了"建议"就直接当作一般问题跳过JQL生成。

请输出可直接执行的JQL语句。系统始终获取全量数据，AI后续会根据用户意图从全量数据中筛选和分析，所以你的JQL只需要反映用户明确的过滤条件（如项目、时间、优先级等），不需要加 resolution 条件。"""

ANALYSIS_SYSTEM_PROMPT = """你是一位资深项目质量经理（Project Quality Manager），负责对单一项目进行专业的风险评估和质量分析。你的报告风格严谨、结构化，直接服务于项目决策。

## ⚠️ 数据使用规则
1. **全量数据**：你收到的是该项目的完整全量数据（包含已解决和未解决），所有统计指标必须基于全量数据
2. **风险聚焦未解决**：风险判断和风险结论只聚焦于未解决的问题。已解决/已关闭的问题只用于计算质量指标
3. **严禁使用"样本"、"抽样"、"前N个"等暗示数据不完整的表述**

## 🔥 风险判断依据（重要性排序）
1. **Must_Resolve=MP Block**（🚫MP标记）：必须解决的最高优先级问题，最核心风险指标
2. **标签=阻塞测试**（🧱阻塞标记）：阻塞测试流程的直接证据
3. **Priority等级**：仅作为辅助参考。Priority=Blocker不等于阻塞测试

## 📋 工作流状态含义
- submitted=提交审核 | open=已开单 | in progress=修复中 | modifying=打回修改
- fixed=已修复未合入 | resolved=待owner审核 | verified=待测试验证 | closed=闭环
- reopened=验收打回 | abandoned=已打回非问题

## 📊 输出结构要求（必须严格遵循）
请按以下章节组织你的分析报告，每个章节必须有明确的小标题：

### 一、执行摘要
- 项目名称、数据范围、Bug总数、未解决数/解决率/闭环率
- **一句话风险定级**：🟢低风险 / 🟡中风险 / 🔴高风险 / 🔴🔴严重风险
- 核心结论（2-3句话概括项目当前状态）

### 二、未解决问题风险分析
- **关键风险条目**：🚫MP Block和🧱阻塞测试问题的详细分析（这是最重要的部分，放在最前面）
- **严重问题清单**：Blocker/Critical问题的逐条分析（Bug编号、摘要、状态、负责人、影响）
- **按模块/领域归类**：将上述问题按功能模块和业务领域归类

### 三、严重问题深度分析
- **根因归类**：将严重问题按根因分类（如：驱动问题、兼容性问题、性能问题、设计缺陷等）
- **模块分布**：哪些模块是重灾区（Camera、通信、显示、性能等）
- **业务领域分布**：哪些业务领域风险集中
- **影响范围评估**：对项目交付进度和质量的影响

### 四、模块与领域风险分布
- 列出各功能模块的Bug分布和未解决情况
- 标注高风险模块（未解决多/严重问题集中的模块）
- 跨模块共性问题识别

### 五、质量指标分析
- 闭环率、解决率、修复率
- 已解决/已关闭/待验证/已打回的数量分布
- 质量趋势判断

### 六、改进建议
- 针对高风险项的具体处理建议
- 针对系统性共性的改进措施
- 建议的优先级（P0/P1/P2）

## 🚫 输出禁令
1. **绝对禁止使用"可能"、"好像"、"或许"、"大概"、"无法确定"等模糊词汇**
2. **禁止输出原始数据表格**（不要用 `| --- |` 格式）
3. **所有结论必须用自然语言段落呈现**，融入具体数据
4. **结论必须有数据支持**（引用Bug数量、百分比、问题ID等）

请用中文输出，语言专业、简洁、有层次感。重点突出高风险项，让读者一眼看到核心问题所在。"""

PORTFOLIO_SYSTEM_PROMPT = """你是一位拥有20年以上经验的顶级软件项目群管理专家（Program Manager），负责对多项目/项目群进行专业风险评估。你的报告风格如同向CTO/VP汇报，严谨、结构化、决策导向。
## ⚠️ 数据使用规则
1. **全量数据**：你收到的是完整的全量数据（包含所有项目已解决和未解决的问题），所有统计基于全量计算
2. **风险聚焦未解决**：风险结论只聚焦于未解决的问题。已解决问题的数据用于计算闭环率/解决率等质量指标
3. **严禁使用"样本"、"抽样"、"前N个"等暗示数据不完整的表述**

## 🔥 风险判断依据（重要性排序）
1. **Must_Resolve=MP Block**（🚫MP标记）：必须解决的最高优先级问题
2. **标签=阻塞测试**（🧱阻塞标记）：阻塞测试流程的直接证据
3. **Priority等级**：Blocker/Critical/High/Major，作为辅助参考

## 📋 工作流状态含义
- submitted=提交审核 | open=已开单 | in progress=修复中 | modifying=打回修改
- fixed=已修复未合入 | resolved=待owner审核 | verified=待测试验证 | closed=闭环
- reopened=验收打回 | abandoned=已打回非问题

## 📊 输出结构要求（必须严格遵循）

### 一、项目群执行摘要
- 覆盖项目范围、Bug总数、未解决总数、整体解决率/闭环率
- **一句话风险定级**：🟢低风险 / 🟡中风险 / 🔴高风险 / 🔴🔴严重风险
- 项目级一览（每个项目一句话标注风险等级）

### 二、各项目风险详情（每个项目独立分析）
对每个项目按以下格式逐一分析：

**项目名称**
- 数据：Bug总数X | 未解决X | 解决率X% | 闭环率X%
- 高风险项（MP Block/阻塞测试/Blocker/Critical）
- 主要风险模块和领域
- 核心风险判断（一句话）
- 影响评估（对版本交付的影响）

### 三、跨项目共性问题
- 多个项目中同时存在的同类风险
- 按领域/模块归类，列出涉及的具体项目
- 判断是偶发问题还是系统性问题

### 四、严重问题深度分析
- **根因归类**：将各项目的严重问题按根因分类
- **模块分布**：哪些模块是跨项目的重灾区
- **业务领域分布**：哪些业务领域风险最集中
- **影响范围评估**：对项目群整体交付进度的影响

### 五、质量指标横向对比
- 各项目闭环率/解决率/修复率对比
- 突出表现异常的项目（闭环率显著偏低等）

### 六、改进建议
- 针对高风险项目的具体建议
- 针对系统性共性问题的改进措施
- 建议优先级和预期效果

## 🚫 输出禁令
1. **绝对禁止使用"可能"、"好像"、"或许"、"大概"、"无法确定"等模糊词汇**
2. **禁止输出原始Markdown表格**（不要用 `| --- |` 格式，数据融入文字描述）
3. **所有结论用自然语言段落呈现**
4. **结论必须有数据支持**（引用Bug数量、百分比、项目名称等）
5. **项目名称必须使用数据中的业务项目名**（如 CN6、X6898、LK7），不要用Jira键名

请用中文输出，语言专业、精炼、层次分明，让读者能快速定位核心风险和决策要点。"""


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
# filter_conditions 由 schema 严格约束
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


def _load_jira_rules():
    """加载Jira规则知识库JSON，返回格式化后的知识文本"""
    rules_file = os.path.join(os.path.dirname(__file__), 'jira_rules_knowledge.json')
    try:
        with open(rules_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return ""

    parts = []

    # 字段定义
    fd = data.get("field_definitions", {})
    parts.append("## Jira字段知识")
    for cid, info in fd.items():
        name = info.get("name", cid)
        desc = info.get("description", "")
        parts.append(f"- {name} ({cid}): {desc}")
        if "values" in info:
            parts.append(f"  可选值: {', '.join(info['values'])}")
        if "risk_mapping" in info:
            for k, v in info["risk_mapping"].items():
                parts.append(f"  - {k}: {v}")

    # 工作流状态
    ws = data.get("workflow_states", {})
    parts.append("\n## 工作流状态风险含义")
    for state, info in ws.items():
        parts.append(f"- {state} → {info.get('category', '未知')} (风险相关性: {info.get('risk_relevance', '未知')})")

    # 摘要模板
    bst = data.get("bug_summary_template", {})
    parts.append(f"\n## Bug摘要模板格式")
    parts.append(f"标准格式: {bst.get('format', '')}")
    parts.append(f"示例: {bst.get('example', '')}")
    fe = bst.get("fields_explanation", {})
    for k, v in fe.items():
        parts.append(f"- 【{k}】: {v}")

    # 模块-领域映射（精简）
    mdm = data.get("module_domain_mapping", {})
    parts.append(f"\n## 模块→领域映射（仅列出常见映射）")
    domain_map = {}
    for mod, domain in mdm.items():
        if mod == "_note":
            continue
        if domain not in domain_map:
            domain_map[domain] = []
        if len(domain_map[domain]) < 5:
            domain_map[domain].append(mod)
    for domain, mods in sorted(domain_map.items()):
        parts.append(f"- {domain}: {', '.join(mods)}")

    return "\n".join(parts)


def _call_ai_silent(messages, system_prompt, max_tokens=None, temperature=0.7):
    """
    静默调用AI——不流式输出，只返回完整响应文本。
    用于分批分析中的子批次提取，避免中间结果污染前端answer区域。
    """
    response = call_ai_api(
        messages=messages,
        system_prompt=system_prompt,
        temperature=temperature,
        stream=False,
        max_retries=2,
        retry_delay=3,
        max_tokens=max_tokens,
        timeout=120
    )
    if response and response.status_code == 200:
        try:
            data = response.json()
            choices = data.get('choices', [])
            if choices:
                return choices[0].get('message', {}).get('content', '')
        except Exception:
            return None
    return None


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
        self.issues_all = []
        self.issues_unresolved = []
        self.last_analysis = None
        self.context_memory = context_memory or ContextMemory()
        self._ai_disabled = False  # AI服务是否不可用（降级标志）

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
        ai_available = True
        if result:
            query_mode = result.get("query_mode", "UNRESOLVED")
            llm_jql = result.get("filter_conditions", "")
        else:
            # Fallback：API 不支持 function calling 时回退到文本解析
            llm_output = _call_llm_structured(messages, FILTER_ONLY_SYSTEM, temperature=0.2)
            if llm_output:
                lines = [l.strip() for l in llm_output.strip().split('\n') if l.strip()]
                if len(lines) >= 2:
                    query_mode = lines[0].upper()
                    llm_jql = lines[1]
                else:
                    query_mode = "UNRESOLVED"
                    llm_jql = lines[0] if lines else ""
            else:
                # AI 不可用 → 降级到规则解析
                _log("warning", "AI服务不可用（API返回非200状态码），降级到规则解析生成JQL")
                ai_available = False
                self._ai_disabled = True
                llm_jql = _generate_jql_fallback(project_name, template_clause, raw_query)
                if not llm_jql:
                    return False
                query_mode = "UNRESOLVED"

        # ── 后处理：去掉 LLM 可能乱加的 project = "X" 或 labels = "风险" 或 summary ~ "项目名" ──
        if ai_available:
            llm_jql = _strip_project_clause(llm_jql)
            llm_jql = _strip_risk_label(llm_jql)
            llm_jql = _strip_summary_clause(llm_jql, project_name)

        # ── 拼接：强制使用模板 project 子句 ──
        if template_clause:
            if llm_jql and ai_available:
                jql = f"{template_clause} AND {llm_jql}"
            elif llm_jql and not ai_available:
                # 规则降级已包含完整JQL（含project子句），直接用
                jql = llm_jql
            else:
                jql = template_clause
        else:
            jql = llm_jql if llm_jql else ""

        # ── 二次清理：防止任何 "AND ALL AND" 残留 ──
        jql = re.sub(r'\s+AND\s+ALL(\s+AND\s+)?', ' AND ', jql, flags=re.IGNORECASE).strip()
        jql = re.sub(r'\s+AND\s+$', '', jql).strip()

        # ── 双 JQL 模式：始终生成全量和未解决两种 JQL ──
        # 全量数据（无 resolution 过滤）用于全局统计，未解决子集用于风险判断
        self.jql_all = jql

        return True

    def fetch_data(self):
        """Step 3: Fetch Jira data using existing infrastructure"""
        max_fetch = None  # 无限制，获取全量数据
        if self.jql_all:
            self.issues_all = fetch_all_issues(self.jql_all, max_fetch=max_fetch)
        self.issues_unresolved = self._filter_unresolved_by_status(self.issues_all)

        total = len(self.issues_all) if self.issues_all else 0
        _log("info", f"全量获取完成: 共 {total} 条问题")
        self._large_dataset = total > 3000

    @staticmethod
    def _filter_unresolved_by_status(issues=None):
        """按状态机判定未解决问题：只有 abandoned 和 closed 算已解决，其他状态都算未解决"""
        resolved_statuses = {"abandoned", "closed"}
        if issues is None:
            return []
        return [i for i in issues
                if i.get('fields', {}).get('status', {}).get('name', '').lower() not in resolved_statuses]

    def _batch_analyze(self, issues, enhanced_query, sse_queue, system_prompt):
        """
        分批分析大规模数据（>3000条）。

        流程：
        1. 按优先级排序后分 BATCH_SIZE=2000 条一批
        2. 每批静默调用AI提取关键风险条目（不流式输出到前端）
        3. 每批完成后发送 thinking 事件告知进度
        4. 所有批次提取完毕后，将全量统计 + 各批条目输入AI做最终汇总（流式输出为answer）

        Returns:
            str: 最终综合分析报告
        """
        BATCH_SIZE = 2000
        total = len(issues)
        total_batches = max(1, (total + BATCH_SIZE - 1) // BATCH_SIZE)

        sse_queue.put(('thinking', f'📊 数据量较大（共{total}条），将分{total_batches}批提取关键风险条目... '))

        priority_sorted = sorted(
            issues,
            key=lambda x: {
                "Block": 0, "阻塞": 0, " blocker": 0,
                "Critical": 1, "紧急": 1, " critical": 1,
                "High": 2, "高": 2, "Major": 2, " major": 2,
            }.get(x.get('fields', {}).get('priority', {}).get('name', '').lower(), 99)
        )

        batch_extractions = []
        for batch_idx in range(0, total, BATCH_SIZE):
            batch = priority_sorted[batch_idx:batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1

            sse_queue.put(('thinking', f'⏳ 正在提取第 {batch_num}/{total_batches} 批的关键风险条目... '))

            jira_data = format_portfolio_data(batch, max_detail=80)

            batch_prompt = (
                f"用户问题：{enhanced_query}\n\n"
                f"### 第{batch_num}/{total_batches}批（全部共{total}条）\n"
                f"这是全量数据按优先级排序后的第{batch_idx+1}-{min(batch_idx+BATCH_SIZE, total)}条。\n\n"
                f"请从这批数据中提取需要关注的具体风险条目，注意：\n"
                f"1. 只列举你在这批数据中观察到的具体异常条目（如某条Block级别的未解决问题）\n"
                f"2. 不要做汇总性分析，不要评价整体情况\n"
                f"3. 不要使用'本批次'、'本批'等字眼\n\n"
                f"真实Jira数据：\n{jira_data}"
            )

            batch_system = (
                "你是一个Jira风险分析助手，当前正在处理全量数据中的一个子集。\n"
                "请只做以下事情：\n"
                "1. 列出需要关注的具体风险条目（Block/Critical的未解决问题、MP Block标记、阻塞测试标签等）\n"
                "2. 如果发现本子集中有特别的异常模式（如某个模块集中出现阻塞问题），简要说明\n"
                "3. 不要输出汇总统计——统计信息会在最终阶段统一处理\n"
                "输出要简洁，每行一条。"
            )

            # 静默调用AI——不推送到SSE队列，只获取返回文本
            content = _call_ai_silent(
                messages=[{"role": "user", "content": batch_prompt}],
                system_prompt=batch_system,
                max_tokens=4096
            )
            batch_extractions.append(content or "")

            sse_queue.put(('thinking', f'✅ 第{batch_num}/{total_batches}批提取完成'))

        # ── 最终汇总：全量统计 + 各批关键条目 → AI流式输出 ──
        sse_queue.put(('thinking', f'🔄 正在对全量{total}条数据做最终综合分析... '))

        # 全量数据统计（format_portfolio_data 输出完整统计+严重问题列表）
        full_stats = format_portfolio_data(priority_sorted, max_detail=150)

        # 所有批次的关键条目汇总
        extractions_text = ""
        for i, s in enumerate(batch_extractions):
            extractions_text += f"\n--- 第{i+1}批关键条目 ---\n{s}\n"

        merge_prompt = (
            f"用户问题：{enhanced_query}\n\n"
            f"以下是全量{total}条Jira数据的完整统计以及各批次提取的关键风险条目。\n"
            f"请基于这些信息生成一份专业的综合风险评估报告。\n\n"
            f"【全量数据统计】\n{full_stats}\n\n"
            f"【各批次关键风险条目】\n{extractions_text}\n\n"
            f"要求：\n"
            f"1. 基于全量统计做定量分析（闭环率、解决率、阻塞分布等）\n"
            f"2. 结合各批次提取的关键条目做定性分析（具体风险项）\n"
            f"3. 输出综合性结论，不要提及'批次'概念"
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
        # AI不可用时跳过分析（_run_jira_pipeline 会触发降级摘要）
        if self._ai_disabled:
            return None

        is_portfolio = self.intent and self.intent.get("project") == "ALL"
        system_prompt = PORTFOLIO_SYSTEM_PROMPT if is_portfolio else ANALYSIS_SYSTEM_PROMPT

        # 注入Jira规则知识
        jira_rules_text = _load_jira_rules()
        if jira_rules_text:
            system_prompt = f"{system_prompt}\n\n## Jira规则知识（用于辅助分析）\n{jira_rules_text}"

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

        # 全量数据传给AI做统计分类（提示词已约束风险结论聚焦未解决）
        issues = self.issues_all or []
        total = len(issues)

        # 超大数据集（>3000条）→ 分批提取+最终汇总
        if total > 3000:
            return self._batch_analyze(issues, enhanced_query, sse_queue, system_prompt)

        priority_sorted = sorted(
            issues,
            key=lambda x: {
                "Block": 0, "阻塞": 0, " blocker": 0,
                "Critical": 1, "紧急": 1, " critical": 1,
                "High": 2, "高": 2, "Major": 2, " major": 2,
            }.get(x.get('fields', {}).get('priority', {}).get('name', '').lower(), 99)
        )

        jira_data = format_portfolio_data(priority_sorted, max_detail=80)
        messages = [{"role": "user", "content": f"用户问题：{enhanced_query}\n\n真实Jira数据：{jira_data}"}]

        full_content = stream_ai_to_queue(
            messages=messages,
            system_prompt=system_prompt,
            sse_queue=sse_queue,
            max_tokens=16384
        )
        return full_content

    def _generate_fallback_analysis(self, sse_queue):
        """AI 不可用时，基于已获取的 Jira 数据生成基础统计摘要"""
        if not self.issues_all:
            sse_queue.put(('answer', '⚠️ AI服务暂时不可用，且无Jira数据可展示。'))
            return

        issues = self.issues_all
        total = len(issues)

        # 按状态统计
        status_counts = {}
        # 按优先级统计
        priority_counts = {}
        # 按类型统计
        type_counts = {}
        # 按解决状态统计
        resolved_count = 0
        unresolved_count = 0

        for issue in issues:
            fields = issue.get('fields', issue)
            # 状态
            raw_status = fields.get('status', {})
            status_name = raw_status.get('name', '未知') if isinstance(raw_status, dict) else str(raw_status)
            status_counts[status_name] = status_counts.get(status_name, 0) + 1

            # 优先级
            raw_priority = fields.get('priority', {})
            priority_name = raw_priority.get('name', '未知') if isinstance(raw_priority, dict) else str(raw_priority)
            priority_counts[priority_name] = priority_counts.get(priority_name, 0) + 1

            # 类型
            raw_type = fields.get('issuetype', {})
            type_name = raw_type.get('name', '未知') if isinstance(raw_type, dict) else str(raw_type)
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

            # 解决状态
            if any(s in status_name for s in ['Resolved', 'Closed', 'Fixed', '已解决', '关闭']):
                resolved_count += 1
            else:
                unresolved_count += 1

        # 构建摘要报告
        status_summary = ', '.join(f'{k}: {v}个' for k, v in sorted(status_counts.items(), key=lambda x: -x[1]))
        priority_summary = ', '.join(f'{k}: {v}个' for k, v in sorted(priority_counts.items(), key=lambda x: -x[1]))
        type_summary = ', '.join(f'{k}: {v}个' for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
        close_rate = round(resolved_count / total * 100, 1) if total > 0 else 0

        project_name = self.intent.get("project", "") if self.intent else ""
        report = (
            f"⚠️ AI分析服务暂时不可用，以下为基础数据统计（共{total}条问题）：\n\n"
            f"**📊 项目 {project_name} 数据概览**\n\n"
            f"**闭环情况**：已解决 {resolved_count} 条（{close_rate}%），未解决 {unresolved_count} 条\n\n"
            f"**按状态分布**：{status_summary}\n\n"
            f"**按优先级分布**：{priority_summary}\n\n"
            f"**按类型分布**：{type_summary}\n\n"
            f"> 🔔 请检查AI服务配置（AI_BASE_URL/AI_API_KEY），恢复后即可获得深度AI分析报告。"
        )

        sse_queue.put(('answer', report))

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
            customfield_10002 = fields.get('customfield_10002', '')  # Business Domain

            # Business_Domain
            business_domain = ''
            if isinstance(customfield_10002, str):
                business_domain = customfield_10002.strip()
            elif isinstance(customfield_10002, dict):
                business_domain = customfield_10002.get('value', '') or customfield_10002.get('name', '')
            if not business_domain and comp_str:
                business_domain = lookup_domain(comp_str)

            # ResolutionDate
            resolution_date = fields.get('resolutiondate', '')

            # Must_Resolve - try to extract from custom field or labels
            must_resolve = ''
            if isinstance(customfield_10000, str) and 'MP Block' in customfield_10000:
                must_resolve = 'MP Block'
            elif isinstance(customfield_10000, dict) and 'MP Block' in customfield_10000.get('value', ''):
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
                "Labels": labels,
                "Business_Domain": business_domain,
                "ResolutionDate": resolution_date
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


    def _handle_clarification_reply(self, user_query, sse_queue, cancel_event):
        """处理用户对澄清问题的回复。匹配选项后执行对应操作。"""
        pending = self.context_memory.get_clarification()
        if not pending:
            return False

        # Check if user's input contains an option label or id
        options = pending.get("options", [])
        matched_option = None
        for opt in options:
            if opt["id"] in user_query or opt.get("label", "") in user_query:
                matched_option = opt
                break

        if not matched_option:
            # User didn't match any option — clear clarification and treat as new query
            self.context_memory.resolve_clarification("")
            return False

        # Resolve the clarification in memory
        self.context_memory.resolve_clarification(matched_option["id"])

        # Execute based on option type
        opt_type = matched_option.get("type", "jira")
        if opt_type == "jira":
            project_name = matched_option.get("project", "")
            sse_queue.put(('thinking', f'📊 开始分析项目「{project_name}」的风险...'))
            self.intent = {
                "project": project_name,
                "time_range": None,
                "query_type": "single_project",
                "raw_query": user_query,
                "_route": "clarification_choice",
            }
            self.context_memory.update_after_query(self.intent)
            self._run_jira_pipeline(user_query, sse_queue, cancel_event, None)
            return True

        elif opt_type == "knowledge_base":
            sse_queue.put(('thinking', '📚 搜索知识库...'))
            self._handle_via_knowledge_tool(user_query, sse_queue, cancel_event)
            return True

        elif opt_type == "general":
            sse_queue.put(('answer', matched_option.get("response", "好的，请继续。")))
            sse_queue.put(('done', '回答完成'))
            return True

        return False

    def _ask_clarify_projects(self, matched_projects, user_query, sse_queue):
        """检测到多个项目名时，向用户发送澄清选项。"""
        options = []
        for proj in matched_projects:
            # Check if it maps to a JIRA project
            options.append({
                "id": f"project_{proj}",
                "label": f"分析项目「{proj}」的风险",
                "type": "jira",
                "project": proj,
            })

        # Also offer general chat option
        options.append({
            "id": "general_chat",
            "label": "都不是，我想问其他问题",
            "type": "general",
            "response": "好的，请告诉我你想了解什么？",
        })

        # Build the clarify message
        project_list = "、".join(matched_projects)
        reason = f"检测到多个项目名：{project_list}。请选择你要分析的项目，或者选择「其他问题」。"

        # Store clarification state
        self.context_memory.set_clarification(reason, options)

        # Send to frontend
        sse_queue.put(('clarify', reason))
        sse_queue.put(('clarify_options', options))
        sse_queue.put(('answer', f'🤔 我检测到多个项目名（{project_list}），请告诉我你要分析哪个项目？'))
        sse_queue.put(('done', '请求确认'))

    def _run_jira_pipeline(self, user_query, sse_queue, cancel_event, conversation_history):
        """Jira 分析流水线：生成JQL → 获取数据 → AI分析。从 run() 中提取的公共逻辑。"""
        # Step 2: Generate JQL
        sse_queue.put(('thinking', '📊 正在基于AI理解生成JQL查询...'))
        success = self.generate_jql()
        if cancel_event and cancel_event.is_set():
            sse_queue.put(('done', '分析已取消'))
            return

        if not success:
            sse_queue.put(('error', 'JQL生成失败，请重试'))
            return

        if self._ai_disabled:
            sse_queue.put(('thinking', '⚠️ AI服务暂不可用，已切换到规则模式生成JQL'))
        if self.jql_all:
            sse_queue.put(('jql', f"📋 生成的JQL: {self.jql_all}"))

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
            kanban_data = self._generate_kanban_data()
            if kanban_data:
                sse_queue.put(('kanban_data', json.dumps(kanban_data, ensure_ascii=False)))
            try:
                page_issues = self._extract_kanban_page_data()
                token = str(uuid.uuid4())
                store_kanban_page_data(token, {
                    "issues": page_issues,
                    "project": self.intent.get("project", ""),
                    "jql_all": self.jql_all
                })
                sse_queue.put(('kanban_page_url', f'/kanban-page?token={token}'))
            except Exception as e:
                logging.getLogger(__name__).warning(f"生成看板页面数据失败: {e}")
            sse_queue.put(('done', '分析完成'))
            return

        # Step 4: Stream AI analysis（或降级摘要）
        if self._ai_disabled:
            sse_queue.put(('thinking', '📊 AI暂不可用，生成基础数据统计...'))
        else:
            sse_queue.put(('thinking', '🤖 专家正在深入分析数据...'))
        self.last_analysis = self.stream_analysis(sse_queue, user_query, conversation_history)
        if cancel_event and cancel_event.is_set():
            sse_queue.put(('done', '分析已取消'))
            return

        if self.last_analysis:
            summary = self.last_analysis[:500]
            self.context_memory.update_after_query(self.intent, summary)
        else:
            # AI 分析失败时降级：生成基础统计摘要
            self._generate_fallback_analysis(sse_queue)

        kanban_data = self._generate_kanban_data()
        if kanban_data:
            sse_queue.put(('kanban_data', json.dumps(kanban_data, ensure_ascii=False)))

        try:
            page_issues = self._extract_kanban_page_data()
            token = str(uuid.uuid4())
            store_kanban_page_data(token, {
                "issues": page_issues,
                "project": self.intent.get("project", ""),
                "jql_all": self.jql_all
            })
            sse_queue.put(('kanban_page_url', f'/kanban-page?token={token}'))
        except Exception as e:
            logging.getLogger(__name__).warning(f"生成看板页面数据失败: {e}")

        sse_queue.put(('done', '分析完成'))


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
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            # Step 0: Handle reply to a previous clarification
            if self.context_memory.has_pending_clarification():
                handled = self._handle_clarification_reply(user_query, sse_queue, cancel_event)
                if handled:
                    return

            # Pre-routing: detect known project names in query
            matched_projects = _detect_projects_in_query(user_query)
            has_analysis_kw = _has_analysis_intent(user_query)

            # Multiple projects found -> ask user to clarify
            if len(matched_projects) > 1:
                self._ask_clarify_projects(matched_projects, user_query, sse_queue)
                return

            # Single project + analysis keywords -> HIGH confidence, skip LLM intent guess
            if len(matched_projects) == 1 and has_analysis_kw:
                project_name = matched_projects[0]
                sse_queue.put(('thinking', f'📊 检测到项目「{project_name}」，开始风险分析...'))
                self.intent = {
                    "project": project_name,
                    "time_range": None,
                    "query_type": "single_project",
                    "raw_query": user_query,
                    "_route": "project_detected",
                }
                self.context_memory.update_after_query(self.intent)
                self._run_jira_pipeline(user_query, sse_queue, cancel_event, conversation_history)
                return

            # No match / single project without keywords -> use LLM via understand_intent
            sse_queue.put(('thinking', '🔍 正在解析查询意图...'))
            self.understand_intent(user_query, conversation_history)
            import logging
            logging.warning(f"[Agent Debug] intent解析结果: {json.dumps(self.intent, ensure_ascii=False)}")
            if cancel_event and cancel_event.is_set():
                sse_queue.put(('done', '分析已取消'))
                return

            # Check vague query
            if self.intent.get("_is_vague"):
                clarification = self.context_memory.get_clarification_question(user_query)
                sse_queue.put(('answer', f'🤔 我需要确认一下：\n\n{clarification}'))
                sse_queue.put(('done', '请求确认'))
                return

            # LLM routing decision
            if self.intent.get("query_type") == "general_question" or not self.intent.get("project"):
                # If we detected a project name, override LLM's wrong classification
                if len(matched_projects) == 1:
                    project_name = matched_projects[0]
                    sse_queue.put(('thinking', f'📊 检测到项目「{project_name}」，按Jira风险分析处理...'))
                    self.intent["project"] = project_name
                    self.intent["query_type"] = "single_project"
                    self.context_memory.update_after_query(self.intent)
                else:
                    sse_queue.put(('thinking', '📚 正在分析是否需要搜索知识库...'))
                    self._handle_via_knowledge_tool(user_query, sse_queue, cancel_event)
                    return

            # Jira analysis pipeline
            self._run_jira_pipeline(user_query, sse_queue, cancel_event, conversation_history)

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            sse_queue.put(('error', f'分析过程出错: {str(e)}'))
            print(f"[RiskAgent] Error: {error_detail}")
