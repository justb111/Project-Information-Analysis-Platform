#!/bin/bash

# 部署脚本

# 安装依赖
echo "安装依赖..."
pip install -r requirements.txt

# 启动应用
echo "启动应用..."
gunicorn -w 4 -b 0.0.0.0:5002 e:app
