"""
知识库API模块
提供文件上传、知识检索、AI学习等接口
"""

import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path
from langchain_components import analyze_with_langchain

from flask import Blueprint, request, jsonify, current_app, send_file, send_from_directory
from werkzeug.utils import secure_filename

# 导入知识库组件
from knowledge_models import get_knowledge_db, KnowledgeFile, ContentChunk, KnowledgeCategory, AILearningLog
from knowledge_categories import category_manager, file_type_manager, get_category_options, get_subcategory_options
from chunk_processor import ChunkProcessor
from storage_manager import get_storage_manager, validate_file_extension, sanitize_filename
from feishu_integration import validate_feishu_url, check_feishu_permission, get_feishu_parser
from vector_database import get_vector_database, chunk_to_vector_document

# 创建蓝图
knowledge_bp = Blueprint('knowledge', __name__, url_prefix='/api/knowledge')

# 设置日志
logger = logging.getLogger(__name__)

# 调试：添加before_request钩子
@knowledge_bp.before_request
def before_knowledge_request():
    logger.debug(f"知识库蓝图请求: {request.path}, 方法: {request.method}")
    logger.debug(f"请求端点: {request.endpoint}")

# 全局实例
chunk_processor = ChunkProcessor(strategy='advanced')
storage_manager = get_storage_manager()

# 向量数据库延迟初始化（避免模块加载时下载HuggingFace模型导致服务器启动阻塞）
_vector_db_instance = None

def get_vector_db():
    """延迟获取向量数据库实例"""
    global _vector_db_instance
    if _vector_db_instance is None:
        _vector_db_instance = get_vector_database()
        # 首次初始化时自动索引JQL模板
        ensure_jql_templates_indexed()
    return _vector_db_instance


