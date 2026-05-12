import sys
import os
import io

# 解决Windows控制台编码问题
if sys.platform == "win32":
    # 设置标准输出编码为UTF-8
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    # 设置默认编码
    import locale
    if locale.getpreferredencoding().lower() != 'utf-8':
        os.environ['PYTHONIOENCODING'] = 'utf-8'

import requests
import json
import base64
import urllib3
from urllib.parse import quote
import argparse
import re
from collections import defaultdict
import httpx
import openai
import traceback
from datetime import datetime, timedelta

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 数据库相关导入
import sqlite3
try:
    from sqlalchemy import create_engine, Column, String, DateTime, Boolean, Text, Integer
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker, scoped_session
except ImportError:
    create_engine = Column = String = DateTime = Boolean = Text = Integer = None
    declarative_base = None
    sessionmaker = None
    scoped_session = None
import threading

# 导入工具函数
from utils import call_ai_api, parse_thinking_answer, process_sse_stream, generate_sse_message, send_thinking_chars, send_answer_chars
from domain_mapping import lookup_domain

def get_friendly_ai_error():
    """获取用户友好的AI错误消息"""
    import time
    # 根据时间戳提供不同的友好提示，避免单调
    options = [
        "AI服务暂时不可用，请稍后重试",
        "网络连接不稳定，请检查网络后重试",
        "AI服务繁忙，请稍后再试",
        "服务器响应超时，请稍后重试",
        "服务暂时不可用，请联系管理员"
    ]
    # 使用时间戳选择选项，实现伪随机
    index = int(time.time() * 1000) % len(options)
    return options[index]

# 让 Windows PowerShell 下中文输出尽量正常
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# 加载 .env 文件（如果存在）
def load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        try:
            loaded_vars = []
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # 简单解析 key=value
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # 移除可选的引号
                        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        os.environ[key] = value
                        loaded_vars.append(key)
            print(f"✅ 已加载环境变量文件: {env_path}")
            print(f"✅ 加载的变量: {', '.join(loaded_vars)}")
            # 调试输出关键变量
            for var in ['AI_API_KEY', 'X_USER_NO', 'X_USER_NAME', 'X_USER_DEPT_NAME']:
                val = os.getenv(var)
                if val:
                    print(f"✅ {var}: {'*' * min(10, len(val))}... (长度: {len(val)})")
                else:
                    print(f"❌ {var}: 未设置")
        except Exception as e:
            print(f"⚠️  加载 .env 文件失败: {e}")

# 在读取配置前加载 .env 文件
load_env_file()

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
    # 从输入中提取项目键，支持XX、X6840、CN6、CN6C、tOS16.1、tOS16.2、tOS16.3等格式
    # 优先匹配带点号的版本号（如tOS16.3）
    # 模式：1. 字母+数字+可选点号和数字（如tOS16.3）2. X+4位数字+可选后缀 3. 纯字母项目键（如XX）
    match = re.search(r'[A-Za-z]+\d+(?:\.\d+)?|X?\d{4}(?:-[a-zA-Z0-9]+)?|[A-Z]{2,}', input_str)
    if match:
        extracted = match.group()
        # 如果提取到的是纯字母且长度大于1，返回
        if re.match(r'^[A-Z]{2,}$', extracted):
            return extracted
        return extracted
    return input_str

# 中文日期格式规范化
def _normalize_chinese_date(text):
    """将中文日期描述（如'4月11到5月20'）转换为ISO格式（如'2026-04-11 到 2026-05-20'）"""
    now = datetime.now()
    current_year = now.year

    # 处理年份前缀
    year = current_year
    if '去年' in text:
        year = current_year - 1
        text = text.replace('去年', '')
    elif '今年' in text:
        text = text.replace('今年', '')

    # 先匹配日期范围：X月Y日到X月Y日 / X月Y号到X月Y号 / X月Y到X月Y
    range_pattern = re.search(
        r'(?:(\d{1,2})月(\d{1,2})[日号]?)\s*[到至\-]\s*(?:(\d{1,2})月(\d{1,2})[日号]?)',
        text
    )
    if range_pattern:
        start_month, start_day, end_month, end_day = range_pattern.groups()
        start = f"{year}-{int(start_month):02d}-{int(start_day):02d}"
        end = f"{year}-{int(end_month):02d}-{int(end_day):02d}"
        return f"{start} 到 {end}"

    # 匹配完整日期：2026年4月11日 / 2026年4月11
    full_date = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]?', text)
    if full_date:
        y, m, d = full_date.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"

    # 匹配单日：X月Y日 / X月Y号 / X月Y
    single_date = re.search(r'(\d{1,2})月(\d{1,2})[日号]?', text)
    if single_date:
        month, day = single_date.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


# 意图识别函数
def recognize_intent(user_query):
    """识别用户意图"""
    intent = {
        "project": None,
        "time_range": "本周",   # 根据业务调整默认值
        "query_type": "bug总量",
        "department": None,      # 部门
        "domain": None           # 业务领域
    }

    # 提取项目键 - 支持带点号的版本号，如tOS16.3，以及纯字母项目键如XX
    project_match = re.search(r'[A-Za-z]+\d+(?:\.\d+)?|X?\d{4}(?:-[a-zA-Z0-9]+)?|[A-Z]{2,}', user_query)
    if project_match:
        intent["project"] = project_match.group()

    # 特殊处理：tOS系统风险 - 只在没有提取到项目键且查询是通用tOS查询时应用
    if not intent["project"] and "tOS" in user_query:
        # 检查是否包含具体版本号（tOS16.1, tOS16.2, tOS16.3等）
        has_specific_version = re.search(r'tOS\d+(?:\.\d+)?', user_query, re.IGNORECASE)
        if not has_specific_version:
            # 通用tOS查询，设置为tOS16
            intent["project"] = "tOS16"

    # 提取时间范围
    if "整体" in user_query or "全部" in user_query or "所有" in user_query or "总体" in user_query:
        intent["time_range"] = "全部"
    elif "今天" in user_query or "今日" in user_query:
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
    else:
        # 尝试中文日期格式识别：4月11、4月11日、4月11到5月20、2026年4月11日等
        normalized = _normalize_chinese_date(user_query)
        if normalized:
            intent["time_range"] = normalized
        else:
            # 如果没有提到时间相关的词语，默认使用"本周"
            intent["time_range"] = "本周"

    # 提取部门
    department_patterns = [
        r'交付测试部',
        r'交付一部', r'交付二部', r'交付三部', r'交付四部', r'交付五部',
        r'研发测试部',
        r'研发一部', r'研发二部', r'研发三部',
        r'系统部', r'产品部', r'测试部',
        r'性能测试', r'功能测试', r'安全测试', r'自动化测试'
    ]
    for pattern in department_patterns:
        if pattern in user_query:
            intent["department"] = pattern
            break

    # 提取业务领域
    domain_patterns = [
        r'系统产品', r'性能测试', r'功能测试', r'安全测试',
        r'充电', r'电池', r'网络', r'相机', r'UI', r'系统'
    ]
    for pattern in domain_patterns:
        if pattern in user_query and not intent["domain"]:
            intent["domain"] = pattern

    # 提取查询类型
    if "MP block" in user_query or "MP BLOCK" in user_query or "MP Block" in user_query:
        intent["query_type"] = "MP BLOCK问题"
    elif "交付测试" in user_query:
        intent["query_type"] = "交付测试部bug"
    elif "研发测试" in user_query:
        intent["query_type"] = "研发测试部bug"
    elif "bug" in user_query or "Bug" in user_query or "风险" in user_query or "排行" in user_query or "分析" in user_query:
        intent["query_type"] = "bug总量"

    return intent

# 匹配 JQL 模板
def match_jql_template(intent):
    """根据用户意图匹配 JQL 模板"""
    project = intent.get("project")
    query_type = intent.get("query_type")
    department = intent.get("department")

    # 调试信息
    import sys
    print(f"[DEBUG match_jql_template] 输入intent: {intent}", file=sys.stderr)
    print(f"[DEBUG match_jql_template] project={repr(project)}, query_type={repr(query_type)}, department={repr(department)}", file=sys.stderr)

    # 首先，优先匹配包含部门的模板，并且项目精确匹配
    if department:
        for template in JQL_TEMPLATES:
            template_name = template.get("name", "").lower()
            # 检查查询类型和部门是否都匹配（忽略大小写）
            # 灵活的查询类型匹配
            query_matched_dept = False
            if query_type and query_type.lower() in template_name:
                query_matched_dept = True
            elif "bug" in query_type.lower() and "bug" in template_name:
                query_matched_dept = True
            elif "block" in query_type.lower() and "block" in template_name:
                query_matched_dept = True
            elif "测试" in query_type.lower() and "测试" in template_name:
                query_matched_dept = True

            if query_matched_dept and department.lower() in template_name:
                # 检查项目是否在模板的适用列表中 - 优先精确匹配
                if project and project in template.get("projects", {}):
                    return template
                # 如果模板的 projects 是字典且不为空，且项目不在其中，继续下一个模板
                elif isinstance(template.get("projects"), dict) and template.get("projects"):
                    continue
                # 否则，这个模板适用于所有项目
                else:
                    return template

    # 如果没有匹配到包含部门的模板，再尝试匹配仅查询类型的模板
    # 优先匹配项目精确匹配的模板
    exact_match_templates = []
    generic_match_templates = []

    for template in JQL_TEMPLATES:
        # 检查查询类型是否匹配模板名称（忽略大小写）
        template_name = template.get("name", "").lower()
        # 更灵活的匹配逻辑：检查是否有共同关键词或部分匹配
        query_matched = False
        # 优先精确匹配：查询类型完全包含在模板名称中
        if query_type and query_type.lower() in template_name:
            query_matched = True
        # 特殊处理：如果查询类型是"bug总量"，优先匹配名称中包含"bug总量"的模板
        elif query_type and query_type.lower() == "bug总量" and "bug总量" in template_name:
            query_matched = True
        # 然后是通用匹配：查询类型和模板名称都包含"bug"
        elif "bug" in query_type.lower() and "bug" in template_name:
            query_matched = True
        elif "block" in query_type.lower() and "block" in template_name:
            query_matched = True
        elif "测试" in query_type.lower() and "测试" in template_name:
            query_matched = True

        if query_matched:
            # 检查项目是否在模板的适用列表中
            if project and project in template.get("projects", {}):
                # 项目精确匹配，优先考虑
                exact_match_templates.append(template)
            # 如果模板的 projects 是字典且不为空，且项目不在其中，可能匹配通用键
            elif isinstance(template.get("projects"), dict) and template.get("projects"):
                # 检查是否有通用键匹配（如tOS16匹配tOS16.3）
                # 项目键可能以通用键开头，例如tOS16.3以tOS16开头
                # 但对于带点号的版本号，我们不应该匹配通用键，应该精确匹配
                if project:
                    # 如果项目键包含点号（如tOS16.3），我们不匹配通用键
                    if '.' in project:
                        # 只检查精确匹配，不检查startswith
                        continue

                    # 对于不带点号的项目键，检查通用匹配
                    for key in template.get("projects", {}).keys():
                        if isinstance(key, str) and project.startswith(key):
                            # 但也要避免反向匹配：key包含点号而project不包含时
                            if '.' in key:
                                # key是具体的（如tOS16.3），而project是通用的（如tOS16）
                                # 这种匹配是无效的
                                continue
                            generic_match_templates.append(template)
                            break
                else:
                    continue
            # 否则，这个模板适用于所有项目
            else:
                generic_match_templates.append(template)

    # 优先返回项目精确匹配的模板
    if exact_match_templates:
        # 根据查询类型对模板进行排序，让最相关的模板排在前面
        query_type = intent.get("query_type", "").lower()
        time_range = intent.get("time_range", "全部")

        # 定义排序函数：模板名称中包含查询类型的优先级更高
        def template_sort_score(template):
            name = template.get("name", "").lower()
            score = 0

            # 如果查询类型是"bug总量"，优先匹配名称中包含"bug总量"的模板
            if query_type == "bug总量":
                if "bug总量" in name:
                    score += 10
                elif "每日提交" in name:
                    # "每日提交"模板包含今天的时间限制，优先级降低
                    # 如果时间范围是"全部"，则完全排除"每日提交"模板
                    if time_range == "全部":
                        score -= 100  # 大幅减分，确保不会被选中
                    else:
                        score -= 5
                elif "mp block" in name or "block" in name:
                    # MP BLOCK模板优先级更低
                    score -= 10

            # 如果查询类型包含"block"，优先匹配名称中包含"block"的模板
            elif "block" in query_type:
                if "block" in name:
                    score += 10
                elif "bug总量" in name:
                    # bug总量模板优先级降低
                    score -= 5

            return score

        # 按得分排序（降序）
        exact_match_templates.sort(key=template_sort_score, reverse=True)

        print(f"[DEBUG match_jql_template] 找到{len(exact_match_templates)}个精确匹配模板，排序后返回: {exact_match_templates[0].get('name')}", file=sys.stderr)
        print(f"[DEBUG match_jql_template] 所有匹配模板: {[t.get('name') for t in exact_match_templates]}", file=sys.stderr)
        return exact_match_templates[0]

    # 如果没有精确匹配，返回通用匹配的模板
    if generic_match_templates:
        print(f"[DEBUG match_jql_template] 找到{len(generic_match_templates)}个通用匹配模板，返回第一个: {generic_match_templates[0].get('name')}", file=sys.stderr)
        return generic_match_templates[0]

    # 如果没有匹配到模板，返回默认模板
    print(f"[DEBUG match_jql_template] 没有找到匹配模板，返回默认模板", file=sys.stderr)
    print(f"[DEBUG match_jql_template] JQL_TEMPLATES长度: {len(JQL_TEMPLATES)}", file=sys.stderr)
    print(f"[DEBUG match_jql_template] 第一个模板名称: {JQL_TEMPLATES[0].get('name') if JQL_TEMPLATES else '空'}", file=sys.stderr)
    print(f"[DEBUG match_jql_template] exact_match_templates: {len(exact_match_templates)}, generic_match_templates: {len(generic_match_templates)}", file=sys.stderr)
    return {
        "name": "默认模板",
        "projects": {},
        "jql": "project = {project} AND issuetype = Bug AND {date_field} >= {start} AND {date_field} <= {end} ORDER BY priority DESC",
        "date_field": "created",
        "time_condition": ""
    }

# 时间解析与 JQL 动态修改
def parse_time_range(time_range: str):
    """
    解析时间范围，返回 JQL 可直接使用的表达式字符串。
    支持：全部、今天、昨天、前天、本周、上周、本月、上月、具体日期(YYYY-MM-DD)、日期范围(YYYY-MM-DD to YYYY-MM-DD)
    """
    time_range = time_range.strip()

    # 处理空字符串或None
    if not time_range:
        return "", ""

    # 处理全部时间范围
    if time_range == "全部":
        return "", ""

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
        # 未知时间范围，返回空（不添加时间条件）
        return "", ""

# 生成 JQL 查询
def generate_jql(template, intent):
    """根据模板和意图生成 JQL 查询"""
    project = intent.get("project")
    time_range = intent.get("time_range", "全部")  # 默认查询全部时间范围，除非用户明确指定

    # 动态生成时间条件
    start, end = parse_time_range(time_range)
    date_field = template.get("date_field", "created")

    # 获取基础 JQL
    if isinstance(template.get("projects"), dict) and project in template.get("projects", {}):
        jql = template.get("projects", {}).get(project, "")
    else:
        jql = template.get("jql", "")

    # 如果没有获取到 JQL，返回空字符串
    if not jql:
        return ""

    # 替换占位符（如果存在）
    if project and "{project}" in jql:
        jql = jql.replace("{project}", project)

    if "{date_field}" in jql:
        jql = jql.replace("{date_field}", date_field)

    if "{start}" in jql and start:
        jql = jql.replace("{start}", start)

    if "{end}" in jql and end:
        jql = jql.replace("{end}", end)

    # 只有当时间范围不是"全部"时，才添加时间条件
    # 注意：如果JQL中已经通过占位符包含了时间条件，就不需要再添加
    if start and end and "{start}" not in jql and "{end}" not in jql:
        dynamic_time_condition = f"{date_field} >= {start} AND {date_field} <= {end}"
        # 组装时间条件
        time_condition_str = f" {dynamic_time_condition} AND "

        # 插入到 JQL 中合适位置
        if "creator" in jql:
            jql = jql.replace("creator", time_condition_str + "creator")
        elif "ORDER BY" in jql:
            parts = jql.split("ORDER BY")
            jql = parts[0] + time_condition_str + "ORDER BY" + parts[1]
        else:
            # 只有当 JQL 不为空时才添加时间条件
            if jql:
                jql = jql + time_condition_str
            else:
                return ""

    # ========== 移除强制清洗，改为拉取全量数据 ==========
    # 不再强制添加 resolution = Unresolved，以支持全量数据拉取
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

# 调试：检查系统编码（在_log函数定义后）
_log("debug", f"Python version: {sys.version}")
_log("debug", f"Default encoding (sys.getdefaultencoding()): {sys.getdefaultencoding()}")
_log("debug", f"File system encoding (sys.getfilesystemencoding()): {sys.getfilesystemencoding()}")
try:
    import locale
    _log("debug", f"Locale encoding: {locale.getpreferredencoding()}")
except:
    _log("debug", "Cannot get locale encoding")

