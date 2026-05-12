"""LangChain版本的Flask应用：使用LangChain框架，保持现有API接口"""

import sys
import os
import io

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 解决Windows控制台编码问题
# 注意：禁用以下代码，因为它可能导致I/O操作在已关闭的文件上执行
# if sys.platform == "win32":
#     # 设置标准输出编码为UTF-8
#     sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
#     sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
#     # 设置默认编码
#     import locale
#     if locale.getpreferredencoding().lower() != 'utf-8':
#         os.environ['PYTHONIOENCODING'] = 'utf-8'

import json
import re
import time
import base64
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse
import socket
import uuid

# 业务逻辑导入
from e import fetch_all_issues, format_portfolio_data
from utils import call_ai_api
from progress_analyzer import parse_progress_excel, analyze_with_intelligence
from risk_agent import RiskAnalysisAgent, get_kanban_page_data

# 知识库系统导入
if os.getenv('DISABLE_KNOWLEDGE') == '1':
    KNOWLEDGE_SYSTEM_AVAILABLE = False
    print("✅ 知识库系统已禁用（DISABLE_KNOWLEDGE=1）")
else:
    try:
        from knowledge_api import knowledge_bp
        KNOWLEDGE_SYSTEM_AVAILABLE = True
        print(f"✅ 知识库蓝图导入成功: {knowledge_bp}, url_prefix={knowledge_bp.url_prefix}")
    except ImportError as e:
        KNOWLEDGE_SYSTEM_AVAILABLE = False
        print(f"警告: 知识库系统不可用，知识库API将不可用: {e}")
    except Exception as e:
        KNOWLEDGE_SYSTEM_AVAILABLE = False
        print(f"警告: 知识库系统导入时发生错误: {e}")

from workforce_api import workforce_bp
print(f"✅ 人力洞察蓝图导入成功: {workforce_bp}, url_prefix={workforce_bp.url_prefix}")

from delivery_api import delivery_bp
print(f"✅ 交付路线图蓝图导入成功: {delivery_bp}, url_prefix={delivery_bp.url_prefix}")

# Flask相关
from flask import Flask, request, jsonify, Response, send_from_directory, render_template_string
from flask_cors import CORS

# 让 Windows PowerShell 下中文输出尽量正常
# 注意：禁用以下代码，因为它可能导致I/O操作在已关闭的文件上执行
# try:
#     sys.stdout.reconfigure(encoding="utf-8")
#     sys.stderr.reconfigure(encoding="utf-8")
# except Exception:

def fix_gbk_utf8_mixed_string(s: str) -> str:
    """修复GBK/UTF-8混合编码的字符串
    
    在Windows环境中，UTF-8编码的URL参数可能被错误解码为GBK。
    这个函数尝试检测并修复这种编码问题。
    """
    if not s:
        return s
    
    # 如果字符串已经是有效的UTF-8，直接返回
    try:
        s.encode('utf-8').decode('utf-8')
        return s
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    
    # 尝试修复：将字符串编码为GBK（假设它当前是GBK解码的UTF-8字节）
    # 然后解码为UTF-8
    try:
        # 先编码为GBK（获取原始字节）
        gbk_bytes = s.encode('gbk', errors='ignore')
        # 然后将这些字节解码为UTF-8
        fixed = gbk_bytes.decode('utf-8', errors='ignore')
        if fixed and fixed != s:
            print(f"[编码修复] 修复字符串: {repr(s)} -> {repr(fixed)}")
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError) as e:
        print(f"[编码修复] 修复失败: {e}, 字符串: {repr(s)}")
    
    # 如果修复失败，尝试另一种方法：直接使用UTF-8编码并忽略错误
    try:
        utf8_bytes = s.encode('utf-8', errors='ignore')
        fixed = utf8_bytes.decode('utf-8', errors='ignore')
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        print(f"[编码修复] UTF-8回退失败: {e}")
    
    return s
#     pass