def index_jql_templates_to_kb():
    """将 jql_templates.json 中的JQL模板索引到知识库向量数据库，支持语义搜索"""
    try:
        template_file = os.path.join(os.path.dirname(__file__), 'jql_templates.json')
        if not os.path.exists(template_file):
            logger.warning("JQL模板文件不存在，跳过索引")
            return False

        with open(template_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        vector_db = get_vector_db()
        if not vector_db or not vector_db.is_available:
            logger.warning("向量数据库不可用，跳过JQL模板索引")
            return False

        documents = []
        for tmpl in data.get("templates", []):
            template_name = tmpl.get("name", "未命名模板")
            for proj_name, proj_jql in tmpl.get("projects", {}).items():
                text = (
                    f"JQL模板名称：{template_name}\n"
                    f"项目名称：{proj_name}\n"
                    f"JQL查询语句：{proj_jql}\n"
                    f"用途：该模板用于查询{proj_name}项目在Jira中的{template_name}相关数据"
                )
                doc_id = f"jql_template_{template_name}_{proj_name}"
                documents.append({
                    "id": doc_id,
                    "text": text,
                    "metadata": {
                        "source": "jql_templates.json",
                        "template_name": template_name,
                        "project_name": proj_name,
                        "type": "jql_template"
                    }
                })

        if documents:
            ids = vector_db.add_documents("knowledge_jira_spec", documents)
            logger.info(f"JQL模板索引完成: 共 {len(ids)} 条记录导入知识库")
            return True
        return False
    except Exception as e:
        logger.error(f"JQL模板索引失败: {e}")
        return False


# 启动时自动索引JQL模板
_index_jql_done = False

def ensure_jql_templates_indexed():
    """确保JQL模板已被索引（只执行一次）"""
    global _index_jql_done
    if not _index_jql_done:
        _index_jql_done = True
        index_jql_templates_to_kb()


def allowed_file(filename: str, category: str = None, subcategory: str = None) -> Tuple[bool, str]:
    """检查文件是否允许上传"""
    # 获取文件扩展名
    file_ext = Path(filename).suffix.lower()
    if not file_ext:
        return False, "文件无扩展名"

    # 移除点号
    file_ext = file_ext[1:] if file_ext.startswith('.') else file_ext

    # 检查分类是否允许该文件类型
    if category:
        is_valid = category_manager.validate_file_type(category, file_ext, subcategory)
        if not is_valid:
            allowed_types = category_manager.get_allowed_file_types(category, subcategory)
            if subcategory:
                subcat_name = category_manager.get_subcategory_name(category, subcategory)
                return False, f"子分类 '{subcat_name}' 不支持文件类型 '{file_ext}'，允许的类型: {', '.join(allowed_types)}"
            return False, f"分类 '{category}' 不支持文件类型 '{file_ext}'，允许的类型: {', '.join(allowed_types)}"

    # 检查文件类型管理器是否支持
    file_type_info = file_type_manager.get_file_type_info(file_ext)
    if not file_type_info or file_type_info.get('parser') == 'default_parser':
        logger.warning(f"文件类型 '{file_ext}' 不在标准配置中，但仍允许上传")

    return True, f"文件类型 '{file_ext}' 允许上传"


def _parse_tags(tags_str: str) -> list:
    """安全解析标签字符串，兼容JSON数组和普通逗号分隔字符串"""
    if not tags_str or not tags_str.strip():
        return []
    try:
        parsed = json.loads(tags_str)
        if isinstance(parsed, list):
            return parsed
        return [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        parts = [t.strip() for t in tags_str.split(',') if t.strip()]
        return parts if parts else [tags_str.strip()]


@knowledge_bp.route('/upload', methods=['POST'])
def upload_file():
    """
    文件上传接口
    支持普通文件上传和飞书文档URL
    """
    try:
        # 获取上传类型
        upload_type = request.form.get('upload_type', 'file')  # 'file' 或 'feishu'

        # 通用参数
        category = request.form.get('category')
        subcategory = request.form.get('subcategory', '')
        tags = request.form.get('tags', '')
        description = request.form.get('description', '')
        upload_user = request.form.get('upload_user', 'anonymous')

        if not category:
            return jsonify({
                'success': False,
                'error': '缺少必填参数: category'
            }), 400

        # 验证分类
        try:
            category_info = category_manager.get_category_info(category)
        except ValueError:
            return jsonify({
                'success': False,
                'error': f'无效的分类: {category}'
            }), 400

        if upload_type == 'file':
            # 处理文件上传
            if 'file' not in request.files:
                return jsonify({
                    'success': False,
                    'error': '没有上传文件'
                }), 400

            file = request.files['file']
            if file.filename == '':
                return jsonify({
                    'success': False,
                    'error': '文件名为空'
                }), 400

            # 验证文件
            is_allowed, message = allowed_file(file.filename, category, subcategory)
            if not is_allowed:
                return jsonify({
                    'success': False,
                    'error': message
                }), 400

            # 清理文件名
            safe_filename = sanitize_filename(file.filename)

            # 保存文件到对象存储
            storage_info = storage_manager.save_file(
                file_stream=file.stream,
                filename=safe_filename,
                category=category,
                subcategory=subcategory if subcategory else None,
                metadata={
                    'original_filename': file.filename,
                    'upload_type': 'file',
                    'upload_user': upload_user
                }
            )

            # 创建数据库记录
            db = get_knowledge_db()
            with db.get_session() as session:
                knowledge_file = KnowledgeFile(
                    filename=safe_filename,
                    file_type=Path(safe_filename).suffix[1:].lower(),  # 移除点号
                    file_size=storage_info['file_size'],
                    upload_user=upload_user,
                    category=category,
                    subcategory=subcategory if subcategory else None,
                    status='uploaded',
                    storage_path=storage_info['storage_path'],
                    tags=_parse_tags(tags),
                    description=description,
                    file_metadata={
                        'storage_info': storage_info,
                        'upload_time': datetime.now().isoformat()
                    }
                )

                session.add(knowledge_file)
                session.commit()

                file_id = knowledge_file.id

                logger.info(f"文件上传成功: {safe_filename}, ID: {file_id}")

            # 异步处理文件（使用线程避免阻塞上传响应）
            threading.Thread(
                target=process_file_async,
                args=(file_id, storage_info['absolute_path'], knowledge_file.file_type),
                daemon=True
            ).start()

            return jsonify({
                'success': True,
                'file_id': file_id,
                'filename': safe_filename,
                'storage_path': storage_info['storage_path'],
                'message': '文件上传成功，正在处理中'
            })

        elif upload_type == 'feishu':
            # 处理飞书文档上传
            feishu_url = request.form.get('feishu_url')
            if not feishu_url:
                return jsonify({
                    'success': False,
                    'error': '缺少飞书文档URL'
                }), 400

            # 验证飞书URL
            is_valid, message, doc_id = validate_feishu_url(feishu_url)
            if not is_valid:
                return jsonify({
                    'success': False,
                    'error': f'无效的飞书文档URL: {message}'
                }), 400

            # 检查权限
            permission_result = check_feishu_permission(feishu_url)
            if not permission_result.get('can_access', False):
                return jsonify({
                    'success': False,
                    'error': f'没有权限访问飞书文档: {permission_result.get("reason", "未知原因")}'
                }), 403

            # 创建数据库记录（飞书文档）
            db = get_knowledge_db()
            with db.get_session() as session:
                knowledge_file = KnowledgeFile(
                    filename=f"飞书文档_{doc_id}",
                    file_type='feishu',
                    file_size=0,
                    upload_user=upload_user,
                    category=category,
                    subcategory=subcategory if subcategory else None,
                    status='uploaded',
                    storage_path=feishu_url,  # 存储URL
                    feishu_doc_id=doc_id,
                    feishu_url=feishu_url,
                    permissions=permission_result.get('permissions', {}),
                    tags=_parse_tags(tags),
                    description=description,
                    file_metadata={
                        'permission_check': permission_result,
                        'upload_time': datetime.now().isoformat()
                    }
                )

                session.add(knowledge_file)
                session.commit()

                file_id = knowledge_file.id

                logger.info(f"飞书文档上传成功: {doc_id}, ID: {file_id}")

            # 异步处理飞书文档
            threading.Thread(
                target=process_feishu_document_async,
                args=(file_id, feishu_url, doc_id),
                daemon=True
            ).start()

            return jsonify({
                'success': True,
                'file_id': file_id,
                'doc_id': doc_id,
                'filename': knowledge_file.filename,
                'message': '飞书文档上传成功，正在处理中'
            })

        else:
            return jsonify({
                'success': False,
                'error': f'不支持的上传类型: {upload_type}'
            }), 400

    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        return jsonify({
            'success': False,
            'error': f'上传失败: {str(e)}'
        }), 500


def process_file_async(file_id: str, file_path: str, file_type: str):
    """异步处理文件（简化版本）"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 更新状态为处理中
            knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if not knowledge_file:
                logger.error(f"文件不存在: {file_id}")
                return

            knowledge_file.status = 'processing'
            session.commit()

            # 处理文件
            chunks, metadata = chunk_processor.process_file(file_path, file_type)

            # 保存切片到数据库
            chunk_records = []
            for i, chunk_elements in enumerate(chunks):
                # 将切片转换为文本
                chunk_text = chunk_processor.chunk_to_text(chunk_elements)

                # 确定切片类型：分析chunk中的元素类型分布
                element_types = {}
                for elem in chunk_elements:
                    t = elem.element_type if hasattr(elem, 'element_type') else 'text'
                    element_types[t] = element_types.get(t, 0) + 1

                # 根据元素类型决定chunk_type优先级：code > table > image > heading > mixed
                if element_types.get('code', 0) > 0:
                    chunk_type = 'code'
                elif element_types.get('table', 0) > 0:
                    chunk_type = 'table'
                elif element_types.get('image', 0) > 0:
                    chunk_type = 'image'
                elif element_types.get('heading', 0) > 0 and len(element_types) <= 2:
                    chunk_type = 'heading'
                elif len(element_types) <= 1:
                    chunk_type = 'text'
                else:
                    chunk_type = 'mixed'

                # 收集每个元素的内容摘要用于切片预览
                element_previews = []
                for elem in chunk_elements:
                    elem_type = elem.element_type if hasattr(elem, 'element_type') else 'text'
                    content = str(elem.content) if hasattr(elem, 'content') else ''
                    preview = content[:200] if content else ''
                    lang = ''
                    if elem_type == 'code' and hasattr(elem, 'metadata') and elem.metadata:
                        lang = elem.metadata.get('language', '')
                    element_previews.append({
                        'type': elem_type,
                        'preview': preview,
                        'language': lang
                    })

                # 创建切片记录
                chunk = ContentChunk(
                    file_id=file_id,
                    chunk_index=i,
                    chunk_type=chunk_type,
                    content_text=chunk_text,
                    content_summary=f"切片 {i+1} ({chunk_type})",
                    semantic_context=f"文件 {knowledge_file.filename} 的第 {i+1} 个切片",
                    start_position=i * 1000,  # 简化
                    end_position=(i + 1) * 1000,
                    chunk_metadata={
                        'element_count': len(chunk_elements),
                        'element_types': element_types,
                        'element_previews': element_previews,
                        'processing_metadata': metadata
                    }
                )

                session.add(chunk)
                chunk_records.append(chunk)

            # 更新文件状态
            knowledge_file.status = 'processed'
            meta = dict(knowledge_file.file_metadata) if isinstance(knowledge_file.file_metadata, dict) else {}
            meta['processing_result'] = {
                'chunk_count': len(chunks),
                'element_count': metadata.get('element_count', 0),
                'processing_time': datetime.now().isoformat()
            }
            knowledge_file.file_metadata = meta

            session.commit()

            # 将切片添加到向量数据库
            try:
                # 准备切片数据
                chunks_data = []
                for chunk in chunk_records:
                    chunks_data.append({
                        'chunk_id': chunk.id,
                        'file_id': file_id,
                        'content_text': chunk.content_text,
                        'chunk_type': chunk.chunk_type,
                        'chunk_index': chunk.chunk_index,
                        'metadata': chunk.chunk_metadata
                    })

                # 添加到向量数据库
                if chunks_data:
                    vector_ids = get_vector_db().add_chunks(
                        file_id=file_id,
                        chunks=chunks_data,
                        category=knowledge_file.category
                    )

                    # 更新切片记录中的向量ID
                    for chunk, vector_id in zip(chunk_records, vector_ids):
                        chunk.vector_id = vector_id

                    session.commit()
                    logger.info(f"切片向量化完成: {file_id}, 向量ID数量: {len(vector_ids)}")

            except Exception as vector_error:
                logger.error(f"切片向量化失败: {file_id}, 错误: {vector_error}")
                # 不阻塞主流程，仅记录错误

            logger.info(f"文件处理完成: {file_id}, 生成 {len(chunks)} 个切片")

    except Exception as e:
        logger.error(f"文件处理失败: {file_id}, 错误: {e}")

        # 更新状态为失败
        try:
            db = get_knowledge_db()
            with db.get_session() as session:
                knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()
                if knowledge_file:
                    knowledge_file.status = 'error'
                    meta = dict(knowledge_file.file_metadata) if isinstance(knowledge_file.file_metadata, dict) else {}
                    meta['error'] = str(e)
                    knowledge_file.file_metadata = meta
                    session.commit()
        except Exception as db_error:
            logger.error(f"更新文件状态失败: {db_error}")


def process_feishu_document_async(file_id: str, feishu_url: str, doc_id: str):
    """异步处理飞书文档"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 更新状态为处理中
            knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if not knowledge_file:
                logger.error(f"文件不存在: {file_id}")
                return

            knowledge_file.status = 'processing'
            session.commit()

            # 使用飞书解析器处理
            parser = get_feishu_parser()
            elements = parser.parse_document_to_elements(doc_id)

            # 将元素转换为切片（简化处理）
            # 在实际应用中，应该使用chunk_processor来处理元素列表
            chunk_size = 5  # 每5个元素一个切片
            chunks = [elements[i:i + chunk_size] for i in range(0, len(elements), chunk_size)]

            # 保存切片到数据库
            chunk_records = []
            for i, chunk_elements in enumerate(chunks):
                # 将切片转换为文本
                chunk_text = "\n\n".join([
                    f"{elem['element_type']}: {elem['content']}"
                    for elem in chunk_elements
                ])

                # 创建切片记录
                chunk = ContentChunk(
                    file_id=file_id,
                    chunk_index=i,
                    chunk_type='mixed',
                    content_text=chunk_text,
                    content_summary=f"飞书文档切片 {i+1}",
                    semantic_context=f"飞书文档 {doc_id} 的第 {i+1} 个切片",
                    start_position=i * 1000,
                    end_position=(i + 1) * 1000,
                    chunk_metadata={
                        'element_count': len(chunk_elements),
                        'source': 'feishu',
                        'doc_id': doc_id
                    }
                )

                session.add(chunk)
                chunk_records.append(chunk)

            # 更新文件状态
            knowledge_file.status = 'processed'
            meta = dict(knowledge_file.file_metadata) if isinstance(knowledge_file.file_metadata, dict) else {}
            meta['processing_result'] = {
                'chunk_count': len(chunks),
                'element_count': len(elements),
                'processing_time': datetime.now().isoformat(),
                'doc_id': doc_id
            }
            knowledge_file.file_metadata = meta

            session.commit()

            # 将切片添加到向量数据库
            try:
                # 准备切片数据
                chunks_data = []
                for chunk in chunk_records:
                    chunks_data.append({
                        'chunk_id': chunk.id,
                        'file_id': file_id,
                        'content_text': chunk.content_text,
                        'chunk_type': chunk.chunk_type,
                        'chunk_index': chunk.chunk_index,
                        'metadata': chunk.chunk_metadata
                    })

                # 添加到向量数据库
                if chunks_data:
                    vector_ids = get_vector_db().add_chunks(
                        file_id=file_id,
                        chunks=chunks_data,
                        category=knowledge_file.category
                    )

                    # 更新切片记录中的向量ID
                    for chunk, vector_id in zip(chunk_records, vector_ids):
                        chunk.vector_id = vector_id

                    session.commit()
                    logger.info(f"飞书文档切片向量化完成: {file_id}, 向量ID数量: {len(vector_ids)}")

            except Exception as vector_error:
                logger.error(f"飞书文档切片向量化失败: {file_id}, 错误: {vector_error}")
                # 不阻塞主流程，仅记录错误

            logger.info(f"飞书文档处理完成: {file_id}, 生成 {len(chunks)} 个切片")

    except Exception as e:
        logger.error(f"飞书文档处理失败: {file_id}, 错误: {e}")

        # 更新状态为失败
        try:
            db = get_knowledge_db()
            with db.get_session() as session:
                knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()
                if knowledge_file:
                    knowledge_file.status = 'error'
                    meta = dict(knowledge_file.file_metadata) if isinstance(knowledge_file.file_metadata, dict) else {}
                    meta['error'] = str(e)
                    knowledge_file.file_metadata = meta
                    session.commit()
        except Exception as db_error:
            logger.error(f"更新飞书文档状态失败: {db_error}")


@knowledge_bp.route('/files', methods=['GET'])
def list_files():
    """获取文件列表"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 构建查询
            query = session.query(KnowledgeFile)

            # 过滤条件
            category = request.args.get('category')
            if category:
                query = query.filter_by(category=category)

            subcategory = request.args.get('subcategory')
            if subcategory:
                query = query.filter_by(subcategory=subcategory)

            file_type = request.args.get('file_type')
            if file_type:
                query = query.filter_by(file_type=file_type)

            status = request.args.get('status')
            if status:
                query = query.filter_by(status=status)

            # 分页
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 20))
            offset = (page - 1) * per_page

            # 获取总数
            total = query.count()

            # 获取数据
            files = query.order_by(KnowledgeFile.upload_time.desc()).offset(offset).limit(per_page).all()

            # 转换为字典
            files_data = [file.to_dict() for file in files]

            return jsonify({
                'success': True,
                'data': files_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': (total + per_page - 1) // per_page
                }
            })

    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取文件列表失败: {str(e)}'
        }), 500


@knowledge_bp.route('/files/<file_id>', methods=['GET'])
def get_file(file_id: str):
    """获取文件详情"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()

            if not knowledge_file:
                return jsonify({
                    'success': False,
                    'error': '文件不存在'
                }), 404

            file_data = knowledge_file.to_dict()

            # 获取切片信息
            chunks = session.query(ContentChunk).filter_by(file_id=file_id).all()
            file_data['chunks'] = [chunk.to_dict() for chunk in chunks]

            return jsonify({
                'success': True,
                'data': file_data
            })

    except Exception as e:
        logger.error(f"获取文件详情失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取文件详情失败: {str(e)}'
        }), 500


@knowledge_bp.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id: str):
    """删除文件"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            knowledge_file = session.query(KnowledgeFile).filter_by(id=file_id).first()

            if not knowledge_file:
                return jsonify({
                    'success': False,
                    'error': '文件不存在'
                }), 404

            # 删除对象存储中的文件（如果是本地文件）
            if knowledge_file.storage_path and not knowledge_file.storage_path.startswith('http'):
                storage_manager.delete_file(knowledge_file.storage_path)

            # 删除数据库记录（级联删除切片）
            session.delete(knowledge_file)
            session.commit()

            logger.info(f"文件删除成功: {file_id}")

            return jsonify({
                'success': True,
                'message': '文件删除成功'
            })

    except Exception as e:
        logger.error(f"删除文件失败: {e}")
        return jsonify({
            'success': False,
            'error': f'删除文件失败: {str(e)}'
        }), 500


@knowledge_bp.route('/categories', methods=['GET'])
def get_categories():
    """获取分类信息"""
    try:
        categories = category_manager.get_all_categories_display()

        return jsonify({
            'success': True,
            'data': categories
        })

    except Exception as e:
        logger.error(f"获取分类信息失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取分类信息失败: {str(e)}'
        }), 500


@knowledge_bp.route('/search', methods=['GET'])
def search_knowledge():
    """搜索知识库（支持关键词搜索和语义搜索）"""
    try:
        query = request.args.get('query', '')
        category = request.args.get('category')
        file_type = request.args.get('file_type')
        semantic_search = request.args.get('semantic_search', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 10))

        if not query:
            return jsonify({
                'success': False,
                'error': '搜索关键词不能为空'
            }), 400

        results = []
        search_method = 'semantic' if semantic_search else 'keyword'

        if semantic_search:
            # 语义搜索（向量数据库）
            try:
                vector_results = get_vector_db().search_knowledge(
                    query=query,
                    category=category,
                    n_results=limit
                )

                # 转换为统一结果格式
                db = get_knowledge_db()
                with db.get_session() as session:
                    for vector_result in vector_results:
                        chunk_id = vector_result['metadata'].get('chunk_id')
                        file_id = vector_result['metadata'].get('file_id')

                        # 获取切片和文件信息
                        chunk = None
                        file = None

                        if chunk_id:
                            chunk = session.query(ContentChunk).filter_by(id=chunk_id).first()

                        if file_id and not chunk:
                            # 如果没有切片ID但有文件ID，尝试获取文件
                            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
                        elif chunk:
                            file = chunk.file

                        if chunk and file:
                            results.append({
                                'chunk_id': chunk.id,
                                'file_id': file.id,
                                'filename': file.filename,
                                'file_type': file.file_type,
                                'category': file.category,
                                'chunk_index': chunk.chunk_index,
                                'content_text': chunk.content_text[:500] + '...' if len(chunk.content_text) > 500 else chunk.content_text,
                                'content_summary': chunk.content_summary,
                                'score': vector_result['score'],
                                'vector_id': vector_result['id'],
                                'search_method': 'semantic'
                            })
                        else:
                            # 如果没有找到对应的数据库记录，使用向量结果中的信息
                            results.append({
                                'chunk_id': chunk_id,
                                'file_id': file_id,
                                'filename': vector_result['metadata'].get('filename', '未知文件'),
                                'file_type': vector_result['metadata'].get('file_type', '未知'),
                                'category': vector_result['metadata'].get('category', '未知'),
                                'chunk_index': vector_result['metadata'].get('chunk_index', 0),
                                'content_text': vector_result['text'][:500] + '...' if len(vector_result['text']) > 500 else vector_result['text'],
                                'content_summary': vector_result['metadata'].get('content_summary', ''),
                                'score': vector_result['score'],
                                'vector_id': vector_result['id'],
                                'search_method': 'semantic'
                            })

                logger.info(f"语义搜索完成: 查询 '{query[:50]}...', 结果数量: {len(results)}")

            except Exception as vector_error:
                logger.error(f"语义搜索失败，回退到关键词搜索: {vector_error}")
                search_method = 'keyword_fallback'
                semantic_search = False

        if not semantic_search:
            # 关键词搜索（数据库全文搜索）
            db = get_knowledge_db()
            with db.get_session() as session:
                chunks_query = session.query(ContentChunk).join(KnowledgeFile)

                # 过滤条件
                if category:
                    chunks_query = chunks_query.filter(KnowledgeFile.category == category)

                if file_type:
                    chunks_query = chunks_query.filter(KnowledgeFile.file_type == file_type)

                # 简单文本搜索
                chunks = chunks_query.filter(
                    ContentChunk.content_text.contains(query)
                ).limit(limit).all()

                # 转换为结果
                for chunk in chunks:
                    file = chunk.file
                    results.append({
                        'chunk_id': chunk.id,
                        'file_id': file.id,
                        'filename': file.filename,
                        'file_type': file.file_type,
                        'category': file.category,
                        'chunk_index': chunk.chunk_index,
                        'content_text': chunk.content_text[:500] + '...' if len(chunk.content_text) > 500 else chunk.content_text,
                        'content_summary': chunk.content_summary,
                        'score': 0.8,  # 简化评分
                        'search_method': 'keyword'
                    })

                logger.info(f"关键词搜索完成: 查询 '{query[:50]}...', 结果数量: {len(results)}")

        return jsonify({
            'success': True,
            'query': query,
            'search_method': search_method,
            'semantic_search': semantic_search,
            'results': results,
            'count': len(results)
        })

    except Exception as e:
        logger.error(f"搜索知识库失败: {e}")
        return jsonify({
            'success': False,
            'error': f'搜索知识库失败: {str(e)}'
        }), 500


@knowledge_bp.route('/ai-learn', methods=['POST'])
def ai_learn():
    """AI学习接口"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400

        query_text = data.get('query')
        chunk_id = data.get('chunk_id')
        agent_name = data.get('agent_name', 'default')
        feedback_score = data.get('feedback_score')

        if not query_text:
            return jsonify({
                'success': False,
                'error': '查询文本不能为空'
            }), 400

        db = get_knowledge_db()
        with db.get_session() as session:
            # 创建学习日志
            learning_log = AILearningLog(
                chunk_id=chunk_id,
                query_text=query_text,
                used_context='',  # 简化处理
                response_text='',  # 简化处理
                feedback_score=feedback_score,
                agent_name=agent_name,
                metadata={
                    'learning_time': datetime.now().isoformat(),
                    'source': 'api'
                }
            )

            session.add(learning_log)
            session.commit()

            logger.info(f"AI学习记录创建成功: {learning_log.id}")

            return jsonify({
                'success': True,
                'log_id': learning_log.id,
                'message': '学习记录保存成功'
            })

    except Exception as e:
        logger.error(f"AI学习接口失败: {e}")
        return jsonify({
            'success': False,
            'error': f'AI学习接口失败: {str(e)}'
        }), 500