# 详细报告 Prompt 模板
DETAILED_REPORT_PROMPT = """你是一位拥有20年以上经验的顶级软件项目风险分析专家，曾任多家世界500强科技公司的首席质量官。用户请求了一份详细的风险报告，请提供全面、深入、专业的分析。

## 你的详细分析框架：
### 第一阶段：全量数据深度扫描
- **数据完整性分析**：扫描所有问题数据（包括已关闭和未关闭）
- **智能分类统计**：自动识别并统计各类风险问题数量
- **趋势洞察**：分析问题的时间分布和演变趋势

### 第二阶段：多维风险深度评估
- **阻塞风险深度分析**：详细分析每个阻塞问题的根本原因、影响范围和解决难度
- **版本卡点专项评估**：对MP Block问题进行技术可行性分析和时间影响评估
- **交付风险全景扫描**：全面评估交付相关问题的系统性和流程性风险
- **项目类型专项分析**：分别深入分析整机项目风险和tOS系统风险的独特特征

### 第三阶段：系统性解决方案设计
- **根因分析**：识别问题的系统性根源和流程缺陷
- **解决方案矩阵**：为每类风险设计多层次、可落地的解决方案
- **资源规划**：评估所需的人力、时间和成本投入
- **预防机制**：设计预防类似问题再次发生的长效机制

## 输出格式要求
你必须将输出分为两个部分：
- 第一部分用 `<thinking>` 标签包裹，内部写出你的详细推理过程（包括数据深度扫描、多维风险评估、系统性解决方案设计）。
- 第二部分用 `<answer>` 标签包裹，内部写出详细的风险分析报告。

### <answer>部分的输出原则（非固定模板）：
1. **报告摘要**：用精炼语言总结核心发现和关键建议
2. **数据全景**：提供完整的统计数据，包括全量问题分析
3. **风险深度剖析**：对每类风险进行深入分析，包括：
   - 阻塞问题根因分析和技术评估
   - MP Block问题的版本影响和时间预测
   - 交付风险的流程性和系统性缺陷
   - 整机项目和tOS系统的专项风险评估
4. **解决方案框架**：提供系统性、分层次的解决方案
   - 紧急应对措施（24小时内）
   - 短期改进方案（1周内）
   - 中长期优化计划（1个月内）
   - 预防性机制建设（长期）
5. **资源需求评估**：明确各项方案所需的人力、时间和资源
6. **风险等级和优先级**：综合评估整体风险等级和各项问题的处理优先级

## 输出示例风格（非模板，仅为参考）：
<thinking>
[详细的思考过程...]
</thinking>
<answer>
# 详细风险分析报告

## 执行摘要
本项目共识别出**58个问题**，其中**12个为高风险问题**。核心风险集中在支付模块的3个阻塞问题和客户端团队的2个MP Block版本卡点。整体风险等级评估为【高】，预计对版本发布时间影响为**1-2周延迟**。

## 一、全量数据分析
### 1.1 基础统计
- **总问题数**：58个（全量数据，含已关闭问题）
- **未解决问题**：42个（72.4%）
- **已解决问题**：16个（27.6%）
- **解决率**：27.6%（需提升至行业基准50%以上）
- **本周新增**：8个（环比上升33%）

### 1.2 风险分类统计
- **阻塞问题**：7个（优先级Block或标签含"阻塞"）
- **MP Block版本卡点**：5个（可能严重延迟发布时间）
- **交付风险问题**：9个（标题或标签含"交付"）
- **整机项目风险**：23个（X系列项目）
- **tOS系统风险**：14个（tOS相关项目）

## 二、核心风险深度剖析
### 2.1 阻塞问题根因分析
**X6840-123（支付流程异常）**：
- **根因**：第三方支付接口兼容性问题，缺乏完善的错误处理机制
- **影响**：支付模块完全不可用，影响100%用户支付功能
- **技术难度**：中等，需要协调第三方服务商
- **预计解决时间**：3-5个工作日

**X6840-456（客户端崩溃）**：
- **根因**：内存泄漏问题，每运行24小时增长200MB内存
- **影响**：客户端稳定性差，用户留存率下降15%
- **技术难度**：高，需要资深客户端开发深度调试
- **预计解决时间**：5-7个工作日

### 2.2 MP Block版本卡点评估
**X6840-234（三方库兼容性）**：
- **技术评估**：当前使用的React Native 0.72版本与目标设备系统不兼容
- **版本影响**：如不解决，无法发布到30%的目标设备
- **解决方案**：升级到React Native 0.74或开发兼容层
- **时间影响**：预计延迟发布时间2周

## 三、系统性解决方案
### 3.1 紧急应对措施（24小时内）
1. **成立支付问题专项小组**：抽调3名支付领域专家，今天下午3点前启动
2. **客户端崩溃热修复**：发布临时补丁，缓解用户影响

### 3.2 短期改进方案（1周内）
1. **三方库兼容性攻关**：组建5人技术攻关小组，本周五前确定技术方案
2. **交付流程优化**：与交付测试部联合制定验收标准，减少交付争议

### 3.3 中长期优化计划（1个月内）
1. **内存泄漏治理专项**：建立客户端内存监控体系，预防类似问题
2. **第三方服务治理**：建立第三方服务兼容性测试标准和应急预案

## 四、资源需求评估
- **人力需求**：需要额外8人·日的技术专家投入
- **时间成本**：紧急问题需3-5天，整体风险缓解需2-3周
- **资金预算**：第三方服务协调和技术攻关预计需2万元

## 五、综合评估
- **整体风险等级**：【高】- 需要管理层重点关注和资源倾斜
- **版本发布时间影响**：预计延迟10-14天
- **建议优先级**：立即启动紧急应对措施，同步推进短期改进方案
</answer>

请根据实际数据提供深度、专业、可落地的详细分析报告，避免使用固定模板，确保每个分析都针对项目的独特风险特征。"""


# 专家级系统 Prompt 模板
SYSTEM_PROMPT = """你是一位拥有20年以上经验的顶级软件项目风险分析专家，曾任多家世界500强科技公司的首席质量官。你以敏锐的风险洞察力、精准的问题定位和务实的解决方案而闻名业界。

## 你的核心原则：
1. **数据驱动**：所有分析必须基于实际的Jira数据，确保结论有据可依
2. **意图导向**：根据用户查询的具体意图和范围调整输出的详细程度和格式
3. **自然对话**：使用自然、专业的语言进行交流，避免机械的模板式回答
4. **重点突出**：只展示最关键的问题和数据，避免信息过载
5. **实用建议**：提供具体、可执行的建议，明确责任人和时间

## 输出格式指导（灵活调整）：
你可以根据用户查询的意图和范围，选择最合适的输出格式：

### 对于整体风险分析查询（如"分析X6840的项目风险"）：
- 可以使用结构化的格式，但不必严格遵守固定的标题顺序
- 包含关键部分：风险概览、主要问题、风险等级、建议措施
- 适当使用emoji和视觉元素增强可读性

### 对于单领域或特定类型查询（如"X6840的稳定性问题"、"tOS16.3的阻塞问题"）：
- 直接列出该领域的关键问题ID、简要描述和核心结论
- 使用简洁的列表格式，无需完整结构
- 专注于用户询问的领域，不扩展无关内容

### 对于简单查询（如"X6840有多少个未解决bug"）：
- 直接给出数据和简要分析
- 无需复杂格式，自然回答即可

## 数据使用要求：
1. **始终基于Jira数据**：你的分析必须基于提供的Jira问题数据，引用具体的问题ID和统计数据
2. **准确反映数据**：风险等级、问题分类必须与数据一致，不得虚构
3. **关注趋势和模式**：识别问题趋势、常见模式、风险集中领域
4. **提供数据支持**：在结论中引用具体数据，如"共发现15个未解决问题，其中3个为阻塞问题"

## 对话风格：
1. **自然专业**：像专家顾问一样对话，使用专业但不晦涩的语言
2. **上下文感知**：考虑用户之前的查询，保持对话连贯性
3. **主动追问**：当用户查询不够明确时，可以主动询问澄清
4. **循序渐进**：从概览到细节，逐步深入分析

## 示例风格：
用户："分析一下X6840的项目风险"
你："基于Jira数据，X6840项目目前共有42个未解决问题，其中5个被标记为阻塞问题。风险等级评估为🟡中风险。

主要问题集中在：
• **稳定性问题**：3个阻塞问题涉及内存泄漏和崩溃（X6840-123、X6840-456、X6840-789）
• **性能问题**：8个未解决bug影响系统响应时间
• **交付流程**：2个阻塞问题导致版本发布延迟

建议优先解决3个稳定性阻塞问题，预计需要2周时间。项目整体风险可控，但需要加强测试覆盖。"

用户："X6840的稳定性问题有哪些？"
你："X6840项目中目前有3个稳定性相关的阻塞问题：
1. **X6840-123**：内存泄漏导致应用运行时间超过2小时后崩溃
2. **X6840-456**：特定设备上的系统级崩溃，影响5%用户
3. **X6840-789**：多线程竞争条件导致随机死锁

建议立即分配资源解决X6840-123，因其影响范围最广。"

请根据用户查询的意图和数据情况，提供最合适的风险分析。记住：数据是基础，意图是导向，自然对话是目标。"""


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


def stream_ai_response(prompt, return_content=False):
    """
    统一的AI流式响应处理函数

    Args:
        prompt: 用户提示词
        return_content: 是否返回完整内容（False时仅打印输出）

    Returns:
        当return_content=True时返回完整响应内容，否则返回None
    """

    response = call_ai_api(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT,
        temperature=0.7,
        stream=True
    )

    if response is None:
        error_msg = "请求 AI 失败: 无法连接到AI服务"
        _log("err", error_msg)
        print(error_msg)
        return "" if return_content else None

    if response.status_code != 200:
        error_msg = f"请求 AI 失败: 状态码 {response.status_code}"
        _log("err", error_msg)
        print(error_msg)
        return "" if return_content else None

    full_content = ""
    for line in response.iter_lines():
        if line:
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
                                if return_content:
                                    full_content += content
                    except json.JSONDecodeError:
                        pass

    return full_content if return_content else None


