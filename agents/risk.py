import json
import re
import os
import sys
from typing import Dict, List, Any, Generator, Optional
from datetime import datetime, timedelta

from .base import BaseAgent
from utils import call_ai_api, parse_thinking_answer, generate_sse_message, send_thinking_chars, send_answer_chars

# 从e.py导入必要的函数
# 注意：为了避免循环导入，我们将稍后重构这些函数到独立的模块
# 暂时先复制相关代码，后续再提取


class RiskAgent(BaseAgent):
    def __init__(self):
        system_prompt = self._load_system_prompt()
        super().__init__(name="风险分析代理", system_prompt=system_prompt)
        
        # 从e.py加载配置
        self.JIRA_USERNAME = os.getenv("JIRA_USERNAME")
        self.JIRA_PASSWORD = os.getenv("JIRA_PASSWORD")
        self.JIRA_URL = os.getenv("JIRA_URL", "http://jira.transsion.com")
        self.AI_MODEL = os.getenv("AI_MODEL", "gpt-5.4")
        
        # 加载JQL模板
        self.JQL_TEMPLATES = self._load_jql_templates()
        
    def _load_system_prompt(self) -> str:
        """加载系统提示词"""
        # 从e.py复制SYSTEM_PROMPT和DETAILED_REPORT_PROMPT
        # 暂时返回简化版本
        return """你是一位拥有20年以上经验的顶级软件项目风险分析专家，曾任多家世界500强科技公司的首席质量官。你以敏锐的风险洞察力、精准的问题定位和务实的解决方案而闻名业界。"""
    
    def _load_jql_templates(self) -> List[Dict]:
        """加载JQL模板"""
        import json
        template_file = os.path.join(os.path.dirname(__file__), '..', 'jql_templates.json')
        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                templates = json.load(f)
            return templates.get('templates', [])
        except Exception as e:
            print(f"加载 JQL 模板失败: {e}")
            return []
    
    def _normalize_chinese_date(self, text):
        """将中文日期描述（如'4月11到5月20'）转换为ISO格式（如'2026-04-11 到 2026-05-20'）"""
        now = datetime.now()
        current_year = now.year

        year = current_year
        if '去年' in text:
            year = current_year - 1
            text = text.replace('去年', '')
        elif '今年' in text:
            text = text.replace('今年', '')

        range_pattern = re.search(
            r'(?:(\d{1,2})月(\d{1,2})[日号]?)\s*[到至\-]\s*(?:(\d{1,2})月(\d{1,2})[日号]?)',
            text
        )
        if range_pattern:
            start_month, start_day, end_month, end_day = range_pattern.groups()
            start = f"{year}-{int(start_month):02d}-{int(start_day):02d}"
            end = f"{year}-{int(end_month):02d}-{int(end_day):02d}"
            return f"{start} 到 {end}"

        full_date = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]?', text)
        if full_date:
            y, m, d = full_date.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"

        single_date = re.search(r'(\d{1,2})月(\d{1,2})[日号]?', text)
        if single_date:
            month, day = single_date.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return None

    def _recognize_intent(self, user_query: str) -> Dict[str, Any]:
        """识别用户意图（从e.py复制）"""
        intent = {
            "project": None,
            "time_range": "本周",
            "query_type": "bug总量",
            "department": None,
            "domain": None
        }
        
        # 提取项目键
        project_match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', user_query)
        if project_match:
            intent["project"] = project_match.group()
        
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
            normalized = self._normalize_chinese_date(user_query)
            if normalized:
                intent["time_range"] = normalized
            else:
                intent["time_range"] = "本周"
        
        # 提取查询类型
        if "MP block" in user_query or "MP BLOCK" in user_query or "MP Block" in user_query:
            intent["query_type"] = "MP BLOCK问题"
        elif "交付测试" in user_query:
            intent["query_type"] = "交付测试部bug"
        elif "研发测试" in user_query:
            intent["query_type"] = "研发测试部bug"
        elif "bug" in user_query or "Bug" in user_query:
            intent["query_type"] = "bug总量"
        
        return intent
    
    def _parse_time_range(self, time_range: str):
        """解析时间范围（从e.py复制）"""
        time_range = time_range.strip()
        
        if time_range == "全部":
            return "", ""
        
        # 处理具体日期范围
        range_match = re.search(r'(\d{4}-\d{2}-\d{2})\s*[到至\-]\s*(\d{4}-\d{2}-\d{2})', time_range)
        if range_match:
            start_date = range_match.group(1)
            end_date = range_match.group(2)
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            end_date_inclusive = end_dt.strftime("%Y-%m-%d")
            return f'"{start_date}"', f'"{end_date_inclusive}"'
        
        # 处理单个具体日期
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
            return "startOfDay()", "endOfDay()"
    
    def _match_jql_template(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """匹配JQL模板（从e.py复制）"""
        project = intent.get("project")
        query_type = intent.get("query_type")
        department = intent.get("department")
        
        # 首先，优先匹配包含部门的模板
        if department:
            for template in self.JQL_TEMPLATES:
                template_name = template.get("name", "").lower()
                if query_type.lower() in template_name and department.lower() in template_name:
                    if project and project in template.get("projects", {}):
                        return template
                    elif isinstance(template.get("projects"), dict) and template.get("projects"):
                        continue
                    else:
                        return template
        
        # 如果没有匹配到包含部门的模板，再尝试匹配仅查询类型的模板
        for template in self.JQL_TEMPLATES:
            if query_type.lower() in template.get("name", "").lower():
                if project and project in template.get("projects", {}):
                    return template
                elif isinstance(template.get("projects"), dict) and template.get("projects"):
                    continue
                else:
                    return template
        
        # 如果没有匹配到模板，返回默认模板
        return {
            "name": "默认模板",
            "projects": {},
            "jql": "project = {project} AND issuetype = Bug AND {date_field} >= {start} AND {date_field} <= {end} ORDER BY priority DESC",
            "date_field": "created",
            "time_condition": ""
        }
    
    def _generate_jql(self, template: Dict[str, Any], intent: Dict[str, Any]) -> str:
        """生成JQL查询（从e.py复制）"""
        project = intent.get("project")
        time_range = intent.get("time_range", "本周")
        
        # 动态生成时间条件
        start, end = self._parse_time_range(time_range)
        date_field = template.get("date_field", "created")
        
        # 获取基础JQL
        if isinstance(template.get("projects"), dict) and project in template.get("projects", {}):
            jql = template.get("projects", {}).get(project, "")
        else:
            jql = template.get("jql", "")
        
        # 如果没有获取到JQL，返回空字符串
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
        if start and end and "{start}" not in jql and "{end}" not in jql:
            dynamic_time_condition = f"{date_field} >= {start} AND {date_field} <= {end}"
            time_condition_str = f" {dynamic_time_condition} AND "
            
            # 插入到JQL中合适位置
            if "creator" in jql:
                jql = jql.replace("creator", time_condition_str + "creator")
            elif "ORDER BY" in jql:
                parts = jql.split("ORDER BY")
                jql = parts[0] + time_condition_str + "ORDER BY" + parts[1]
            else:
                if jql:
                    jql = jql + time_condition_str
        
        # 清理多余空格
        jql = ' '.join(jql.split())
        return jql
    
    def _generate_final_jql(self, user_query: str) -> str:
        """生成最终JQL（从e.py复制）"""
        intent = self._recognize_intent(user_query)
        template = self._match_jql_template(intent)
        
        if template and (template.get("jql") or template.get("projects")):
            jql = self._generate_jql(template, intent)
            if jql:
                return jql
        
        # 降级：summary模糊搜索
        project_match = re.search(r'[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?', user_query)
        project = project_match.group() if project_match else "X6840"
        fallback_jql = f"summary ~ '{project}' ORDER BY priority DESC"
        return fallback_jql
    
    def _fetch_all_issues(self, jql: str, user_query: str = None, project_key: str = None) -> List[Dict]:
        """从Jira获取全量问题（集成策略自学习）"""
        import requests
        import base64
        
        # 首先尝试原始JQL
        issues = self._fetch_jira_data(jql)
        
        # 如果原始JQL返回空数据，尝试策略缓存
        if not issues and user_query and project_key:
            from strategy_manager import get_strategy_manager
            strategy_manager = get_strategy_manager()
            
            # 提取关键词
            keywords = strategy_manager.extract_keywords(user_query, project_key)
            
            # 检查是否有缓存策略
            for keyword in keywords:
                cached_jql = strategy_manager.get_strategy(keyword)
                if cached_jql:
                    print(f"[策略缓存] 使用缓存策略: {keyword} -> {cached_jql[:50]}...")
                    issues = self._fetch_jira_data(cached_jql)
                    if issues:
                        return issues
            
            # 如果没有缓存策略，尝试生成备用JQL
            backup_jqls = strategy_manager.generate_backup_jqls(jql, keywords)
            
            for backup_jql in backup_jqls:
                print(f"[策略尝试] 尝试备用JQL: {backup_jql[:50]}...")
                issues = self._fetch_jira_data(backup_jql)
                if issues:
                    # 保存成功的策略
                    for keyword in keywords:
                        strategy_manager.save_strategy(keyword, backup_jql)
                    print(f"[策略保存] 保存成功策略: {keywords[0] if keywords else 'unknown'}")
                    return issues
        
        return issues
    
    def _fetch_jira_data(self, jql: str) -> List[Dict]:
        """实际执行Jira数据获取"""
        import requests
        import base64
        
        all_issues = []
        start_at = 0
        max_results = 100
        
        # 构建认证头
        auth_str = f"{self.JIRA_USERNAME}:{self.JIRA_PASSWORD}"
        auth_bytes = auth_str.encode("ascii")
        base64_auth = base64.b64encode(auth_bytes).decode("ascii")
        
        headers = {
            "Authorization": f"Basic {base64_auth}",
            "Content-Type": "application/json"
        }
        
        while True:
            url = f"{self.JIRA_URL}/rest/api/2/search"
            params = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": "summary,status,priority,issuetype,assignee,created,updated,resolutiondate,resolution,labels,key,customfield_10000,customfield_10001,affectsVersions"
            }
            
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    verify=False,
                    timeout=15
                )
                
                if response.status_code != 200:
                    print(f"请求 Jira 失败，状态码：{response.status_code}")
                    break
                
                data = response.json()
                issues = data.get("issues", [])
                all_issues.extend(issues)
                
                if len(all_issues) >= data.get("total", 0):
                    break
                
                start_at += max_results
            except Exception as e:
                print(f"获取 Jira 数据失败: {e}")
                break
        
        return all_issues
    
    def process(self, user_query: str, context: Dict[str, Any], **kwargs) -> Generator[str, None, None]:
        """处理风险分析查询
        
        Args:
            user_query: 用户查询字符串
            context: 上下文字典，包含conversation_id、project_key等信息
            **kwargs: 其他参数，如project_key、detailed_report等
            
        Yields:
            SSE格式的事件字符串
        """
        # 从context或kwargs中提取参数
        project_key = kwargs.get('project_key') or context.get('project_key') or 'X6840'
        conversation_id = context.get('conversation_id', 'default')
        detailed_report = kwargs.get('detailed_report', False)
        
        # 生成JQL
        jql = self._generate_final_jql(user_query)
        yield generate_sse_message('thinking', f'📋 生成的 JQL: {jql.replace(chr(10), "").replace(chr(13), "")} ')
        
        # 获取数据
        yield generate_sse_message('thinking', '⏳ 正在从 Jira 获取数据... ')
        issues_all = self._fetch_all_issues(jql, user_query, project_key)
        
        if not issues_all:
            yield generate_sse_message('thinking', '⚠️ 未获取到 Jira 问题 ')
            yield generate_sse_message('answer', '暂无问题反馈，请自行前往jira查看\n')
            yield "data: [DONE]\n\n"
            return
        
        yield generate_sse_message('thinking', f'📊 数据统计: 共获取到{len(issues_all)}个问题 ')
        
        # 计算统计数据（临时占位，后续将由stats_calculator.py实现）
        risk_data = self._compute_risk_data(issues_all, project_key)
        
        # 发送数据更新事件给前端
        yield generate_sse_message('data', risk_data)
        
        # 构建AI分析上下文
        data_context = self._build_data_context(issues_all, user_query)
        
        # 调用AI分析
        yield generate_sse_message('thinking', '🤖 专家正在深度分析数据... ')
        yield generate_sse_message('thinking_complete', '')
        
        # 构建AI消息
        messages = []
        # 添加系统提示词
        system_prompt = self.system_prompt  # 简化版本，实际应使用详细提示词
        if detailed_report:
            # 使用详细报告提示词
            pass
        
        # 添加历史对话（如果有）
        history = context.get('history', [])
        for msg in history:
            messages.append(msg)
        
        # 添加当前数据上下文
        messages.append({"role": "user", "content": data_context})
        
        # 调用AI
        response = call_ai_api(messages, system_prompt=system_prompt, stream=True)
        
        if not response:
            yield generate_sse_message('error', 'AI服务异常')
            yield "data: [DONE]\n\n"
            return
        
        # 解析AI流式输出
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
                # 逐字发送回答内容
                yield generate_sse_message('answer', content)
            except json.JSONDecodeError:
                continue
        
        # 发送结束标记
        yield "data: [DONE]\n\n"
    
    def _compute_risk_data(self, issues_all: List[Dict], project_key: str) -> Dict[str, Any]:
        """计算风险数据（临时实现，后续将由stats_calculator.py替换）"""
        # 临时返回空数据，后续实现
        return {
            "project_key": project_key,
            "total_all": len(issues_all),
            "total_unresolved": 0,
            "submission_trend": {},
            "verification_trend": {},
            "resolution_rate": 0,
            "blocking_total": 0,
            "blocking_resolved": 0,
            "mp_block_total": 0,
            "delivery_risk_total": 0,
            "issues": [],
            "issues_unresolved": []
        }
    
    def _build_data_context(self, issues_all: List[Dict], user_query: str) -> str:
        """构建数据上下文（临时实现）"""
        return f"""
        用户查询：「{user_query}」
        已从Jira获取到 {len(issues_all)} 个问题。
        详细分析将在此处进行。
        """