def _log(kind: str, msg: str) -> None:
    """简单的日志函数，同时输出到控制台和文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color_codes = {
        "debug": "\033[90m",
        "info": "\033[94m",
        "warn": "\033[93m",
        "error": "\033[91m",
        "success": "\033[92m",
    }
    color = color_codes.get(kind, "\033[0m")
    console_msg = f"{color}[{timestamp}] [{kind.upper():<7}] {msg}\033[0m"
    print(console_msg)
    
    # 同时写入日志文件
    log_file = os.path.join(os.path.dirname(__file__), "app.log")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            # 去除颜色转义码
            plain_msg = f"[{timestamp}] [{kind.upper():<7}] {msg}"
            f.write(plain_msg + "\n")
    except Exception as e:
        # 如果文件写入失败，只打印到控制台
        print(f"{color}[{timestamp}] [ERROR  ] 写入日志文件失败: {e}\033[0m")


def get_friendly_ai_error(exception=None):
    """获取用户友好的AI错误消息"""
    if exception:
        # 根据异常类型提供更具体的错误信息
        error_msg = str(exception)
        error_type = type(exception).__name__
        
        # 常见错误类型映射
        if "api_key" in error_msg.lower() or "OPENAI_API_KEY" in error_msg:
            return "🔑 AI服务认证失败：API密钥未设置或无效。请检查AI_API_KEY环境变量配置。"
        elif "connection" in error_msg.lower() or "network" in error_msg.lower():
            return "🌐 网络连接失败：无法连接到AI服务。请检查网络连接和AI_BASE_URL配置。"
        elif "timeout" in error_msg.lower():
            return "⏰ 请求超时：AI服务响应时间过长。请稍后重试或联系管理员检查AI服务状态。"
        elif "rate limit" in error_msg.lower():
            return "🚦 速率限制：AI服务请求过于频繁。请稍后重试。"
        elif "model" in error_msg.lower():
            return "🤖 模型错误：指定的AI模型不可用。请检查AI_MODEL配置。"
        else:
            return f"⚠️ AI服务错误 ({error_type})：{error_msg[:200]}"
    
    # 如果没有异常信息，返回通用错误
    import time
    options = [
        "AI服务暂时不可用，请稍后重试",
        "网络连接不稳定，请检查网络后重试",
        "AI服务繁忙，请稍后再试",
        "服务器响应超时，请稍后重试",
        "服务暂时不可用，请联系管理员"
    ]
    index = int(time.time() * 1000) % len(options)
    return options[index]


def generate_sse_message(event_type: str, data: Any) -> str:
    """生成SSE格式消息
    
    Args:
        event_type: 事件类型，如 'thinking', 'answer', 'data', 'error'
        data: 事件数据，可以是字符串、字典或其他可JSON序列化的类型
    """
    print(f"[generate_sse_message] event_type: {event_type}, data type: {type(data)}")
    import sys
    sys.stdout.flush()
    
    # 如果data已经是JSON字符串，避免双重编码
    if isinstance(data, str):
        try:
            # 尝试解析，如果是JSON字符串，则使用解析后的对象
            parsed = json.loads(data)
            data = parsed
        except json.JSONDecodeError:
            # 不是JSON字符串，保持原样
            pass
    
    result = f"data: {json.dumps({'type': event_type, 'content': data}, ensure_ascii=False)}\n\n"
    print(f"[generate_sse_message] result: {repr(result[:200])}...")
    sys.stdout.flush()
    return result


def send_thinking_chars(text: str, delay: float = 0.05):
    """发送思考字符（模拟打字效果）"""
    import time
    for char in text:
        yield generate_sse_message('thinking', char)
        time.sleep(delay)


def send_answer_chars(text: str, delay: float = 0.03):
    """发送回答字符（模拟打字效果）"""
    import time
    # 修复文本编码
    text = fix_gbk_utf8_mixed_string(text)
    for char in text:
        yield generate_sse_message('answer', char)
        time.sleep(delay)


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
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        os.environ[key] = value
                        loaded_vars.append(key)
            _log("info", f"已加载环境变量文件: {env_path}")
            _log("info", f"加载的变量: {', '.join(loaded_vars)}")
        except Exception as e:
            _log("error", f"加载 .env 文件失败: {e}")

# 在读取配置前加载 .env 文件
load_env_file()

# 配置
JIRA_URL = os.getenv("JIRA_URL", "http://jira.transsion.com")
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD", "")

# Flask应用
app = Flask(__name__)

# 启用CORS支持，允许所有来源，支持凭证
# 暂时注释CORS以调试路由问题
# CORS(app, 
#      resources={r"/api/*": {"origins": "*"}},
#      supports_credentials=True,
#      allow_headers=["Content-Type", "Authorization"],
#      methods=["GET", "POST", "OPTIONS"])

# 注册知识库蓝图
if KNOWLEDGE_SYSTEM_AVAILABLE:
    print(f"✅ 知识库系统可用，准备注册蓝图")
    print(f"  蓝图对象: {knowledge_bp}")
    print(f"  蓝图名称: {knowledge_bp.name}")
    print(f"  蓝图URL前缀: {knowledge_bp.url_prefix}")
    print(f"  蓝图导入名称: {knowledge_bp.import_name}")
    
    # 注册前检查蓝图路由（通过deferred_functions）
    print("  蓝图中的路由（deferred_functions）:")
    for func in knowledge_bp.deferred_functions:
        # func是一个函数，我们无法直接获取路由信息，但可以打印函数名
        print(f"    装饰器函数: {func.__name__ if hasattr(func, '__name__') else func}")
    
    app.register_blueprint(knowledge_bp)
    print("✅ 知识库API已注册: /api/knowledge/*")
    
    # 添加请求日志记录（调试路由问题）
    @app.before_request
    def log_request():
        _log("debug", f"请求到达: {request.method} {request.path} -> 端点: {request.endpoint}")
        _log("debug", f"环境变量 PATH_INFO: {request.environ.get('PATH_INFO')}, SCRIPT_NAME: {request.environ.get('SCRIPT_NAME')}")
        _log("debug", f"完整 URL: {request.url}")
    
    @app.after_request
    def log_response(response):
        _log("debug", f"响应状态: {response.status_code} for {request.path}")
        return response
    
    # 直接测试路由（绕过蓝图）
    @app.route('/api/knowledge/debug2')
    def direct_knowledge_test():
        return jsonify({'success': True, 'message': 'Direct route works'})

    # 注册后检查应用路由
    print("  应用中的知识库路由:")
    knowledge_routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('knowledge.'):
            knowledge_routes.append(rule.rule)
            print(f"    {rule.rule} -> {rule.endpoint}")
    print(f"  总计: {len(knowledge_routes)} 个路由")
    
    # 调试路由：列出知识库路由
    @app.route('/debug/knowledge-routes')
    def debug_knowledge_routes():
        """调试：列出知识库路由"""
        routes = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint.startswith('knowledge.'):
                routes.append({
                    'rule': rule.rule,
                    'endpoint': rule.endpoint,
                    'methods': list(rule.methods)
                })
        return jsonify({
            'success': True,
            'knowledge_routes': routes,
            'total': len(routes)
        })
else:
    print("⚠️ 知识库系统不可用，知识库API未注册")

# 注册人力洞察蓝图（独立于知识库系统）
app.register_blueprint(workforce_bp)
print("✅ 人力洞察API已注册: /api/workforce/*")

# 注册交付路线图蓝图
app.register_blueprint(delivery_bp)
print("✅ 交付路线图API已注册: /api/delivery/*")

# 凭据过期时间（24小时）
CREDENTIALS_EXPIRY_SECONDS = 24 * 60 * 60
# 凭据持久化文件路径
CREDENTIALS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jira_credentials.json')

def load_credentials_from_file():
    """从文件加载Jira凭据"""
    try:
        if os.path.exists(CREDENTIALS_FILE_PATH):
            with open(CREDENTIALS_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 清理过期的凭据
            now = datetime.now().isoformat()
            valid_creds = {}
            for ip, creds in data.items():
                last_updated = creds.get('last_updated', '')
                if last_updated:
                    try:
                        from datetime import datetime as dt
                        last_dt = dt.fromisoformat(last_updated)
                        if (dt.now() - last_dt).total_seconds() <= CREDENTIALS_EXPIRY_SECONDS * 2:
                            valid_creds[ip] = creds
                    except:
                        pass
            _log("info", f"从文件加载凭据: 共{len(data)}个，有效{len(valid_creds)}个")
            return valid_creds
    except Exception as e:
        _log("error", f"从文件加载凭据失败: {e}")
    return {}

# 全局变量
conversation_history = {}  # 会话历史记录
# Jira凭据存储：IP -> {username, password, last_updated}
jira_credentials_store = load_credentials_from_file()
# 服务器访问白名单：IP -> {user_info, access_level, last_access, first_access}
server_access_whitelist = {}
# 访问审计日志：记录所有访问尝试
MAX_ACCESS_LOG_SIZE = 1000  # 最多保留1000条审计日志
from collections import deque
access_log = deque(maxlen=MAX_ACCESS_LOG_SIZE)
# 用于线程安全的锁
import threading
credentials_lock = threading.Lock()
access_lock = threading.Lock()
access_log_lock = threading.Lock()
conversation_lock = threading.Lock()  # 保护conversation_history和conversation_jqls
cancel_lock = threading.Lock()  # 保护cancel_events
cancel_events = {}  # conversation_id -> threading.Event，用于取消正在进行的分析

# 项目进度风险看板缓存
progress_cache = {}
progress_cache_lock = threading.Lock()

# 并发处理统计
concurrent_requests = 0
concurrent_lock = threading.Lock()
MAX_CONCURRENT_REQUESTS = 500  # 最大并发请求数（支持500用户）

# 访问控制配置
ACCESS_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 访问权限过期时间（7天）
ACCESS_LEVELS = {
    'guest': ['GET /', 'GET /static/', 'POST /api/auth/jira', 'GET /api/test'],
    'user': ['* /api/knowledge/*', '* /api/analyze', 'GET /api/auth/jira/list'],
    'admin': ['* /api/admin/*', 'POST /api/admin/access/revoke', 'GET /api/admin/access/list']
}
DEFAULT_ACCESS_LEVEL = 'user'  # 默认权限级别

def save_credentials_to_file():
    """将Jira凭据保存到文件"""
    try:
        with credentials_lock:
            data = dict(jira_credentials_store)
        with open(CREDENTIALS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _log("debug", f"凭据已保存到文件: {len(data)}个")
    except Exception as e:
        _log("error", f"保存凭据到文件失败: {e}")

# 系统提示词
SYSTEM_PROMPT = """你是Jira风险分析助手，专门帮助分析项目风险、Bug趋势和共性问题。
你的能力包括：
1. 分析特定项目（如X6840）的风险状况
2. 分析tOS库中的共性问题
3. 提供专业的风险分析和建议

请根据用户的问题类型提供相应的分析：
- 如果用户询问特定项目，先获取该项目的Jira数据，然后分析风险
- 如果用户询问共性问题，分析tOS库的问题聚类
- 如果是一般问题，直接回答

始终使用中文回答，保持专业、详细、实用。"""

DETAILED_REPORT_PROMPT = """你是Jira详细风险报告助手，专门生成详细的项目风险报告。
你需要：
1. 获取项目的详细Jira数据
2. 分析未解决问题、高风险问题、趋势等
3. 生成结构化的详细报告，包括：
   - 执行摘要
   - 关键发现
   - 风险等级评估
   - 建议的行动项
   - 重点关注问题列表