def stream_ai_to_queue(messages, system_prompt, sse_queue, max_tokens=None, temperature=0.7):
    """
    流式AI分析并将token实时推送到SSE队列

    适用于Flask SSE场景：AI生成的每个token立即推送到SSE队列，
    前端可以实时展示分析过程，无需等待完整响应。

    Args:
        messages: OpenAI消息列表 (list of dict)
        system_prompt: 系统提示词
        sse_queue: queue.Queue，用于推送SSE事件（格式: (event_type, data)）
        max_tokens: 最大输出token数，None表示不限制
        temperature: 温度参数

    Returns:
        str: 完整的分析结果文本，失败时返回None（SSE队列中已包含错误事件）
    """
    import queue

    response = call_ai_api(
        messages=messages,
        system_prompt=system_prompt,
        temperature=temperature,
        stream=True,
        max_retries=3,
        retry_delay=5,
        max_tokens=max_tokens,
        timeout=180  # 流式读取每块超时180秒，防止AI服务暂停发送导致永久挂起
    )

    if response and response.status_code == 200:
        full_content = ""
        for line in response.iter_lines():
            if line:
                line_text = line.decode('utf-8')
                if line_text.startswith('data: '):
                    data_text = line_text[6:]
                    if data_text == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data_text)
                        choices = chunk.get('choices', [])
                        if choices:
                            delta = choices[0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                full_content += content
                                sse_queue.put(('answer', content))
                    except json.JSONDecodeError:
                        pass
        return full_content
    elif response:
        try:
            error_text = response.text[:300]
        except Exception:
            error_text = ""
        sse_queue.put(('error', f'AI分析请求失败: 状态码 {response.status_code}。{error_text}'))
        return None
    else:
        sse_queue.put(('error', 'AI分析失败: AI服务返回空响应'))
        return None


def _get_risk_markers(fields):
    """提取 issue 的关键风险标记，返回 (标记字符串, 是否为 MP Block, 是否含阻塞标签)"""
    must_resolve = fields.get('customfield_10000', '')
    labels = fields.get('labels', []) or []
    markers = []
    is_mp = (isinstance(must_resolve, str) and 'MP Block' in must_resolve) or \
            (isinstance(must_resolve, dict) and 'MP Block' in must_resolve.get('value', ''))
    is_blk = any('阻塞' in l for l in labels)
    if is_mp:
        markers.append('🚫MP')
    if is_blk:
        markers.append('🧱阻塞')
    return (' [' + '/'.join(markers) + ']' if markers else ''), is_mp, is_blk


def _module_from_summary(summary):
    """从Bug摘要中提取【模块】信息，如【驱动组】【CN6】【SDV】【Camera】预览画面黑屏 → Camera"""
    if not summary:
        return ''
    # 匹配【XXX】格式，尝试取最后一个或倒数第二个（取决于summary格式）
    matches = re.findall(r'【([^】]+)】', summary)
    if len(matches) >= 4:
        # 标准格式：【部门】【项目】【阶段】【模块】
        return matches[3].strip()
    elif len(matches) >= 2:
        # 非标准格式，取最后一个
        return matches[-1].strip()
    return ''


def format_portfolio_data(issues, project_names=None, max_detail=80):
    """
    格式化Jira问题数据为AI可读的结构化文本。
    清晰分离未解决问题（风险分析用）和已解决问题（质量指标用）。

    Args:
        issues: Jira issue列表
        project_names: 业务项目名列表（用于显示）
        max_detail: 详细列表最大条目数
    """
    if not issues:
        return '未查询到相关Jira数据。'

    total = len(issues)

    # ── 状态分类 ──
    RESOLVED_STATUSES = {'closed', 'verified', 'abandoned'}
    # 未解决（用于风险分析）: submitted, open, in progress, modifying, fixed, resolved, reopened

    unresolved_issues = []  # 风险分析用
    resolved_issues = []    # 质量指标用

    for issue in issues:
        status = (issue.get('fields', {}).get('status', {}) or {}).get('name', '').lower()
        if status in RESOLVED_STATUSES:
            resolved_issues.append(issue)
        else:
            unresolved_issues.append(issue)

    # ── 统计变量 ──
    priority_counts = {}
    module_counts = {}
    domain_counts = {}
    project_counts = {}
    mp_block_issues = []
    blocking_label_issues = []
    blocking_issues = []   # Blocker优先级
    critical_issues = []   # Critical优先级

    PRIO_ORDER = {'Blocker': 0, 'Block': 0, 'Critical': 1, 'High': 2, 'Major': 3,
                  'Medium': 4, 'Minor': 5, 'Low': 6, 'Trivial': 7}

    for issue in issues:
        fields = issue.get('fields', {})
        key = issue.get('key', '')
        status = fields.get('status', {}).get('name', '未知')
        priority = fields.get('priority', {}).get('name', '未知')
        summary = fields.get('summary', '')

        priority_counts[priority] = priority_counts.get(priority, 0) + 1

        # 模块（优先从components，fallback到summary解析）
        components = fields.get('components', []) or []
        comp_names = []
        for comp in components:
            if isinstance(comp, dict) and comp.get('name'):
                comp_names.append(comp['name'])
        if comp_names:
            for cn in comp_names:
                module_counts[cn] = module_counts.get(cn, 0) + 1
        else:
            mod = _module_from_summary(summary)
            if mod:
                module_counts[mod] = module_counts.get(mod, 0) + 1

        # 业务领域（customfield_10002）
        domain_raw = fields.get('customfield_10002', '')
        if isinstance(domain_raw, str) and domain_raw.strip():
            domain_counts[domain_raw.strip()] = domain_counts.get(domain_raw.strip(), 0) + 1
        elif isinstance(domain_raw, dict):
            domain_val = domain_raw.get('value', '') or domain_raw.get('name', '')
            if domain_val:
                domain_counts[domain_val] = domain_counts.get(domain_val, 0) + 1

        # 项目分布
        affect_project = fields.get('customfield_10001', '')
        if affect_project and isinstance(affect_project, str) and affect_project.strip():
            proj = affect_project.strip().split(',')[0].strip()
        else:
            proj = key.split('-')[0] if '-' in key else '未知'
        if proj not in project_counts:
            project_counts[proj] = {'total': 0, 'unresolved': 0}
        project_counts[proj]['total'] += 1
        if status.lower() not in RESOLVED_STATUSES:
            project_counts[proj]['unresolved'] += 1

        # 关键风险标记
        must_resolve = fields.get('customfield_10000', '')
        is_mp = (isinstance(must_resolve, str) and 'MP Block' in must_resolve) or \
                (isinstance(must_resolve, dict) and 'MP Block' in must_resolve.get('value', ''))
        issue_labels = fields.get('labels', []) or []
        is_blk = any('阻塞' in l for l in issue_labels)
        if is_mp:
            mp_block_issues.append(issue)
        if is_blk:
            blocking_label_issues.append(issue)

        # 按优先级收集严重问题（仅未解决）
        if status.lower() not in RESOLVED_STATUSES:
            p_lower = priority.lower()
            if p_lower in ('blocker', 'block', '阻塞'):
                blocking_issues.append(issue)
            elif p_lower in ('critical', '紧急'):
                critical_issues.append(issue)

    # ═══════════════════ 构建输出 ═══════════════════
    lines = []

    # ── 项目名称映射 ──
    if project_names:
        lines.append('【项目名称映射】')
        lines.append(f'业务项目名: {", ".join(project_names)}')
        lines.append('⚠️ 报告中请直接使用以上业务项目名，不要使用Jira issue key前缀（如CN6OS16等）')
        lines.append('')

    # ── 数据概览 ──
    lines.append('【数据概览】')
    lines.append(f'Bug总数: {total} | 未解决: {len(unresolved_issues)} | 已闭环: {len(resolved_issues)}')

    # 质量指标
    closed_count = sum(1 for i in resolved_issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() == 'closed')
    verified_count = sum(1 for i in resolved_issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() == 'verified')
    abandoned_count = sum(1 for i in resolved_issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() == 'abandoned')
    fixed_count = sum(1 for i in issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() == 'fixed')
    unresolved_mp = sum(1 for i in mp_block_issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() not in RESOLVED_STATUSES)
    unresolved_blk = sum(1 for i in blocking_label_issues if (i.get('fields', {}).get('status', {}) or {}).get('name', '').lower() not in RESOLVED_STATUSES)

    closure_rate = round(closed_count / total * 100, 1) if total > 0 else 0
    resolution_rate = round((closed_count + verified_count) / total * 100, 1) if total > 0 else 0
    fix_rate = round(fixed_count / total * 100, 1) if total > 0 else 0

    lines.append('')
    lines.append('【质量指标】')
    lines.append(f'闭环率(closed/总计): {closed_count}/{total} = {closure_rate}%')
    lines.append(f'解决率(closed+verified/总计): {closed_count + verified_count}/{total} = {resolution_rate}%')
    lines.append(f'修复率(fixed/总计): {fixed_count}/{total} = {fix_rate}%')
    lines.append(f'已闭环: {closed_count} | 待验证: {verified_count} | 已修复: {fixed_count} | 已打回: {abandoned_count}')

    lines.append('')
    lines.append('【关键风险标记】')
    lines.append(f'Must_Resolve=MP Block: {len(mp_block_issues)}个 (未解决{unresolved_mp}个)')
    lines.append(f'标签=阻塞测试: {len(blocking_label_issues)}个 (未解决{unresolved_blk}个)')
    lines.append('⚠️ 以上为Jira表面不可见的真实风险标记，Priority只是等级分类不代表阻塞测试')

    lines.append('')
    lines.append('【优先级分布】')
    prio_parts = []
    for p in ['Blocker', 'Critical', 'High', 'Major', 'Medium', 'Minor', 'Low', 'Trivial']:
        if p in priority_counts:
            prio_parts.append(f'{p}: {priority_counts[p]}')
    for p, c in sorted(priority_counts.items()):
        if p not in ('Blocker', 'Critical', 'High', 'Major', 'Medium', 'Minor', 'Low', 'Trivial'):
            prio_parts.append(f'{p}: {c}')
    lines.append(' | '.join(prio_parts))

    # ── 项目分布 ──
    lines.append('')
    lines.append('【项目分布】')
    for proj, counts in sorted(project_counts.items()):
        u = f' (未解决{counts["unresolved"]})' if counts['unresolved'] > 0 else '（已全部解决）'
        lines.append(f'  {proj}: {counts["total"]}个{u}')

    # ── 模块分布（Top 15） ──
    lines.append('')
    lines.append('【模块分布】')
    if module_counts:
        for mod, cnt in sorted(module_counts.items(), key=lambda x: -x[1])[:15]:
            lines.append(f'  {mod}: {cnt}个')
        if len(module_counts) > 15:
            lines.append(f'  ...另有{len(module_counts) - 15}个模块')
    else:
        lines.append('  无模块数据')

    # ── 业务领域分布 ──
    lines.append('')
    lines.append('【业务领域分布】')
    if domain_counts:
        for dom, cnt in sorted(domain_counts.items(), key=lambda x: -x[1]):
            lines.append(f'  {dom}: {cnt}个')
    else:
        lines.append('  无业务领域数据')

    # ═══════════════════ 未解决问题明细 ═══════════════════
    lines.append('')
    lines.append('=' * 60)
    lines.append(f'【未解决问题清单】（共{len(unresolved_issues)}个 — 风险分析核心）')
    lines.append('=' * 60)

    if not unresolved_issues:
        lines.append('✅ 所有问题均已解决，当前无风险。')
    else:
        # 按优先级排序
        def _sort_key(issue):
            p = issue.get('fields', {}).get('priority', {}).get('name', '')
            return PRIO_ORDER.get(p, 99)
        unresolved_issues.sort(key=_sort_key)

        # 严重问题列表（Blocker + Critical）
        severe = [i for i in unresolved_issues if _sort_key(i) <= 1]
        if severe:
            lines.append('')
            lines.append(f'【严重问题 — Blocker/Critical】（共{len(severe)}个）')
            for issue in severe[:max_detail]:
                f = issue.get('fields', {})
                marker, _, _ = _get_risk_markers(f)
                key = issue.get('key', '')
                s = f.get('summary', '')[:60]
                p = f.get('priority', {}).get('name', '')
                st = f.get('status', {}).get('name', '')
                a = (f.get('assignee', {}) or {}).get('displayName', '未分配')
                lines.append(f'  [{p}] {key}{marker} {s} | 状态:{st} | @{a}')

        # 高优先级问题（High/Major）
        high_major = [i for i in unresolved_issues if _sort_key(i) in (2, 3)]
        if high_major:
            lines.append('')
            lines.append(f'【中优先级 — High/Major】（共{len(high_major)}个）')
            for issue in high_major[:max_detail]:
                f = issue.get('fields', {})
                marker, _, _ = _get_risk_markers(f)
                key = issue.get('key', '')
                s = f.get('summary', '')[:60]
                p = f.get('priority', {}).get('name', '')
                st = f.get('status', {}).get('name', '')
                lines.append(f'  [{p}] {key}{marker} {s} | {st}')

            if len(high_major) > max_detail:
                lines.append(f'  ... 另有{len(high_major) - max_detail}个未列出')

        # 其他未解决
        other = [i for i in unresolved_issues if _sort_key(i) > 3]
        if other:
            lines.append('')
            lines.append(f'【其他未解决】（共{len(other)}个 — Medium及以下）')

        # MP Block + 阻塞测试 汇总
        high_risk_items = []
        for issue in unresolved_issues:
            f = issue.get('fields', {})
            marker, is_mp, is_blk = _get_risk_markers(f)
            if is_mp or is_blk:
                high_risk_items.append((issue, marker))
        if high_risk_items:
            lines.append('')
            lines.append(f'【关键风险条目】（未解决中MP Block + 阻塞测试，共{len(high_risk_items)}个）')
            lines.append('⚠️ 这些是真正的核心风险项，请重点分析：')
            lines.append('  🚫MP = Must_Resolve=MP Block | 🧱阻塞 = 标签=阻塞测试')
            for issue, marker in high_risk_items[:50]:
                f = issue.get('fields', {})
                key = issue.get('key', '')
                s = f.get('summary', '')[:60]
                p = f.get('priority', {}).get('name', '')
                st = f.get('status', {}).get('name', '')
                lines.append(f'  {key}{marker} [{p}] {s} | {st}')

    # ═══════════════════ 已解决问题统计 ═══════════════════
    lines.append('')
    lines.append('=' * 60)
    lines.append(f'【已解决问题明细】（共{len(resolved_issues)}个 — 质量指标参考）')
    lines.append('=' * 60)
    if resolved_issues:
        # 按状态分组
        from collections import defaultdict
        by_status = defaultdict(list)
        for issue in resolved_issues:
            st = (issue.get('fields', {}).get('status', {}) or {}).get('name', '')
            by_status[st].append(issue)
        for st, items in sorted(by_status.items()):
            lines.append(f'  {st}: {len(items)}个')
            for issue in items[:20]:
                f = issue.get('fields', {})
                key = issue.get('key', '')
                s = f.get('summary', '')[:40]
                p = f.get('priority', {}).get('name', '')
                marker, _, _ = _get_risk_markers(f)
                lines.append(f'    {key}{marker} [{p}] {s}')
            if len(items) > 20:
                lines.append(f'    ... 另有{len(items) - 20}个')
    else:
        lines.append('  无已解决问题数据。')

    lines.append('')
    lines.append('【分析说明】')
    lines.append('1. 未解决问题 = 风险分析核心，请重点分析其中的严重问题、MP Block、阻塞测试项')
    lines.append('2. 已解决问题 = 质量指标计算（闭环率/解决率/修复率）')
    lines.append('3. 模块/领域分布帮助识别系统性风险集中区域')
    return '\n'.join(lines)


def stream_portfolio_analysis(issues_all, issues_unresolved, enhanced_query, sse_queue, system_prompt, max_tokens=16384, project_names=None):
    """
    全量项目群风险分析 - 统一入口

    接收所有Jira问题，进行全量统计和智能格式化，
    然后流式调用AI进行分析，将结果推送到SSE队列。

    Args:
        issues_all: 全量Jira问题列表
        issues_unresolved: 未解决的Jira问题列表
        enhanced_query: 增强后的用户查询
        sse_queue: SSE事件队列
        system_prompt: AI系统提示词
        max_tokens: AI输出最大token数

    Returns:
        (full_content, jira_data): AI完整回复和输入数据
    """
    import json

    priority_sorted = sorted(issues_all,
        key=lambda x: {
            "Block": 0, "阻塞": 0, " blocker": 0,
            "Critical": 1, "紧急": 1, " critical": 1,
            "High": 2, "高": 2, "Major": 2, " major": 2,
        }.get(x.get('fields', {}).get('priority', {}).get('name', '').lower(), 99)
    )

    jira_data = format_portfolio_data(priority_sorted, project_names=project_names)

    messages = [
        {"role": "user", "content": f"用户问题：{enhanced_query}\n\n真实Jira数据：\n{jira_data}"}
    ]

    full_content = stream_ai_to_queue(
        messages=messages,
        system_prompt=system_prompt,
        sse_queue=sse_queue,
        max_tokens=max_tokens
    )

    return full_content, jira_data


# JQL 模板管理

def load_jql_templates():
    """加载 JQL 模板"""
    template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
    try:
        # 方法1：使用二进制模式读取，确保正确解码
        with open(template_file, 'rb') as f:
            file_bytes = f.read()

        _log("debug", f"文件前100字节十六进制: {file_bytes[:100].hex()}")

        # 尝试解码为UTF-8（使用utf-8-sig处理可能的BOM）
        try:
            file_text = file_bytes.decode('utf-8-sig')
            _log("debug", f"UTF-8解码后前200字符 repr: {repr(file_text[:200])}")
        except Exception as decode_err:
            _log("debug", f"UTF-8解码失败: {decode_err}")
            # 尝试用latin-1解码
            file_text = file_bytes.decode('latin-1')
            _log("debug", f"Latin-1解码后前200字符 repr: {repr(file_text[:200])}")

        # 调试：检查RT_部分的具体内容
        rt_index = file_text.find('RT_')
        if rt_index != -1:
            rt_snippet = file_text[rt_index:rt_index+30]
            _log("debug", f"找到RT_在位置 {rt_index}: {repr(rt_snippet)}")
            _log("debug", f"RT_部分原始字节: {rt_snippet.encode('latin-1').hex() if 'latin-1' in locals() else rt_snippet.encode('utf-8').hex()}")

        # 修复已知的乱码字符串（UTF-8字节被错误解码的结果）
        # 常见乱码模式：UTF-8字节被错误解码为其他编码
        mojibake_map = {
            '浜や粯娴嬭瘯閮ㄦ瘡鏃ユ彁浜ug': '交付测试部每日提交bug',
            '浜や粯娴嬭瘯閮╞ug鎬婚噺': '交付测试部bug总量',
            '浜や粯娴嬭瘯閮╞P BLOCK': '交付测试部MP BLOCK',
            '浜や粯娴嬭瘯閮?': '交付测试部',
            '浜や粯娴嬭瘯閮': '交付测试部',
            '娴嬭瘯閮': '测试部',
            'RT_浜や粯娴嬭瘯閮': 'RT_交付测试部',
            'RT_浜や粯娴嬭瘯閮?': 'RT_交付测试部',
            'RT_浜や粯娴嬭瘯閮?)': 'RT_交付测试部)',  # 带括号的版本
        }

        original_file_text = file_text
        for bad_str, good_str in mojibake_map.items():
            if bad_str in file_text:
                file_text = file_text.replace(bad_str, good_str)
                _log("debug", f"修复乱码: {bad_str[:20]}... -> {good_str}")

        if original_file_text != file_text:
            _log("info", "已修复文件中的乱码字符串")
            _log("debug", f"修复后前200字符: {repr(file_text[:200])}")


        # 加载JSON
        templates = json.loads(file_text)
        templates_list = templates.get('templates', [])
        _log("ok", f"成功加载 JQL 模板，共 {len(templates_list)} 个模板")

        # 调试：检查第一个模板的名称
        if templates_list:
            first_name = templates_list[0].get('name', '')
            _log("debug", f"第一个模板名称 repr: {repr(first_name)}")
            _log("debug", f"第一个模板名称: {first_name}")

            # 如果名称看起来是乱码，尝试修复
            if '浜' in first_name or 'や' in first_name or '粯' in first_name:
                _log("warn", "检测到乱码字符串，尝试修复...")
                # 尝试修复：假设乱码是UTF-8字节被错误解码的结果
                try:
                    # 方法1：尝试用latin-1编码乱码字符串（获取原始字节），然后用UTF-8解码
                    try:
                        latin1_bytes = first_name.encode('latin-1')
                        _log("debug", f"Latin-1编码字节: {latin1_bytes.hex()}")
                        fixed_utf8 = latin1_bytes.decode('utf-8')
                        _log("debug", f"方法1修复后: {fixed_utf8}")
                        templates_list[0]['name'] = fixed_utf8
                    except Exception as e1:
                        _log("debug", f"方法1失败: {e1}")

                    # 方法2：尝试用cp936编码乱码字符串（因为locale是cp936）
                    try:
                        cp936_bytes = first_name.encode('cp936')
                        _log("debug", f"CP936编码字节: {cp936_bytes.hex()}")
                        # 这些字节可能是原始UTF-8字节？尝试用UTF-8解码
                        fixed_utf8_2 = cp936_bytes.decode('utf-8')
                        _log("debug", f"方法2修复后: {fixed_utf8_2}")
                        templates_list[0]['name'] = fixed_utf8_2
                    except Exception as e2:
                        _log("debug", f"方法2失败: {e2}")

                except Exception as e:
                    _log("debug", f"修复尝试失败: {e}")

        # 验证 X6840 在"交付测试部bug总量"模板中的 JQL
        for tpl in templates_list:
            if tpl.get('name') == '交付测试部bug总量':
                x6840_jql = tpl.get('projects', {}).get('X6840', '')
                _log("info", f"模板[交付测试部bug总量]中X6840的JQL: {x6840_jql[:150]}...")
                # 调试：打印字符串的repr以查看实际内容
                _log("debug", f"JQL repr: {repr(x6840_jql[:200])}")
                break

        # 修复乱码字符串
        _log("debug", "开始修复乱码字符串...")
        fixed_count = 0

        def fix_mojibake(s):
            """尝试修复乱码字符串"""
            if not isinstance(s, str):
                return s
            # 检查是否包含乱码字符
            if '浜' in s or 'や' in s or '粯' in s:
                try:
                    # 方法：用latin-1编码，然后用utf-8解码
                    latin_bytes = s.encode('latin-1')
                    fixed = latin_bytes.decode('utf-8')
                    return fixed
                except:
                    # 如果失败，尝试其他方法
                    try:
                        # 用cp936编码，然后用utf-8解码
                        cp936_bytes = s.encode('cp936')
                        fixed = cp936_bytes.decode('utf-8')
                        return fixed
                    except:
                        # 如果还是失败，返回原字符串
                        return s
            return s

        def traverse_and_fix(obj):
            """递归遍历并修复字符串"""
            nonlocal fixed_count
            if isinstance(obj, dict):
                for k, v in obj.items():
                    obj[k] = traverse_and_fix(v)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    obj[i] = traverse_and_fix(v)
            elif isinstance(obj, str):
                fixed = fix_mojibake(obj)
                if fixed != obj:
                    fixed_count += 1
                return fixed
            return obj

        # 应用修复
        traverse_and_fix(templates_list)
        _log("debug", f"修复了 {fixed_count} 个字符串")

        return templates_list
    except Exception as e:
        _log("err", f"加载 JQL 模板失败: {e}")
        import traceback
        _log("err", f"详细错误: {traceback.format_exc()}")
        return []

# 加载 JQL 模板
JQL_TEMPLATES = load_jql_templates()


def get_all_projects_from_templates():
    """从所有模板中提取所有唯一的项目键"""
    all_projects = set()
    for template in JQL_TEMPLATES:
        projects = template.get('projects', {})
        if isinstance(projects, dict):
            for project_key in projects.keys():
                all_projects.add(project_key)
    return sorted(list(all_projects))


def analyze_all_projects_batch(intent, time_range="全部"):
    """批量分析所有项目的风险

    Args:
        intent: 分析意图（如"风险分析"）
        time_range: 时间范围（默认"全部"）

    Returns:
        dict: 包含所有项目分析结果的字典
    """
    from datetime import datetime
    import concurrent.futures

    all_projects = get_all_projects_from_templates()
    _log("info", f"从模板中提取到 {len(all_projects)} 个唯一项目: {all_projects}")

    results = {}

    # 使用线程池并行获取数据
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_project = {}

        for project in all_projects:
            # 为每个项目创建分析任务
            future = executor.submit(
                analyze_single_project,
                project=project,
                intent=intent,
                time_range=time_range
            )
            future_to_project[future] = project

        # 收集结果
        for future in concurrent.futures.as_completed(future_to_project):
            project = future_to_project[future]
            try:
                result = future.result()
                results[project] = result
                _log("info", f"项目 {project} 分析完成")
            except Exception as e:
                _log("err", f"项目 {project} 分析失败: {e}")
                results[project] = {"error": str(e)}

    return results


def analyze_single_project(project, intent, time_range="全部"):
    """分析单个项目的风险（内部函数，用于批量分析）"""
    from datetime import datetime

    _log("debug", f"开始分析项目 {project}, 意图: {intent}, 时间范围: {time_range}")

    # 匹配模板
    matched_template = match_jql_template(intent, project)
    if not matched_template:
        return {"error": f"未找到匹配的模板 for {project}"}

    # 生成JQL
    intent_dict = {"project": project, "time_range": time_range}
    jql = generate_jql(matched_template, intent_dict)

    if not jql:
        return {"error": f"无法生成JQL for {project}"}

    # 获取数据
    try:
        raw_issues = fetch_all_issues(jql)
        _log("debug", f"项目 {project} 获取到 {len(raw_issues)} 个问题")

        # 基本统计
        total_issues = len(raw_issues)
        unresolved_issues = [issue for issue in raw_issues if
                           issue.get('fields', {}).get('resolution') is None]

        # 风险级别统计
        risk_counts = {"高": 0, "中": 0, "低": 0}
        for issue in raw_issues:
            fields = issue.get('fields', {})
            priority = fields.get('priority', {}).get('name', '无') if fields.get('priority') else '无'
            labels = fields.get('labels', [])
            summary = fields.get('summary', '')
            risk_level = get_risk_level(priority, labels, summary)
            risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

        return {
            "project": project,
            "total_issues": total_issues,
            "unresolved_issues": len(unresolved_issues),
            "risk_counts": risk_counts,
            "resolution_rate": round((total_issues - len(unresolved_issues)) / total_issues * 100, 2) if total_issues > 0 else 0,
            "sample_issues": [issue.get('key', '') for issue in raw_issues[:5]] if raw_issues else []
        }
    except Exception as e:
        _log("err", f"分析项目 {project} 时出错: {e}")
        return {"error": str(e)}


def extract_tos_version_from_jql(jql):
    """从JQL查询中提取tOS版本（tOS16.1、tOS16.2、tOS16.3）"""
    import re
    # 匹配 project = tOS16.1 或 project = tOS16.2 或 project = tOS16.3
    pattern = r'project\s*=\s*(tOS16\.\d+)'
    matches = re.findall(pattern, jql, re.IGNORECASE)
    if matches:
        # 返回第一个匹配的版本（通常只有一个）
        _log("debug", f"从JQL中提取到tOS版本: {matches[0]}, JQL片段: {jql[:100]}")
        return matches[0]
    # 如果没有直接匹配，尝试匹配 project = XXXX-tOS16 并推断版本
    # 但更好的方法是从其他 project = tOS16.X 推断
    _log("debug", f"未从JQL中提取到tOS版本: {jql[:100]}")
    # 如果还是找不到，返回 None
    return None


def group_projects_by_tos_version():
    """按tOS版本分组项目

    Returns:
        dict: 按tOS版本分组的项目字典，格式为 {tos_version: [project_key1, project_key2, ...]}
    """
    # 收集所有项目及其JQL查询（从所有5个模板中提取）
    projects_with_jql = {}
    if not JQL_TEMPLATES:
        _log("warn", "JQL_TEMPLATES 为空，无法分组项目")
        return {}
    _log("debug", f"JQL_TEMPLATES 长度: {len(JQL_TEMPLATES)}")

    # 从所有模板中提取项目
    for template in JQL_TEMPLATES:
        projects = template.get('projects', {})
        for project_key, jql in projects.items():
            # 只添加尚未添加的项目，避免重复
            if project_key not in projects_with_jql:
                projects_with_jql[project_key] = jql

    _log("debug", f"收集到 {len(projects_with_jql)} 个唯一项目")

    # 按tOS版本分组
    tos_groups = {}
    for project_key, jql in projects_with_jql.items():
        tos_version = extract_tos_version_from_jql(jql)
        if not tos_version:
            # 如果找不到tOS版本，跳过（可能是tOS项目本身）
            # 检查项目键是否是tOS版本
            if project_key.startswith('tOS16.'):
                tos_version = project_key
            else:
                _log("warn", f"无法从JQL中提取tOS版本 for {project_key}")
                continue

        if tos_version not in tos_groups:
            tos_groups[tos_version] = []
        tos_groups[tos_version].append(project_key)

    _log("debug", f"按tOS版本分组结果: {tos_groups}")

    # 对每个版本的项目列表进行排序（确保tOS项目在前）
    for tos_version, projects in tos_groups.items():
        # 将tOS项目移到列表前面
        tos_projects = [p for p in projects if p.startswith('tOS16.')]
        other_projects = [p for p in projects if not p.startswith('tOS16.')]
        tos_groups[tos_version] = tos_projects + sorted(other_projects)

    return tos_groups


def extract_summary_pattern(project_key, jql):
    """从JQL中提取summary模式（如summary ~ X6840）

    Args:
        project_key: 项目键（如X6840）
        jql: JQL查询字符串

    Returns:
        str: summary模式，如"summary ~ X6840"，如果找不到则返回None
    """
    import re

    # 方法1：直接搜索 summary ~ pattern
    pattern1 = r'summary\s*~\s*([^\s\)]+)'
    matches1 = re.findall(pattern1, jql, re.IGNORECASE)
    if matches1:
        # 返回第一个匹配的summary模式
        return f"summary ~ {matches1[0]}"

    # 方法2：如果找不到，尝试从项目键推断
    # 有些JQL中summary条件可能和AND连接在一起，如"project = tOS16.1 AND summary ~ X6840"
    # 我们已经匹配了这种情况

    # 方法3：如果还是找不到，尝试从项目键生成
    # 有些项目键可能包含特殊字符，需要清理
    clean_project_key = project_key.replace('-tOS16', '').replace('-tOS16.2', '').replace('-tOS16.1', '').replace('-tOS16.3', '')
    if clean_project_key and clean_project_key != project_key:
        return f"summary ~ {clean_project_key}"

    return None


def create_batch_jql_queries(tos_groups, batch_size=3):
    """为每个tOS版本组创建批量JQL查询（每batch_size个项目一批）

    Args:
        tos_groups (dict): 按tOS版本分组的项目字典
        batch_size (int): 每批的项目数量，默认为3

    Returns:
        list: 批量JQL查询列表，每个元素为 (batch_id, tos_version, project_keys, jql)
    """
    batch_queries = []
    batch_counter = 0
    _log("debug", f"create_batch_jql_queries: tos_groups keys: {list(tos_groups.keys())}")

    # 从所有模板中收集JQL模板
    if not JQL_TEMPLATES:
        return batch_queries

    # 创建一个合并的模板项目字典（从所有模板中提取）
    all_template_projects = {}
    for template in JQL_TEMPLATES:
        projects = template.get('projects', {})
        for project_key, jql in projects.items():
            # 只添加尚未添加的项目，避免重复
            if project_key not in all_template_projects:
                all_template_projects[project_key] = jql

    for tos_version, projects in tos_groups.items():
        # 将项目分成批次（每batch_size个一批）
        for i in range(0, len(projects), batch_size):
            batch_projects = projects[i:i+batch_size]
            batch_id = f"batch_{batch_counter}"
            batch_counter += 1

            # 为这批项目生成JQL查询
            # 新模板格式： (project = XXX OR project = YYY OR project = ZZZ OR project = tOS16.X) AND (summary ~ XXX OR summary ~ YYY OR summary ~ ZZZ) AND type = Bug AND reporter in (membersOf(RT_交付测试部)) AND creator != IssueCarrier ORDER BY created DESC
            if batch_projects:
                # 收集项目条件和summary条件
                project_conditions = []
                summary_conditions = []

                # 确保包含tOS项目条件（根据用户模板要求）
                project_conditions.append(f"project = {tos_version}")

                for project_key in batch_projects:
                    project_jql = all_template_projects.get(project_key, '')

                    # 生成项目条件
                    if project_key.startswith('tOS16.'):
                        # tOS项目：直接使用 project = tOS16.X
                        project_conditions.append(f"project = {project_key}")
                        # tOS项目没有summary条件
                    else:
                        # 整机项目：需要生成适当的项目条件
                        # 从JQL模板中提取项目条件，过滤掉-Aee后缀的项目
                        if project_jql:
                            # 尝试提取项目条件模式
                            import re
                            # 匹配 project = XXX-tOS16 或 project = XXX-tOS16.2 等
                            project_patterns = re.findall(r'project\s*=\s*([^\s\)]+)', project_jql)
                            if project_patterns:
                                # 过滤掉-Aee后缀的项目，只保留-tOS16、-tOS16.2、-tOS16.3格式
                                filtered_patterns = []
                                for pattern in project_patterns:
                                    # 排除-Aee后缀的项目
                                    if not pattern.endswith('-Aee') and not pattern.endswith('-AEE'):
                                        # 保留-tOS16、-tOS16.2、-tOS16.3格式，以及tOS16.X格式
                                        if pattern.endswith('-tOS16') or pattern.endswith('-tOS16.2') or pattern.endswith('-tOS16.3') or 'tOS16.' in pattern:
                                            filtered_patterns.append(pattern)

                                # 如果没有找到合适的项目模式，使用默认格式
                                if not filtered_patterns:
                                    # 使用默认格式：项目键 + -tOS16
                                    filtered_patterns.append(f"{project_key}-tOS16")

                                # 添加过滤后的项目模式
                                for pattern in filtered_patterns:
                                    if pattern not in project_conditions:
                                        project_conditions.append(f"project = {pattern}")
                            else:
                                # 默认格式
                                project_conditions.append(f"project = {project_key}-tOS16")
                        else:
                            # 默认格式
                            project_conditions.append(f"project = {project_key}-tOS16")

                        # 提取summary条件
                        if project_jql:
                            summary_pattern = extract_summary_pattern(project_key, project_jql)
                            if summary_pattern:
                                summary_conditions.append(summary_pattern)
                        else:
                            # 默认summary条件
                            summary_conditions.append(f"summary ~ {project_key}")

                # 确保至少有一个项目条件
                if not project_conditions:
                    _log("warn", f"批次 {batch_id} 没有项目条件")
                    continue

                # 去重项目条件
                project_conditions = list(set(project_conditions))

                # 构建项目条件子句
                if len(project_conditions) == 1:
                    project_clause = f"({project_conditions[0]})"
                else:
                    project_clause = f"({' OR '.join(project_conditions)})"

                # 构建summary条件子句（如果有summary条件）
                summary_clause = ""
                if summary_conditions:
                    # 去重summary条件
                    summary_conditions = list(set(summary_conditions))
                    if len(summary_conditions) == 1:
                        summary_clause = f" AND ({summary_conditions[0]})"
                    else:
                        summary_clause = f" AND ({' OR '.join(summary_conditions)})"

                # 提取基础条件（从第一个非tOS项目或第一个项目）
                base_jql = None
                for project_key in batch_projects:
                    if not project_key.startswith('tOS16.'):
                        base_jql = all_template_projects.get(project_key, '')
                        if base_jql:
                            break

                # 如果没找到，使用第一个项目
                if not base_jql and batch_projects:
                    base_jql = all_template_projects.get(batch_projects[0], '')

                if base_jql:
                    # 提取基础条件（type = Bug 之后的部分），并移除时间条件
                    # 查找 "type = Bug" 之后的部分
                    type_index = base_jql.find('type = Bug')
                    if type_index != -1:
                        # 找到 "type = Bug" 之后的部分
                        type_and_index = base_jql.find(' AND ', type_index)
                        if type_and_index != -1:
                            # 从 "type = Bug" 开始
                            base_conditions = base_jql[type_index:]
                        else:
                            # 如果找不到 AND，使用剩余部分
                            base_conditions = base_jql[type_index:]
                    else:
                        # 备用方法：查找第一个 AND 之后的部分
                        and_index = base_jql.find(' AND ')
                        if and_index != -1:
                            base_conditions = base_jql[and_index:]
                        else:
                            base_conditions = " AND type = Bug AND reporter in (membersOf(RT_交付测试部)) AND creator != IssueCarrier ORDER BY created DESC"

                    # 移除时间条件（createdDate >= startOfDay() 或 created >= {start} 等）
                    import re
                    # 移除 createdDate 条件
                    base_conditions = re.sub(r'AND\s+createdDate\s*>=\s*startOfDay\(\)\s*AND\s+createdDate\s*<=\s*endOfDay\(\)', '', base_conditions, flags=re.IGNORECASE)
                    base_conditions = re.sub(r'AND\s+createdDate\s*>=\s*startOfDay\(\)\s*AND\s+createdDate\s*<=\s*endOfDay\(\)\s*AND', ' AND', base_conditions, flags=re.IGNORECASE)
                    base_conditions = re.sub(r'AND\s+createdDate\s*>=\s*startOfDay\(\)', '', base_conditions, flags=re.IGNORECASE)
                    base_conditions = re.sub(r'AND\s+createdDate\s*<=\s*endOfDay\(\)', '', base_conditions, flags=re.IGNORECASE)
                    # 移除 created 条件（用于模板中的时间条件）
                    base_conditions = re.sub(r'AND\s+created\s*>=\s*\{start\}\s*AND\s+created\s*<=\s*\{end\}', '', base_conditions, flags=re.IGNORECASE)
                    base_conditions = re.sub(r'AND\s+created\s*>=\s*\{start\}', '', base_conditions, flags=re.IGNORECASE)
                    base_conditions = re.sub(r'AND\s+created\s*<=\s*\{end\}', '', base_conditions, flags=re.IGNORECASE)
                    # 移除可能残留的连续 AND
                    base_conditions = re.sub(r'AND\s+AND', ' AND', base_conditions)
                    base_conditions = re.sub(r'\s+AND\s+$', '', base_conditions)
                    base_conditions = re.sub(r'^\s+AND\s+', '', base_conditions)

                    # 确保 ORDER BY 是 DESC（根据用户模板）
                    if 'ORDER BY created ASC' in base_conditions:
                        base_conditions = base_conditions.replace('ORDER BY created ASC', 'ORDER BY created DESC')
                    elif 'ORDER BY created' not in base_conditions:
                        # 如果没有 ORDER BY，添加 DESC
                        base_conditions = base_conditions + ' ORDER BY created DESC'

                    # 构建完整JQL
                    jql = f"{project_clause}{summary_clause} AND {base_conditions}"

                    batch_queries.append({
                        "batch_id": batch_id,
                        "tos_version": tos_version,
                        "projects": batch_projects,
                        "jql": jql,
                        "project_count": len(batch_projects),
                        "summary_conditions": summary_conditions
                    })
                else:
                    _log("warn", f"找不到批次 {batch_id} 的基础JQL模板")

    return batch_queries


def analyze_projects_by_batch(intent="风险分析", time_range="全部"):
    """按tOS版本批次分析所有项目

    Args:
        intent: 分析意图（如"风险分析"）
        time_range: 时间范围（默认"全部"）

    Returns:
        dict: 包含批次分析结果的字典
    """
    import concurrent.futures

    # 1. 按tOS版本分组项目
    tos_groups = group_projects_by_tos_version()
    _log("info", f"按tOS版本分组结果: 版本数量={len(tos_groups)}, 详情={tos_groups}")

    # 2. 创建批量JQL查询（每3个项目一批）
    batch_queries = create_batch_jql_queries(tos_groups, batch_size=3)
    _log("info", f"创建了 {len(batch_queries)} 个批量查询")

    results = {
        "batch_results": [],
        "summary_by_tos": {},
        "overall_summary": {}
    }

    # 3. 并行执行批量查询
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_batch = {}

        for batch in batch_queries:
            future = executor.submit(
                analyze_batch_issues,
                batch_jql=batch["jql"],
                batch_id=batch["batch_id"],
                tos_version=batch["tos_version"],
                projects=batch["projects"],
                intent=intent,
                time_range=time_range
            )
            future_to_batch[future] = batch

        # 收集结果
        for future in concurrent.futures.as_completed(future_to_batch):
            batch = future_to_batch[future]
            try:
                batch_result = future.result()
                results["batch_results"].append(batch_result)
                _log("info", f"批次 {batch['batch_id']} 分析完成")
            except Exception as e:
                _log("err", f"批次 {batch['batch_id']} 分析失败: {e}")
                results["batch_results"].append({
                    "batch_id": batch["batch_id"],
                    "tos_version": batch["tos_version"],
                    "projects": batch["projects"],
                    "error": str(e)
                })

    # 4. 按tOS版本汇总结果
    for tos_version in tos_groups.keys():
        tos_batches = [r for r in results["batch_results"] if r.get("tos_version") == tos_version]
        total_issues = sum(b.get("total_issues", 0) for b in tos_batches if "total_issues" in b)
        unresolved_issues = sum(b.get("unresolved_issues", 0) for b in tos_batches if "unresolved_issues" in b)
        high_risk = sum(b.get("high_risk_count", 0) for b in tos_batches if "high_risk_count" in b)
        medium_risk = sum(b.get("medium_risk_count", 0) for b in tos_batches if "medium_risk_count" in b)
        low_risk = sum(b.get("low_risk_count", 0) for b in tos_batches if "low_risk_count" in b)

        results["summary_by_tos"][tos_version] = {
            "total_issues": total_issues,
            "unresolved_issues": unresolved_issues,
            "high_risk_count": high_risk,
            "medium_risk_count": medium_risk,
            "low_risk_count": low_risk,
            "resolution_rate": round((total_issues - unresolved_issues) / total_issues * 100, 2) if total_issues > 0 else 0,
            "batch_count": len(tos_batches)
        }

    # 5. 整体汇总
    all_total_issues = sum(s["total_issues"] for s in results["summary_by_tos"].values())
    all_unresolved_issues = sum(s["unresolved_issues"] for s in results["summary_by_tos"].values())
    all_high_risk = sum(s["high_risk_count"] for s in results["summary_by_tos"].values())
    all_medium_risk = sum(s["medium_risk_count"] for s in results["summary_by_tos"].values())
    all_low_risk = sum(s["low_risk_count"] for s in results["summary_by_tos"].values())

    results["overall_summary"] = {
        "total_issues": all_total_issues,
        "unresolved_issues": all_unresolved_issues,
        "high_risk_count": all_high_risk,
        "medium_risk_count": all_medium_risk,
        "low_risk_count": all_low_risk,
        "resolution_rate": round((all_total_issues - all_unresolved_issues) / all_total_issues * 100, 2) if all_total_issues > 0 else 0,
        "tos_version_count": len(tos_groups),
        "batch_count": len(batch_queries)
    }

    # 计算总项目数量（所有批次中项目的总和）
    total_projects = sum(len(batch.get('projects', [])) for batch in results["batch_results"])
    results["total_projects"] = total_projects
    results["total_batches"] = len(batch_queries)

    return results


def analyze_batch_issues(batch_jql, batch_id, tos_version, projects, intent, time_range):
    """分析单个批次的问题

    Args:
        batch_jql: 批次的JQL查询
        batch_id: 批次ID
        tos_version: tOS版本
        projects: 项目列表
        intent: 分析意图
        time_range: 时间范围

    Returns:
        dict: 批次分析结果（包含原始issue数据，向后兼容）
    """
    try:
        # 获取数据
        raw_issues = fetch_all_issues(batch_jql)
        _log("debug", f"批次 {batch_id} 获取到 {len(raw_issues)} 个问题")

        # 风险级别统计
        risk_counts = {"高": 0, "中": 0, "低": 0}
        for issue in raw_issues:
            fields = issue.get('fields', {})
            priority = fields.get('priority', {}).get('name', '无') if fields.get('priority') else '无'
            labels = fields.get('labels', [])
            summary = fields.get('summary', '')
            risk_level = get_risk_level(priority, labels, summary)
            risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

        # 未解决问题
        unresolved_issues = [issue for issue in raw_issues if
                           issue.get('fields', {}).get('resolution') is None]

        # 按项目分组 issue 明细（取优先级最高的前30条，用于后续详细分析）
        issues_detail = []
        for issue in raw_issues[:45]:  # 每批次最多提取45条明细
            fields = issue.get('fields', {})
            issues_detail.append({
                "key": issue.get('key', ''),
                "summary": fields.get('summary', ''),
                "priority": fields.get('priority', {}).get('name', '无') if fields.get('priority') else '无',
                "status": fields.get('status', {}).get('name', '未知') if fields.get('status') else '未知',
                "resolution": fields.get('resolution', {}).get('name', '') if fields.get('resolution') else '',
                "labels": fields.get('labels', []),
                "project_key": fields.get('project', {}).get('key', '') if fields.get('project') else '',
                "project_name": fields.get('project', {}).get('name', '') if fields.get('project') else '',
                "issuetype": fields.get('issuetype', {}).get('name', '') if fields.get('issuetype') else '',
                "risk_level": get_risk_level(
                    fields.get('priority', {}).get('name', '无') if fields.get('priority') else '无',
                    fields.get('labels', []),
                    fields.get('summary', '')
                )
            })

        return {
            "batch_id": batch_id,
            "tos_version": tos_version,
            "projects": projects,
            "total_issues": len(raw_issues),
            "unresolved_issues": len(unresolved_issues),
            "high_risk_count": risk_counts["高"],
            "medium_risk_count": risk_counts["中"],
            "low_risk_count": risk_counts["低"],
            "resolution_rate": round((len(raw_issues) - len(unresolved_issues)) / len(raw_issues) * 100, 2) if len(raw_issues) > 0 else 0,
            "sample_issues": [issue.get('key', '') for issue in raw_issues[:3]] if raw_issues else [],
            "issues_detail": issues_detail
        }
    except Exception as e:
        _log("err", f"分析批次 {batch_id} 时出错: {e}")
        raise


# ========== 1. 配置 Jira 连接信息（自托管版） ==========
JIRA_CONFIG = {'server': 'http://jira.transsion.com'}
JIRA_USERNAME = os.getenv("JIRA_USERNAME")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD")
JIRA_URL = os.getenv("JIRA_URL", JIRA_CONFIG["server"])
_log("info", f"Jira配置: URL={JIRA_URL}, 用户={JIRA_USERNAME}, 密码={'已设置' if JIRA_PASSWORD else '未设置'}")

# ========== 2. 配置 AI 服务信息 ==========
# 走你提供的内网代理（tranai-proxy）
AI_BASE_URL = "https://hk-intra-paas.transsion.com/tranai-proxy/v1"

# AI配置 - 必须通过环境变量设置
AI_API_KEY = os.getenv("AI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.4")

# 供代理鉴权的头信息 - 必须通过环境变量设置
X_USER_NO = os.getenv("X_USER_NO", "")
X_USER_NAME = os.getenv("X_USER_NAME", "")
X_USER_DEPT_NAME = os.getenv("X_USER_DEPT_NAME", "")

# 配置验证
if not AI_API_KEY:
    print("❌ 错误: AI_API_KEY 环境变量未设置!")
    print("💡 请创建 .env 文件并设置 AI_API_KEY=您的AI密钥")
    print("💡 或运行: export AI_API_KEY=您的AI密钥 (Linux/Mac)")
    print("💡 或运行: set AI_API_KEY=您的AI密钥 (Windows CMD)")
    print("💡 或运行: $env:AI_API_KEY='您的AI密钥' (Windows PowerShell)")
    exit(1)

if not X_USER_NO:
    print("⚠️  警告: X_USER_NO 环境变量未设置，AI鉴权可能失败")

if not X_USER_NAME:
    print("⚠️  警告: X_USER_NAME 环境变量未设置，AI鉴权可能失败")

if not X_USER_DEPT_NAME:
    print("⚠️  警告: X_USER_DEPT_NAME 环境变量未设置，AI鉴权可能失败")

_log("info", f"使用 AI 模型: {AI_MODEL}")
# ========== 自定义 httpx 客户端，添加需要的请求头 ==========
def add_custom_headers(request: httpx.Request) -> None:
    """在每次请求前添加自定义头"""
    # 使用服务器共享身份，确保所有用户都能访问AI服务
    server_user_no = "JIRA_RISK_SERVER"
    server_user_name = "Jira风险分析服务器"
    server_user_dept_name = "公共分析服务"

    request.headers["Authorization"] = f"Bearer {AI_API_KEY}"
    request.headers["X-USER-NO"] = server_user_no
    request.headers["X-USER-NAME"] = server_user_name
    request.headers["X-USER-DEPT-NAME"] = server_user_dept_name

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
    # 从输入中提取项目键，支持X6840、CN6、CN6C、tOS16.1、tOS16.2、tOS16.3等格式
    # 优先匹配带点号的版本号（如tOS16.3）
    match = re.search(r'[A-Za-z]+\d+(?:\.\d+)?|X?\d{4}(?:-[a-zA-Z0-9]+)?', input_str)
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
    fallback_jql = f"summary ~ '{project}' ORDER BY priority DESC"
    _log("info", f"降级 JQL: {fallback_jql}")
    return fallback_jql

def fetch_tos_issues(time_range=None):
    """拉取所有tOS项目的未解决问题"""
    # 根据实际Jira项目键调整，从日志看tOS16可能不存在，但tOS16.1存在
    tos_projects = ["tOS16.1", "tOS16.2", "tOS16.3"]  # 根据实际调整
    jql = f"project in ({','.join(tos_projects)}) AND resolution is empty"

    # 如果提供了时间范围，添加时间条件
    if time_range:
        start, end = parse_time_range(time_range)
        if start and end:
            jql += f" AND created >= {start} AND created <= {end}"

    _log("info", f"tOS项目JQL: {jql}")
    return fetch_all_issues(jql)

def cluster_common_issues(issues):
    """按模块关键词聚类"""
    clusters = defaultdict(list)
    for issue in issues:
        summary = issue['fields']['summary']
        match = re.search(r'\[(.*?)\]', summary)
        module = match.group(1) if match else summary.split()[0][:20]
        clusters[module].append(issue['key'])
    return {k: v for k, v in clusters.items() if len(v) >= 2}

# 从Jira获取全量问题（分页）
def fetch_all_issues(jql: str, username=None, password=None, url=None, max_fetch=None) -> list:
    """从Jira获取全量问题（分页）

    Args:
        jql: JQL查询语句
        username: Jira用户名
        password: Jira密码
        url: Jira URL
        max_fetch: 最大获取条数（超出后停止），None表示获取全量
    """
    # 记录传入的参数
    _log("debug", f"fetch_all_issues被调用，参数: username={username}, password={'***' if password else 'None'}, url={url}")
    _log("debug", f"传入的JQL: {jql}")

    # 使用传入的凭据或全局变量
    _username = username or JIRA_USERNAME
    _password = password or JIRA_PASSWORD
    _url = url or JIRA_URL

    # 检查Jira环境变量
    _log("debug", f"JIRA_USERNAME全局: {'已设置' if JIRA_USERNAME else '未设置'} ({JIRA_USERNAME[:3] if JIRA_USERNAME else 'None'}...), JIRA_PASSWORD全局: {'已设置' if JIRA_PASSWORD else '未设置'}, JIRA_URL: {JIRA_URL}")
    _log("debug", f"最终使用的凭据: username={_username}, password={'***' if _password else 'None'}, url={_url}")
    if not _username or not _password or not _url:
        _log("error", "Jira环境变量未设置！请设置JIRA_USERNAME、JIRA_PASSWORD、JIRA_URL环境变量。")
        return []

    # 记录使用的凭据
    _log("info", f"fetch_all_issues 使用凭据: username={_username}, url={_url}")

    # 先测试Jira服务器连接
    _log("info", "测试Jira服务器连接...")
    try:
        import socket
        from urllib.parse import urlparse

        parsed_url = urlparse(_url)
        jira_host = parsed_url.hostname
        jira_port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)

        # 测试TCP连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((jira_host, jira_port))
        sock.close()

        if result != 0:
            _log("error", f"Jira服务器连接失败: {jira_host}:{jira_port} (错误代码: {result})")
            # 仍然尝试HTTP请求，但记录警告
            _log("warn", "TCP连接失败，但仍尝试HTTP请求")
    except Exception as e:
        _log("warn", f"Jira连接测试异常: {e}")

    all_issues = []
    start_at = 0
    max_results = 100  # 单次最大可取100

    # 构建认证头
    auth_str = f"{_username}:{_password}"
    auth_bytes = auth_str.encode("ascii")
    base64_auth = base64.b64encode(auth_bytes).decode("ascii")

    headers = {
        "Authorization": f"Basic {base64_auth}",
        "Content-Type": "application/json"
    }

    while True:
        url = f"{_url}/rest/api/2/search"

        # 直接使用原始JQL，requests会自动处理URL编码
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
            "fields": "summary,status,priority,issuetype,assignee,created,updated,resolutiondate,resolution,labels,key,customfield_10000,customfield_10001,customfield_10002,affectsVersions,components"
        }

        # 添加调试日志
        _log("info", f"发送请求到: {url}")
        _log("info", f"JQL: {jql}")

        try:
            # 添加重试逻辑
            max_retries = 2
            retry_count = 0
            response = None

            while retry_count <= max_retries:
                try:
                    _log("info", f"尝试请求Jira (尝试 {retry_count + 1}/{max_retries + 1})...")
                    response = requests.get(
                        url,
                        headers=headers,
                        params=params,
                        verify=False,
                        timeout=15  # 减少超时时间
                    )
                    break  # 请求成功，退出重试循环
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    retry_count += 1
                    if retry_count <= max_retries:
                        _log("warn", f"Jira请求超时或连接错误，正在重试 ({retry_count}/{max_retries}): {e}")
                        import time
                        time.sleep(1)  # 等待1秒后重试
                    else:
                        raise e  # 重试次数用完，抛出异常

            _log("info", f"响应状态码: {response.status_code}")
            if response.status_code != 200:
                _log("err", f"请求 Jira 失败，状态码：{response.status_code}")
                _log("err", f"错误响应: {response.text[:1000]}")
                # 尝试解析错误信息
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict):
                        error_messages = []
                        if 'errorMessages' in error_data:
                            error_messages.extend(error_data['errorMessages'])
                        if 'errors' in error_data:
                            error_messages.extend([f"{k}: {v}" for k, v in error_data['errors'].items()])
                        if error_messages:
                            _log("err", f"Jira错误信息: {' | '.join(error_messages)}")
                except:
                    pass
                break
            else:
                _log("info", f"响应内容: {response.text[:500]}")
                data = response.json()
            issues = data.get("issues", [])
            _log("info", f"本次获取到 {len(issues)} 条问题，累计 {len(all_issues) + len(issues)} 条")
            all_issues.extend(issues)

            if max_fetch and len(all_issues) >= max_fetch:
                _log("warn", f"已达到最大获取条数限制 ({max_fetch})，停止获取")
                break

            if len(all_issues) >= data.get("total", 0):
                break

            start_at += max_results
        except requests.exceptions.Timeout as e:
            _log("err", f"Jira请求超时: {e}")
            break
        except requests.exceptions.ConnectionError as e:
            _log("err", f"Jira连接错误: {e}")
            break
        except Exception as e:
            _log("err", f"获取 Jira 数据失败: {e}")
            import traceback
            _log("err", f"详细错误: {traceback.format_exc()}")
            break

    _log("info", f"共获取到 {len(all_issues)} 条问题")
    return all_issues

