#!/usr/bin/env python3
"""
生产环境启动脚本
使用 Waitress WSGI 服务器，支持 Windows 和 Linux
"""

import sys
import os
import argparse

try:
    from waitress import serve
except ImportError:
    serve = None

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    parser = argparse.ArgumentParser(description='启动生产服务器')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5002, help='监听端口 (默认: 5002)')
    parser.add_argument('--threads', type=int, default=100, help='工作线程数 (默认: 100)')
    parser.add_argument('--url-scheme', default='http', help='URL协议 (默认: http)')
    
    args = parser.parse_args()
    
    print(f"🚀 启动生产服务器 (Waitress)")
    print(f"📡 监听地址: {args.host}:{args.port}")
    print(f"🧵 工作线程: {args.threads}")
    print(f"🔗 URL协议: {args.url_scheme}")
    print("=" * 50)
    
    try:
        # 导入 Flask 应用
        from e import app
        
        # 启动 Waitress 服务器
        serve(
            app,
            host=args.host,
            port=args.port,
            threads=args.threads,
            url_scheme=args.url_scheme
        )
    except ImportError as e:
        print(f"❌ 导入错误: {e}")
        print("请确保已安装依赖: pip install waitress")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()