报告要求：专业、详细、数据驱动、实用。使用中文，保持清晰的结构。"""





def get_client_ip():
    """获取客户端IP地址，支持代理"""
    # 尝试从常见代理头获取IP
    if request.headers.get('X-Forwarded-For'):
        # X-Forwarded-For: client, proxy1, proxy2
        xff = request.headers.get('X-Forwarded-For')
        _log("debug", f"X-Forwarded-For头: {xff}")
        ip = xff.split(',')[0].strip()
        _log("debug", f"从X-Forwarded-For获取IP: {ip}")
        return ip
    elif request.headers.get('X-Real-IP'):
        ip = request.headers.get('X-Real-IP')
        _log("debug", f"从X-Real-IP获取IP: {ip}")
        return ip
    else:
        ip = request.remote_addr
        _log("debug", f"从remote_addr获取IP: {ip}")
        _log("debug", f"所有请求头: {dict(request.headers)}")
        return ip

def log_access(client_ip, path, method, status, message=""):
    """记录访问审计日志"""
    from datetime import datetime
    with access_log_lock:
        access_log.append({
            'timestamp': datetime.now().isoformat(),
            'ip': client_ip,
            'path': path,
            'method': method,
            'status': status,
            'message': message
        })

def check_access_permission(client_ip, path, method):
    """检查IP是否有权限访问指定路径"""
    # 公开路由不需要检查
    public_paths = ['/', '/static/', '/api/test', '/api/auth/jira']
    if any(path.startswith(public_path) for public_path in public_paths):
        return True, "公开路由"
    
    # 检查IP是否在白名单中
    with access_lock:
        if client_ip not in server_access_whitelist:
            return False, "IP不在访问白名单中"
        
        # 检查访问是否过期（7天）
        access_info = server_access_whitelist[client_ip]
        last_access_str = access_info.get('last_access', '')
        if last_access_str:
            try:
                from datetime import datetime
                last_access = datetime.fromisoformat(last_access_str)
                now = datetime.now()
                expiry_seconds = (now - last_access).total_seconds()
                if expiry_seconds > ACCESS_EXPIRY_SECONDS:
                    # 自动清理过期条目
                    del server_access_whitelist[client_ip]
                    _log("info", f"自动清理过期IP: {client_ip} (过期{int(expiry_seconds/86400)}天)")
                    return False, f"访问权限已过期（{int(expiry_seconds/86400)}天），已自动清理"
            except Exception as e:
                _log("warning", f"解析访问时间失败: {e}")
        
        # 更新最后访问时间
        server_access_whitelist[client_ip]['last_access'] = datetime.now().isoformat()
    
    return True, "访问允许"

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
        _log("warning", f"访问被拒绝: IP={client_ip}, Path={path}, Method={method}, Reason={message}")
        return jsonify({
            "success": False,
            "error": "访问被拒绝",
            "message": message,
            "action_required": True,
            "auth_endpoint": "/api/auth/jira"
        }), 403

@app.route('/api/test', methods=['GET'])
def test_api():
    """测试API端点是否可用"""
    _log("debug", "test_api函数被调用")
    return jsonify({
        "success": True,
        "message": "API测试端点正常",
        "endpoints": ["/api/test", "/api/auth/jira", "/api/analyze"],
        "timestamp": datetime.now().isoformat()
    })


@app.route('/api/auth/jira', methods=['POST', 'GET'])
def auth_jira():
    """接收Jira凭据并绑定到客户端IP（POST）或测试端点（GET）"""
    from datetime import datetime
    _log("debug", f"auth_jira函数被调用，方法: {request.method}")
    if request.method == 'GET':
        try:
            _log("debug", f"开始处理GET请求，当前凭据存储大小: {len(jira_credentials_store)}")
            
            with credentials_lock:
                _log("debug", f"获取锁后，凭据存储内容: {jira_credentials_store}")
                # 如果存储中有凭据，返回凭据列表
                if jira_credentials_store:
                    _log("debug", f"凭据存储非空，开始构建列表")
                    credentials_list = []
                    total_credentials = len(jira_credentials_store)
                    valid_credentials = 0
                    expired_credentials = 0
                    
                    now = datetime.now()
                    
                    for ip, creds in jira_credentials_store.items():
                        username = creds.get('username', '')
                        last_updated_str = creds.get('last_updated', '')
                        
                        # 计算剩余有效期
                        is_valid = True
                        remaining_seconds = CREDENTIALS_EXPIRY_SECONDS
                        
                        if last_updated_str:
                            try:
                                last_updated = datetime.fromisoformat(last_updated_str)
                                expiry_seconds = (now - last_updated).total_seconds()
                                
                                if expiry_seconds > CREDENTIALS_EXPIRY_SECONDS:
                                    is_valid = False
                                    expired_credentials += 1
                                    remaining_seconds = 0
                                else:
                                    valid_credentials += 1
                                    remaining_seconds = CREDENTIALS_EXPIRY_SECONDS - expiry_seconds
                            except Exception as e:
                                _log("warning", f"解析凭据时间戳失败: {e}")
                                is_valid = False
                                remaining_seconds = 0
                        
                        credentials_list.append({
                            'ip': ip,
                            'username': username,
                            'password': '***' + username[-2:] if username else '***',  # 部分掩码，显示用户名最后两位作为提示
                            'last_updated': last_updated_str,
                            'is_valid': is_valid,
                            'remaining_hours': round(remaining_seconds / 3600, 1) if remaining_seconds > 0 else 0,
                            'expires_in': f"{remaining_seconds:.0f}秒" if remaining_seconds > 0 else "已过期"
                        })
                    
                    _log("debug", f"构建凭据列表完成，共{total_credentials}个凭据")
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
                else:
                    _log("debug", "凭据存储为空，返回空列表响应")
                    # 没有存储凭据，返回空列表
                    return jsonify({
                        "success": True,
                        "message": "当前没有存储任何Jira凭据",
                        "statistics": {
                            "total": 0,
                            "valid": 0,
                            "expired": 0,
                            "expiry_hours": CREDENTIALS_EXPIRY_SECONDS / 3600
                        },
                        "credentials": [],
                        "timestamp": datetime.now().isoformat()
                    })
                    
        except Exception as e:
            _log("error", f"查看Jira凭据列表时出错: {e}", exc_info=True)
            # 出错时返回默认响应
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
        _log("info", f"收到Jira凭据绑定请求: IP={client_ip}, 用户名={username}, 部门={department}")

        # 使用线程锁安全地更新凭据存储
        with credentials_lock:
            _log("debug", f"保存凭据前，当前存储大小: {len(jira_credentials_store)}")
            jira_credentials_store[client_ip] = {
                'username': username,
                'password': password,
                'department': department,
                'last_updated': datetime.now().isoformat()
            }
            _log("debug", f"保存凭据后，当前存储大小: {len(jira_credentials_store)}")
            _log("debug", f"保存的凭据内容: {jira_credentials_store[client_ip]}")
        
        # 持久化凭据到文件
        save_credentials_to_file()
        
        # 同时添加到服务器访问白名单
        with access_lock:
            now = datetime.now()
            server_access_whitelist[client_ip] = {
                'username': username,
                'department': department,
                'access_level': DEFAULT_ACCESS_LEVEL,
                'first_access': now.isoformat(),
                'last_access': now.isoformat(),
                'jira_bound': True,
                'last_updated': now.isoformat()
            }
            _log("debug", f"IP {client_ip} 已添加到服务器访问白名单")
        
        _log("success", f"Jira凭据已绑定到IP: {client_ip}，并添加到访问白名单")
        response = {
            "success": True,
            "message": "Jira凭据已保存并绑定到您的IP地址",
            "ip": client_ip,
            "timestamp": datetime.now().isoformat()
        }
        return jsonify(response)
        
    except Exception as e:
        _log("error", f"处理Jira凭据绑定请求时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/jira/list', methods=['GET'])
def list_jira_credentials():
    """查看所有存储的Jira凭据（不显示密码）"""
    try:
        from datetime import datetime
        
        with credentials_lock:
            credentials_list = []
            total_credentials = len(jira_credentials_store)
            valid_credentials = 0
            expired_credentials = 0
            
            now = datetime.now()
            
            for ip, creds in jira_credentials_store.items():
                username = creds.get('username', '')
                last_updated_str = creds.get('last_updated', '')
                
                # 计算剩余有效期
                is_valid = True
                remaining_seconds = CREDENTIALS_EXPIRY_SECONDS
                
                if last_updated_str:
                    try:
                        last_updated = datetime.fromisoformat(last_updated_str)
                        expiry_seconds = (now - last_updated).total_seconds()
                        
                        if expiry_seconds > CREDENTIALS_EXPIRY_SECONDS:
                            is_valid = False
                            expired_credentials += 1
                            remaining_seconds = 0
                        else:
                            valid_credentials += 1
                            remaining_seconds = CREDENTIALS_EXPIRY_SECONDS - expiry_seconds
                    except Exception as e:
                        _log("warning", f"解析凭据时间戳失败: {e}")
                        is_valid = False
                        remaining_seconds = 0
                
                credentials_list.append({
                    'ip': ip,
                    'username': username,
                    'password': '***' + username[-2:] if username else '***',  # 部分掩码，显示用户名最后两位作为提示
                    'last_updated': last_updated_str,
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
        _log("error", f"查看Jira凭据列表时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/')
def index():
    """首页 - 提供完整的应用页面"""
    try:
        # 读取index.html文件
        index_path = os.path.join(os.path.dirname(__file__), 'index.html')
        with open(index_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # 添加缓存控制头，防止浏览器缓存旧版本
        response = Response(html_content, mimetype='text/html')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        # 如果读取失败，返回错误页面
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>错误 - 智能体分析平台</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background: #f5f5f5;
                }}
                .error-container {{
                    max-width: 800px;
                    margin: 50px auto;
                    background: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                h1 {{ color: #d32f2f; }}
                .error-details {{
                    background: #fff8e1;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 15px 0;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <h1>页面加载失败</h1>
                <p>无法加载应用页面。请检查服务器配置。</p>
                <div class="error-details">
                    <strong>错误信息：</strong> {str(e)}
                </div>
                <p>你可以尝试：</p>
                <ul>
                    <li>刷新页面</li>
                    <li>检查服务器日志</li>
                    <li>联系系统管理员</li>
                </ul>
            </div>
        </body>
        </html>
        """
        return Response(error_html, mimetype='text/html', status=500)