# ── 自定义字段常量（如需修改请在此处调整） ──
BUSINESS_DOMAIN_FIELD = "customfield_10002"  # Business Domain 业务领域


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
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import json

# ========== 数据库初始化 ==========
Base = declarative_base()

class IPAccessRecord(Base):
    """IP访问记录表"""
    __tablename__ = 'ip_access_records'

    ip = Column(String(45), primary_key=True)  # IPv4或IPv6
    username = Column(String(100))
    department = Column(String(100))
    access_level = Column(String(20), default='user')  # guest, user, admin
    permissions = Column(Text, default='')  # JSON字符串，存储功能权限列表
    jira_bound = Column(Boolean, default=False)
    first_access = Column(DateTime)
    last_access = Column(DateTime)
    last_updated = Column(DateTime)

    def to_dict(self):
        return {
            'ip': self.ip,
            'username': self.username,
            'department': self.department,
            'access_level': self.access_level,
            'permissions': self.permissions,
            'jira_bound': self.jira_bound,
            'first_access': self.first_access.isoformat() if self.first_access else None,
            'last_access': self.last_access.isoformat() if self.last_access else None,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }

class JiraCredential(Base):
    """Jira凭据表"""
    __tablename__ = 'jira_credentials'

    ip = Column(String(45), primary_key=True)
    username = Column(String(100))
    password = Column(String(500))  # 加密存储
    department = Column(String(100))
    last_updated = Column(DateTime)