@knowledge_bp.route('/storage-usage', methods=['GET'])
def get_storage_usage():
    """获取存储使用情况"""
    try:
        usage = storage_manager.get_storage_usage()

        return jsonify({
            'success': True,
            'data': usage
        })

    except Exception as e:
        logger.error(f"获取存储使用情况失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取存储使用情况失败: {str(e)}'
        }), 500


@knowledge_bp.route('/ai-answer', methods=['POST'])
def ai_answer():
    """AI问答接口（基于知识库的智能回答）"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400

        query = data.get('query', '')
        category = data.get('category')
        semantic_search = data.get('semantic_search', True)
        max_context_chunks = data.get('max_context_chunks', 5)

        if not query:
            return jsonify({
                'success': False,
                'error': '查询文本不能为空'
            }), 400

        # 从知识库检索相关上下文
        search_results = []
        if semantic_search:
            # 语义搜索
            vector_results = get_vector_db().search_knowledge(
                query=query,
                category=category,
                n_results=max_context_chunks
            )

            for result in vector_results:
                search_results.append({
                    'content': result['text'],
                    'score': result['score'],
                    'source': 'semantic_search',
                    'metadata': result['metadata']
                })
        else:
            # 关键词搜索
            db = get_knowledge_db()
            with db.get_session() as session:
                chunks_query = session.query(ContentChunk).join(KnowledgeFile)

                if category:
                    chunks_query = chunks_query.filter(KnowledgeFile.category == category)

                chunks = chunks_query.filter(
                    ContentChunk.content_text.contains(query)
                ).limit(max_context_chunks).all()

                for chunk in chunks:
                    file = chunk.file
                    search_results.append({
                        'content': chunk.content_text,
                        'score': 0.8,
                        'source': 'keyword_search',
                        'metadata': {
                            'file_id': file.id,
                            'filename': file.filename,
                            'category': file.category,
                            'chunk_id': chunk.id
                        }
                    })

        # 构建上下文
        context_parts = []
        for i, result in enumerate(search_results):
            context_parts.append(f"[上下文片段 {i+1}, 相关度: {result['score']:.2f}]\n{result['content']}")

        context = "\n\n".join(context_parts)

        if not context:
            context = "知识库中没有找到相关上下文。"

        # 使用LangChain生成答案
        try:
            # 调用现有的LangChain分析函数
            answer = analyze_with_langchain(
                user_query=query,
                jira_data=context,  # 将上下文作为jira_data传递
                ai_config={
                    'use_knowledge_base': True,
                    'context_chunks': len(search_results)
                }
            )

            # 记录学习日志
            db = get_knowledge_db()
            with db.get_session() as session:
                learning_log = AILearningLog(
                    chunk_id=None,
                    query_text=query,
                    used_context=context[:1000] if context else '',  # 截断
                    response_text=answer[:1000] if answer else '',  # 截断
                    feedback_score=None,
                    agent_name='knowledge_ai',
                    metadata={
                        'search_results_count': len(search_results),
                        'semantic_search': semantic_search,
                        'category': category,
                        'timestamp': datetime.now().isoformat()
                    }
                )

                session.add(learning_log)
                session.commit()

            logger.info(f"AI问答完成: 查询 '{query[:50]}...', 使用上下文片段: {len(search_results)}")

            return jsonify({
                'success': True,
                'answer': answer,
                'context_used': {
                    'chunk_count': len(search_results),
                    'sources': [result['source'] for result in search_results],
                    'search_method': 'semantic' if semantic_search else 'keyword'
                },
                'learning_log_id': learning_log.id
            })

        except Exception as llm_error:
            logger.error(f"LangChain调用失败: {llm_error}")

            # 如果LangChain失败，返回检索到的上下文
            return jsonify({
                'success': True,
                'answer': f"根据知识库检索到的信息，相关内容如下：\n\n{context}",
                'context_used': {
                    'chunk_count': len(search_results),
                    'sources': [result['source'] for result in search_results],
                    'search_method': 'semantic' if semantic_search else 'keyword'
                },
                'note': 'LLM生成失败，返回原始上下文',
                'learning_log_id': None
            })

    except Exception as e:
        logger.error(f"AI问答接口失败: {e}")
        return jsonify({
            'success': False,
            'error': f'AI问答接口失败: {str(e)}'
        }), 500


# 知识库管理页面专用端点
@knowledge_bp.route('/files-grouped', methods=['GET'])
def list_files_grouped():
    """获取按板块分组的文件列表"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 获取所有文件
            files = session.query(KnowledgeFile).order_by(KnowledgeFile.upload_time.desc()).all()
            files_data = [file.to_dict() for file in files]

            # 按板块（category）分组
            grouped_data = {}
            for file in files_data:
                category = file['category']
                if category not in grouped_data:
                    grouped_data[category] = []
                grouped_data[category].append(file)

            # 获取板块显示名称
            category_display_names = {
                'project_info': '项目信息',
                'project_management': '项目管理',
                'jira_spec': 'Jira规范'
            }

            # 构建响应
            response_data = []
            for category_id, files_list in grouped_data.items():
                display_name = category_display_names.get(category_id, category_id)
                response_data.append({
                    'category_id': category_id,
                    'category_name': display_name,
                    'files': files_list,
                    'file_count': len(files_list),
                    'trained_count': len([f for f in files_list if f.get('is_trained', False)])
                })

            return jsonify({
                'success': True,
                'data': response_data
            })

    except Exception as e:
        logger.error(f"获取分组文件列表失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取分组文件列表失败: {str(e)}'
        }), 500


