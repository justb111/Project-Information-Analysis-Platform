"""
飞书云文档集成模块
处理飞书文档的权限验证和内容提取
"""

import os
import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse, parse_qs
import requests
import time

# 设置日志
logger = logging.getLogger(__name__)


class FeishuAPIError(Exception):
    """飞书API错误"""
    pass


class FeishuPermissionError(FeishuAPIError):
    """飞书权限错误"""
    pass


class FeishuDocumentParser:
    """飞书文档解析器"""
    
    def __init__(self, app_id: str = None, app_secret: str = None):
        """
        初始化飞书解析器
        
        Args:
            app_id: 飞书应用ID（从环境变量FEISHU_APP_ID获取）
            app_secret: 飞书应用密钥（从环境变量FEISHU_APP_SECRET获取）
        """
        self.app_id = app_id or os.getenv('FEISHU_APP_ID')
        self.app_secret = app_secret or os.getenv('FEISHU_APP_SECRET')
        self.access_token = None
        self.token_expire_time = 0
        
        # API端点
        self.base_url = 'https://open.feishu.cn/open-apis'
        
        # 权限缓存
        self.permission_cache = {}  # doc_id -> {user_id: permissions, expiry}
        
        # 文档内容缓存
        self.document_cache = {}  # doc_id -> {content, metadata, expiry}
        
        logger.info(f"飞书解析器初始化完成，应用ID: {self.app_id[:8] if self.app_id else '未设置'}...")
    
    def get_access_token(self) -> str:
        """获取飞书访问令牌"""
        # 如果令牌未过期，直接返回缓存的令牌
        if self.access_token and self.token_expire_time > time.time():
            return self.access_token
        
        # 从飞书API获取新令牌
        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        payload = {
            'app_id': self.app_id,
            'app_secret': self.app_secret
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != 0:
                raise FeishuAPIError(f"获取访问令牌失败: {data.get('msg', '未知错误')}")
            
            self.access_token = data['tenant_access_token']
            # 令牌有效期通常是2小时，这里设置1小时50分钟以提前刷新
            self.token_expire_time = time.time() + (data.get('expire', 7200) - 600)
            
            logger.info("飞书访问令牌获取成功")
            return self.access_token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"获取飞书访问令牌失败: {e}")
            raise FeishuAPIError(f"网络请求失败: {e}")
    
    def extract_doc_id_from_url(self, url: str) -> Optional[str]:
        """从飞书文档URL提取文档ID"""
        # 飞书文档URL格式示例:
        # https://example.feishu.cn/docs/doccnABC123
        # https://example.feishu.cn/wiki/ABC123
        patterns = [
            r'feishu\.cn/docs/([a-zA-Z0-9]+)',
            r'feishu\.cn/wiki/([a-zA-Z0-9]+)',
            r'doc_id=([a-zA-Z0-9]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        logger.warning(f"无法从URL提取文档ID: {url}")
        return None
    
    def check_permission(self, doc_id: str, user_id: str = None) -> Dict[str, Any]:
        """
        检查用户对文档的权限
        
        Args:
            doc_id: 文档ID
            user_id: 用户ID（可选，默认为当前应用）
            
        Returns:
            权限信息字典，包含read、write、share等权限
        """
        cache_key = f"{doc_id}:{user_id or 'app'}"
        
        # 检查缓存
        if cache_key in self.permission_cache:
            cached = self.permission_cache[cache_key]
            if cached['expiry'] > time.time():
                logger.debug(f"使用缓存的权限信息: {cache_key}")
                return cached['permissions']
        
        # 如果没有配置飞书凭证，返回默认权限
        if not self.app_id or not self.app_secret:
            logger.warning("飞书应用凭证未配置，返回默认权限")
            default_permissions = {
                'can_read': True,
                'can_write': False,
                'can_share': False,
                'can_comment': False,
                'can_download': False,
                'reason': '未配置飞书凭证，使用默认权限'
            }
            self.permission_cache[cache_key] = {
                'permissions': default_permissions,
                'expiry': time.time() + 300  # 5分钟缓存
            }
            return default_permissions
        
        try:
            access_token = self.get_access_token()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # 飞书文档权限检查API（需要对应权限）
            # 注意：实际API可能需要调整
            url = f"{self.base_url}/drive/v1/permissions/{doc_id}/members/{user_id}" if user_id \
                else f"{self.base_url}/drive/v1/permissions/{doc_id}"
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 403:
                # 权限不足
                raise FeishuPermissionError("应用没有权限访问此文档")
            elif response.status_code == 404:
                # 文档不存在或无权限
                raise FeishuPermissionError("文档不存在或没有访问权限")
            
            response.raise_for_status()
            data = response.json()
            
            # 解析权限信息
            permissions = {
                'can_read': data.get('permission', {}).get('read', False),
                'can_write': data.get('permission', {}).get('write', False),
                'can_share': data.get('permission', {}).get('share', False),
                'can_comment': data.get('permission', {}).get('comment', False),
                'can_download': data.get('permission', {}).get('download', False),
                'permission_level': data.get('permission_level', 'unknown'),
                'reason': '权限检查通过'
            }
            
            # 缓存权限信息（1小时）
            self.permission_cache[cache_key] = {
                'permissions': permissions,
                'expiry': time.time() + 3600
            }
            
            logger.info(f"权限检查成功: {doc_id}, 用户: {user_id or 'app'}")
            return permissions
            
        except (requests.exceptions.RequestException, FeishuAPIError) as e:
            logger.error(f"权限检查失败: {doc_id}, 错误: {e}")
            
            # 在错误情况下，根据配置决定是否允许访问
            allow_on_error = os.getenv('FEISHU_ALLOW_ON_ERROR', 'false').lower() == 'true'
            
            if allow_on_error:
                logger.warning(f"权限检查失败但配置允许继续访问: {doc_id}")
                fallback_permissions = {
                    'can_read': True,
                    'can_write': False,
                    'can_share': False,
                    'can_comment': False,
                    'can_download': False,
                    'reason': f'权限检查失败但配置允许访问: {str(e)}'
                }
                return fallback_permissions
            else:
                raise FeishuPermissionError(f"文档权限检查失败: {str(e)}")
    
    def fetch_document_content(self, doc_id: str) -> Dict[str, Any]:
        """
        获取飞书文档内容
        
        Args:
            doc_id: 文档ID
            
        Returns:
            文档内容和元数据
        """
        # 检查缓存
        if doc_id in self.document_cache:
            cached = self.document_cache[doc_id]
            if cached['expiry'] > time.time():
                logger.debug(f"使用缓存的文档内容: {doc_id}")
                return cached['content']
        
        # 如果没有配置飞书凭证，返回模拟内容
        if not self.app_id or not self.app_secret:
            logger.warning("飞书应用凭证未配置，返回模拟内容")
            mock_content = {
                'title': f'飞书文档 {doc_id}',
                'content': '由于未配置飞书应用凭证，无法获取真实文档内容。请配置FEISHU_APP_ID和FEISHU_APP_SECRET环境变量。',
                'blocks': [{
                    'type': 'paragraph',
                    'content': '这是飞书文档的模拟内容。'
                }],
                'metadata': {
                    'created_at': '2026-01-01T00:00:00Z',
                    'updated_at': '2026-01-01T00:00:00Z',
                    'owner': '模拟用户',
                    'size': 100
                }
            }
            self.document_cache[doc_id] = {
                'content': mock_content,
                'expiry': time.time() + 300  # 5分钟缓存
            }
            return mock_content
        
        try:
            access_token = self.get_access_token()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # 飞书文档内容API
            url = f"{self.base_url}/docx/v1/documents/{doc_id}/raw_content"
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 403:
                raise FeishuPermissionError("没有权限读取文档内容")
            elif response.status_code == 404:
                raise FeishuPermissionError("文档不存在")
            
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != 0:
                raise FeishuAPIError(f"获取文档内容失败: {data.get('msg', '未知错误')}")
            
            document_content = {
                'title': data.get('data', {}).get('document', {}).get('title', ''),
                'content': data.get('data', {}).get('content', ''),
                'blocks': data.get('data', {}).get('blocks', []),
                'metadata': {
                    'created_at': data.get('data', {}).get('document', {}).get('create_time', ''),
                    'updated_at': data.get('data', {}).get('document', {}).get('edit_time', ''),
                    'owner': data.get('data', {}).get('document', {}).get('owner_id', ''),
                    'size': len(str(data.get('data', {}).get('content', '')))
                }
            }
            
            # 缓存文档内容（30分钟）
            self.document_cache[doc_id] = {
                'content': document_content,
                'expiry': time.time() + 1800
            }
            
            logger.info(f"文档内容获取成功: {doc_id}, 大小: {document_content['metadata']['size']} 字符")
            return document_content
            
        except (requests.exceptions.RequestException, FeishuAPIError) as e:
            logger.error(f"获取文档内容失败: {doc_id}, 错误: {e}")
            raise FeishuAPIError(f"获取文档内容失败: {str(e)}")
    
    def parse_document_to_elements(self, doc_id: str) -> List[Dict[str, Any]]:
        """
        将飞书文档解析为内容元素
        
        Args:
            doc_id: 文档ID
            
        Returns:
            内容元素列表，每个元素包含type、content、metadata
        """
        try:
            document = self.fetch_document_content(doc_id)
            elements = []
            
            # 添加标题作为单独元素
            if document.get('title'):
                elements.append({
                    'element_type': 'heading',
                    'content': document['title'],
                    'metadata': {
                        'level': 1,
                        'source': 'feishu',
                        'doc_id': doc_id
                    }
                })
            
            # 解析内容块
            blocks = document.get('blocks', [])
            for i, block in enumerate(blocks):
                block_type = block.get('type', 'paragraph')
                block_content = block.get('content', '')
                
                if block_type == 'paragraph' and block_content:
                    elements.append({
                        'element_type': 'text',
                        'content': block_content,
                        'metadata': {
                            'block_index': i,
                            'block_type': block_type,
                            'source': 'feishu',
                            'doc_id': doc_id
                        }
                    })
                elif block_type == 'heading':
                    elements.append({
                        'element_type': 'heading',
                        'content': block_content,
                        'metadata': {
                            'level': block.get('level', 2),
                            'block_index': i,
                            'source': 'feishu',
                            'doc_id': doc_id
                        }
                    })
                elif block_type == 'table':
                    # 表格处理
                    table_data = block.get('table_content', [])
                    elements.append({
                        'element_type': 'table',
                        'content': table_data,
                        'metadata': {
                            'block_index': i,
                            'rows': len(table_data),
                            'columns': len(table_data[0]) if table_data else 0,
                            'source': 'feishu',
                            'doc_id': doc_id
                        }
                    })
                elif block_type == 'bullet' or block_type == 'ordered':
                    # 列表处理
                    list_items = block.get('items', [])
                    for item in list_items:
                        if item.get('content'):
                            elements.append({
                                'element_type': 'text',
                                'content': f"• {item['content']}",
                                'metadata': {
                                    'block_index': i,
                                    'list_type': block_type,
                                    'source': 'feishu',
                                    'doc_id': doc_id
                                }
                            })
            
            logger.info(f"文档解析完成: {doc_id}, 生成 {len(elements)} 个元素")
            return elements
            
        except Exception as e:
            logger.error(f"文档解析失败: {doc_id}, 错误: {e}")
            raise
    
    def get_document_info(self, url: str) -> Dict[str, Any]:
        """
        获取飞书文档信息
        
        Args:
            url: 飞书文档URL
            
        Returns:
            文档信息字典
        """
        doc_id = self.extract_doc_id_from_url(url)
        if not doc_id:
            raise ValueError(f"无效的飞书文档URL: {url}")
        
        try:
            # 检查权限
            permissions = self.check_permission(doc_id)
            
            # 获取文档基本信息
            if self.app_id and self.app_secret:
                access_token = self.get_access_token()
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                }
                
                url = f"{self.base_url}/docx/v1/documents/{doc_id}"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0:
                        doc_info = data.get('data', {}).get('document', {})
                    else:
                        doc_info = {}
                else:
                    doc_info = {}
            else:
                doc_info = {}
            
            return {
                'doc_id': doc_id,
                'url': url,
                'permissions': permissions,
                'title': doc_info.get('title', f'飞书文档 {doc_id}'),
                'owner': doc_info.get('owner_id', '未知'),
                'created_at': doc_info.get('create_time', ''),
                'updated_at': doc_info.get('edit_time', ''),
                'size': doc_info.get('size', 0),
                'can_access': permissions.get('can_read', False),
                'reason': permissions.get('reason', '')
            }
            
        except Exception as e:
            logger.error(f"获取文档信息失败: {url}, 错误: {e}")
            return {
                'doc_id': doc_id,
                'url': url,
                'permissions': {'can_read': False, 'reason': str(e)},
                'title': f'飞书文档 {doc_id}',
                'owner': '未知',
                'can_access': False,
                'error': str(e)
            }