class AccessLog(Base):
    """访问审计日志表"""
    __tablename__ = 'access_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime)
    ip = Column(String(45))
    path = Column(String(500))
    method = Column(String(10))
    status = Column(String(20))  # allowed, denied
    message = Column(Text)

# 初始化数据库
engine = create_engine('sqlite:///ip_access.db', echo=False)
Base.metadata.create_all(engine)
SessionFactory = sessionmaker(bind=engine)
Session = scoped_session(SessionFactory)

# 线程局部存储数据库会话
db_session = Session()

# 简单内存会话存储（生产环境可换 Redis）
conversation_history = {}   # key: conversation_id, value: list of messages
MAX_HISTORY_MESSAGES = 20   # 最多保留多少条历史消息（每轮包含 user 和 assistant）

app = Flask(__name__)
CORS(app)  # 添加CORS支持

# ========== 数据库辅助函数 ==========
def get_ip_access_record(ip):
    """获取IP访问记录"""
    from datetime import datetime
    record = db_session.query(IPAccessRecord).filter_by(ip=ip).first()
    if record:
        # 检查是否过期（7天）
        if record.last_access:
            expiry_seconds = (datetime.now() - record.last_access).total_seconds()
            if expiry_seconds > ACCESS_EXPIRY_SECONDS:
                db_session.delete(record)
                db_session.commit()
                return None
    return record

def update_ip_access_record(ip, username=None, department=None, access_level=None,
                           jira_bound=None, permissions=None):
    """更新或创建IP访问记录"""
    from datetime import datetime
    record = db_session.query(IPAccessRecord).filter_by(ip=ip).first()
    now = datetime.now()

    if not record:
        record = IPAccessRecord(
            ip=ip,
            username=username or '',
            department=department or '',
            access_level=access_level or DEFAULT_ACCESS_LEVEL,
            permissions=permissions or '',
            jira_bound=jira_bound or False,
            first_access=now,
            last_access=now,
            last_updated=now
        )
        db_session.add(record)
    else:
        if username is not None:
            record.username = username
        if department is not None:
            record.department = department
        if access_level is not None:
            record.access_level = access_level
        if jira_bound is not None:
            record.jira_bound = jira_bound
        if permissions is not None:
            record.permissions = permissions
        record.last_access = now
        record.last_updated = now

    db_session.commit()
    return record

def delete_ip_access_record(ip):
    """删除IP访问记录"""
    record = db_session.query(IPAccessRecord).filter_by(ip=ip).first()
    if record:
        db_session.delete(record)
        db_session.commit()
        return True
    return False

def get_jira_credential(ip):
    """获取Jira凭据"""
    return db_session.query(JiraCredential).filter_by(ip=ip).first()

def save_jira_credential(ip, username, password, department):
    """保存Jira凭据"""
    from datetime import datetime
    credential = db_session.query(JiraCredential).filter_by(ip=ip).first()
    now = datetime.now()

    if not credential:
        credential = JiraCredential(
            ip=ip,
            username=username,
            password=password,  # 注意：实际应用中应该加密
            department=department,
            last_updated=now
        )
        db_session.add(credential)
    else:
        credential.username = username
        credential.password = password
        credential.department = department
        credential.last_updated = now

    db_session.commit()
    return credential

