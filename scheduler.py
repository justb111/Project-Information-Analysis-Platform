from typing import Optional
from agents.risk import RiskAgent
from agents.base import BaseAgent


class AgentScheduler:
    def __init__(self):
        self.risk_agent = RiskAgent()
        # 未来可以添加其他代理
        # self.info_agent = InfoAgent()
        # self.project_agent = ProjectAgent()
        self.default_agent = self.risk_agent  # 默认使用风险代理
    
    def route(self, query: str) -> BaseAgent:
        """根据查询内容路由到相应的代理
        
        Args:
            query: 用户查询字符串
            
        Returns:
            BaseAgent: 对应的代理实例
        """
        query_lower = query.lower()
        
        # 风险分析相关关键词
        risk_keywords = ["风险", "bug", "jira", "阻塞", "问题", "分析", "项目", "x6840", "x6856", "x6870", "x6895", "交付", "mp block"]
        
        # 信息查询相关关键词（未来）
        info_keywords = ["器件", "选型", "预装", "配置", "信息", "查询", "资料"]
        
        # 项目管理相关关键词（未来）
        project_keywords = ["进度", "里程碑", "滞后", "计划", "时间", "任务"]
        
        # 检查关键词匹配
        if any(keyword in query_lower for keyword in risk_keywords):
            return self.risk_agent
        
        # 未来扩展：信息查询代理
        # elif any(keyword in query_lower for keyword in info_keywords):
        #     return self.info_agent
        
        # 未来扩展：项目管理代理
        # elif any(keyword in query_lower for keyword in project_keywords):
        #     return self.project_agent
        
        # 默认返回风险代理（因为当前主要功能是风险分析）
        return self.default_agent
    
    def get_agent_by_name(self, name: str) -> Optional[BaseAgent]:
        """根据名称获取代理实例
        
        Args:
            name: 代理名称
            
        Returns:
            BaseAgent: 代理实例，如果不存在则返回None
        """
        agents = {
            "risk": self.risk_agent,
            # "info": self.info_agent,
            # "project": self.project_agent
        }
        return agents.get(name.lower())


# 创建全局调度器实例
scheduler = AgentScheduler()