@app.route('/ping', methods=['GET'])
def ping():
    """健康检查端点"""
    with conversation_lock:
        conv_count = len(conversation_history)
    return jsonify({
        "status": "ok",
        "message": "API服务运行正常",
        "timestamp": datetime.now().isoformat(),
        "conversations": conv_count
    })





@app.route('/config', methods=['GET'])
def get_config():
    """获取配置信息"""
    jira_configured = bool(JIRA_USERNAME and JIRA_PASSWORD and JIRA_URL)
    with conversation_lock:
        conv_count = len(conversation_history)
    
    return jsonify({
        "status": "ok",
        "jira": {
            "url": JIRA_URL,
            "configured": jira_configured,
            "username": "***" if JIRA_USERNAME else None
        },
        "ai": {
            "framework": "LangChain",
            "model": os.getenv("AI_MODEL", "gpt-5.4")
        },
        "server": {
            "host": "0.0.0.0",
            "port": 5002,
            "conversations": conv_count
        }
    })


@app.route('/analyze', methods=['GET'])
@app.route('/api/analyze', methods=['GET'])
def analyze_api():
    """分析项目 - 使用AI Agent驱动（替代所有规则匹配）"""
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referer = request.headers.get('Referer', 'None')

    _log("debug", f"API请求来自: 真实IP={client_ip}, remote_addr={request.remote_addr}, UA={user_agent[:50]}, Referer={referer}")

    # 忽略 project_key 参数（Agent 完全通过 LLM 理解意图）
    _ = request.args.get('project_key', '')
    user_query = request.args.get('user_query', '')
    _log("debug", f"原始参数 - user_query: {repr(user_query)}")

    original_user_query = user_query
    user_query = fix_gbk_utf8_mixed_string(user_query)
    if user_query != original_user_query:
        _log("debug", f"编码修复: {repr(original_user_query)} -> {repr(user_query)}")

    conversation_id = request.args.get('conversation_id', 'default')
    context_memory_json = request.args.get('context_memory', None)

    if not user_query or user_query.strip() == '':
        _log("error", "user_query参数为空")
        return jsonify({"error": "user_query参数不能为空"}), 400

    def generate():
        global concurrent_requests

        concurrent_incremented = False
        with concurrent_lock:
            if concurrent_requests >= MAX_CONCURRENT_REQUESTS:
                _log("warn", f"并发请求数已达上限({MAX_CONCURRENT_REQUESTS})，拒绝新请求")
            else:
                concurrent_requests += 1
                concurrent_incremented = True

        if not concurrent_incremented:
            yield generate_sse_message('error', '系统繁忙，请稍后再试（并发请求数已达上限）')
            yield "data: [DONE]\n\n"
            return

        import queue
        sse_queue = queue.Queue()

        cancel_event = threading.Event()
        with cancel_lock:
            cancel_events[conversation_id] = cancel_event

        analysis_error = None

        try:
            # Jira 凭据查找
            jira_username = None
            jira_password = None
            credentials_expired = False
            with credentials_lock:
                if client_ip in jira_credentials_store:
                    creds = jira_credentials_store[client_ip]
                    try:
                        last_updated = datetime.fromisoformat(creds.get('last_updated', ''))
                        if (datetime.now() - last_updated).total_seconds() > CREDENTIALS_EXPIRY_SECONDS:
                            del jira_credentials_store[client_ip]
                            credentials_expired = True
                        else:
                            jira_username = creds['username']
                            jira_password = creds['password']
                    except Exception as e:
                        jira_username = creds.get('username')
                        jira_password = creds.get('password')

            if credentials_expired:
                save_credentials_to_file()

            # 获取对话历史和上下文记忆
            history = []
            context_memory_dict = None
            with conversation_lock:
                if conversation_id in conversation_history:
                    history = conversation_history[conversation_id][:]
                    if isinstance(conversation_history[conversation_id], dict) and 'context_memory' in conversation_history[conversation_id]:
                        context_memory_dict = conversation_history[conversation_id].get('context_memory')
                    elif len(conversation_history) > 1:
                        # 兼容旧格式 - 从最近保存的记忆中获取
                        pass

            # 从请求中获取前端传递的 context_memory（如果有）
            if context_memory_json:
                try:
                    context_memory_dict = json.loads(context_memory_json)
                except:
                    pass

            from langchain_components import ContextMemory
            saved_memory = ContextMemory.from_dict(context_memory_dict) if context_memory_dict else None

            _log("info", f"开始AI Agent分析: query='{user_query[:50]}', history_len={len(history)}, has_context_memory={saved_memory is not None}")

            # 启动 Agent 后台线程
            analysis_done = threading.Event()
            agent = RiskAnalysisAgent(context_memory=saved_memory)

            def run_agent():
                nonlocal analysis_error
                try:
                    agent.run(user_query, sse_queue, cancel_event, history)

                    if cancel_event.is_set():
                        return

                    if agent.last_analysis:
                        with conversation_lock:
                            if conversation_id not in conversation_history:
                                conversation_history[conversation_id] = []
                            conversation_history[conversation_id].append({"role": "user", "content": user_query})
                            conversation_history[conversation_id].append({"role": "assistant", "content": agent.last_analysis})
                            if len(conversation_history[conversation_id]) > 20:
                                conversation_history[conversation_id] = conversation_history[conversation_id][-20:]
                            # 保存上下文记忆
                            if agent.context_memory:
                                conversation_history[conversation_id + ":memory"] = agent.context_memory.to_dict()
                    else:
                        # 即使没有分析结果，也保存上下文记忆（如模糊查询场景）
                        if agent.context_memory:
                            with conversation_lock:
                                conversation_history[conversation_id + ":memory"] = agent.context_memory.to_dict()
                except Exception as e:
                    analysis_error = e
                    _log("error", f"Agent分析线程异常: {e}")
                    import traceback
                    traceback.print_exc()
                    sse_queue.put(('error', f'分析出错: {str(e)}'))
                finally:
                    analysis_done.set()

            agent_thread = threading.Thread(target=run_agent, daemon=True)
            agent_thread.start()

            # 看门狗循环：从队列读取 SSE 事件并发送
            _analysis_start_time = time.time()
            _last_heartbeat_time = time.time()
            _heartbeat_interval = 2
            _max_wait_time = 900

            while not analysis_done.is_set() or not sse_queue.empty():
                if cancel_event.is_set():
                    yield generate_sse_message('error', '⏹️ 分析已中断')
                    analysis_done.set()
                    break

                try:
                    event_type, data = sse_queue.get(timeout=0.1)
                    if event_type in ('answer', 'data', 'error', 'thinking', 'jql'):
                        _last_heartbeat_time = time.time()
                    yield generate_sse_message(event_type, data)
                except queue.Empty:
                    _now = time.time()
                    _elapsed = _now - _analysis_start_time

                    if _now - _last_heartbeat_time >= _heartbeat_interval:
                        _last_heartbeat_time = _now
                        yield generate_sse_message('thinking', f'AI分析正在生成中...已等待{int(_elapsed)}秒，请稍候')

                    if _elapsed > _max_wait_time and not analysis_done.is_set():
                        yield generate_sse_message('error', f'AI分析超时（已等待{int(_elapsed)}秒），请稍后重试')
                        analysis_done.set()
                        break
                    continue
                except OSError as e:
                    _log("error", f"SSE连接错误: {e}")
                    analysis_done.set()
                    break
                except Exception as e:
                    _log("error", f"发送SSE事件时出错: {e}")

            agent_thread.join(timeout=10)

            if analysis_error:
                raise analysis_error

            if analysis_done.is_set():
                yield generate_sse_message('thinking_complete', '分析完成')

            yield "data: [DONE]\n\n"

        except Exception as e:
            _log("error", f"分析过程中出现异常: {e}")
            import traceback
            traceback.print_exc()
            yield generate_sse_message('error', get_friendly_ai_error(e))
            yield "data: [DONE]\n\n"

        finally:
            with cancel_lock:
                cancel_events.pop(conversation_id, None)
            if concurrent_incremented:
                with concurrent_lock:
                    concurrent_requests -= 1

    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache',
                           'Connection': 'keep-alive',
                           'X-Accel-Buffering': 'no'})