# 全局实例
_feishu_parser = None

def get_feishu_parser() -> FeishuDocumentParser:
    """获取飞书解析器全局实例"""
    global _feishu_parser
    if _feishu_parser is None:
        _feishu_parser = FeishuDocumentParser()
    return _feishu_parser


# 工具函数
def validate_feishu_url(url: str) -> Tuple[bool, str, Optional[str]]:
    """验证飞书文档URL"""
    if not url:
        return False, "URL不能为空", None
    
    # 检查是否是飞书域名
    parsed = urlparse(url)
    if 'feishu.cn' not in parsed.netloc and 'larksuite.com' not in parsed.netloc:
        return False, "不是有效的飞书文档URL", None
    
    # 提取文档ID
    parser = FeishuDocumentParser()
    doc_id = parser.extract_doc_id_from_url(url)
    
    if not doc_id:
        return False, "无法从URL提取文档ID", None
    
    return True, "URL验证成功", doc_id


def check_feishu_permission(url: str, user_id: str = None) -> Dict[str, Any]:
    """检查飞书文档权限（便捷函数）"""
    parser = get_feishu_parser()
    doc_id = parser.extract_doc_id_from_url(url)
    
    if not doc_id:
        return {
            'can_access': False,
            'reason': '无效的飞书文档URL',
            'doc_id': None
        }
    
    try:
        permissions = parser.check_permission(doc_id, user_id)
        return {
            'can_access': permissions.get('can_read', False),
            'permissions': permissions,
            'doc_id': doc_id,
            'reason': permissions.get('reason', '权限检查完成')
        }
    except Exception as e:
        return {
            'can_access': False,
            'reason': f'权限检查失败: {str(e)}',
            'doc_id': doc_id,
            'error': str(e)
        }


if __name__ == "__main__":
    print("飞书集成模块测试")
    print("=" * 60)
    
    # 测试URL验证
    test_url = "https://example.feishu.cn/docs/doccnABC123"
    is_valid, message, doc_id = validate_feishu_url(test_url)
    print(f"URL验证: {test_url}")
    print(f"  有效: {is_valid}")
    print(f"  消息: {message}")
    print(f"  文档ID: {doc_id}")
    
    # 测试解析器初始化
    parser = FeishuDocumentParser()
    print(f"\n解析器初始化: {'成功' if parser else '失败'}")
    print(f"  应用ID配置: {'是' if parser.app_id else '否'}")
    print(f"  应用密钥配置: {'是' if parser.app_secret else '否'}")
    
    # 测试权限检查（模拟）
    print("\n权限检查测试:")
    permissions = parser.check_permission("doccnABC123")
    for key, value in permissions.items():
        print(f"  {key}: {value}")
    
    print("\n✅ 飞书集成模块测试完成")