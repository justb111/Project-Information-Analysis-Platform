"""LangChain工具定义：Jira数据获取、JQL生成、风险分析等"""

import os
import re
import base64
import requests
import socket
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import json
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

# 环境变量
JIRA_URL = os.getenv("JIRA_URL", "http://jira.transsion.com")
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD", "")


def _log(kind: str, msg: str) -> None:
    """简单的日志函数"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color_codes = {
        "debug": "\033[90m",
        "info": "\033[94m",
        "warn": "\033[93m",
        "error": "\033[91m",
        "success": "\033[92m",
    }
    color = color_codes.get(kind, "\033[0m")
    print(f"{color}[{timestamp}] [{kind.upper():<7}] {msg}\033[0m")


class JiraQueryInput(BaseModel):
    """Jira查询工具的输入参数"""
    jql: str = Field(description="JQL查询语句")
    max_results: Optional[int] = Field(default=100, description="最大返回结果数")


class JiraQueryTool(BaseTool):
    """Jira数据查询工具"""
    name: str = "jira_query"
    description: str = """查询Jira问题数据。输入应为有效的JQL语句，支持分页获取最多1000条数据。"""
    args_schema: type = JiraQueryInput
    
    # Pydantic字段定义
    jira_username: Optional[str] = None
    jira_password: Optional[str] = None
    jira_url: Optional[str] = None
    
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None, url: Optional[str] = None, **kwargs):
        """初始化Jira查询工具，支持自定义凭据"""
        # 设置字段值
        kwargs['jira_username'] = username or JIRA_USERNAME
        kwargs['jira_password'] = password or JIRA_PASSWORD
        kwargs['jira_url'] = url or JIRA_URL
        super().__init__(**kwargs)

    def _run(self, jql: str, max_results: Optional[int] = 100) -> List[Dict]:
        """执行Jira查询"""
        if not self.jira_username or not self.jira_password or not self.jira_url:
            _log("error", "Jira环境变量未设置！请设置JIRA_USERNAME、JIRA_PASSWORD、JIRA_URL环境变量。")
            return []
        
        # 测试Jira服务器连接
        try:
            parsed_url = urlparse(self.jira_url)
            jira_host = parsed_url.hostname
            jira_port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((jira_host, jira_port))
            sock.close()
            
            if result != 0:
                _log("warn", f"Jira服务器连接可能有问题，但仍尝试HTTP请求")
        except Exception as e:
            _log("warn", f"Jira连接测试异常: {e}")
        
        all_issues = []
        start_at = 0
        page_size = min(100, max_results)
        
        # 构建认证头
        auth_str = f"{self.jira_username}:{self.jira_password}"
        auth_bytes = auth_str.encode("ascii")
        base64_auth = base64.b64encode(auth_bytes).decode("ascii")
        
        headers = {
            "Authorization": f"Basic {base64_auth}",
            "Content-Type": "application/json"
        }
        
        while True:
            if len(all_issues) >= max_results:
                break
                
            url = f"{self.jira_url}/rest/api/2/search"
            params = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": "summary,status,priority,issuetype,assignee,created,updated,resolutiondate,resolution,labels,key,customfield_10000,customfield_10001,affectsVersions"
            }
            
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    issues = data.get("issues", [])
                    all_issues.extend(issues)
                    
                    total = data.get("total", 0)
                    if len(all_issues) >= total or len(all_issues) >= max_results:
                        break
                    
                    start_at += page_size
                else:
                    _log("error", f"Jira请求失败: {response.status_code} - {response.text[:200]}")
                    break
            except Exception as e:
                _log("error", f"Jira查询异常: {e}")
                break
        
        _log("info", f"Jira查询完成，获取到 {len(all_issues)} 个问题")
        return all_issues
    
    async def _arun(self, jql: str, max_results: Optional[int] = 100) -> List[Dict]:
        """异步执行Jira查询"""
        return self._run(jql, max_results)


class JQLGenerationInput(BaseModel):
    """JQL生成工具的输入参数"""
    project_key: str = Field(description="项目键，如X6840")
    user_query: str = Field(description="用户查询内容")


class JQLGenerationTool(BaseTool):
    """JQL语句生成工具"""
    name: str = "jql_generation"
    description: str = """根据项目键和用户查询生成JQL语句。"""
    args_schema: type = JQLGenerationInput

    def _run(self, project_key: str, user_query: str) -> str:
        """生成JQL语句"""
        # 简化版的JQL生成逻辑，实际应从现有代码提取
        from e import generate_final_jql
        try:
            jql = generate_final_jql(user_query)
            _log("info", f"生成的JQL: {jql[:100]}...")
            return jql
        except Exception as e:
            _log("error", f"JQL生成失败: {e}")
            # 返回默认JQL
            return f'project = {project_key} AND type = Bug ORDER BY created DESC'
    
    async def _arun(self, project_key: str, user_query: str) -> str:
        """异步生成JQL语句"""
        return self._run(project_key, user_query)


class CommonIssueAnalysisInput(BaseModel):
    """共性问题分析工具的输入参数"""
    time_range: Optional[str] = Field(default="7天", description="时间范围，如'7天'、'30天'、'全部'")


class CommonIssueAnalysisTool(BaseTool):
    """共性问题分析工具"""
    name: str = "common_issue_analysis"
    description: str = """分析tOS库中的共性问题。"""
    args_schema: type = CommonIssueAnalysisInput

    def _run(self, time_range: Optional[str] = "7天") -> Dict[str, Any]:
        """分析共性问题"""
        from e import fetch_tos_issues, cluster_common_issues
        try:
            if time_range and time_range.lower() != '全部':
                issues = fetch_tos_issues(time_range)
            else:
                issues = fetch_tos_issues()
            
            common_clusters = cluster_common_issues(issues)
            _log("info", f"共性问题聚类成功: {len(common_clusters)} 个模块聚类")
            
            # 准备返回数据
            result = {
                "total_issues": len(issues),
                "cluster_count": len(common_clusters),
                "clusters": {}
            }
            
            # 只返回前10个聚类
            for mod, keys in list(common_clusters.items())[:10]:
                result["clusters"][mod] = {
                    "count": len(keys),
                    "example_ids": keys[:3]
                }
            
            return result
        except Exception as e:
            _log("error", f"共性问题分析失败: {e}")
            return {"error": str(e), "total_issues": 0, "cluster_count": 0, "clusters": {}}
    
    async def _arun(self, time_range: Optional[str] = "7天") -> Dict[str, Any]:
        """异步分析共性问题"""
        return self._run(time_range)


class RiskAnalysisInput(BaseModel):
    """风险分析工具的输入参数"""
    issues: List[Dict] = Field(description="Jira问题列表")
    project_key: str = Field(description="项目键")


class RiskAnalysisTool(BaseTool):
    """风险分析工具"""
    name: str = "risk_analysis"
    description: str = """分析Jira问题的风险等级。"""
    args_schema: type = RiskAnalysisInput

    def _run(self, issues: List[Dict], project_key: str) -> Dict[str, Any]:
        """分析风险"""
        if not issues:
            return {
                "project_key": project_key,
                "total_issues": 0,
                "unresolved_count": 0,
                "risk_summary": "无问题数据",
                "high_risk_issues": []
            }
        
        # 统计信息
        unresolved_count = 0
        high_risk_issues = []
        
        for issue in issues:
            fields = issue.get('fields', {})
            resolution = fields.get('resolution')
            
            # 判断是否未解决
            if resolution is None or str(resolution).strip() == '':
                unresolved_count += 1
                
                # 简单风险判断（可根据实际需求扩展）
                priority = str(fields.get('priority', '')).lower()
                summary = str(fields.get('summary', '')).lower()
                
                is_high_risk = False
                if 'critical' in priority or 'highest' in priority:
                    is_high_risk = True
                elif 'blocker' in summary or 'critical' in summary or '崩溃' in summary or '死机' in summary:
                    is_high_risk = True
                
                if is_high_risk:
                    high_risk_issues.append({
                        "key": issue.get('key'),
                        "summary": fields.get('summary'),
                        "priority": fields.get('priority'),
                        "status": fields.get('status', {}).get('name')
                    })
        
        # 生成风险摘要
        risk_level = "低"
        if unresolved_count > 20:
            risk_level = "高"
        elif unresolved_count > 10:
            risk_level = "中"
        
        result = {
            "project_key": project_key,
            "total_issues": len(issues),
            "unresolved_count": unresolved_count,
            "risk_level": risk_level,
            "risk_summary": f"共发现{unresolved_count}个未解决问题，风险等级：{risk_level}",
            "high_risk_issues": high_risk_issues[:5]  # 只返回前5个高风险问题
        }
        
        _log("info", f"风险分析完成: {result['risk_summary']}")
        return result
    
    async def _arun(self, issues: List[Dict], project_key: str) -> Dict[str, Any]:
        """异步分析风险"""
        return self._run(issues, project_key)


# 工具集合
def get_all_tools() -> List[BaseTool]:
    """获取所有工具"""
    return [
        JiraQueryTool(),
        JQLGenerationTool(),
        CommonIssueAnalysisTool(),
        RiskAnalysisTool()
    ]