@app.route('/api/cancel-analysis', methods=['POST', 'GET'])
def cancel_analysis():
    """取消正在进行的AI分析"""
    conversation_id = request.args.get('conversation_id') or (request.json or {}).get('conversation_id', 'default')
    _log("info", f"收到取消分析请求, conversation_id={conversation_id}")

    with cancel_lock:
        cancel_event = cancel_events.get(conversation_id)
        if cancel_event:
            cancel_event.set()
            _log("info", f"已设置取消事件, conversation_id={conversation_id}")
            return jsonify({"status": "cancelled", "conversation_id": conversation_id})
        else:
            _log("warn", f"未找到对应conversation_id的取消事件, conversation_id={conversation_id}")
            return jsonify({"status": "not_found", "conversation_id": conversation_id})


@app.route('/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    """获取指定会话的历史记录"""
    with conversation_lock:
        history = conversation_history.get(conversation_id, [])
    return jsonify({
        "conversation_id": conversation_id,
        "history": history,
        "count": len(history)
    })


@app.route('/conversations', methods=['GET'])
def list_conversations():
    """列出所有会话"""
    with conversation_lock:
        conv_list = list(conversation_history.keys())
        conv_count = len(conversation_history)
    return jsonify({
        "conversations": conv_list,
        "count": conv_count
    })


@app.route('/style.css', methods=['GET'])
def style_css():
    """提供CSS样式文件"""
    try:
        css_path = os.path.join(os.path.dirname(__file__), 'style.css')
        with open(css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        # 添加缓存控制头，防止浏览器缓存旧版本
        response = Response(css_content, mimetype='text/css')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        _log("error", f"无法加载style.css文件: {e}")
        # 返回一个基本的CSS作为后备
        return Response("body { font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }", mimetype='text/css')


@app.route('/script.js', methods=['GET'])
def script_js():
    """提供JavaScript文件"""
    try:
        script_path = os.path.join(os.path.dirname(__file__), 'script.js')
        with open(script_path, 'r', encoding='utf-8') as f:
            js_content = f.read()
        # 添加缓存控制头，防止浏览器缓存旧版本
        response = Response(js_content, mimetype='application/javascript')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        _log("error", f"无法加载script.js文件: {e}")
        # 返回一个基本的JS作为后备
        return Response("console.log('Jira风险分析系统脚本加载失败');", mimetype='application/javascript')


@app.route('/test-api', methods=['GET'])
def test_api_page():
    """提供API测试页面"""
    try:
        test_path = os.path.join(os.path.dirname(__file__), 'test_api.html')
        with open(test_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        # 添加缓存控制头，防止浏览器缓存旧版本
        response = Response(html_content, mimetype='text/html')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        _log("error", f"无法加载test_api.html文件: {e}")
        # 返回一个基本的错误页面
        return Response("""
        <!DOCTYPE html>
        <html>
        <head><title>测试页面错误</title></head>
        <body>
            <h1>无法加载测试页面</h1>
            <p>错误: {}</p>
            <p>请确保test_api.html文件存在。</p>
        </body>
        </html>
        """.format(str(e)), mimetype='text/html')


def main():
    """主函数"""
    # 启动前初始化分析器（已移除，等待新实现）
    _log("info", "系统正在重构，LangChain分析器已移除")
    
    # 打印配置信息
    _log("info", f"Jira URL: {JIRA_URL}")
    _log("info", f"Jira用户名: {'已设置' if JIRA_USERNAME else '未设置'}")
    _log("info", f"AI模型: {os.getenv('AI_MODEL', 'gpt-5.4')}")
    _log("info", f"框架版本: LangChain")
    
    # 启动Flask应用
    host = '0.0.0.0'
    port = int(os.getenv('PORT', '5002'))
    
    # 获取所有IPv4地址
    ipv4_addresses = []
    try:
        import socket
        hostname = socket.gethostname()
        # 获取所有IP地址
        all_ips = socket.gethostbyname_ex(hostname)[2]
        # 过滤IPv4地址（排除127.0.0.1）
        ipv4_addresses = [ip for ip in all_ips if ip != '127.0.0.1' and '.' in ip]
        
        # 如果没有获取到其他IP，尝试通过socket连接获取
        if not ipv4_addresses:
            try:
                # 创建一个临时socket来获取本地IP
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                if local_ip != '127.0.0.1':
                    ipv4_addresses.append(local_ip)
            except:
                pass
    except Exception as e:
        _log("warn", f"获取IP地址时出错: {e}")
        ipv4_addresses = []
    
    _log("info", f"启动Flask API服务...")
    _log("info", f"本地访问: http://127.0.0.1:{port}")
    
    # 显示所有可用的网络访问地址
    if ipv4_addresses:
        for ip in ipv4_addresses:
            _log("info", f"网络访问: http://{ip}:{port}")
    else:
        _log("info", f"网络访问: http://<你的本地IP>:{port} (请检查防火墙设置)")
    
    # 打印所有已注册的路由（调试）
    _log("info", "已注册的路由:")
    for rule in app.url_map.iter_rules():
        _log("info", f"  {rule.rule} -> {rule.endpoint}")
    
    # 测试路由是否工作（Flask测试客户端）
    # _log("info", "使用Flask测试客户端测试路由...")
    # with app.test_client() as client:
    #     # 测试知识库路由
    #     response = client.get('/api/knowledge/test')
    #     _log("info", f"  测试 /api/knowledge/test: 状态码 {response.status_code}")
    #     if response.status_code == 200:
    #         _log("info", f"      响应: {response.get_data(as_text=True)[:100]}")
    #     else:
    #         _log("warn", f"      路由测试失败")
    #     
    #     # 测试调试路由
    #     response = client.get('/debug/knowledge-routes')
    #     _log("info", f"  测试 /debug/knowledge-routes: 状态码 {response.status_code}")
    #     
    #     # 测试已知工作路由
    #     response = client.get('/api/test')
    #     _log("info", f"  测试 /api/test: 状态码 {response.status_code}")
    

    # ==================== 管理API ====================
    
    def require_admin_access():
        """装饰器：要求管理员权限"""
        from functools import wraps
        
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                client_ip = get_client_ip()
                
                # 检查IP是否在白名单中且为管理员
                with access_lock:
                    if client_ip not in server_access_whitelist:
                        return jsonify({
                            "success": False,
                            "error": "未授权的访问",
                            "message": "请先绑定Jira凭据"
                        }), 403
                    
                    access_info = server_access_whitelist[client_ip]
                    if access_info.get('access_level') != 'admin':
                        return jsonify({
                            "success": False,
                            "error": "权限不足",
                            "message": "需要管理员权限"
                        }), 403
                
                return f(*args, **kwargs)
            return decorated_function
        return decorator
    
    @app.route('/api/admin/access/list', methods=['GET'])
    @require_admin_access()
    def list_access_whitelist():
        """查看所有已授权IP"""
        with access_lock:
            # 创建安全的副本，隐藏敏感信息
            safe_list = {}
            for ip, info in server_access_whitelist.items():
                safe_info = {
                    'username': info.get('username', '未知'),
                    'department': info.get('department', '未知'),
                    'access_level': info.get('access_level', 'user'),
                    'first_access': info.get('first_access', ''),
                    'last_access': info.get('last_access', ''),
                    'jira_bound': info.get('jira_bound', False)
                }
                safe_list[ip] = safe_info
            
            return jsonify({
                "success": True,
                "whitelist": safe_list,
                "total": len(safe_list),
                "timestamp": datetime.now().isoformat()
            })
    
    @app.route('/api/admin/access/log', methods=['GET'])
    @require_admin_access()
    def get_access_log():
        """查看访问审计日志"""
        with access_log_lock:
            # 返回最近的日志（deque不支持切片，使用反向迭代取最近100条）
            total = len(access_log)
            recent = []
            for item in reversed(access_log):
                recent.append(item)
                if len(recent) >= 100:
                    break
            recent.reverse()
            return jsonify({
                "success": True,
                "logs": recent,
                "total": total,
                "max_size": MAX_ACCESS_LOG_SIZE
            })
    
    @app.route('/api/admin/access/revoke', methods=['POST'])
    @require_admin_access()
    def revoke_access():
        """撤销IP访问权限"""
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "需要JSON数据"}), 400
        
        ip_to_revoke = data.get('ip')
        if not ip_to_revoke:
            return jsonify({"success": False, "error": "需要IP地址参数"}), 400
        
        with access_lock:
            if ip_to_revoke in server_access_whitelist:
                del server_access_whitelist[ip_to_revoke]
                _log("info", f"管理员已撤销IP {ip_to_revoke} 的访问权限")
                return jsonify({
                    "success": True, 
                    "message": f"已撤销IP {ip_to_revoke} 的访问权限"
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"IP {ip_to_revoke} 不在白名单中"
                }), 404
    
    @app.route('/api/admin/access/promote', methods=['POST'])
    @require_admin_access()
    def promote_access_level():
        """提升用户权限级别"""
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "需要JSON数据"}), 400
        
        target_ip = data.get('ip')
        new_level = data.get('level', 'admin')
        
        if not target_ip:
            return jsonify({"success": False, "error": "需要IP地址参数"}), 400
        
        if new_level not in ['guest', 'user', 'admin']:
            return jsonify({"success": False, "error": "无效的权限级别"}), 400
        
        with access_lock:
            if target_ip in server_access_whitelist:
                server_access_whitelist[target_ip]['access_level'] = new_level
                _log("info", f"管理员已将IP {target_ip} 的权限提升为 {new_level}")
                return jsonify({
                    "success": True,
                    "message": f"已将IP {target_ip} 的权限提升为 {new_level}"
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"IP {target_ip} 不在白名单中"
                }), 404
    
    @app.route('/api/admin/access/cleanup', methods=['POST'])
    @require_admin_access()
    def cleanup_expired_access():
        """清理过期的访问权限"""
        from datetime import datetime
        now = datetime.now()
        expired_count = 0
        
        with access_lock:
            ips_to_remove = []
            for ip, info in server_access_whitelist.items():
                last_access_str = info.get('last_access', '')
                if last_access_str:
                    try:
                        last_access = datetime.fromisoformat(last_access_str)
                        expiry_seconds = (now - last_access).total_seconds()
                        if expiry_seconds > ACCESS_EXPIRY_SECONDS:
                            ips_to_remove.append(ip)
                    except Exception as e:
                        _log("warning", f"解析访问时间失败: {e}")
            
            for ip in ips_to_remove:
                del server_access_whitelist[ip]
                expired_count += 1
        
        _log("info", f"已清理 {expired_count} 个过期的访问权限")
        return jsonify({
            "success": True,
            "message": f"已清理 {expired_count} 个过期的访问权限",
            "cleaned_count": expired_count
        })
    
    # ==================== 调试API ====================
    
    @app.route('/debug/access-info', methods=['GET'])
    def debug_access_info():
        """调试：查看当前IP的访问信息"""
        client_ip = get_client_ip()
        with access_lock:
            access_info = server_access_whitelist.get(client_ip, {})
            return jsonify({
                "success": True,
                "client_ip": client_ip,
                "has_access": client_ip in server_access_whitelist,
                "access_info": access_info,
                "whitelist_size": len(server_access_whitelist),
                "access_log_size": len(access_log)
            })
    
    # 捕获所有未匹配的路由（调试）
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def catch_all(path):
        _log("error", f"未匹配的路由: {request.path} (路径: {path})")
        _log("error", f"请求方法: {request.method}, 端点: {request.endpoint}")
        _log("error", f"URL Map: {[rule.rule for rule in app.url_map.iter_rules()]}")
        return jsonify({
            'success': False,
            'error': 'Route not found',
            'path': request.path,
            'matched_rules': [rule.rule for rule in app.url_map.iter_rules()]
        }), 404

    # WSGI中间件：记录所有请求
    class LoggingMiddleware:
        def __init__(self, app):
            self.app = app
        
        def __call__(self, environ, start_response):
            import sys
            print(f"[WSGI] PATH_INFO: {environ.get('PATH_INFO')}, REQUEST_METHOD: {environ.get('REQUEST_METHOD')}", file=sys.stderr)
            return self.app(environ, start_response)
    
    app.wsgi_app = LoggingMiddleware(app.wsgi_app)
    
    # 启用多线程支持，提高并发处理能力
    # 注意：对于生产环境，建议使用Gunicorn或uWSGI
    app.run(host=host, port=port, debug=False, threaded=True)


