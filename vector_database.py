"""
向量数据库集成模块
使用ChromaDB存储和检索文本向量，支持语义搜索
"""

import os
import json
import logging
from typing import List, Dict, Any
from pathlib import Path

# 设置pydantic.v1兼容性环境变量，以支持Python 3.14
os.environ.setdefault('PYDANTIC_V1_COMPATIBILITY', '1')

logger = logging.getLogger(__name__)

# 延迟导入标记 - 首次使用时才尝试导入
VECTOR_DB_AVAILABLE = False
_chromadb_import_tried = False

# 缓存导入的模块引用（sentence_transformers 延迟到实际使用时才尝试导入，避免阻塞）
_chromadb_mod = None
_chromadb_settings_mod = None
_sentence_transformer_mod = None
_numpy_mod = None


def _ensure_chromadb() -> bool:
    """延迟导入chromadb和相关库，仅在首次使用时执行"""
    global VECTOR_DB_AVAILABLE, _chromadb_import_tried
    global _chromadb_mod, _chromadb_settings_mod, _numpy_mod
    if _chromadb_import_tried:
        return VECTOR_DB_AVAILABLE
    _chromadb_import_tried = True

    try:
        import chromadb  # type: ignore
        from chromadb.config import Settings  # type: ignore
        import numpy as np
        _chromadb_mod = chromadb
        _chromadb_settings_mod = Settings
        _numpy_mod = np
        VECTOR_DB_AVAILABLE = True
        logger.info("向量数据库模块可用：ChromaDB和numpy已成功导入")
    except ImportError as e:
        logger.warning(f"向量数据库模块导入失败: {e}")
        logger.warning("向量搜索功能将不可用，但文件上传和关键词搜索仍可工作")
    except Exception as e:
        logger.error(f"向量数据库初始化错误: {e}")
        logger.warning("向量搜索功能将不可用，但文件上传和关键词搜索仍可工作")

    return VECTOR_DB_AVAILABLE


class VectorDatabaseError(Exception):
    """向量数据库错误"""
    pass