def delete_jira_credential(ip):
    """删除Jira凭据"""
    credential = db_session.query(JiraCredential).filter_by(ip=ip).first()
    if credential:
        db_session.delete(credential)
        db_session.commit()
        return True
    return False

def add_access_log(ip, path, method, status, message=""):
    """添加访问审计日志"""
    from datetime import datetime
    log = AccessLog(
        timestamp=datetime.now(),
        ip=ip,
        path=path,
        method=method,
        status=status,
        message=message
    )
    db_session.add(log)
    db_session.commit()

    # 自动清理旧日志（保留最近10000条）
    try:
        count = db_session.query(AccessLog).count()
        if count > 10000:
            # 删除最旧的5000条
            old_logs = db_session.query(AccessLog).order_by(AccessLog.timestamp).limit(5000).all()
            for log in old_logs:
                db_session.delete(log)
            db_session.commit()
    except Exception:
        pass

def get_admin_ips():
    """获取管理员IP列表"""
    records = db_session.query(IPAccessRecord).filter_by(access_level='admin').all()
    return {record.ip for record in records}

def is_admin_ip(ip):
    """检查IP是否为管理员"""
    record = db_session.query(IPAccessRecord).filter_by(ip=ip, access_level='admin').first()
    return record is not None

def promote_to_admin(ip):
    """提升IP为管理员"""
    record = db_session.query(IPAccessRecord).filter_by(ip=ip).first()
    if record:
        record.access_level = 'admin'
        db_session.commit()
        return True
    return False

def revoke_admin(ip):
    """撤销管理员权限"""
    record = db_session.query(IPAccessRecord).filter_by(ip=ip).first()
    if record and record.access_level == 'admin':
        record.access_level = 'user'
        db_session.commit()
        return True
    return False

# ========== IP访问控制系统 ==========
import threading

# 访问控制配置（常量）
CREDENTIALS_EXPIRY_SECONDS = 24 * 60 * 60  # 凭据过期时间（24小时）
ACCESS_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 访问权限过期时间（7天）
ACCESS_LEVELS = {
    'guest': ['GET /', 'GET /static/', 'POST /api/auth/jira', 'GET /api/test'],
    'user': ['* /api/knowledge/*', '* /api/analyze', 'GET /api/auth/jira/list'],
    'admin': ['* /api/admin/*', 'POST /api/admin/access/revoke', 'GET /api/admin/access/list', 'POST /api/admin/access/promote', 'GET /api/admin/access/log']
}
DEFAULT_ACCESS_LEVEL = 'user'  # 默认权限级别

# 功能权限定义（为未来细粒度权限控制预留）
FEATURE_PERMISSIONS = {
    'ai_access': '访问AI功能',
    'knowledge_access': '访问知识库',
    'analyze_access': '访问分析功能',
    'admin_panel': '访问管理面板',
    'jira_integration': 'Jira集成'
}

# 路径到功能权限的映射（为未来细粒度权限控制预留）
PATH_TO_FEATURE_MAP = {
    '/api/analyze': 'analyze_access',
    '/api/knowledge/': 'knowledge_access',
    '/api/admin/': 'admin_panel'
}

def get_client_ip():
    """获取客户端IP地址，支持代理"""
    # 尝试从常见代理头获取IP
    if request.headers.get('X-Forwarded-For'):
        # X-Forwarded-For: client, proxy1, proxy2
        xff = request.headers.get('X-Forwarded-For')
        ip = xff.split(',')[0].strip()
        return ip
    elif request.headers.get('X-Real-IP'):
        ip = request.headers.get('X-Real-IP')
        return ip
    else:
        ip = request.remote_addr
        return ip

def log_access(client_ip, path, method, status, message=""):
    """记录访问审计日志（使用数据库存储）"""
    add_access_log(client_ip, path, method, status, message)

def check_access_permission(client_ip, path, method):
    """检查IP是否有权限访问指定路径（使用数据库存储）"""
    # 1. 检查是否是管理员IP
    if is_admin_ip(client_ip):
        return True, "管理员IP"

    # 2. 公开路由不需要检查
    public_paths = ['/', '/static/', '/api/test', '/api/auth/jira']

    # 特殊处理根路径
    if path == '/':
        return True, "公开路由"

    # 检查其他公开路径
    for public_path in public_paths:
        if public_path != '/' and path.startswith(public_path):
            return True, "公开路由"

    # 3. 检查IP是否有访问记录（数据库）
    record = get_ip_access_record(client_ip)
    if not record:
        return False, "IP不在访问白名单中"

    # 4. 检查功能权限（未来扩展）
    # 这里可以添加基于PATH_TO_FEATURE_MAP的细粒度权限检查

    # 5. 更新最后访问时间（已在get_ip_access_record中自动更新）
    return True, "访问允许"

def require_admin_access(f):
    """管理员权限装饰器（使用数据库存储）"""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = get_client_ip()
        if not is_admin_ip(client_ip):
            return jsonify({
                "success": False,
                "error": "需要管理员权限",
                "message": "只有管理员可以访问此端点"
            }), 403
        return f(*args, **kwargs)
    return decorated_function

# IP访问控制中间件
@app.before_request
def check_ip_access():
    """IP访问控制中间件"""
    client_ip = get_client_ip()
    path = request.path
    method = request.method

    # 检查访问权限
    allowed, message = check_access_permission(client_ip, path, method)

    if allowed:
        log_access(client_ip, path, method, "allowed", message)
    else:
        log_access(client_ip, path, method, "denied", message)
        return jsonify({
            "success": False,
            "error": "访问被拒绝",
            "message": message,
            "action_required": True,
            "auth_endpoint": "/api/auth/jira"
        }), 403