# ============================================================
# 项目群/组合分析专用的 SYSTEM_PROMPT
# ============================================================
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

## 🚫 输出格式禁令：
**严禁输出原始Markdown表格**（即使用 `|` 和 `---` 绘制的表格格式）。你的报告必须是**专业自然语言格式**的项目管理报告，使用以下格式：
- 使用中文段落、标题、要点列表等自然文本格式
- 数据点应融入文字描述中，而非以表格行列形式呈现
- 每个项目/模块的分析应有清晰的小标题和说明性文字
- 确保报告可以直接复制粘贴到邮件、PPT或文档中，无需二次格式化

## 输出结构要求（全项目群风险分析）：
必须采用以下专业报告结构，用自然语言呈现：

### 一、项目群执行摘要（Executive Summary）
- 覆盖的项目列表、版本范围
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

## 参考风格（不是模板，仅示意语气和格式）：
用户："分析一下所有项目的项目风险"
你：
一、项目群执行摘要

本次分析覆盖LK6、LK7、X6890、X6898、CN6c及AEE稳定性专项共6个项目/产品线，涉及tOS16、tOS163等版本。

整体来看，全项目群Bug总数为X个，其中未解决X个，整体解决率X%。在优先级分布上，Blocker问题X个、Critical问题X个、Major问题X个。从状态分布看，绝大多数问题已修复关闭，仅少量遗留问题。

