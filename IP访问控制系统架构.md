# IP访问控制系统架构文档

## 概述
本系统实现了基于IP绑定的访问控制，允许用户通过配置Jira凭据自动绑定IP地址，后续访问时自动识别并授权使用AI和接口功能。系统支持约500并发用户，具备管理员权限管理和细粒度权限控制框架。

## 核心功能
1. **IP自动绑定**：用户访问服务器URL，配置Jira账号密码后自动捕获并绑定IP地址
2. **访问控制**：下次访问时自动切换到用户局域网IP，允许使用AI和接口
3. **管理员框架**：第一个绑定用户自动成为管理员，可管理其他IP权限
4. **权限分级**：guest/user/admin三级权限，预留细粒度功能权限框架
5. **审计日志**：记录所有访问尝试，支持日志轮转和清理
6. **自动过期**：IP访问权限7天过期，自动清理

## 系统架构

### 1. 数据存储层
- **SQLite数据库**：存储IP访问记录、Jira凭据、审计日志
- **数据库模型**：
  - `IPAccessRecord`：IP访问记录（IP、用户名、部门、权限级别、功能权限等）
  - `JiraCredential`：Jira凭据（IP、用户名、密码、部门）
  - `AccessLog`：访问审计日志

### 2. 业务逻辑层
- **IP识别**：支持X-Forwarded-For、X-Real-IP代理头部
- **访问控制中间件**：`@app.before_request`拦截所有请求，检查IP权限
- **权限检查**：基于数据库查询，支持公开路由、白名单IP、管理员IP
- **自动过期清理**：7天未访问自动移除权限

### 3. API层
- **公开API**：
  - `GET /`：前端页面
  - `GET/POST /api/auth/jira`：Jira凭据绑定和查询
- **受保护API**：
  - `GET /api/analyze`：AI分析功能
  - `GET /api/knowledge/*`：知识库功能
- **管理员API**：
  - `GET /api/admin/access/list`：查看所有IP权限（待修复）
  - `POST /api/admin/access/promote`：提升IP为管理员
  - `POST /api/admin/access/revoke`：撤销IP访问权限
  - `GET /api/admin/access/log`：查看审计日志
  - `POST /api/admin/access/cleanup`：清理过期IP

### 4. 权限框架
- **权限级别**：guest（仅公开路由）、user（AI和接口）、admin（管理功能）
- **功能权限预留**：`FEATURE_PERMISSIONS`定义功能标签，`PATH_TO_FEATURE_MAP`映射路径到功能
- **扩展性**：支持未来按功能分配权限给特定IP

## 部署配置

### 开发环境
```bash
python e.py --api
```

### 生产环境（推荐）
1. 安装依赖：`pip install waitress`
2. 启动生产服务器：`python run_production.py --host 0.0.0.0 --port 5002 --threads 100`
3. 支持Windows和Linux，多线程处理并发请求

### 负载测试
```bash
python load_test.py
```
- 模拟多用户并发访问
- 测试公开路由、受保护路由、IP绑定功能
- 生成性能报告和错误统计

## 性能优化
1. **数据库存储**：替代内存字典，支持持久化和更高并发
2. **连接池**：SQLAlchemy连接池管理数据库连接
3. **线程安全**：数据库会话线程局部存储，避免竞争条件
4. **日志轮转**：自动清理旧日志，限制数据库大小
5. **生产服务器**：Waitress WSGI服务器，支持多线程和高并发

## 扩展性设计

### 1. 细粒度权限控制（预留框架）
```python
FEATURE_PERMISSIONS = {
    'ai_access': '访问AI功能',
    'knowledge_access': '访问知识库',
    'analyze_access': '访问分析功能',
    'admin_panel': '访问管理面板',
    'jira_integration': 'Jira集成'
}

PATH_TO_FEATURE_MAP = {
    '/api/analyze': 'analyze_access',
    '/api/knowledge/': 'knowledge_access',
    '/api/admin/': 'admin_panel'
}
```

### 2. 自动化切片功能（未来扩展）
- 系统预留了权限框架，可轻松添加新的功能权限
- 管理员API可扩展为按功能分配权限
- 数据库模型已包含`permissions`字段存储JSON权限列表

## 故障排除

### 常见问题
1. **管理员API错误**：`access_lock`未定义 - 需要更新管理员API使用数据库函数
2. **数据库连接问题**：确保有写入权限，`ip_access.db`文件可创建
3. **IP识别问题**：检查代理头部配置，确保`X-Forwarded-For`正确传递

### 日志查看
- 访问日志存储在`access_logs`表中
- 可通过`/api/admin/access/log`查看（需要管理员权限）
- 数据库文件：`ip_access.db`

## 安全考虑
1. **密码存储**：当前明文存储，生产环境应加密
2. **IP欺骗**：依赖代理头部，确保反向代理正确设置
3. **权限隔离**：不同权限级别访问不同功能
4. **审计跟踪**：所有访问尝试记录，便于安全审计

## 后续优化方向
1. **Redis缓存**：高频访问数据缓存，提升性能
2. **分布式部署**：多实例部署，负载均衡
3. **OAuth集成**：第三方认证支持
4. **实时监控**：性能指标和异常报警
5. **自动化切片**：基于权限的功能动态加载

---
*文档版本：1.0 | 更新日期：2026-04-23*