@knowledge_bp.route('/files/<file_id>/train', methods=['POST'])
def train_file(file_id):
    """训练文件：将文件内容向量化并更新训练状态"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 获取文件
            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if not file:
                return jsonify({
                    'success': False,
                    'error': f'文件不存在: {file_id}'
                }), 404

            # 检查文件状态
            if file.status != 'processed':
                return jsonify({
                    'success': False,
                    'error': f'文件未处理完成，当前状态: {file.status}'
                }), 400

            # 更新训练状态
            file.training_status = 'training'
            file.last_training_time = datetime.utcnow()
            session.commit()

            try:
                # 获取文件的切片
                chunks = session.query(ContentChunk).filter_by(file_id=file_id).all()
                if not chunks:
                    return jsonify({
                        'success': False,
                        'error': '文件没有内容切片，无法训练'
                    }), 400

                # 将切片添加到向量数据库
                vector_ids = []
                for chunk in chunks:
                    # 构建向量文档（将ORM对象转为dict并补充category）
                    chunk_dict = chunk.to_dict()
                    chunk_dict['category'] = file.category
                    vector_doc = chunk_to_vector_document(chunk_dict)
                    # 添加到向量数据库
                    vector_id = get_vector_db().add_documents(collection_name=f"knowledge_{file.category}", documents=[vector_doc])
                    if vector_id:
                        vector_ids.append(vector_id[0])  # add_documents返回列表
                        # 更新切片的vector_id
                        chunk.vector_id = vector_id[0]

                # 更新文件训练状态
                file.is_trained = True
                file.trained_time = datetime.utcnow()
                file.trained_user = request.json.get('user', 'system') if request.is_json else 'system'
                file.training_status = 'completed'
                session.commit()

                return jsonify({
                    'success': True,
                    'message': '文件训练完成',
                    'data': {
                        'file_id': file_id,
                        'chunk_count': len(chunks),
                        'vector_count': len(vector_ids),
                        'trained_time': file.trained_time.isoformat()
                    }
                })

            except Exception as train_error:
                # 训练失败，更新状态
                file.training_status = 'failed'
                session.commit()
                logger.error(f"文件训练失败: {train_error}")
                return jsonify({
                    'success': False,
                    'error': f'文件训练失败: {str(train_error)}'
                }), 500

    except Exception as e:
        logger.error(f"训练文件失败: {e}")
        return jsonify({
            'success': False,
            'error': f'训练文件失败: {str(e)}'
        }), 500


@knowledge_bp.route('/files/<file_id>/chunks', methods=['GET'])
def get_file_chunks(file_id):
    """获取文件的切片详情"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 检查文件是否存在
            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if not file:
                return jsonify({
                    'success': False,
                    'error': f'文件不存在: {file_id}'
                }), 404

            # 获取切片
            chunks = session.query(ContentChunk).filter_by(file_id=file_id).order_by(ContentChunk.chunk_index).all()
            chunks_data = [chunk.to_dict() for chunk in chunks]

            return jsonify({
                'success': True,
                'data': {
                    'file': file.to_dict(),
                    'chunks': chunks_data,
                    'chunk_count': len(chunks_data)
                }
            })

    except Exception as e:
        logger.error(f"获取文件切片失败: {e}")
        return jsonify({
            'success': False,
            'error': f'获取文件切片失败: {str(e)}'
        }), 500


