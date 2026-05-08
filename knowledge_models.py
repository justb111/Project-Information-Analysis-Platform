"""
知识库数据库模型
结构化数据库模型定义
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import json

# 使用SQLAlchemy（如果已安装）或简化版
try:
    from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, BigInteger
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import relationship, sessionmaker, Session
    from sqlalchemy.dialects.postgresql import UUID
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    # 简化版本，用于开发环境
    SQLALCHEMY_AVAILABLE = False
    print("警告: SQLAlchemy未安装，使用简化模型")

if SQLALCHEMY_AVAILABLE:
    Base = declarative_base()
else:
    # 简化基类
    class Base:
        pass

# 文件类型枚举
FILE_TYPES = {
    'pdf': 'PDF文档',
    'docx': 'Word文档',
    'xlsx': 'Excel表格',
    'csv': 'CSV文件',
    'txt': '文本文件',
    'md': 'Markdown文件',
    'jpg': 'JPEG图片',
    'png': 'PNG图片',
    'feishu': '飞书云文档',
    'other': '其他格式'
}

# 文件状态枚举
FILE_STATUS = {
    'uploaded': '已上传',
    'processing': '处理中',
    'processed': '已处理',
    'error': '处理失败'
}

# 切片类型枚举
CHUNK_TYPES = {
    'text': '文本段落',
    'table': '表格数据',
    'image': '图片内容',
    'mixed': '混合内容',
    'code': '代码块',
    'heading': '标题'
}

# 知识分类枚举
CATEGORY_TYPES = {
    'project_info': '项目信息知识',
    'project_management': '项目管理知识',
    'jira_spec': 'Jira库规范知识'
}

class KnowledgeFile(Base):
    """知识文件表"""
    __tablename__ = 'knowledge_files'
    
    if SQLALCHEMY_AVAILABLE:
        id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        filename = Column(String(500), nullable=False)
        file_type = Column(String(50))  # pdf, docx, xlsx等
        file_size = Column(BigInteger)  # 文件大小（字节）
        upload_time = Column(DateTime, default=datetime.utcnow)
        upload_user = Column(String(100))
        category = Column(String(50))  # project_info, project_management, jira_spec
        subcategory = Column(String(50), nullable=True)  # 子分类
        status = Column(String(20), default='uploaded')  # uploaded, processing, processed, error
        storage_path = Column(String(1000))  # 对象存储路径
        feishu_doc_id = Column(String(200))  # 飞书文档ID
        feishu_url = Column(String(500))  # 飞书文档URL
        permissions = Column(JSON)  # 权限信息
        file_metadata = Column(JSON)  # 扩展元数据（避免与SQLAlchemy metadata属性冲突）
        tags = Column(JSON)  # 标签列表
        description = Column(Text)  # 文件描述
        is_trained = Column(Boolean, default=False)  # 是否已训练
        trained_time = Column(DateTime, nullable=True)  # 训练时间
        trained_user = Column(String(100), nullable=True)  # 训练用户
        training_status = Column(String(20), default='pending')  # 训练状态: pending, training, completed, failed
        last_training_time = Column(DateTime, nullable=True)  # 最后一次训练时间
        
        # 关系
        chunks = relationship("ContentChunk", back_populates="file", cascade="all, delete-orphan")
    else:
        # 简化版本
        def __init__(self, **kwargs):
            self.id = str(uuid.uuid4())
            self.filename = kwargs.get('filename', '')
            self.file_type = kwargs.get('file_type', '')
            self.file_size = kwargs.get('file_size', 0)
            self.upload_time = kwargs.get('upload_time', datetime.now(timezone.utc))
            self.upload_user = kwargs.get('upload_user', '')
            self.category = kwargs.get('category', '')
            self.subcategory = kwargs.get('subcategory', '')
            self.status = kwargs.get('status', 'uploaded')
            self.storage_path = kwargs.get('storage_path', '')
            self.feishu_doc_id = kwargs.get('feishu_doc_id', '')
            self.feishu_url = kwargs.get('feishu_url', '')
            self.permissions = kwargs.get('permissions', {})
            self.file_metadata = kwargs.get('file_metadata', {})
            self.tags = kwargs.get('tags', [])
            self.description = kwargs.get('description', '')
            self.is_trained = kwargs.get('is_trained', False)
            self.trained_time = kwargs.get('trained_time', None)
            self.trained_user = kwargs.get('trained_user', '')
            self.training_status = kwargs.get('training_status', 'pending')
            self.last_training_time = kwargs.get('last_training_time', None)
            self.chunks = []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        if SQLALCHEMY_AVAILABLE:
            return {
                'id': self.id,
                'filename': self.filename,
                'file_type': self.file_type,
                'file_size': self.file_size,
                'upload_time': self.upload_time.isoformat() if self.upload_time else None,
                'upload_user': self.upload_user,
                'category': self.category,
                'status': self.status,
                'storage_path': self.storage_path,
                'feishu_doc_id': self.feishu_doc_id,
                'feishu_url': self.feishu_url,
                'permissions': self.permissions,
                'metadata': self.file_metadata,
                'tags': self.tags,
                'description': self.description,
                'is_trained': self.is_trained,
                'trained_time': self.trained_time.isoformat() if self.trained_time else None,
                'trained_user': self.trained_user,
                'training_status': self.training_status,
                'last_training_time': self.last_training_time.isoformat() if self.last_training_time else None,
                'chunk_count': len(self.chunks) if hasattr(self, 'chunks') else 0
            }
        else:
            return {
                'id': self.id,
                'filename': self.filename,
                'file_type': self.file_type,
                'file_size': self.file_size,
                'upload_time': self.upload_time.isoformat() if hasattr(self.upload_time, 'isoformat') else str(self.upload_time),
                'upload_user': self.upload_user,
                'category': self.category,
                'status': self.status,
                'storage_path': self.storage_path,
                'feishu_doc_id': self.feishu_doc_id,
                'feishu_url': self.feishu_url,
                'permissions': self.permissions,
                'metadata': self.file_metadata,
                'tags': self.tags,
                'description': self.description,
                'is_trained': self.is_trained,
                'trained_time': self.trained_time.isoformat() if hasattr(self.trained_time, 'isoformat') else str(self.trained_time) if self.trained_time else None,
                'trained_user': self.trained_user,
                'training_status': self.training_status,
                'last_training_time': self.last_training_time.isoformat() if hasattr(self.last_training_time, 'isoformat') else str(self.last_training_time) if self.last_training_time else None,
                'chunk_count': len(self.chunks)
            }


class ContentChunk(Base):
    """内容切片表"""
    __tablename__ = 'content_chunks'
    
    if SQLALCHEMY_AVAILABLE:
        id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        file_id = Column(String(36), ForeignKey('knowledge_files.id'), nullable=False)
        chunk_index = Column(Integer)  # 切片序号
        chunk_type = Column(String(50))  # text, table, image, mixed等
        content_text = Column(Text)  # 文本内容
        content_summary = Column(Text)  # 内容摘要
        semantic_context = Column(Text)  # 语义上下文
        start_position = Column(Integer)  # 在原文件中的起始位置
        end_position = Column(Integer)  # 结束位置
        vector_id = Column(String(200))  # 向量数据库中的ID
        parent_chunk_id = Column(String(36), ForeignKey('content_chunks.id'))  # 父切片ID
        chunk_metadata = Column(JSON)  # 切片元数据（避免与SQLAlchemy metadata属性冲突）
        created_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
        
        # 关系
        file = relationship("KnowledgeFile", back_populates="chunks")
        parent = relationship("ContentChunk", remote_side=[id], backref="children")
    else:
        # 简化版本
        def __init__(self, **kwargs):
            self.id = str(uuid.uuid4())
            self.file_id = kwargs.get('file_id', '')
            self.chunk_index = kwargs.get('chunk_index', 0)
            self.chunk_type = kwargs.get('chunk_type', 'text')
            self.content_text = kwargs.get('content_text', '')
            self.content_summary = kwargs.get('content_summary', '')
            self.semantic_context = kwargs.get('semantic_context', '')
            self.start_position = kwargs.get('start_position', 0)
            self.end_position = kwargs.get('end_position', 0)
            self.vector_id = kwargs.get('vector_id', '')
            self.parent_chunk_id = kwargs.get('parent_chunk_id', None)
            self.chunk_metadata = kwargs.get('chunk_metadata', {})
            self.created_time = kwargs.get('created_time', datetime.now(timezone.utc))
            self.children = []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        if SQLALCHEMY_AVAILABLE:
            return {
                'id': self.id,
                'file_id': self.file_id,
                'chunk_index': self.chunk_index,
                'chunk_type': self.chunk_type,
                'content_text': self.content_text,
                'content_summary': self.content_summary,
                'semantic_context': self.semantic_context,
                'start_position': self.start_position,
                'end_position': self.end_position,
                'vector_id': self.vector_id,
                'parent_chunk_id': self.parent_chunk_id,
                'metadata': self.chunk_metadata,
                'created_time': self.created_time.isoformat() if self.created_time else None,
                'has_children': len(self.children) > 0 if hasattr(self, 'children') else False
            }
        else:
            return {
                'id': self.id,
                'file_id': self.file_id,
                'chunk_index': self.chunk_index,
                'chunk_type': self.chunk_type,
                'content_text': self.content_text,
                'content_summary': self.content_summary,
                'semantic_context': self.semantic_context,
                'start_position': self.start_position,
                'end_position': self.end_position,
                'vector_id': self.vector_id,
                'parent_chunk_id': self.parent_chunk_id,
                'metadata': self.chunk_metadata,
                'created_time': self.created_time.isoformat() if hasattr(self.created_time, 'isoformat') else str(self.created_time),
                'has_children': len(self.children) > 0
            }


class KnowledgeCategory(Base):
    """知识分类表"""
    __tablename__ = 'knowledge_categories'
    
    if SQLALCHEMY_AVAILABLE:
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String(100), nullable=False)
        description = Column(Text)
        category_type = Column(String(50))  # project_info, project_management, jira_spec
        parent_id = Column(Integer, ForeignKey('knowledge_categories.id'))
        created_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
        
        # 关系
        parent = relationship("KnowledgeCategory", remote_side=[id], back_populates="children")
        children = relationship("KnowledgeCategory", back_populates="parent")
    else:
        # 简化版本
        def __init__(self, **kwargs):
            self.id = kwargs.get('id', 0)
            self.name = kwargs.get('name', '')
            self.description = kwargs.get('description', '')
            self.category_type = kwargs.get('category_type', '')
            self.parent_id = kwargs.get('parent_id', None)
            self.created_time = kwargs.get('created_time', datetime.now(timezone.utc))
            self.children = []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'category_type': self.category_type,
            'parent_id': self.parent_id,
            'created_time': self.created_time.isoformat() if hasattr(self.created_time, 'isoformat') else str(self.created_time),
            'children_count': len(self.children) if hasattr(self, 'children') else 0
        }


class AILearningLog(Base):
    """AI学习日志表"""
    __tablename__ = 'ai_learning_logs'
    
    if SQLALCHEMY_AVAILABLE:
        id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        chunk_id = Column(String(36), ForeignKey('content_chunks.id'))
        query_text = Column(Text)  # 用户查询
        used_context = Column(Text)  # 使用的上下文
        response_text = Column(Text)  # AI响应
        feedback_score = Column(Integer)  # 反馈评分
        learning_time = Column(DateTime, default=datetime.utcnow)
        agent_name = Column(String(100))  # 使用的AI代理
        log_metadata = Column(JSON)  # 扩展元数据（避免与SQLAlchemy metadata属性冲突）
    else:
        # 简化版本
        def __init__(self, **kwargs):
            self.id = str(uuid.uuid4())
            self.chunk_id = kwargs.get('chunk_id', '')
            self.query_text = kwargs.get('query_text', '')
            self.used_context = kwargs.get('used_context', '')
            self.response_text = kwargs.get('response_text', '')
            self.feedback_score = kwargs.get('feedback_score', 0)
            self.learning_time = kwargs.get('learning_time', datetime.now(timezone.utc))
            self.agent_name = kwargs.get('agent_name', '')
            self.log_metadata = kwargs.get('log_metadata', {})
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'chunk_id': self.chunk_id,
            'query_text': self.query_text,
            'used_context': self.used_context,
            'response_text': self.response_text,
            'feedback_score': self.feedback_score,
            'learning_time': self.learning_time.isoformat() if hasattr(self.learning_time, 'isoformat') else str(self.learning_time),
            'agent_name': self.agent_name,
            'metadata': self.log_metadata
        }


# 数据库管理器
class KnowledgeDatabase:
    """知识库数据库管理器"""
    
    def __init__(self, db_path: str = 'knowledge.db'):
        self.db_path = db_path
        self.engine = None
        self.SessionLocal = None
        
        if SQLALCHEMY_AVAILABLE:
            self._init_sqlalchemy()
        else:
            print("使用简化内存数据库（SQLAlchemy未安装）")
            self._init_simple_db()
    
    def _init_sqlalchemy(self):
        """初始化SQLAlchemy"""
        # 使用SQLite
        db_url = f'sqlite:///{self.db_path}'
        self.engine = create_engine(db_url, connect_args={'check_same_thread': False})
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
        # 创建表
        Base.metadata.create_all(bind=self.engine)
        # 自动迁移：同步模型列到数据库表
        self._sync_schema()
        print(f"SQLAlchemy数据库初始化完成: {db_url}")
    
    def _sync_schema(self):
        """自动同步模型列到数据库表，处理新增列的迁移"""
        import sqlalchemy as sa
        try:
            inspector = sa.inspect(self.engine)
            for table_name, table in Base.metadata.tables.items():
                existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
                model_columns = {col.name for col in table.columns}
                missing = model_columns - existing_columns
                if missing:
                    with self.engine.connect() as conn:
                        for col_name in missing:
                            col = table.columns[col_name]
                            col_type = col.type
                            conn.execute(
                                sa.text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}')
                            )
                        conn.commit()
                    print(f"数据库迁移: 表 '{table_name}' 添加了新列: {', '.join(missing)}")
        except Exception as e:
            print(f"数据库模式同步跳过: {e}")
    
    def _init_simple_db(self):
        """初始化简化数据库（内存存储）"""
        self.files = {}  # id -> KnowledgeFile
        self.chunks = {}  # id -> ContentChunk
        self.categories = {}  # id -> KnowledgeCategory
        self.learning_logs = {}  # id -> AILearningLog
        print("简化内存数据库初始化完成")
    
    def get_session(self):
        """获取数据库会话"""
        if SQLALCHEMY_AVAILABLE and self.SessionLocal:
            return self.SessionLocal()
        else:
            return SimpleDBSession(self)
    
    def close(self):
        """关闭数据库连接"""
        if self.engine:
            self.engine.dispose()


# 简化数据库会话
class SimpleDBSession:
    """简化数据库会话（用于SQLAlchemy不可用时）"""
    
    def __init__(self, db_manager):
        self.db = db_manager
        self._files = db_manager.files
        self._chunks = db_manager.chunks
        self._categories = db_manager.categories
        self._learning_logs = db_manager.learning_logs
    
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def add(self, obj):
        """添加对象"""
        if isinstance(obj, KnowledgeFile):
            self._files[obj.id] = obj
        elif isinstance(obj, ContentChunk):
            self._chunks[obj.id] = obj
        elif isinstance(obj, KnowledgeCategory):
            if obj.id == 0:
                obj.id = max(self._categories.keys(), default=0) + 1
            self._categories[obj.id] = obj
        elif isinstance(obj, AILearningLog):
            self._learning_logs[obj.id] = obj
    
    def commit(self):
        """提交更改（简化版本无操作）"""
        pass
    
    def close(self):
        """关闭会话（简化版本无操作）"""
        pass
    
    def query(self, model):
        """查询接口"""
        if model == KnowledgeFile:
            return SimpleQuery(self._files.values())
        elif model == ContentChunk:
            return SimpleQuery(self._chunks.values())
        elif model == KnowledgeCategory:
            return SimpleQuery(self._categories.values())
        elif model == AILearningLog:
            return SimpleQuery(self._learning_logs.values())
        else:
            return SimpleQuery([])


# 简化查询对象
class SimpleQuery:
    """简化查询对象"""
    
    def __init__(self, data):
        self.data = list(data)
    
    def filter(self, **kwargs):
        """过滤"""
        result = self.data
        for key, value in kwargs.items():
            if hasattr(value, '__call__'):
                # 处理 like 等操作
                pass
            else:
                result = [item for item in result if getattr(item, key, None) == value]
        self.data = result
        return self
    
    def all(self):
        """获取所有结果"""
        return self.data
    
    def first(self):
        """获取第一个结果"""
        return self.data[0] if self.data else None
    
    def count(self):
        """计数"""
        return len(self.data)


# 全局数据库实例
knowledge_db = None

def init_knowledge_database(db_path: str = 'knowledge.db'):
    """初始化知识库数据库"""
    global knowledge_db
    knowledge_db = KnowledgeDatabase(db_path)
    return knowledge_db

def get_knowledge_db():
    """获取知识库数据库实例"""
    global knowledge_db
    if knowledge_db is None:
        knowledge_db = init_knowledge_database()
    return knowledge_db

# 示例使用
if __name__ == "__main__":
    # 初始化数据库
    db = init_knowledge_database('test_knowledge.db')
    
    # 创建示例数据
    with db.get_session() as session:
        # 创建分类
        category = KnowledgeCategory(
            name="项目文档",
            description="项目相关的需求、设计等文档",
            category_type="project_info"
        )
        session.add(category)
        
        # 创建文件
        file = KnowledgeFile(
            filename="项目需求规格说明书.docx",
            file_type="docx",
            file_size=1024000,
            upload_user="admin",
            category="project_info",
            description="项目需求规格说明书"
        )
        session.add(file)
        
        session.commit()
        
        print("数据库初始化测试完成")
        print(f"创建分类: {category.name}")
        print(f"创建文件: {file.filename}")