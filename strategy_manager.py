"""
策略管理器 - 实现JQL策略自学习和缓存
"""

import re
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import redis
import os


class StrategyManager:
    """策略管理器，用于JQL策略自学习和缓存"""
    
    def __init__(self, redis_client=None):
        """
        初始化策略管理器
        
        Args:
            redis_client: Redis客户端实例，如果为None则使用内存缓存
        """
        self.redis_client = redis_client
        self.memory_cache = {}  # 内存缓存，用于测试或Redis不可用时
        self.strategy_ttl = 7 * 24 * 3600  # 策略缓存过期时间：7天
        
    def _get_cache_key(self, keyword: str) -> str:
        """生成缓存键"""
        return f"strategy:{keyword.lower()}"
    
    def save_strategy(self, keyword: str, jql: str):
        """
        保存策略到缓存
        
        Args:
            keyword: 关键词（如项目名、模块名）
            jql: 成功的JQL语句
        """
        cache_key = self._get_cache_key(keyword)
        strategy_data = {
            "jql": jql,
            "keyword": keyword,
            "created_at": datetime.now().isoformat(),
            "hit_count": 1
        }
        
        if self.redis_client:
            try:
                # 使用Redis存储
                self.redis_client.setex(
                    cache_key,
                    self.strategy_ttl,
                    json.dumps(strategy_data, ensure_ascii=False)
                )
                print(f"[策略缓存] 保存策略到Redis: {keyword} -> {jql[:50]}...")
            except Exception as e:
                print(f"[策略缓存] Redis保存失败，使用内存缓存: {e}")
                self.memory_cache[cache_key] = {
                    "data": strategy_data,
                    "expires_at": datetime.now() + timedelta(seconds=self.strategy_ttl)
                }
        else:
            # 使用内存缓存
            self.memory_cache[cache_key] = {
                "data": strategy_data,
                "expires_at": datetime.now() + timedelta(seconds=self.strategy_ttl)
            }
            print(f"[策略缓存] 保存策略到内存: {keyword} -> {jql[:50]}...")
    
    def get_strategy(self, keyword: str) -> Optional[str]:
        """
        从缓存获取策略
        
        Args:
            keyword: 关键词
            
        Returns:
            JQL语句或None
        """
        cache_key = self._get_cache_key(keyword)
        
        if self.redis_client:
            try:
                cached_data = self.redis_client.get(cache_key)
                if cached_data:
                    strategy_data = json.loads(cached_data)
                    # 更新命中次数
                    strategy_data["hit_count"] = strategy_data.get("hit_count", 0) + 1
                    strategy_data["last_used"] = datetime.now().isoformat()
                    self.redis_client.setex(
                        cache_key,
                        self.strategy_ttl,
                        json.dumps(strategy_data, ensure_ascii=False)
                    )
                    print(f"[策略缓存] 从Redis命中策略: {keyword}")
                    return strategy_data["jql"]
            except Exception as e:
                print(f"[策略缓存] Redis读取失败: {e}")
        
        # 检查内存缓存
        if cache_key in self.memory_cache:
            cache_entry = self.memory_cache[cache_key]
            if datetime.now() < cache_entry["expires_at"]:
                # 更新命中次数
                cache_entry["data"]["hit_count"] = cache_entry["data"].get("hit_count", 0) + 1
                cache_entry["data"]["last_used"] = datetime.now().isoformat()
                print(f"[策略缓存] 从内存命中策略: {keyword}")
                return cache_entry["data"]["jql"]
            else:
                # 缓存已过期，删除
                del self.memory_cache[cache_key]
        
        return None
    
    def extract_keywords(self, user_query: str, project_key: str = None) -> List[str]:
        """
        从用户查询中提取关键词
        
        Args:
            user_query: 用户查询
            project_key: 项目键
            
        Returns:
            关键词列表
        """
        keywords = []
        
        # 添加项目键作为关键词
        if project_key:
            keywords.append(project_key.lower())
        
        # 从查询中提取可能的项目键（如X6840, X6856等）
        project_patterns = [
            r'X\d{4}',  # X后跟4位数字
            r'[A-Z]{2,3}\d{1,2}[A-Z]?',  # 类似CN7C, LK7K等
            r'tOS\d+(?:\.\d+)?',  # tOS版本
        ]
        
        for pattern in project_patterns:
            matches = re.findall(pattern, user_query, re.IGNORECASE)
            keywords.extend([match.lower() for match in matches])
        
        # 提取中文关键词
        chinese_keywords = ["风险", "bug", "jira", "阻塞", "问题", "分析", "项目", "交付", "mp block"]
        for keyword in chinese_keywords:
            if keyword in user_query.lower():
                keywords.append(keyword)
        
        # 去重
        return list(set(keywords))
    
    def generate_backup_jqls(self, original_jql: str, keywords: List[str]) -> List[str]:
        """
        生成备用JQL列表
        
        Args:
            original_jql: 原始JQL
            keywords: 关键词列表
            
        Returns:
            备用JQL列表
        """
        backup_jqls = []
        
        # 策略1: 移除resolution is empty条件
        if "resolution is empty" in original_jql.lower():
            backup_jql = original_jql.replace("resolution is empty", "resolution is not empty")
            backup_jqls.append(backup_jql)
        
        # 策略2: 放宽项目名匹配
        for keyword in keywords:
            if keyword.upper().startswith('X') and len(keyword) >= 5:
                # 对于X系列项目，尝试只匹配项目前缀
                project_prefix = keyword.upper()[:3]  # 例如X68
                if f"project = {keyword.upper()}" in original_jql:
                    backup_jql = original_jql.replace(
                        f"project = {keyword.upper()}",
                        f"project ~ \"{project_prefix}*\""
                    )
                    backup_jqls.append(backup_jql)
        
        # 策略3: 移除时间范围限制（如果有）
        if "created >=" in original_jql and "created <=" in original_jql:
            # 尝试匹配时间范围条件并移除
            import re
            time_pattern = r'created >= [^ ]+ AND created <= [^ ]+(?: AND|$)'
            backup_jql = re.sub(time_pattern, '', original_jql).strip()
            if backup_jql.endswith('AND'):
                backup_jql = backup_jql[:-3].strip()
            backup_jqls.append(backup_jql)
        
        # 策略4: 移除部门限制
        if "reporter in (membersOf(" in original_jql:
            backup_jql = original_jql.replace("reporter in (membersOf(", "reporter is not empty AND (")
            backup_jqls.append(backup_jql)
        
        return backup_jqls
    
    def cleanup_expired_strategies(self):
        """清理过期的策略"""
        if self.redis_client:
            try:
                # Redis会自动处理过期，这里只需要清理内存缓存
                pass
            except Exception as e:
                print(f"[策略清理] Redis清理失败: {e}")
        
        # 清理内存缓存
        now = datetime.now()
        expired_keys = []
        for key, entry in self.memory_cache.items():
            if now >= entry["expires_at"]:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.memory_cache[key]
        
        if expired_keys:
            print(f"[策略清理] 清理了 {len(expired_keys)} 个过期策略")


# 全局策略管理器实例
_strategy_manager = None

def get_strategy_manager() -> StrategyManager:
    """获取策略管理器实例"""
    global _strategy_manager
    if _strategy_manager is None:
        # 尝试连接Redis
        redis_client = None
        try:
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
            redis_client = redis.from_url(redis_url, decode_responses=True)
            # 测试连接
            redis_client.ping()
            print("[策略管理器] Redis连接成功")
        except Exception as e:
            print(f"[策略管理器] Redis连接失败，使用内存缓存: {e}")
            redis_client = None
        
        _strategy_manager = StrategyManager(redis_client)
    
    return _strategy_manager