**总体风险评估：🟡 中风险 / � 高风险**

核心判断：项目群当前的主要矛盾不是"问题数量失控"，而是"高优先级问题密集、涉及面广，但大部分已闭环，仅少量遗留问题具备即时交付风险"。需重点关注X6898的BTS/GTS认证风险和CN6c的SAR法规测试风险。

二、各项目风险详情

**LK7 / LK7OS163 / TOS163-LK7相关**
Bug总数14个，全部已解决，解决率100%。其中Critical问题10个、Major问题4个。虽无遗留未解决问题，但Critical问题密度高，涉及系统体验、通知栏、ThemeEditor、性能、影像显示一致性等多个领域。建议关注后续引入问题，防止质量回退。

**X6898 / X6898OS16 / TOS163-X6898相关**
Bug总数8个，全部已解决。但Blocker问题高达5个、Critical问题3个，优先级结构极为严峻。核心风险集中在BTS/GTS认证、射频TRP指标、相机长衰、SAR Sensor和NFC性能等关键领域，直接影响版本认证和市场准入。虽然当前已闭环，但需确保验证充分，防止复发。

（后续项目以此类推...）

请根据用户查询的具体意图和提供的数据情况，提供最专业的项目群风险分析。记住：数据是全量的、完整的，你的分析必须基于完整数据给出专业判断，严禁输出任何形式的原始Markdown表格。"""



@app.route('/api/health', methods=['GET'])
def health_check():
    """系统健康检查接口"""
    try:
        # 检查凭据存储状态
        with credentials_lock:
            creds_count = len(jira_credentials_store)
        
        # 检查并发请求状态
        with concurrent_lock:
            current_concurrent = concurrent_requests
        
        return jsonify({
            "success": True,
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "system": {
                "concurrent_requests": current_concurrent,
                "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
                "credentials_count": creds_count,
                "credentials_file_exists": os.path.exists(CREDENTIALS_FILE_PATH)
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500


# ========== 项目进度风险看板 ==========

TEMP_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'temp_uploads')

@app.route('/api/progress/upload', methods=['POST'])
def progress_upload():
    session_id = str(uuid.uuid4())
    file = request.files.get('file')
    if not file:
        return jsonify({"success": False, "error": "未上传文件"}), 400
    if not file.filename.endswith('.xlsx'):
        return jsonify({"success": False, "error": "仅支持 .xlsx 文件"}), 400
    if not os.path.exists(TEMP_UPLOAD_DIR):
        os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
    tmp_path = os.path.join(TEMP_UPLOAD_DIR, f"{session_id}.xlsx")
    file.save(tmp_path)
    try:
        # 优先使用标准解析器（数据更完整：泳道映射、偏差计算、任务统计）
        try:
            _log("info", f"开始使用标准解析器解析: {tmp_path}")
            data = parse_progress_excel(tmp_path)
            _log("info", f"标准解析成功: {data['summary'].get('total_projects', 0)} 个项目")
        except Exception as e:
            _log("warn", f"标准解析失败: {e}，尝试智能解析")
            import traceback
            _log("warn", f"标准解析详细错误: {traceback.format_exc()}")
            data = analyze_with_intelligence(tmp_path)
            if data is None:
                raise
            _log("info", f"智能解析成功: {data['summary'].get('total_projects', 0)} 个项目")
        column_info = {}
        if "_column_mapping" in data:
            column_info = {"mapping": data.pop("_column_mapping", {}), "detected": data.pop("_columns_detected", [])}
        with progress_cache_lock:
            progress_cache[session_id] = {"data": data, "filepath": tmp_path, "column_info": column_info}
        return jsonify({"success": True, "session_id": session_id, "column_info": column_info if column_info else None})
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"}), 400


@app.route('/api/progress/data/<session_id>', methods=['GET'])
def progress_data(session_id):
    with progress_cache_lock:
        entry = progress_cache.get(session_id)
    if not entry:
        return jsonify({"success": False, "error": "session_id 无效或已过期"}), 404
    return jsonify({"success": True, "data": entry["data"]})


@app.route('/api/progress/debug/<session_id>', methods=['GET'])
def progress_debug(session_id):
    """调试端点：返回缓存的原始数据，方便排查进度值异常"""
    with progress_cache_lock:
        entry = progress_cache.get(session_id)
    if not entry:
        return jsonify({"success": False, "error": "session_id 无效或已过期"}), 404
    data = entry["data"]
    dbg = {
        "summary": data.get("summary", {}),
        "project_progress_sample": [],
        "project_tree_preview": {},
        "column_info": entry.get("column_info", {}),
        "total_project_progress_entries": len(data.get("project_progress", [])),
        "total_project_tree_keys": len(data.get("project_tree", {}))
    }
    for i, p in enumerate(data.get("project_progress", [])[:10]):
        dbg["project_progress_sample"].append({
            "project": p.get("project", ""),
            "progress": p.get("progress", 0),
            "deviation": p.get("deviation", 0),
            "risk": p.get("risk", ""),
            "parent_project": p.get("parent_project", ""),
            "deadline": p.get("deadline", ""),
            "tasks_count": p.get("tasks_count", 0),
            "planned": p.get("planned", 0),
            "executed": p.get("executed", 0)
        })
    pt = data.get("project_tree", {})
    count = 0
    for parent_name, tree in pt.items():
        if count >= 5:
            break
        subs = []
        for sp in tree.get("sub_projects", [])[:5]:
            subs.append({
                "name": sp.get("name", ""),
                "progress": sp.get("progress", 0),
                "deviation": sp.get("deviation", 0),
                "risk": sp.get("risk", "")
            })
        dbg["project_tree_preview"][parent_name] = {
            "sub_count": len(tree.get("sub_projects", [])),
            "sample_subs": subs
        }
        count += 1
    return jsonify({"success": True, "debug": dbg})


@app.route('/api/progress/analyze/<session_id>', methods=['GET'])
def progress_analyze(session_id):
    with progress_cache_lock:
        entry = progress_cache.get(session_id)
    if not entry:
        return jsonify({"success": False, "error": "session_id 无效或已过期"}), 404
    data = entry["data"]
    summary = data["summary"]

    try:
        from excel_parser import build_ai_prompt
        prompt = build_ai_prompt(data)
    except ImportError:
        project_tree = data.get("project_tree", {})
        all_tasks = []
        for parent_name, tree in project_tree.items():
            for sp in tree.get("sub_projects", []):
                all_tasks.append(sp)

        if not all_tasks:
            for p in data.get("project_progress", []):
                all_tasks.append(p)

        proj_rows = []
        for p in all_tasks:
            name = p.get("name", p.get("project", "?"))
            phase = p.get("stage", p.get("phase", p.get("lane", "?")))
            prog = p.get("progress", 0)
            dev = p.get("deviation", 0)
            risk = p.get("risk", "normal")
            if risk == "high":
                risk_text = "高风险"
            elif risk == "warning":
                risk_text = "预警"
            else:
                risk_text = "正常"
            dpm = p.get("dpm", p.get("manager", ""))
            deadline = p.get("deadline", "")
            dev_txt = f"滞后{dev}%" if dev >= 0 else f"超前{abs(dev)}%"
            proj_rows.append(f"{name} | {phase} | {prog}% | {dev_txt} | {dpm} | {deadline} | {risk_text}")
        proj_table = "\n".join(proj_rows)
        rem_all = data.get("remaining_effort_all", [])
        total_planned = sum(d.get("planned", 0) for d in rem_all)
        total_remaining = sum(d.get("remaining", 0) for d in rem_all)
        overall_rate = round((total_planned - total_remaining) / total_planned * 100, 1) if total_planned > 0 else 0
        dpm_lines = []
        for d in rem_all:
            dpm_lines.append(f"  - {d['dpm']}: 预估{d.get('planned',0)}人天, 剩余{d.get('remaining',0)}人天, 完成率{d.get('completion_rate',0)}%, {d.get('project_count',0)}个项目")
        dpm_table = "\n".join(dpm_lines) if dpm_lines else "无"
        prompt = (
            f"项目进度风险分析数据（执行进度偏差 = 应完成时间进度 - 实际执行进度，正数=滞后，负数=超前）：\n"
            f"共{summary['total_projects']}个项目，{len(all_tasks)}个独立任务。"
            f"正常{summary['normal']}个、预警{summary['warning']}个、高风险{summary['high_risk']}个。"
            f"团队共{summary['team_size']}个负责人。\n"
            f"总计预估{total_planned}人天, 剩余{total_remaining}人天, 整体完成率{overall_rate}%。\n\n"
            f"【人力需求详情（按DPM负责人排序）】\n{dpm_table}\n\n"
            f"【各任务数据（每行一个独立任务，未按项目聚合）】\n"
            f"任务名 | 当前阶段 | 执行进度(%) | 执行进度偏差(%) | 负责人 | 截止日期 | 风险等级\n{proj_table}\n\n"
            f"请生成一份风险分析报告，必须包含一个 Markdown 表格，表头严格按照以下顺序和名称（共7列，与输入数据列一一对应）：\n"
            f"| 任务名 | 当前阶段 | 执行进度(%) | 执行进度偏差(%) | 负责人 | 截止日期 | 当前风险点与总体判断 |\n\n"
            f"要求：正偏差=滞后XX%（执行慢于时间计划），负偏差=超前XX%（执行快于时间计划）；风险点用简短文字描述，每个判断一句话，不要重复描述数据和项目名；最后补充核心风险总结和改进建议。"
        )

    chat_messages = [
        {"role": "system", "content": "你是一位经验丰富的项目管理专家，擅长分析项目进度数据并给出专业建议。"},
        {"role": "user", "content": prompt}
    ]

    filepath_to_cleanup = entry.get("filepath", "") if entry else ""

    def generate():
        try:
            ai_response = call_ai_api(chat_messages, stream=True, temperature=0.3, max_tokens=4096, timeout=180)
            if ai_response is None:
                yield generate_sse_message('error', 'AI服务调用失败，请稍后重试')
                return
            for line in ai_response.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8')
                if not line.startswith('data: '):
                    continue
                chunk_data = line[6:]
                if chunk_data == '[DONE]':
                    break
                try:
                    chunk = json.loads(chunk_data)
                    choices = chunk.get('choices')
                    if not choices or not isinstance(choices, list) or len(choices) == 0:
                        continue
                    delta = choices[0].get('delta', {})
                    if not delta:
                        continue
                    content = delta.get('content', '')
                    if content:
                        yield generate_sse_message('answer', content)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            yield generate_sse_message('error', f'AI分析异常: {str(e)}')
        finally:
            if filepath_to_cleanup and os.path.exists(filepath_to_cleanup):
                try:
                    os.remove(filepath_to_cleanup)
                except Exception:
                    pass

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    })


@app.route('/api/progress/export/<session_id>', methods=['GET'])
def progress_export(session_id):
    with progress_cache_lock:
        entry = progress_cache.get(session_id)
    if not entry:
        return jsonify({"success": False, "error": "session_id 无效或已过期"}), 404
    data = entry["data"]

    try:
        import pandas as pd
        output = io.BytesIO()
        # 优先使用 task_progress（独立任务级），回退到 project_progress（聚合级）
        tasks = data.get("task_progress")
        if tasks:
            df = pd.DataFrame([{
                "任务名称": t.get("name", ""),
                "父级项目": t.get("parent_project", ""),
                "阶段": t.get("phase", ""),
                "泳道": t.get("lane", ""),
                "执行进度(%)": t.get("progress", 0),
                "进度偏差(%)": t.get("deviation", 0),
                "风险等级": "高风险" if t.get("risk") == "high" else ("预警" if t.get("risk") == "warning" else "正常"),
                "负责人": t.get("dpm", ""),
                "计划名称": t.get("plan_name", ""),
                "截止日期": t.get("deadline", ""),
                "开始日期": t.get("start_date", ""),
                "计划用例": t.get("planned", 0),
                "已执行用例": t.get("executed", 0),
                "预估人力": t.get("effort_planned", 0),
                "剩余人力": t.get("effort_remaining", 0)
            } for t in tasks])
            df.to_excel(output, index=False, sheet_name="任务级进度明细", engine='openpyxl')
        else:
            projects = data.get("project_progress", [])
            if projects:
                df = pd.DataFrame([{
                    "项目": p["project"],
                    "阶段": p.get("phase", ""),
                    "进度(%)": p["progress"],
                    "偏差(%)": p["deviation"],
                    "风险等级": p.get("risk_label", ""),
                    "负责人": p.get("manager", ""),
                    "计划用例": p["planned"],
                    "已执行": p["executed"],
                    "任务数": p.get("tasks_count", 0)
                } for p in projects])
                df.to_excel(output, index=False, sheet_name="项目进度风险看板", engine='openpyxl')
            else:
                pd.DataFrame().to_excel(output, index=False, sheet_name="数据", engine='openpyxl')
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename=progress_dashboard_{date.today().isoformat()}.xlsx'
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"导出失败: {str(e)}"}), 500


@app.route('/api/progress/correct-columns/<session_id>', methods=['POST'])
def progress_correct_columns(session_id):
    with progress_cache_lock:
        entry = progress_cache.get(session_id)
    if not entry:
        return jsonify({"success": False, "error": "session_id 无效或已过期"}), 404
    corrections = request.get_json(silent=True)
    if not corrections or not isinstance(corrections, dict):
        return jsonify({"success": False, "error": "请提供列名映射修正"}), 400
    try:
        from excel_parser import update_alias
        for user_col, mapped_field in corrections.items():
            update_alias(user_col, mapped_field)
        data = entry["data"]
        filepath = entry.get("filepath")
        if filepath and os.path.exists(filepath):
            from progress_analyzer import analyze_with_intelligence
            new_data = analyze_with_intelligence(filepath)
            if new_data is None:
                new_data = parse_progress_excel(filepath)
            column_info = {}
            if "_column_mapping" in new_data:
                column_info = {"mapping": new_data.pop("_column_mapping", {}), "detected": new_data.pop("_columns_detected", [])}
            with progress_cache_lock:
                progress_cache[session_id] = {"data": new_data, "filepath": filepath, "column_info": column_info}
            return jsonify({"success": True, "message": "列映射已更新，看板数据已刷新", "column_info": column_info if column_info else None, "data": new_data})
        return jsonify({"success": True, "message": "列映射已记录，下次上传将优先使用"})
    except Exception as e:
        return jsonify({"success": False, "error": f"更新列映射失败: {str(e)}"}), 500


# ── 风险看板页面 ──

@app.route('/kanban-page')
def risk_kanban_page():
    """提供独立风险看板HTML页面"""
    import html as html_mod
    page_path = os.path.join(os.path.dirname(__file__), 'risk_kanban.html')
    if not os.path.exists(page_path):
        return jsonify({"error": "看板页面不存在"}), 404

    token = request.args.get('token', '')
    project = request.args.get('project', '')

    with open(page_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # 如果有token，内嵌数据到页面
    if token:
        data = get_kanban_page_data(token)
        if data and data.get('issues'):
            data_json = json.dumps(data['issues'], ensure_ascii=False)
            # 替换数据占位符
            html_content = html_content.replace(
                'let rawData = [];',
                f'let rawData = {data_json};'
            )
            if data.get('project'):
                html_content = html_content.replace(
                    '<span class="subtitle" id="headerProject"></span>',
                    f'<span class="subtitle" id="headerProject">— {html_mod.escape(data["project"])}</span>'
                )

    return Response(html_content, mimetype='text/html')


@app.route('/api/kanban-data/<token>')
def risk_kanban_data_api(token):
    """API: 获取看板数据"""
    data = get_kanban_page_data(token)
    if not data:
        return jsonify({"error": "数据不存在或已过期"}), 404
    return jsonify(data)


if __name__ == '__main__':
    main()