@app.route('/health')
def health_check():
    """健康检查端点"""
    try:
        # 简化健康检查，只返回基本状态
        from datetime import datetime

        return {
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'service': 'jira-risk-analysis',
            'version': '2.13',
            'endpoints': {
                'index': '/',
                'analyze': '/api/analyze',
                'script': '/script.js',
                'style': '/style.css',
                'status_test': '/status_test'
            }
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 500

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
        return Response(content, mimetype='application/javascript')
    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/status_test')
def status_test():
    """提供连接测试页面"""
    try:
        with open('status_test.html', 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/ping')
def ping():
    """最简单的ping端点，用于测试连接"""
    return {'status': 'ok', 'message': 'pong', 'timestamp': datetime.now().isoformat()}

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
    # 记录请求信息用于调试
    client_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referer = request.headers.get('Referer', 'None')

    _log("debug", f"🔍 API请求来自: IP={client_ip}, UA={user_agent[:50]}, Referer={referer}")
    _log("debug", f"🔍 请求参数: project_key={request.args.get('project_key', '')}, user_query={request.args.get('user_query', '')}")
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
            yield generate_sse_message('thinking', '🔍 正在解析查询意图... ')

            # 分析用户意图
            intent = recognize_intent(user_query)
            user_query_lower = user_query.lower()
            is_project_risk_query = False

            # 检查是否是一般性问题（优先于项目风险检测）
            general_questions = ['你好', '你能做什么', '帮助', '如何', '什么是', '为什么', '教程', '指南',
                                  '是否支持', '支持.*吗', '有没有.*功能', '是什么', '有哪些', '做什么用的',
                                  '规格', '参数', '配置', '特性', 'feature']
            is_general_question = False
            # 先检查精确关键词
            for question in general_questions:
                if re.search(question, user_query_lower):
                    is_general_question = True
                    break
            # 再检查"XX支持XX吗"、"XX是否XX"等问句模式
            if not is_general_question:
                support_patterns = [
                    r'.*支持.*吗', r'.*是否.*', r'.*有没有.*功能',
                    r'什么是.*', r'.*是什么', r'哪.*产品.*'
                ]
                for pattern in support_patterns:
                    if re.match(pattern, user_query):
                        is_general_question = True
                        break

            # 检查是否包含项目键（如X6840、X6878等）（仅当不是一般性问题时）
            if not is_general_question:
                project_match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', user_query)
                if project_match:
                    is_project_risk_query = True

            # 检查是否包含风险分析相关关键词
            risk_keywords = ['风险', '分析', 'bug', '问题', 'jira', '项目']
            for keyword in risk_keywords:
                if keyword in user_query_lower:
                    is_project_risk_query = True
                    break

            # 如果是一般性问题，直接回答
            if is_general_question:
                yield generate_sse_message('thinking', '💭 分析一般性问题... ')

                # 构建AI消息列表，包含历史对话
                messages = []
                for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                    messages.append(msg)
                messages.append({"role": "user", "content": user_query})

                # 调用AI（使用工具函数）
                response = call_ai_api(messages, system_prompt)

                if not response:
                    yield generate_sse_message('error', get_friendly_ai_error())
                    yield "data: [DONE]\n\n"
                    return

                # 解析AI流式输出（使用工具函数）
                full_response = process_sse_stream(response)

                # 解析标签内容（使用工具函数）
                thinking_content, answer_content = parse_thinking_answer(full_response)

                # 发送思考过程（逐字输出）
                if thinking_content:
                    for msg in send_thinking_chars(thinking_content):
                        yield msg
                yield generate_sse_message('thinking_complete', '思考过程完成，开始生成答案...')

                # 发送回答内容（逐字输出）
                for msg in send_answer_chars(answer_content):
                    yield msg

                # 将AI的完整回答存入历史
                history.append({"role": "assistant", "content": full_response})
                if len(history) > MAX_HISTORY_MESSAGES:
                    conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]

                yield "data: [DONE]\n\n"
                return

            # 检查是否是共性问题查询
            is_common_issue_query = '共性问题' in user_query or '共同问题' in user_query or '影响多个项目' in user_query

            if is_common_issue_query:
                yield generate_sse_message('thinking', '🔍 正在分析共性问题... ')

                # 拉取tOS库未解决问题
                yield generate_sse_message('thinking', '📊 正在从tOS库获取数据... ')
                try:
                    # 获取时间范围，如果是"全部"则不添加时间条件
                    time_range = intent.get('time_range')
                    if time_range and time_range.lower() != '全部':
                        issues_tos = fetch_tos_issues(time_range)
                        _log("info", f"使用时间范围拉取tOS问题: {time_range}")
                    else:
                        issues_tos = fetch_tos_issues()
                        _log("info", "拉取全部tOS问题")

                    common_clusters = cluster_common_issues(issues_tos)
                    _log("info", f"共性问题聚类成功: {len(common_clusters)} 个模块聚类")
                except Exception as e:
                    _log("warn", f"共性问题聚类失败: {e}")
                    common_clusters = {}

                if not common_clusters:
                    yield generate_sse_message('thinking', '⚠️ 未发现候选共性问题 ')
                    yield generate_sse_message('answer', '目前tOS库中未发现明显的共性问题模式。\n')
                    yield "data: [DONE]\n\n"
                    return

                # 构建AI分析上下文
                data_context = f"""## tOS库候选共性问题分析请求
用户查询: {user_query}

## 共性问题聚类结果（前10个模块）
"""
                cluster_count = 0
                for mod, keys in list(common_clusters.items())[:10]:
                    cluster_count += 1
                    data_context += f"- 模块「{mod}」出现{len(keys)}次，示例ID: {', '.join(keys[:3])}\n"

                data_context += f"""
## 分析要求
请分析以上候选共性问题，重点回答：
1. 这些模块中哪些可能是真正的共性问题？
2. 可能会影响哪些整机项目？
3. 本周新增的共性问题有哪些？
4. 建议的解决优先级和策略？

请提供专业、简洁的分析报告。
"""

                # 构建AI消息
                messages = []
                for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                    messages.append(msg)
                messages.append({"role": "user", "content": data_context})

                # 调用AI（使用工具函数）
                response = call_ai_api(messages, system_prompt)

                if not response:
                    yield generate_sse_message('error', get_friendly_ai_error())
                    yield "data: [DONE]\n\n"
                    return

                # 解析AI流式输出（使用工具函数）
                full_response = process_sse_stream(response)

                # 解析标签内容（使用工具函数）
                thinking_content, answer_content = parse_thinking_answer(full_response)

                # 发送思考过程（逐字输出）
                if thinking_content:
                    for msg in send_thinking_chars(thinking_content):
                        yield msg
                yield generate_sse_message('thinking_complete', '思考过程完成，开始生成答案...')

                # 发送回答内容（逐字输出）
                for msg in send_answer_chars(answer_content):
                    yield msg

                # 将AI的完整回答存入历史
                history.append({"role": "assistant", "content": full_response})
                if len(history) > MAX_HISTORY_MESSAGES:
                    conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]

                yield "data: [DONE]\n\n"
                return

            # 如果是项目风险查询，继续原有流程
            if is_project_risk_query:
                # 生成 JQL（全量数据）
                jql_all = generate_final_jql(user_query)
                jql_all_clean = jql_all.replace('\n', '').replace('\r', '')
                yield generate_sse_message('thinking', f'📋 生成的 JQL（全量）: {jql_all_clean} ')

                # 生成未解决JQL（用于风险分析）
                # 先复制全量JQL
                jql_unresolved = jql_all

                # 检查是否已包含resolution条件
                resolution_pattern = re.compile(r'\bresolution\s*=\s*[\w"\' ]+', re.IGNORECASE)
                if resolution_pattern.search(jql_unresolved):
                    # 如果已有resolution条件，替换为 resolution = Unresolved
                    jql_unresolved = resolution_pattern.sub('resolution = Unresolved', jql_unresolved)
                else:
                    # 如果没有resolution条件，添加 AND resolution = Unresolved
                    # 处理ORDER BY子句
                    if 'ORDER BY' in jql_unresolved.upper():
                        parts = jql_unresolved.split('ORDER BY')
                        before_order = parts[0].strip()
                        order_by = parts[1].strip()
                        # 确保before_order以AND结尾（如果非空）
                        if before_order and not before_order.upper().endswith(' AND'):
                            if not before_order.endswith(')'):
                                before_order += ' AND'
                        jql_unresolved = before_order + ' resolution = Unresolved ORDER BY ' + order_by
                    else:
                        # 没有ORDER BY子句，直接添加
                        if jql_unresolved and not jql_unresolved.endswith(' AND'):
                            jql_unresolved += ' AND'
                        jql_unresolved += ' resolution = Unresolved'

                jql_unresolved_clean = jql_unresolved.replace('\n', '').replace('\r', '')
                yield generate_sse_message('thinking', f'📋 生成的 JQL（未解决）: {jql_unresolved_clean} ')

                # 拉取全量数据（用于统计和趋势图）
                yield generate_sse_message('thinking', '⏳ 正在从 Jira 获取全量数据... ')
                raw_issues_all = fetch_all_issues(jql_all)

                if not raw_issues_all:
                    yield generate_sse_message('thinking', '⚠️ 未获取到 Jira 问题 ')
                    yield generate_sse_message('answer', '暂无问题反馈，请自行前往jira查看\n')
                    yield "data: [DONE]\n\n"
                    return

                # 拉取未解决数据（用于风险分析）
                yield generate_sse_message('thinking', '⏳ 正在从 Jira 获取未解决数据... ')
                _log("debug", f"未解决JQL: {jql_unresolved}")
                raw_issues_unresolved = fetch_all_issues(jql_unresolved)
                _log("debug", f"获取到未解决问题数: {len(raw_issues_unresolved)}")

                yield generate_sse_message('thinking', f'📊 数据统计: 全量{len(raw_issues_all)}个, 未解决{len(raw_issues_unresolved)}个 ')

                # 使用未解决数据进行风险分析
                raw_issues = raw_issues_unresolved

                # 如果未解决数据为空，尝试从全量数据中过滤未解决问题
                if not raw_issues and raw_issues_all:
                    _log("warn", f"未解决JQL返回空数据，从全量数据中过滤未解决问题")
                    # 过滤未解决问题（resolution为空或Unresolved）
                    unresolved_from_all = []
                    for issue in raw_issues_all:
                        fields = issue.get('fields', {})
                        resolution = fields.get('resolution')
                        if resolution is None or str(resolution).strip() == '':
                            unresolved_from_all.append(issue)

                    if unresolved_from_all:
                        _log("info", f"从全量数据中找到 {len(unresolved_from_all)} 个未解决问题")
                        raw_issues = unresolved_from_all
                    else:
                        _log("warn", "全量数据中也没有未解决问题")
            else:
                # 其他类型的问题，直接调用AI回答
                yield generate_sse_message('thinking', '💭 分析问题... ')

                # 构建AI消息列表，包含历史对话
                messages = []
                for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                    messages.append(msg)
                messages.append({"role": "user", "content": user_query})

                # 调用AI（使用工具函数）
                response = call_ai_api(messages, system_prompt)

                if not response:
                    yield generate_sse_message('error', get_friendly_ai_error())
                    yield "data: [DONE]\n\n"
                    return

                # 解析AI流式输出（使用工具函数）
                full_response = process_sse_stream(response)

                # 解析标签内容（使用工具函数）
                thinking_content, answer_content = parse_thinking_answer(full_response)

                # 发送思考过程（逐字输出）
                if thinking_content:
                    for msg in send_thinking_chars(thinking_content):
                        yield msg
                yield generate_sse_message('thinking_complete', '思考过程完成，开始生成答案...')

                # 发送回答内容（逐字输出）
                for msg in send_answer_chars(answer_content):
                    yield msg

                # 将AI的完整回答存入历史
                history.append({"role": "assistant", "content": full_response})
                if len(history) > MAX_HISTORY_MESSAGES:
                    conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]

                yield "data: [DONE]\n\n"
                return

            # 数据清洗和统计（全量数据用于前端展示和趋势图）
            issues_list_all = []  # 全量结构化issues列表供前端使用
            status_counts_all = {}
            priority_bucket_counts_all = {"高": 0, "中": 0, "低": 0, "无": 0}
            risk_level_counts_all = {"高": 0, "中": 0, "低": 0}

            # 走势图数据：最近15天的提交和验证统计
            from datetime import datetime, timedelta

            # 生成最近15天的日期列表（从14天前到今天）
            today = datetime.now().date()
            date_list = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(14, -1, -1)]

            # 初始化走势图字典
            submission_trend = {date: 0 for date in date_list}
            verification_trend = {date: 0 for date in date_list}

            _log("debug", f"走势图日期范围: {date_list[0]} 到 {date_list[-1]}")

            # 解决率统计
            total_resolved = 0
            total_all = len(raw_issues_all)

            # 阻塞问题、MP Block、交付风险统计
            blocking_total = 0  # 总阻塞问题数（包括已解决和未解决）
            blocking_resolved = 0  # 已解决的阻塞问题数
            mp_block_total = 0  # MP Block问题总数
            delivery_risk_total = 0  # 交付风险问题总数

            for issue in raw_issues_all:
                fields = issue["fields"]
                summary = fields["summary"]
                status = fields["status"]["name"]
                priority = fields["priority"]["name"] if fields.get("priority") else "无"
                assignee = fields.get("assignee", {}).get("displayName", "未分配")
                created = fields["created"][:10]
                labels = fields.get("labels", [])
                resolution = fields.get("resolution")
                bug_key = issue.get("key", "")
                tcid = fields.get("customfield_10000", "")
                risk_level = get_risk_level(priority, labels, summary)

                # 检查是否已解决
                is_resolved = resolution is not None or status in ["Resolved", "Closed", "Fixed", "已解决", "关闭"]
                _log("debug", f"bug_key={bug_key}, status={status}, resolution={resolution}, is_resolved={is_resolved}, created={created}")
                if is_resolved:
                    total_resolved += 1

                # 检查是否阻塞问题（优先级Block或标签含"阻塞"）
                priority_lower = priority.lower() if priority else ""
                labels_lower = [label.lower() for label in labels]
                is_blocking = "block" in priority_lower or "阻塞" in priority_lower or any("阻塞" in label for label in labels_lower)

                # 检查是否MP Block（标签含"MP block"或"mp block"）
                is_mp_block = any("mp block" in label.lower() for label in labels_lower)

                # 检查是否交付风险（标题或标签含"交付"）
                summary_lower = summary.lower() if summary else ""
                is_delivery = "交付" in summary_lower or any("交付" in label for label in labels_lower)

                # 更新统计
                if is_blocking:
                    blocking_total += 1
                    if is_resolved:
                        blocking_resolved += 1

                if is_mp_block:
                    mp_block_total += 1

                if is_delivery:
                    delivery_risk_total += 1

                # 全量数据统计
                status_counts_all[status] = status_counts_all.get(status, 0) + 1
                bucket = bucket_from_priority(priority)
                priority_bucket_counts_all[bucket] = priority_bucket_counts_all.get(bucket, 0) + 1
                risk_level_counts_all[risk_level] = risk_level_counts_all.get(risk_level, 0) + 1

                # 走势图统计（提交）- 统计所有问题的创建日期
                if created in submission_trend:
                    submission_trend[created] += 1
                else:
                    _log("debug", f"提交走势图: created={created} 不在日期范围内")

                # 走势图统计（验证/解决）
                if is_resolved and "resolutiondate" in fields:
                    resolution_date = fields["resolutiondate"][:10] if fields["resolutiondate"] else created
                    if resolution_date in verification_trend:
                        verification_trend[resolution_date] += 1
                    else:
                        _log("debug", f"验证走势图: resolution_date={resolution_date} 不在日期范围内")
                elif is_resolved:
                    # 如果没有resolutiondate，使用created日期
                    if created in verification_trend:
                        verification_trend[created] += 1
                    else:
                        _log("debug", f"验证走势图: created={created} 不在日期范围内 (无resolutiondate)")

                # 构建全量结构化issue对象供前端使用
                # 提取Components
                components = fields.get("components", [])
                comp_names = [c.get("name", "") for c in components if isinstance(c, dict)] if components else []
                comp_str = ", ".join(comp_names)

                # 提取Business_Domain
                business_domain = ""
                raw_domain = fields.get("customfield_10002", "")
                if isinstance(raw_domain, str):
                    business_domain = raw_domain.strip()
                elif isinstance(raw_domain, dict):
                    business_domain = raw_domain.get("value", "") or raw_domain.get("name", "")
                if not business_domain:
                    business_domain = lookup_domain(comp_str, comp_names)

                # 提取Must_Resolve
                must_resolve = ""
                raw_must = fields.get("customfield_10000", "")
                if isinstance(raw_must, str) and "MP Block" in raw_must:
                    must_resolve = "MP Block"
                elif isinstance(raw_must, dict) and "MP Block" in raw_must.get("value", ""):
                    must_resolve = "MP Block"
                if not must_resolve and any("mp block" in l.lower() for l in labels):
                    must_resolve = "MP Block"

                # 从summary中提取模块
                module_from_summary = ""
                m = re.search(r'【[^】]*】【[^】]*】【[^】]*】【([^】]*)】', summary)
                if m:
                    module_from_summary = m.group(1)

                issue_data = {
                    "bug_key": bug_key,
                    "key": bug_key,
                    "summary": summary,
                    "priority": priority,
                    "status": status,
                    "assignee": assignee,
                    "created": created,
                    "labels": labels,
                    "tcid": tcid,
                    "risk_level": risk_level,
                    "affects_versions": fields.get("affectsVersions", []),
                    "customfield_10001": fields.get("customfield_10001", ""),
                    "components": comp_str,
                    "business_domain": business_domain,
                    "must_resolve": must_resolve,
                    "module_from_summary": module_from_summary,
                    "is_tos": 'TOS' in bug_key.upper()
                }
                issues_list_all.append(issue_data)

            # 计算解决率
            resolution_rate = 0
            if total_all > 0:
                resolution_rate = round((total_resolved / total_all) * 100)

            # 数据清洗和统计（未解决数据用于风险分析和AI上下文）
            issue_summaries = []
            status_counts_unresolved = {}
            priority_bucket_counts_unresolved = {"高": 0, "中": 0, "低": 0, "无": 0}
            risk_level_counts_unresolved = {"高": 0, "中": 0, "低": 0}

            # 阻塞问题标签统计和潜在共性问题识别
            blocking_label_counts = {}
            potential_common_issues = []

            for issue in raw_issues:  # raw_issues已经是未解决数据
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

                # 判断是否为阻塞问题
                is_blocking = priority in ["Block", "阻塞"] or any("阻塞" in label for label in labels)

                # 统计阻塞问题的标签
                if is_blocking:
                    for label in labels:
                        blocking_label_counts[label] = blocking_label_counts.get(label, 0) + 1

                # 检查潜在共性问题（基于Affect Project字段）
                customfield_10001 = fields.get("customfield_10001", "")
                affects_versions = fields.get("affectsVersions", [])

                # 收集所有影响的项目
                affected_projects = []

                # 从customfield_10001解析项目
                if customfield_10001:
                    # 假设以逗号或分号分隔
                    projects = re.split(r'[,;]', customfield_10001)
                    for project in projects:
                        project = project.strip()
                        if project:
                            affected_projects.append(project)

                # 从affectsVersions解析项目
                for version in affects_versions:
                    if isinstance(version, dict) and "name" in version:
                        affected_projects.append(version["name"])

                # 提取tOS项目版本（包括子版本，如tOS16.1、tOS16.2）
                affected_tos_versions = set()
                for project in affected_projects:
                    project_upper = project.upper()
                    if 'TOS' in project_upper:
                        # 提取完整的tOS版本号（包括子版本）
                        match = re.search(r'TOS(\d+(?:\.\d+)?)', project_upper)
                        if match:
                            version = match.group(1)  # 例如 "16" 或 "16.1"
                            affected_tos_versions.add(f"tOS{version}")

                # 如果影响多个tOS版本，标记为潜在共性问题
                if len(affected_tos_versions) >= 2:
                    potential_common_issues.append({
                        "bug_key": bug_key,
                        "summary": summary,
                        "affected_tos_versions": list(affected_tos_versions),
                        "affected_projects": affected_projects,
                        "status": status,
                        "priority": priority
                    })

                # 未解决数据统计
                status_counts_unresolved[status] = status_counts_unresolved.get(status, 0) + 1
                bucket = bucket_from_priority(priority)
                priority_bucket_counts_unresolved[bucket] = priority_bucket_counts_unresolved.get(bucket, 0) + 1
                risk_level_counts_unresolved[risk_level] = risk_level_counts_unresolved.get(risk_level, 0) + 1

                # 构建问题摘要用于AI上下文
                issue_summaries.append(f"- {bug_key} (TCID:{tcid}) {summary} (状态:{status}, 优先级:{priority}, 风险:{risk_level}, 负责人:{assignee}, 创建:{created})")

            # 计算阻塞问题解决率
            blocking_resolution_rate = 0
            if blocking_total > 0:
                blocking_resolution_rate = round((blocking_resolved / blocking_total) * 100)

            # 构建当前数据的上下文 Prompt（包含全量关键统计）
            data_context = f"""
            当前用户提问：「{user_query}」
            我已从Jira获取到以下数据：

            【全量统计数据】
            - 总问题数：{len(raw_issues_all)}个
            - 未解决问题数：{len(raw_issues)}个
            - 阻塞问题总数：{blocking_total}个（优先级Block或标签含"阻塞"）
            - 已解决阻塞问题：{blocking_resolved}个
            - 未解决阻塞问题：{blocking_total - blocking_resolved}个
            - 阻塞问题解决率：{blocking_resolution_rate}%（已解决{blocking_resolved}个/共{blocking_total}个阻塞问题）
            - MP Block版本卡点：{mp_block_total}个（标签含"MP block"或"mp block"，可能严重延迟发布时间）
            - 交付风险问题：{delivery_risk_total}个（标题或标签含"交付"）

            【未解决问题分布】
            - 优先级分布：高={priority_bucket_counts_unresolved['高']}，中={priority_bucket_counts_unresolved['中']}，低={priority_bucket_counts_unresolved['低']}
            - 风险等级分布：高风险={risk_level_counts_unresolved['高']}，中风险={risk_level_counts_unresolved['中']}，低风险={risk_level_counts_unresolved['低']}
            - 状态分布：{', '.join([f'{k}={v}' for k,v in sorted(status_counts_unresolved.items())])}

            【前10个未解决问题详情】
            {chr(10).join(issue_summaries[:10])}
            """

            # 共性问题识别（静默降级）
            common_clusters = {}
            try:
                issues_tos = fetch_tos_issues()
                common_clusters = cluster_common_issues(issues_tos)
                _log("info", f"共性问题聚类成功: {len(common_clusters)} 个模块聚类")
            except Exception as e:
                _log("warn", f"共性问题聚类失败: {e}")

            # ===== 模块/领域分布预计算 =====
            module_distribution = {}
            domain_distribution = {}
            mp_block_by_module = {}
            block_by_module = {}
            unresolved_statuses = {"open", "in progress", "reopened", "modifying", "submitted", "fixed"}

            for iss in issues_list_all:
                # 模块分布（所有未解决问题）
                if iss["status"].lower() not in {"closed", "verified", "abandoned"}:
                    mod = iss.get("module_from_summary", "") or iss.get("components", "")
                    if mod:
                        module_distribution[mod] = module_distribution.get(mod, 0) + 1

                    # 业务领域分布
                    domain = iss.get("business_domain", "")
                    if domain:
                        domain_distribution[domain] = domain_distribution.get(domain, 0) + 1

                    # MP Block按模块分布
                    if iss.get("must_resolve") == "MP Block":
                        mp_block_by_module[mod] = mp_block_by_module.get(mod, 0) + 1

                    # 阻塞问题按模块分布
                    labels_lower = [l.lower() for l in iss.get("labels", [])]
                    if "block" in iss.get("priority", "").lower() or any("阻塞" in l for l in labels_lower):
                        block_by_module[mod] = block_by_module.get(mod, 0) + 1

            # 计算闭环率（closed数 / 总问题数）
            closed_count = sum(1 for iss in issues_list_all if iss["status"].lower() == "closed")
            closure_rate = round((closed_count / total_all) * 100) if total_all > 0 else 0

            # 发送数据更新事件给前端（发送全量数据）
            risk_data = {
                "project_key": project_key,
                "total_all": len(raw_issues_all),  # 全量问题数
                "total_unresolved": len(raw_issues),  # 未解决问题数
                "submission_trend": submission_trend,  # 提交走势图数据
                "verification_trend": verification_trend,  # 验证走势图数据
                "resolution_rate": resolution_rate,  # 解决率
                "closure_rate": closure_rate,  # 闭环率
                "blocking_label_counts": blocking_label_counts,  # 阻塞问题标签统计
                "potential_common_issues": potential_common_issues,  # 潜在共性问题
                "common_clusters": common_clusters,  # tOS库候选共性问题聚类
                "issues_unresolved": [],  # 未解决问题列表（用于风险分析）
                "status_counts_all": status_counts_all,  # 全量状态统计
                "priority_bucket_counts_all": priority_bucket_counts_all,  # 全量优先级统计
                "risk_level_counts_all": risk_level_counts_all,  # 全量风险等级统计
                "issues": issues_list_all,  # 发送全量结构化数据
                "issue_summaries": issue_summaries[:10],  # 未解决问题摘要
                # 新增关键统计数据字段
                "blocking_total": blocking_total,  # 总阻塞问题数（全量）
                "blocking_resolved": blocking_resolved,  # 已解决的阻塞问题数
                "blocking_unresolved": blocking_total - blocking_resolved,  # 未解决的阻塞问题数
                "mp_block_total": mp_block_total,  # MP Block问题总数
                "delivery_risk_total": delivery_risk_total,  # 交付风险问题总数
                # 分布数据
                "module_distribution": module_distribution,  # 模块分布
                "domain_distribution": domain_distribution,  # 业务领域分布
                "mp_block_by_module": mp_block_by_module,  # MP Block按模块分布
                "block_by_module": block_by_module  # 阻塞问题按模块分布
            }

            # 构建未解决问题列表（用于前端风险分析）
            issues_unresolved_list = []
            for issue in raw_issues:
                fields = issue["fields"]
                bug_key = issue.get("key", "")
                summary = fields["summary"]
                status = fields["status"]["name"]
                priority = fields["priority"]["name"] if fields.get("priority") else "无"
                assignee = fields.get("assignee", {}).get("displayName", "未分配")
                created = fields["created"][:10]
                labels = fields.get("labels", [])
                risk_level = get_risk_level(priority, labels, summary)

                # 提取Components
                components = fields.get("components", [])
                comp_names = [c.get("name", "") for c in components if isinstance(c, dict)] if components else []
                comp_str = ", ".join(comp_names)

                # 提取Business_Domain
                business_domain = ""
                raw_domain = fields.get("customfield_10002", "")
                if isinstance(raw_domain, str):
                    business_domain = raw_domain.strip()
                elif isinstance(raw_domain, dict):
                    business_domain = raw_domain.get("value", "") or raw_domain.get("name", "")
                if not business_domain:
                    business_domain = lookup_domain(comp_str, comp_names)

                # 提取Must_Resolve
                must_resolve = ""
                raw_must = fields.get("customfield_10000", "")
                if isinstance(raw_must, str) and "MP Block" in raw_must:
                    must_resolve = "MP Block"
                elif isinstance(raw_must, dict) and "MP Block" in raw_must.get("value", ""):
                    must_resolve = "MP Block"
                if not must_resolve and any("mp block" in l.lower() for l in labels):
                    must_resolve = "MP Block"

                # 从summary中提取模块
                module_from_summary = ""
                m = re.search(r'【[^】]*】【[^】]*】【[^】]*】【([^】]*)】', summary)
                if m:
                    module_from_summary = m.group(1)

                issues_unresolved_list.append({
                    "bug_key": bug_key,
                    "key": bug_key,
                    "summary": summary,
                    "priority": priority,
                    "status": status,
                    "assignee": assignee,
                    "created": created,
                    "labels": labels,
                    "risk_level": risk_level,
                    "components": comp_str,
                    "business_domain": business_domain,
                    "must_resolve": must_resolve,
                    "module_from_summary": module_from_summary,
                    "is_tos": 'TOS' in bug_key.upper()
                })

            risk_data["issues_unresolved"] = issues_unresolved_list

            # 日志趋势数据
            _log("info", f"提交走势图数据: {submission_trend}")
            _log("info", f"验证走势图数据: {verification_trend}")

            yield generate_sse_message('data', risk_data)

            # 为AI上下文添加共性问题提示
            common_clusters_text = ""
            if common_clusters:
                common_clusters_text = "\n\n## tOS库候选共性问题\n"
                for mod, keys in list(common_clusters.items())[:3]:
                    common_clusters_text += f"- 模块「{mod}」出现{len(keys)}次，示例ID: {', '.join(keys[:2])}\n"
                common_clusters_text += "请判断这些是否是真正的共性问题，并说明可能影响的整机项目。\n"
                data_context = data_context + common_clusters_text

            # 构建发送给 AI 的消息列表
            messages = []
            # 加入最近的历史对话（但排除刚刚加入的当前 user 消息）
            for msg in history[:-1]:  # 排除最后一条（刚刚加入的 user_query）
                messages.append(msg)
            # 将当前问题与数据上下文合并为一条 user 消息
            messages.append({"role": "user", "content": data_context})

            # 发送状态
            yield generate_sse_message('thinking', '🤖 专家正在深度分析数据... ')

            # 调用AI（使用工具函数，已包含重试机制）
            _log("info", "开始调用AI服务...")
            response = call_ai_api(messages, system_prompt)

            if not response:
                _log("error", "AI服务调用失败")
                yield generate_sse_message('error', get_friendly_ai_error())
                yield "data: [DONE]\n\n"
                return
            else:
                _log("info", "AI服务调用成功，开始解析流式响应")

            # 解析AI流式输出（使用工具函数）
            full_response = process_sse_stream(response)

            # 解析标签内容（使用工具函数）
            thinking_content, answer_content = parse_thinking_answer(full_response)

            # 发送思考过程（逐字输出）
            if thinking_content:
                for msg in send_thinking_chars(thinking_content):
                    yield msg
            yield generate_sse_message('thinking_complete', '思考过程完成，开始生成答案...')

            # 发送回答内容（逐字输出）
            for msg in send_answer_chars(answer_content):
                yield msg

            # 将 AI 的完整回答存入历史
            history.append({"role": "assistant", "content": full_response})
            # 控制历史长度
            if len(history) > MAX_HISTORY_MESSAGES:
                conversation_history[conversation_id] = history[-MAX_HISTORY_MESSAGES:]

            # 构建数据更新事件
            # 整理项目数据
            project_issues = []
            # 初始化统计数据
            status_counts = {}
            priority_bucket_counts = {}
            risk_level_counts = {}
            # 识别用户意图中的部门和业务领域
            user_intent = recognize_intent(user_query)
            department = user_intent.get("department")
            domain = user_intent.get("domain")

            for issue in raw_issues:
                fields = issue["fields"]
                summary = fields.get("summary", "")
                labels = fields.get("labels", [])
                status = fields.get("status", {}).get("name", "")
                priority = fields.get("priority", {}).get("name", "")

                # 更新状态统计
                status_counts[status] = status_counts.get(status, 0) + 1

                # 更新优先级统计（按桶分类）
                priority_bucket = bucket_from_priority(priority)
                priority_bucket_counts[priority_bucket] = priority_bucket_counts.get(priority_bucket, 0) + 1

                # 计算风险等级
                risk_level = get_risk_level(priority, labels, summary)
                risk_level_counts[risk_level] = risk_level_counts.get(risk_level, 0) + 1

                # 自动识别交付测试部的bug
                is_delivery_test = False
                if "交付" in summary or any("交付" in label for label in labels):
                    is_delivery_test = True

                # 根据部门和业务领域过滤数据
                if department or domain:
                    # 检查摘要是否包含部门或业务领域
                    summary_lower = summary.lower()
                    department_match = not department or department in summary
                    domain_match = not domain or domain in summary

                    # 如果不匹配，跳过此问题
                    if not (department_match or domain_match):
                        continue

                # 从AI分析结果中提取tag信息
                # 这里假设AI分析结果中包含tag信息，实际实现可能需要根据AI输出格式调整
                tags = labels  # 暂时使用Jira标签作为tag

                project_issues.append({
                    "bug_key": issue.get("key", ""),
                    "summary": summary,
                    "status": status,
                    "priority": priority,
                    "assignee": fields.get("assignee", {}).get("displayName", "未分配"),
                    "created": fields.get("created", "")[:10],
                    "labels": labels,
                    "risk_level": risk_level,
                    "is_delivery_test": is_delivery_test,
                    "tags": tags
                })

            # 不再发送第二个data事件，使用第一个risk_data即可

            # 发送结束标记
            yield "data: [DONE]\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield generate_sse_message('error', str(e))
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

        # 使用统一的流式AI响应函数
        stream_ai_response(prompt)

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

    analysis = stream_ai_response(prompt, return_content=True)

    print("", flush=True)
    print(_c("=" * 70, "97"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='智能Jira项目风险分析Agent')
    parser.add_argument('--api', action='store_true', help='启动API服务')
    parser.add_argument('--query', type=str, help='用户查询')
    parser.add_argument('--cli', action='store_true', help='命令行模式')
    args = parser.parse_args()

    if args.cli:
        run_command_line_analysis()
    elif args.query:

        user_input = args.query
        # 生成 JQL
        jql = generate_final_jql(user_input)
        _log("info", f"用户查询: {user_input}")
        _log("info", f"生成JQL: {jql}")

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

            # 使用统一的流式AI响应函数
            stream_ai_response(prompt)

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

        analysis = stream_ai_response(prompt, return_content=True)

        print("", flush=True)
        print(_c("=" * 70, "97"))
    else:
        # 默认启动Flask API服务 - 直接注册知识库蓝图到当前应用
        print('启动Flask API服务（注册知识库蓝图）...')

        # 检查知识库系统是否可用
        import os
        if os.getenv('DISABLE_KNOWLEDGE') == '1':
            print('✅ 知识库系统已禁用（DISABLE_KNOWLEDGE=1）')
            KNOWLEDGE_SYSTEM_AVAILABLE = False
        else:
            try:
                # 尝试导入知识库蓝图
                from knowledge_api import knowledge_bp
                # 注册知识库蓝图
                app.register_blueprint(knowledge_bp)
                print('✅ 知识库蓝图注册成功')
                KNOWLEDGE_SYSTEM_AVAILABLE = True
            except Exception as e:
                print(f'⚠️  知识库系统初始化失败: {e}')
                print('知识库功能将不可用')
                KNOWLEDGE_SYSTEM_AVAILABLE = False

        # 注册交付路线图蓝图
        try:
            from delivery_api import delivery_bp
            app.register_blueprint(delivery_bp)
            print('✅ 交付路线图蓝图注册成功')
        except Exception as e:
            print(f'⚠️  交付路线图蓝图注册失败: {e}')

        # ========== 添加IP绑定和访问控制路由 ==========
        @app.route('/api/auth/jira', methods=['POST', 'GET'])
        def auth_jira():
            """接收Jira凭据并绑定到客户端IP（POST）或测试端点（GET）"""
            from datetime import datetime
            if request.method == 'GET':
                try:
                    # 从数据库获取所有Jira凭据
                    credentials = db_session.query(JiraCredential).all()
                    credentials_list = []
                    total_credentials = len(credentials)
                    valid_credentials = 0
                    expired_credentials = 0
                    now = datetime.now()

                    for cred in credentials:
                        username = cred.username
                        last_updated = cred.last_updated
                        is_valid = True
                        remaining_seconds = CREDENTIALS_EXPIRY_SECONDS

                        if last_updated:
                            expiry_seconds = (now - last_updated).total_seconds()
                            if expiry_seconds > CREDENTIALS_EXPIRY_SECONDS:
                                is_valid = False
                                expired_credentials += 1
                                remaining_seconds = 0
                            else:
                                valid_credentials += 1
                                remaining_seconds = CREDENTIALS_EXPIRY_SECONDS - expiry_seconds

                        credentials_list.append({
                            'ip': cred.ip,
                            'username': username,
                            'password': '***' + username[-2:] if username else '***',
                            'last_updated': last_updated.isoformat() if last_updated else '',
                            'is_valid': is_valid,
                            'remaining_hours': round(remaining_seconds / 3600, 1) if remaining_seconds > 0 else 0,
                            'expires_in': f"{remaining_seconds:.0f}秒" if remaining_seconds > 0 else "已过期"
                        })

                    return jsonify({
                        "success": True,
                        "message": f"共找到 {total_credentials} 个Jira凭据",
                        "statistics": {
                            "total": total_credentials,
                            "valid": valid_credentials,
                            "expired": expired_credentials,
                            "expiry_hours": CREDENTIALS_EXPIRY_SECONDS / 3600
                        },
                        "credentials": credentials_list,
                        "timestamp": now.isoformat()
                    })
                except Exception as e:
                    return jsonify({
                        "success": True,
                        "message": "Jira凭据API端点正常",
                        "method": "GET",
                        "timestamp": datetime.now().isoformat()
                    })

            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "请求体必须为JSON格式"}), 400

                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
                department = data.get('department', '').strip()

                if not username or not password:
                    return jsonify({"success": False, "error": "用户名和密码不能为空"}), 400

                client_ip = get_client_ip()

                # 保存Jira凭据到数据库
                save_jira_credential(client_ip, username, password, department)

                # 同时添加到服务器访问白名单（数据库）
                update_ip_access_record(
                    ip=client_ip,
                    username=username,
                    department=department,
                    access_level=DEFAULT_ACCESS_LEVEL,
                    jira_bound=True
                )

                # 构建响应
                response = {
                    "success": True,
                    "message": "Jira凭据已保存并绑定到您的IP地址",
                    "ip": client_ip,
                    "timestamp": datetime.now().isoformat()
                }

                # 如果是第一个绑定的用户，自动设为管理员
                admin_ips = get_admin_ips()
                if not admin_ips:
                    promote_to_admin(client_ip)
                    response["message"] = "Jira凭据已保存并绑定到您的IP地址（您已成为第一个管理员）"
                    response["is_admin"] = True

                return jsonify(response)

            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/auth/jira/list', methods=['GET'])
        def list_jira_credentials():
            """查看所有存储的Jira凭据（不显示密码）"""
            try:
                from datetime import datetime
                # 从数据库获取所有Jira凭据
                credentials = db_session.query(JiraCredential).all()
                credentials_list = []
                total_credentials = len(credentials)
                valid_credentials = 0
                expired_credentials = 0
                now = datetime.now()

                for cred in credentials:
                    username = cred.username
                    last_updated = cred.last_updated
                    is_valid = True
                    remaining_seconds = CREDENTIALS_EXPIRY_SECONDS

                    if last_updated:
                        expiry_seconds = (now - last_updated).total_seconds()
                        if expiry_seconds > CREDENTIALS_EXPIRY_SECONDS:
                            is_valid = False
                            expired_credentials += 1
                            remaining_seconds = 0
                        else:
                            valid_credentials += 1
                            remaining_seconds = CREDENTIALS_EXPIRY_SECONDS - expiry_seconds

                    credentials_list.append({
                        'ip': cred.ip,
                        'username': username,
                        'password': '***' + username[-2:] if username else '***',
                        'last_updated': last_updated.isoformat() if last_updated else '',
                        'is_valid': is_valid,
                        'remaining_hours': round(remaining_seconds / 3600, 1) if remaining_seconds > 0 else 0,
                        'expires_in': f"{remaining_seconds:.0f}秒" if remaining_seconds > 0 else "已过期"
                    })

                return jsonify({
                    "success": True,
                    "message": f"共找到 {total_credentials} 个Jira凭据",
                    "statistics": {
                        "total": total_credentials,
                        "valid": valid_credentials,
                        "expired": expired_credentials,
                        "expiry_hours": CREDENTIALS_EXPIRY_SECONDS / 3600
                    },
                    "credentials": credentials_list,
                    "timestamp": now.isoformat()
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ========== 管理员API ==========
        @app.route('/api/admin/access/list', methods=['GET'])
        @require_admin_access
        def admin_access_list():
            """查看所有IP访问权限（管理员专用）"""
            try:
                from datetime import datetime
                now = datetime.now()

                # 从数据库获取所有IP访问记录
                records = db_session.query(IPAccessRecord).all()
                access_list = []
                admin_count = 0

                for record in records:
                    # 计算剩余时间
                    remaining_seconds = ACCESS_EXPIRY_SECONDS
                    if record.last_access:
                        expiry_seconds = (now - record.last_access).total_seconds()
                        remaining_seconds = max(0, ACCESS_EXPIRY_SECONDS - expiry_seconds)

                    access_list.append({
                        'ip': record.ip,
                        'username': record.username or '',
                        'department': record.department or '',
                        'access_level': record.access_level,
                        'is_admin': record.access_level == 'admin',
                        'jira_bound': record.jira_bound or False,
                        'first_access': record.first_access.isoformat() if record.first_access else '',
                        'last_access': record.last_access.isoformat() if record.last_access else '',
                        'remaining_days': round(remaining_seconds / 86400, 1) if remaining_seconds > 0 else 0,
                        'expires_in': f"{int(remaining_seconds/86400)}天" if remaining_seconds > 0 else "已过期"
                    })

                    if record.access_level == 'admin':
                        admin_count += 1

                # 获取管理员IP列表
                admin_ips = get_admin_ips()

                return jsonify({
                    "success": True,
                    "message": f"共找到 {len(access_list)} 个IP访问权限",
                    "statistics": {
                        "total": len(access_list),
                        "admin_count": admin_count,
                        "user_count": len(access_list) - admin_count,
                        "expiry_days": ACCESS_EXPIRY_SECONDS / 86400
                    },
                    "access_list": access_list,
                    "admin_ips": list(admin_ips),
                    "timestamp": now.isoformat()
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/admin/access/log', methods=['GET'])
        @require_admin_access
        def admin_access_log():
            """查看访问审计日志（管理员专用）"""
            try:
                from datetime import datetime
                # 从数据库获取最近的日志
                recent_logs = db_session.query(AccessLog).order_by(AccessLog.timestamp.desc()).limit(100).all()
                total_logs = db_session.query(AccessLog).count()

                # 转换为字典格式
                logs_data = []
                for log in recent_logs:
                    logs_data.append({
                        'timestamp': log.timestamp.isoformat() if log.timestamp else '',
                        'ip': log.ip or '',
                        'path': log.path or '',
                        'method': log.method or '',
                        'status': log.status or '',
                        'message': log.message or ''
                    })

                return jsonify({
                    "success": True,
                    "message": f"共 {total_logs} 条日志，显示最近 {len(logs_data)} 条",
                    "statistics": {
                        "total_logs": total_logs,
                        "max_log_size": 10000,  # 固定值，未来可配置
                        "recent_logs": len(logs_data)
                    },
                    "logs": logs_data,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/admin/access/revoke', methods=['POST'])
        @require_admin_access
        def admin_access_revoke():
            """撤销IP访问权限（管理员专用）"""
            try:
                from datetime import datetime
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "请求体必须为JSON格式"}), 400

                ip_to_revoke = data.get('ip', '').strip()
                if not ip_to_revoke:
                    return jsonify({"success": False, "error": "IP地址不能为空"}), 400

                # 从数据库删除IP访问记录
                record = db_session.query(IPAccessRecord).filter_by(ip=ip_to_revoke).first()
                if record:
                    db_session.delete(record)
                    # 同时删除Jira凭据
                    jira_cred = db_session.query(JiraCredential).filter_by(ip=ip_to_revoke).first()
                    if jira_cred:
                        db_session.delete(jira_cred)
                    db_session.commit()

                    return jsonify({
                        "success": True,
                        "message": f"已成功撤销IP {ip_to_revoke} 的访问权限",
                        "revoked_ip": ip_to_revoke,
                        "timestamp": datetime.now().isoformat()
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": f"IP {ip_to_revoke} 不在访问记录中",
                        "timestamp": datetime.now().isoformat()
                    }), 404
            except Exception as e:
                db_session.rollback()
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/admin/access/promote', methods=['POST'])
        @require_admin_access
        def admin_access_promote():
            """提升IP为管理员（管理员专用）"""
            try:
                from datetime import datetime
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "请求体必须为JSON格式"}), 400

                ip_to_promote = data.get('ip', '').strip()
                if not ip_to_promote:
                    return jsonify({"success": False, "error": "IP地址不能为空"}), 400

                # 检查IP是否在访问记录中
                record = db_session.query(IPAccessRecord).filter_by(ip=ip_to_promote).first()
                if not record:
                    return jsonify({
                        "success": False,
                        "error": f"IP {ip_to_promote} 不在访问记录中，请先绑定Jira凭据",
                        "timestamp": datetime.now().isoformat()
                    }), 404

                # 使用现有的promote_to_admin函数
                if promote_to_admin(ip_to_promote):
                    admin_ips = get_admin_ips()
                    return jsonify({
                        "success": True,
                        "message": f"已成功将IP {ip_to_promote} 提升为管理员",
                        "promoted_ip": ip_to_promote,
                        "admin_ips": list(admin_ips),
                        "timestamp": datetime.now().isoformat()
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": f"提升IP {ip_to_promote} 为管理员失败",
                        "timestamp": datetime.now().isoformat()
                    }), 500
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/admin/access/cleanup', methods=['POST'])
        @require_admin_access
        def admin_access_cleanup():
            """清理过期IP（管理员专用）"""
            try:
                from datetime import datetime, timedelta
                cleaned_count = 0
                now = datetime.now()

                # 计算过期时间点
                expiry_threshold = now - timedelta(seconds=ACCESS_EXPIRY_SECONDS)

                # 查找过期的IP访问记录
                expired_records = db_session.query(IPAccessRecord).filter(
                    IPAccessRecord.last_access < expiry_threshold
                ).all()

                for record in expired_records:
                    ip = record.ip
                    # 删除IP访问记录
                    db_session.delete(record)
                    # 删除对应的Jira凭据
                    jira_cred = db_session.query(JiraCredential).filter_by(ip=ip).first()
                    if jira_cred:
                        db_session.delete(jira_cred)
                    cleaned_count += 1

                db_session.commit()

                # 获取剩余记录数量
                remaining_count = db_session.query(IPAccessRecord).count()

                return jsonify({
                    "success": True,
                    "message": f"已清理 {cleaned_count} 个过期IP",
                    "cleaned_count": cleaned_count,
                    "remaining_count": remaining_count,
                    "timestamp": now.isoformat()
                })
            except Exception as e:
                db_session.rollback()
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/analyze/batch', methods=['GET'])
        def analyze_batch_api():
            """批量分析所有项目（按tOS版本分组，每3个项目一批）"""
            try:
                intent = request.args.get('intent', '风险分析')
                time_range = request.args.get('time_range', '全部')

                _log("info", f"批量分析请求: intent={intent}, time_range={time_range}")

                # 执行批量分析
                results = analyze_projects_by_batch(intent, time_range)

                return jsonify({
                    "success": True,
                    "message": "批量分析完成",
                    "data": results,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                _log("err", f"批量分析API出错: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }), 500

        # 启动Flask应用
        app.run(host='0.0.0.0', port=5002, debug=False)