@knowledge_bp.route('/files/<file_id>/reupload', methods=['POST'])
def reupload_file(file_id):
    """重新上传文件：删除旧文件并准备新上传"""
    try:
        db = get_knowledge_db()
        with db.get_session() as session:
            # 获取文件
            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if not file:
                return jsonify({
                    'success': False,
                    'error': f'文件不存在: {file_id}'
                }), 404

            # 记录文件信息（用于返回）
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'category': file.category,
                'description': file.description,
                'tags': file.tags
            }

            # 从向量数据库中删除相关向量
            if file.is_trained:
                try:
                    # 获取该文件的所有切片
                    chunks = session.query(ContentChunk).filter_by(file_id=file_id).all()
                    for chunk in chunks:
                        if chunk.vector_id:
                            get_vector_db().delete_document(chunk.vector_id, collection_name=f"knowledge_{file.category}")
                except Exception as vector_error:
                    logger.warning(f"从向量数据库删除失败（可能未启用）: {vector_error}")

            # 从对象存储中删除文件
            try:
                if file.storage_path and os.path.exists(file.storage_path):
                    os.remove(file.storage_path)
            except Exception as storage_error:
                logger.warning(f"删除存储文件失败: {storage_error}")

            # 从数据库中删除文件（级联删除切片）
            session.delete(file)
            session.commit()

            return jsonify({
                'success': True,
                'message': '文件已删除，可以重新上传',
                'data': file_info
            })

    except Exception as e:
        logger.error(f"重新上传处理失败: {e}")
        return jsonify({
            'success': False,
            'error': f'重新上传处理失败: {str(e)}'
        }), 500


# 测试端点
@knowledge_bp.route('/test', methods=['GET'])
def test_endpoint():
    """测试端点"""
    return jsonify({
        'success': True,
        'message': '知识库蓝图测试端点正常工作'
    })

# 健康检查
@knowledge_bp.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'components': {
            'database': 'ok',
            'storage': 'ok',
            'chunk_processor': 'ok'
        }
    })
