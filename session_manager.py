"""
Redis会话管理器 - 使用Redis存储会话数据
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any
import json
import redis
import os


class RedisSessionManager:
    """Redis会话管理器"""
    
    def __init__(self, redis_client=None):
        """
        初始化Redis会话管理器
        
        Args:
            redis_client: Redis客户端实例，如果为None则自动创建
        """
        self.redis_client = redis_client
        self.session_ttl = 3600  # 会话过期时间：1小时
        self.max_messages_per_session = 50  # 每个会话最大消息数
        
        if self.redis_client is None:
            self._init_redis_client()
    
    def _init_redis_client(self):
        """初始化Redis客户端"""
        try:
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            # 测试连接
            self.redis_client.ping()
            print("[会话管理器] Redis连接成功")
        except Exception as e:
            print(f"[会话管理器] Redis连接失败: {e}")
            print("[会话管理器] 使用内存存储（重启后数据会丢失）")
            self.redis_client = None
            self.memory_sessions = {}
    
    def _get_session_key(self, conversation_id: str) -> str:
        """生成会话键"""
        return f"conv:{conversation_id}"
    
    def _get_messages_key(self, conversation_id: str) -> str:
        """生成消息列表键"""
        return f"conv:{conversation_id}:messages"
    
    def get_context(self, conversation_id: str, max_messages: int = 10) -> Dict[str, Any]:
        """
        获取会话上下文
        
        Args:
            conversation_id: 会话ID
            max_messages: 最大消息数
        
        Returns:
            包含会话上下文的字典
        """
        if self.redis_client:
            try:
                # 从Redis获取会话数据
                session_key = self._get_session_key(conversation_id)
                messages_key = self._get_messages_key(conversation_id)
                
                # 获取会话元数据
                session_data = self.redis_client.hgetall(session_key)
                
                # 获取消息列表
                messages = []
                if self.redis_client.exists(messages_key):
                    # 获取最近的消息
                    message_count = self.redis_client.llen(messages_key)
                    start_index = max(0, message_count - max_messages)
                    message_data = self.redis_client.lrange(messages_key, start_index, -1)
                    
                    for msg_json in message_data:
                        try:
                            messages.append(json.loads(msg_json))
                        except:
                            pass
                
                if not session_data:
                    # 创建新会话
                    session_data = {
                        "conversation_id": conversation_id,
                        "project_key": None,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat()
                    }
                    self.redis_client.hset(session_key, mapping=session_data)
                    self.redis_client.expire(session_key, self.session_ttl)
                
                return {
                    "conversation_id": conversation_id,
                    "messages": messages,
                    "project_key": session_data.get("project_key"),
                    "created_at": session_data.get("created_at", datetime.now().isoformat())
                }
                
            except Exception as e:
                print(f"[会话管理器] Redis读取失败: {e}")
                # 降级到内存存储
                return self._get_context_from_memory(conversation_id, max_messages)
        else:
            # 使用内存存储
            return self._get_context_from_memory(conversation_id, max_messages)
    
    def _get_context_from_memory(self, conversation_id: str, max_messages: int) -> Dict[str, Any]:
        """从内存获取会话上下文"""
        if conversation_id not in self.memory_sessions:
            return {
                "conversation_id": conversation_id,
                "messages": [],
                "project_key": None,
                "created_at": datetime.now().isoformat()
            }
        
        session = self.memory_sessions[conversation_id]
        # 返回最近的消息
        recent_messages = session.get("messages", [])[-max_messages:]
        
        return {
            "conversation_id": conversation_id,
            "messages": recent_messages,
            "project_key": session.get("project_key"),
            "created_at": session.get("created_at", datetime.now().isoformat())
        }
    
    def add_message(self, conversation_id: str, role: str, content: str, project_key: str = None):
        """
        添加消息到会话
        
        Args:
            conversation_id: 会话ID
            role: 角色（user/assistant）
            content: 消息内容
            project_key: 项目键
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        if self.redis_client:
            try:
                session_key = self._get_session_key(conversation_id)
                messages_key = self._get_messages_key(conversation_id)
                
                # 检查会话是否存在
                if not self.redis_client.exists(session_key):
                    # 创建新会话
                    session_data = {
                        "conversation_id": conversation_id,
                        "project_key": project_key,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat()
                    }
                    self.redis_client.hset(session_key, mapping=session_data)
                else:
                    # 更新会话
                    self.redis_client.hset(session_key, "updated_at", datetime.now().isoformat())
                    if project_key:
                        self.redis_client.hset(session_key, "project_key", project_key)
                
                # 设置过期时间
                self.redis_client.expire(session_key, self.session_ttl)
                
                # 添加消息到列表
                message_json = json.dumps(message, ensure_ascii=False)
                self.redis_client.rpush(messages_key, message_json)
                self.redis_client.expire(messages_key, self.session_ttl)
                
                # 限制消息数量
                message_count = self.redis_client.llen(messages_key)
                if message_count > self.max_messages_per_session:
                    # 删除旧消息
                    self.redis_client.ltrim(messages_key, -self.max_messages_per_session, -1)
                
                print(f"[会话管理器] 消息已保存到Redis: {conversation_id}")
                
            except Exception as e:
                print(f"[会话管理器] Redis保存失败: {e}")
                # 降级到内存存储
                self._add_message_to_memory(conversation_id, role, content, project_key)
        else:
            # 使用内存存储
            self._add_message_to_memory(conversation_id, role, content, project_key)
    
    def _add_message_to_memory(self, conversation_id: str, role: str, content: str, project_key: str = None):
        """添加消息到内存存储"""
        if conversation_id not in self.memory_sessions:
            self.memory_sessions[conversation_id] = {
                "messages": [],
                "project_key": project_key,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        
        session = self.memory_sessions[conversation_id]
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        session["messages"].append(message)
        session["updated_at"] = datetime.now().isoformat()
        
        # 如果提供了新的project_key，更新会话
        if project_key:
            session["project_key"] = project_key
        
        # 限制消息数量，防止内存泄漏
        if len(session["messages"]) > self.max_messages_per_session:
            session["messages"] = session["messages"][-self.max_messages_per_session:]
    
    def clear_session(self, conversation_id: str):
        """清除会话"""
        if self.redis_client:
            try:
                session_key = self._get_session_key(conversation_id)
                messages_key = self._get_messages_key(conversation_id)
                
                self.redis_client.delete(session_key)
                self.redis_client.delete(messages_key)
                
                print(f"[会话管理器] Redis会话已清除: {conversation_id}")
            except Exception as e:
                print(f"[会话管理器] Redis清除失败: {e}")
                # 降级到内存清除
                if conversation_id in self.memory_sessions:
                    del self.memory_sessions[conversation_id]
        else:
            # 清除内存会话
            if conversation_id in self.memory_sessions:
                del self.memory_sessions[conversation_id]
    
    def cleanup_expired_sessions(self):
        """清理过期的会话"""
        if self.redis_client:
            try:
                # Redis会自动处理过期，这里只需要清理内存会话
                pass
            except Exception as e:
                print(f"[会话清理] Redis清理失败: {e}")
        
        # 清理内存会话（如果超过24小时未更新）
        now = datetime.now()
        expired_keys = []
        for key, session in self.memory_sessions.items():
            updated_at_str = session.get("updated_at")
            if updated_at_str:
                try:
                    updated_at = datetime.fromisoformat(updated_at_str)
                    if now - updated_at > timedelta(hours=24):
                        expired_keys.append(key)
                except:
                    expired_keys.append(key)
        
        for key in expired_keys:
            del self.memory_sessions[key]
        
        if expired_keys:
            print(f"[会话清理] 清理了 {len(expired_keys)} 个过期会话")


# 全局会话管理器实例
session_manager = RedisSessionManager()