class VectorDatabase:
    """向量数据库管理器"""
    
    def __init__(self, persist_directory: str = "chroma_db", 
                 embedding_model: str = "all-MiniLM-L6-v2"):
        """
        初始化向量数据库
        
        Args:
            persist_directory: 持久化存储目录
            embedding_model: 嵌入模型名称
        """
        self.persist_directory = Path(persist_directory)
        self.embedding_model_name = embedding_model
        self.is_available = _ensure_chromadb()
        
        if not self.is_available:
            logger.warning("向量数据库不可用，相关功能将降级为关键词搜索")
            self.client = None
            self.embedding_model = None
            self.embedding_dimension = 0
            return
        
        # 创建目录
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        
        # 初始化嵌入模型
        self._init_embedding_model()
        
        # 初始化ChromaDB客户端
        self._init_chroma_client()
        
        logger.info(f"向量数据库初始化完成，模型: {embedding_model}, 存储路径: {self.persist_directory}")
    
    def _init_embedding_model(self):
        """初始化嵌入模型（优先使用本地离线方案，避免网络阻塞）"""
        if not self.is_available:
            logger.warning("向量数据库不可用，跳过嵌入模型初始化")
            return
        
        # 方案1: 本地HashingVectorizer（首选，无需下载，完全离线，瞬间完成）
        logger.info("使用本地HashingVectorizer作为嵌入方案...")
        try:
            from sklearn.feature_extraction.text import HashingVectorizer
            import numpy as np
            
            class LocalEmbedding:
                def __init__(self):
                    self.vectorizer = HashingVectorizer(n_features=384, norm='l2',
                                                       alternate_sign=False)
                
                def encode(self, texts, convert_to_numpy=True):
                    if isinstance(texts, str):
                        texts = [texts]
                    embeddings = self.vectorizer.transform(texts).toarray()
                    if convert_to_numpy:
                        return np.array(embeddings, dtype=np.float32)
                    return embeddings
                
                def get_sentence_embedding_dimension(self):
                    return 384
            
            self.embedding_model = LocalEmbedding()
            self.embedding_dimension = 384
            logger.info("本地HashingVectorizer嵌入模型初始化成功，维度: 384")
            return
        except Exception as e:
            logger.warning(f"HashingVectorizer初始化失败: {e}")
        
        # 方案2: ChromaDB内置ONNX嵌入（需下载模型，但较小）
        try:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
            self.embedding_model = ONNXMiniLM_L6_V2()
            self.embedding_dimension = 384
            logger.info(f"使用ONNX MiniLM嵌入函数: 维度 {self.embedding_dimension}")
            return
        except Exception as e2:
            logger.warning(f"ONNX嵌入函数加载失败: {e2}")
        
        # 方案3: SentenceTransformer（需下载模型，公司网络可能受限）
        try:
            global _sentence_transformer_mod
            if _sentence_transformer_mod is None:
                from sentence_transformers import SentenceTransformer
                _sentence_transformer_mod = SentenceTransformer
            import os as _os
            old_hf = _os.environ.get('HF_ENDPOINT')
            _os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
            try:
                self.embedding_model = _sentence_transformer_mod(self.embedding_model_name)
            finally:
                if old_hf:
                    _os.environ['HF_ENDPOINT'] = old_hf
                else:
                    _os.environ.pop('HF_ENDPOINT', None)
            self.embedding_dimension = self.embedding_model.get_sentence_embedding_dimension()
            logger.info(f"嵌入模型加载成功: {self.embedding_model_name}, 维度: {self.embedding_dimension}")
            return
        except Exception as e3:
            logger.warning(f"SentenceTransformer加载失败: {e3}")
        
        logger.error("所有嵌入模型都加载失败，向量数据库降级为不可用")
        self.is_available = False
        self.embedding_model = None
        self.embedding_dimension = 0
    
    def _init_chroma_client(self):
        """初始化ChromaDB客户端"""
        if not self.is_available:
            logger.warning("向量数据库不可用，跳过ChromaDB客户端初始化")
            return
            
        try:
            self.client = _chromadb_mod.PersistentClient(
                path=str(self.persist_directory),
                settings=_chromadb_settings_mod(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            logger.info("ChromaDB客户端初始化成功")
        except Exception as e:
            logger.error(f"ChromaDB客户端初始化失败: {e}")
            self.is_available = False
            self.client = None
    
    def get_or_create_collection(self, collection_name: str, 
                                 metadata: Dict[str, Any] = None):
        """
        获取或创建集合（不使用embedding_function参数，我们自己算向量再传入）
        
        Args:
            collection_name: 集合名称
            metadata: 集合元数据
            
        Returns:
            ChromaDB集合对象或None（如果向量数据库不可用）
        """
        if not self.is_available or not self.client:
            logger.warning(f"向量数据库不可用，无法获取或创建集合: {collection_name}")
            return None
            
        try:
            # 先尝试 get_or_create_collection（不传embedding_function）
            effective_metadata = metadata if metadata else None
            collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata=effective_metadata
            )
            logger.info(f"获取或创建集合: {collection_name}")
            return collection
        except Exception as e:
            logger.warning(f"get_or_create_collection失败，尝试分离方式: {e}")
            try:
                collection = self.client.get_collection(collection_name)
                logger.info(f"获取现有集合: {collection_name}")
                return collection
            except Exception:
                try:
                    collection = self.client.create_collection(
                        name=collection_name,
                        metadata=effective_metadata
                    )
                    logger.info(f"创建新集合: {collection_name}")
                    return collection
                except Exception as e3:
                    logger.error(f"创建集合失败: {e3}")
                    return None
    
    def _get_embedding_function(self):
        """获取嵌入函数（兼容ChromaDB）"""
        if not self.is_available or not self.embedding_model:
            logger.warning("向量数据库不可用，无法提供嵌入函数")
            # 返回一个空函数
            def empty_embedding_function(_texts: List[str]) -> List[List[float]]:
                return []
            return empty_embedding_function
        
        # 检测嵌入模型类型并适配
        if hasattr(self.embedding_model, 'encode'):
            # SentenceTransformer风格
            def st_embedding_function(texts: List[str]) -> List[List[float]]:
                embeddings = self.embedding_model.encode(texts, convert_to_numpy=True)
                return embeddings.tolist()
            return st_embedding_function
        else:
            # ChromaDB内置嵌入函数风格（如ONNXMiniLM_L6_V2，是callable）
            return self.embedding_model
    
    def embed_text(self, texts: List[str]) -> Any:
        """
        将文本列表转换为向量
        
        Args:
            texts: 文本列表
            
        Returns:
            向量数组
        """
        if not self.is_available or not self.embedding_model:
            logger.warning("向量数据库不可用，无法生成文本向量")
            # 返回空数组
            if _numpy_mod is not None:
                return _numpy_mod.array([]).reshape(0, 0)
            import numpy as np
            return np.array([]).reshape(0, 0)
        
        try:
            embeddings = self.embedding_model.encode(texts, convert_to_numpy=True)
            return embeddings
        except Exception as e:
            logger.error(f"文本向量化失败: {e}")
            raise VectorDatabaseError(f"文本向量化失败: {str(e)}")
    
    @staticmethod
    def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """清洗元数据，确保所有值都是ChromaDB接受的类型（str, int, float, bool, list, None）
        
        ChromaDB不接受嵌套dict作为metadata值，遇到嵌套dict会转为JSON字符串。
        """
        if metadata is None:
            return {}
        sanitized = {}
        for k, v in metadata.items():
            if v is None or isinstance(v, (str, int, float, bool)):
                sanitized[k] = v
            elif isinstance(v, list):
                sanitized[k] = [str(item) if not isinstance(item, (str, int, float, bool)) else item for item in v]
            elif isinstance(v, dict):
                sanitized[k] = json.dumps(v, ensure_ascii=False)
            else:
                sanitized[k] = str(v)
        return sanitized

    def add_documents(self, collection_name: str, 
                     documents: List[Dict[str, Any]]) -> List[str]:
        """
        添加文档到集合（自行计算向量，不依赖ChromaDB嵌入函数）
        
        Args:
            collection_name: 集合名称
            documents: 文档列表，每个文档包含:
                - text: 文档文本
                - id: 文档ID（可选）
                - metadata: 文档元数据
                
        Returns:
            文档ID列表
        """
        if not self.is_available:
            logger.warning(f"向量数据库不可用，跳过文档添加: {collection_name}")
            return [f"mock_{i}" for i in range(len(documents))]
        
        try:
            collection = self.get_or_create_collection(collection_name)
            if collection is None:
                logger.warning(f"无法获取集合 {collection_name}，跳过文档添加")
                return []
            
            # 准备数据
            ids = []
            texts = []
            metadatas = []
            
            for i, doc in enumerate(documents):
                doc_id = doc.get('id', f"{collection_name}_{i}_{len(ids)}")
                text = doc.get('text', '')
                metadata = doc.get('metadata', {})
                
                if not text:
                    logger.warning(f"文档 {doc_id} 文本为空，跳过")
                    continue
                
                ids.append(doc_id)
                texts.append(text)
                metadatas.append(self._sanitize_metadata(metadata))
            
            if not texts:
                logger.warning("没有有效的文档文本")
                return []
            
            # 预计算向量（使用我们自己的embedding_model，避免ChromaDB默认ONNX下载）
            embeddings = self.embed_text(texts)
            if embeddings is None or (hasattr(embeddings, 'size') and embeddings.size == 0):
                logger.warning("向量计算失败，尝试不使用预计算向量")
                collection.add(
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids
                )
            else:
                embeddings_list = embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings
                collection.add(
                    embeddings=embeddings_list,
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids
                )
            
            logger.info(f"添加到集合 {collection_name}: {len(ids)} 个文档")
            return ids
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            raise VectorDatabaseError(f"添加文档失败: {str(e)}")
    
    def add_chunks(self, file_id: str, chunks: List[Dict[str, Any]], 
                  category: str = None) -> List[str]:
        """
        添加知识库切片到向量数据库
        
        Args:
            file_id: 文件ID
            chunks: 切片列表，每个切片包含:
                - chunk_id: 切片ID
                - content_text: 切片文本
                - metadata: 切片元数据
            category: 分类（用于集合名称）
            
        Returns:
            向量ID列表
        """
        if not self.is_available:
            logger.warning(f"向量数据库不可用，跳过切片添加: 文件 {file_id}")
            # 返回模拟ID列表以保持接口兼容性
            mock_ids = []
            for i, chunk in enumerate(chunks):
                mock_id = f"mock_{file_id}_{i}"
                mock_ids.append(mock_id)
                chunk['vector_id'] = mock_id
            return mock_ids
        
        try:
            # 确定集合名称
            if category:
                collection_name = f"knowledge_{category}"
            else:
                collection_name = "knowledge_default"
            
            # 准备文档
            documents = []
            for chunk in chunks:
                chunk_id = chunk.get('chunk_id')
                content_text = chunk.get('content_text', '')
                metadata = chunk.get('metadata', {})
                
                if not content_text:
                    continue
                
                # 构建文档元数据
                doc_metadata = {
                    'file_id': file_id,
                    'chunk_id': chunk_id,
                    'chunk_type': chunk.get('chunk_type', 'unknown'),
                    'source': 'knowledge_base',
                    **metadata
                }
                
                documents.append({
                    'id': f"{file_id}_{chunk_id}",
                    'text': content_text,
                    'metadata': doc_metadata
                })
            
            # 添加到集合
            vector_ids = self.add_documents(collection_name, documents)
            
            # 记录向量ID到元数据
            for i, vector_id in enumerate(vector_ids):
                if i < len(chunks):
                    chunks[i]['vector_id'] = vector_id
            
            logger.info(f"添加切片到向量数据库: 文件 {file_id}, {len(vector_ids)} 个切片")
            return vector_ids
            
        except Exception as e:
            logger.error(f"添加切片失败: {e}")
            raise VectorDatabaseError(f"添加切片失败: {str(e)}")
    
    def search(self, collection_name: str, query: str, 
               n_results: int = 5, filter_metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        语义搜索（自行计算查询向量，不依赖ChromaDB嵌入函数）
        
        Args:
            collection_name: 集合名称
            query: 查询文本
            n_results: 返回结果数量
            filter_metadata: 过滤条件
            
        Returns:
            搜索结果列表
        """
        if not self.is_available:
            logger.warning(f"向量数据库不可用，无法执行语义搜索: '{query[:50]}...'")
            return []
        
        try:
            collection = self.get_or_create_collection(collection_name)
            if collection is None:
                logger.warning(f"无法获取集合 {collection_name}，跳过搜索")
                return []
            
            # 自行计算查询向量
            query_embedding = self.embed_text([query])
            query_embedding_list = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else query_embedding
            
            # 执行搜索
            results = collection.query(
                query_embeddings=query_embedding_list,
                n_results=n_results,
                where=filter_metadata
            )
            
            # 解析结果
            search_results = []
            
            if (results.get('documents') and results['documents'][0]
                    and results.get('metadatas') and results['metadatas'][0]
                    and results.get('distances') and results['distances'][0]
                    and results.get('ids') and results['ids'][0]):
                for i, (doc, metadata, distance, doc_id) in enumerate(zip(
                    results['documents'][0],
                    results['metadatas'][0],
                    results['distances'][0],
                    results['ids'][0]
                )):
                    search_results.append({
                        'id': doc_id,
                        'text': doc,
                        'metadata': metadata,
                        'score': 1.0 - distance,
                        'rank': i + 1
                    })
            
            logger.info(f"语义搜索完成: 查询 '{query[:50]}...', 结果数量: {len(search_results)}")
            return search_results
            
        except Exception as e:
            logger.error(f"语义搜索失败: {e}")
            raise VectorDatabaseError(f"语义搜索失败: {str(e)}")
    
    def search_knowledge(self, query: str, category: str = None, 
                        n_results: int = 10) -> List[Dict[str, Any]]:
        """
        搜索知识库（混合搜索：向量搜索 + 关键词搜索）
        
        Args:
            query: 查询文本
            category: 分类过滤
            n_results: 返回结果数量
            
        Returns:
            知识库搜索结果
        """
        if not self.is_available or not self.client:
            logger.warning(f"向量数据库不可用，无法执行知识库搜索: '{query[:50]}...'")
            return []
        
        try:
            # 确定搜索的集合
            if category:
                collections = [f"knowledge_{category}"]
            else:
                collections = [
                    "knowledge_project_info",
                    "knowledge_project_management", 
                    "knowledge_jira_spec",
                    "knowledge_default"
                ]
            
            vector_results = []
            seen_texts = set()
            
            # 只搜索实际存在的集合
            existing_collections = [c.name for c in self.client.list_collections()]
            
            for collection_name in collections:
                if collection_name not in existing_collections:
                    continue
                
                try:
                    results = self.search(collection_name, query, n_results)
                    
                    for result in results:
                        result['collection'] = collection_name
                        # 弱向量模型低分结果直接丢弃 （score<0.1基本上就是噪音）
                        if result['score'] < 0.1:
                            continue
                        text_dedup = result['text'][:100]
                        if text_dedup not in seen_texts:
                            seen_texts.add(text_dedup)
                            vector_results.append(result)
                        
                except Exception as e:
                    logger.warning(f"搜索集合 {collection_name} 时出错: {e}")
                    continue
            
            # 关键词搜索补充（SQLite LIKE）
            keyword_results = self._keyword_search(query, category)
            
            # 合并结果（去重）
            for kr in keyword_results:
                text_dedup = kr['text'][:100]
                if text_dedup not in seen_texts:
                    seen_texts.add(text_dedup)
                    vector_results.append(kr)
            
            # 按分数排序
            vector_results.sort(key=lambda x: x['score'], reverse=True)
            
            # 限制结果数量
            if len(vector_results) > n_results:
                vector_results = vector_results[:n_results]
            
            logger.info(f"知识库搜索完成: 查询 '{query[:50]}...', 向量={len(vector_results)}, 关键词={len(keyword_results)}, 总={len(vector_results)}")
            return vector_results
            
        except Exception as e:
            logger.error(f"知识库搜索失败: {e}")
            raise VectorDatabaseError(f"知识库搜索失败: {str(e)}")

    def _keyword_search(self, query: str, category: str = None, n_results: int = 10) -> List[Dict[str, Any]]:
        """关键词搜索（SQLite LIKE），返回与search()相同格式的结果"""
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'knowledge.db')
            if not os.path.exists(db_path):
                logger.warning(f"知识库数据库不存在: {db_path}")
                return []
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            results = []
            seen_texts = set()
            
            # 构建查询 - 对中文关键词做分词-like
            keywords = set()
            query_clean = query.strip()
            if query_clean:
                keywords.add(query_clean)
            
            # 对中文文本尝试按字切分（2-gram）
            import re
            chinese_chars = re.findall(r'[\u4e00-\u9fff]+', query_clean)
            for cc in chinese_chars:
                # 添加每个中文字作为独立关键词（提高召回）
                if len(cc) >= 2:
                    keywords.add(cc)
                # 2-gram
                if len(cc) >= 4:
                    for i in range(len(cc) - 1):
                        bigram = cc[i:i+2]
                        if len(bigram) >= 2:
                            keywords.add(bigram)
            
            # 还保留原始查询词的每个词
            for word in query_clean.replace('-', ' ').replace('_', ' ').split():
                if len(word.strip()) >= 1:
                    keywords.add(word.strip())
            
            # 执行搜索
            for keyword in keywords:
                if len(keyword) < 1:
                    continue
                
                sql = """
                    SELECT cc.id, cc.file_id, cc.chunk_index, cc.chunk_type, 
                           cc.content_text, cc.content_summary, kf.category, kf.filename
                    FROM content_chunks cc
                    JOIN knowledge_files kf ON kf.id = cc.file_id
                    WHERE cc.content_text LIKE ?
                """
                params = [f'%{keyword}%']
                
                if category:
                    sql += " AND kf.category = ?"
                    params.append(category)
                
                sql += " LIMIT ?"
                params.append(n_results)
                
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                
                for row in rows:
                    chunk_id, file_id, chunk_index, chunk_type, content_text, content_summary, chunk_category, filename = row
                    
                    text_dedup = (content_text or '')[:100]
                    if text_dedup in seen_texts:
                        continue
                    seen_texts.add(text_dedup)
                    
                    # 计算关键词匹配得分
                    score = 0.5  # 基础分
                    for kw in keywords:
                        if kw and content_text and kw in content_text:
                            score += 0.1
                    score = min(score, 0.95)
                    
                    results.append({
                        'id': chunk_id,
                        'text': content_text or '',
                        'metadata': {
                            'chunk_id': chunk_id,
                            'file_id': file_id,
                            'chunk_index': chunk_index,
                            'chunk_type': chunk_type,
                            'category': chunk_category,
                            'filename': filename,
                            'source': 'knowledge_base',
                        },
                        'score': score,
                        'rank': len(results) + 1,
                        'collection': f"knowledge_{chunk_category}" if chunk_category else "knowledge_default",
                        'search_method': 'keyword'
                    })
            
            conn.close()
            
            # 按分数排序，去重
            results.sort(key=lambda x: x['score'], reverse=True)
            return results[:n_results]
            
        except Exception as e:
            logger.error(f"关键词搜索失败: {e}")
            return []
    
    def update_document(self, collection_name: str, doc_id: str, 
                       text: str = None, metadata: Dict[str, Any] = None):
        """
        更新文档
        
        Args:
            collection_name: 集合名称
            doc_id: 文档ID
            text: 新文本（可选）
            metadata: 新元数据（可选）
        """
        if not self.is_available:
            logger.warning(f"向量数据库不可用，跳过文档更新: {collection_name}, ID {doc_id}")
            return
        
        try:
            collection = self.get_or_create_collection(collection_name)
            if collection is None:
                logger.warning(f"无法获取集合 {collection_name}，跳过文档更新")
                return
            
            if text:
                # 更新文档文本
                sanitized_meta = self._sanitize_metadata(metadata) if metadata else None
                collection.update(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[sanitized_meta] if sanitized_meta else None
                )
            elif metadata:
                # 只更新元数据
                sanitized_meta = self._sanitize_metadata(metadata)
                collection.update(
                    ids=[doc_id],
                    metadatas=[sanitized_meta]
                )
            
            logger.info(f"文档更新成功: 集合 {collection_name}, ID {doc_id}")
            
        except Exception as e:
            logger.error(f"文档更新失败: {e}")
            raise VectorDatabaseError(f"文档更新失败: {str(e)}")
    
    def delete_document(self, collection_name: str, doc_id: str):
        """删除文档"""
        if not self.is_available:
            logger.warning(f"向量数据库不可用，跳过文档删除: {collection_name}, ID {doc_id}")
            return
        
        try:
            collection = self.get_or_create_collection(collection_name)
            if collection is None:
                logger.warning(f"无法获取集合 {collection_name}，跳过文档删除")
                return
            collection.delete(ids=[doc_id])
            logger.info(f"文档删除成功: 集合 {collection_name}, ID {doc_id}")
        except Exception as e:
            logger.error(f"文档删除失败: {e}")
            raise VectorDatabaseError(f"文档删除失败: {str(e)}")
    
    def delete_by_file_id(self, file_id: str):
        """根据文件ID删除所有相关向量"""
        if not self.is_available or not self.client:
            logger.warning(f"向量数据库不可用，跳过按文件ID删除向量: {file_id}")
            return
        
        try:
            # 搜索所有包含该file_id的集合
            collections = self.client.list_collections()
            
            for collection in collections:
                try:
                    # 查询包含该file_id的文档
                    results = collection.get(where={'file_id': file_id})
                    
                    if results['ids']:
                        collection.delete(ids=results['ids'])
                        logger.info(f"从集合 {collection.name} 删除 {len(results['ids'])} 个文档 (文件ID: {file_id})")
                        
                except Exception as e:
                    logger.warning(f"从集合 {collection.name} 删除文档失败: {e}")
                    continue
            
            logger.info(f"文件ID {file_id} 的所有向量已删除")
            
        except Exception as e:
            logger.error(f"按文件ID删除向量失败: {e}")
            raise VectorDatabaseError(f"按文件ID删除向量失败: {str(e)}")
    
    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """获取集合统计信息"""
        if not self.is_available:
            logger.warning(f"向量数据库不可用，无法获取集合统计: {collection_name}")
            return {
                'collection_name': collection_name,
                'document_count': 0,
                'embedding_model': self.embedding_model_name,
                'embedding_dimension': self.embedding_dimension,
                'available': False
            }
        
        try:
            collection = self.get_or_create_collection(collection_name)
            if collection is None:
                logger.warning(f"无法获取集合 {collection_name}，返回空统计")
                return {
                    'collection_name': collection_name,
                    'document_count': 0,
                    'embedding_model': self.embedding_model_name,
                    'embedding_dimension': self.embedding_dimension,
                    'available': False
                }
            
            # 获取所有文档
            results = collection.get()
            count = len(results['ids']) if results['ids'] else 0
            
            return {
                'collection_name': collection_name,
                'document_count': count,
                'embedding_model': self.embedding_model_name,
                'embedding_dimension': self.embedding_dimension,
                'available': True
            }
            
        except Exception as e:
            logger.error(f"获取集合统计失败: {e}")
            raise VectorDatabaseError(f"获取集合统计失败: {str(e)}")
    
    def get_all_collections_stats(self) -> Dict[str, Any]:
        """获取所有集合统计信息"""
        if not self.is_available or not self.client:
            logger.warning("向量数据库不可用，无法获取所有集合统计")
            return {
                'total_collections': 0,
                'total_documents': 0,
                'collections': {},
                'available': False
            }
        
        try:
            collections = self.client.list_collections()
            
            stats = {}
            total_documents = 0
            
            for collection in collections:
                collection_name = collection.name
                collection_stats = self.get_collection_stats(collection_name)
                stats[collection_name] = collection_stats
                total_documents += collection_stats['document_count']
            
            return {
                'total_collections': len(collections),
                'total_documents': total_documents,
                'collections': stats,
                'available': True
            }
            
        except Exception as e:
            logger.error(f"获取所有集合统计失败: {e}")
            raise VectorDatabaseError(f"获取所有集合统计失败: {str(e)}")
    
    def reset_collection(self, collection_name: str):
        """重置集合（删除所有文档）"""
        if not self.is_available or not self.client:
            logger.warning(f"向量数据库不可用，跳过集合重置: {collection_name}")
            return
        
        try:
            self.client.delete_collection(collection_name)
            logger.info(f"集合重置成功: {collection_name}")
        except Exception as e:
            logger.error(f"集合重置失败: {e}")
            raise VectorDatabaseError(f"集合重置失败: {str(e)}")


# 全局实例
_vector_db = None

def get_vector_database() -> VectorDatabase:
    """获取向量数据库全局实例"""
    global _vector_db
    if _vector_db is None:
        _vector_db = VectorDatabase()
    return _vector_db


# 工具函数
def chunk_to_vector_document(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """将切片转换为向量数据库文档"""
    chunk_id = chunk.get('chunk_id', chunk.get('id'))
    return {
        'id': chunk_id,
        'text': chunk.get('content_text', ''),
        'metadata': VectorDatabase._sanitize_metadata({
            'chunk_id': chunk_id,
            'file_id': chunk.get('file_id'),
            'chunk_index': chunk.get('chunk_index'),
            'chunk_type': chunk.get('chunk_type'),
            'category': chunk.get('category'),
            'source': 'knowledge_base',
            **chunk.get('metadata', {})
        })
    }


def search_similar_chunks(query: str, category: str = None, 
                         n_results: int = 5) -> List[Dict[str, Any]]:
    """搜索相似切片（便捷函数）"""
    vector_db = get_vector_database()
    results = vector_db.search_knowledge(query, category, n_results)
    return results


if __name__ == "__main__":
    print("向量数据库集成模块测试")
    print("=" * 60)
    
    # 创建向量数据库实例
    vector_db = VectorDatabase(persist_directory="test_chroma_db")
    
    # 测试文本向量化
    test_texts = ["这是一个测试文档", "这是另一个测试文档"]
    embeddings = vector_db.embed_text(test_texts)
    print(f"文本向量化测试:")
    print(f"  文本数量: {len(test_texts)}")
    print(f"  向量维度: {embeddings.shape}")
    
    # 测试文档添加
    test_documents = [
        {
            'id': 'test_doc_1',
            'text': '项目管理的核心是时间、成本和质量的控制。',
            'metadata': {'category': 'project_management', 'source': 'test'}
        },
        {
            'id': 'test_doc_2', 
            'text': 'Jira是流行的项目管理工具，支持敏捷开发。',
            'metadata': {'category': 'jira_spec', 'source': 'test'}
        }
    ]
    
    doc_ids = vector_db.add_documents("test_collection", test_documents)
    print(f"文档添加测试:")
    print(f"  添加文档数量: {len(doc_ids)}")
    print(f"  文档ID: {doc_ids}")
    
    # 测试语义搜索
    query = "项目管理工具"
    results = vector_db.search("test_collection", query, n_results=2)
    print(f"语义搜索测试:")
    print(f"  查询: '{query}'")
    print(f"  结果数量: {len(results)}")
    for result in results:
        print(f"    文档: {result['text'][:50]}... (分数: {result['score']:.4f})")
    
    # 获取集合统计
    stats = vector_db.get_collection_stats("test_collection")
    print(f"集合统计:")
    print(f"  集合名称: {stats['collection_name']}")
    print(f"  文档数量: {stats['document_count']}")
    
    # 清理测试目录
    import shutil
    if Path("test_chroma_db").exists():
        shutil.rmtree("test_chroma_db")
    
    print("\n✅ 向量数据库集成模块测试完成")