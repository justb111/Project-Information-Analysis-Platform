"""
对象存储管理器
处理文件在本地文件系统中的存储和管理
"""

import os
import shutil
import uuid
import re
from typing import Dict, Any, Optional, Tuple, BinaryIO, List
from datetime import datetime, timezone
import mimetypes
import logging
from pathlib import Path

# 设置日志
logger = logging.getLogger(__name__)


class StorageError(Exception):
    """存储错误"""
    pass


class ObjectStorageManager:
    """对象存储管理器"""
    
    def __init__(self, base_path: str = None):
        """
        初始化对象存储管理器

        Args:
            base_path: 基础存储路径（默认为项目目录下的 uploads）
        """
        if base_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            base_path = os.path.join(base_dir, 'uploads')
        self.base_path = Path(base_path).absolute()
        
        # 创建目录结构
        self._init_storage_structure()
        
        logger.info(f"对象存储管理器初始化完成，基础路径: {self.base_path}")
    
    def _init_storage_structure(self):
        """初始化存储目录结构"""
        directories = [
            self.base_path / "raw" / "project_info",
            self.base_path / "raw" / "project_management",
            self.base_path / "raw" / "jira_spec",
            self.base_path / "processed",
            self.base_path / "thumbnails",
            self.base_path / "temp"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"创建目录: {directory}")
    
    def generate_file_path(self, filename: str, category: str, subcategory: str = None) -> Tuple[Path, str]:
        """
        生成文件存储路径
        
        Args:
            filename: 原始文件名
            category: 分类（project_info/project_management/jira_spec）
            subcategory: 子分类（可选）
            
        Returns:
            tuple: (完整路径, 存储路径字符串)
        """
        # 生成唯一文件名
        file_ext = Path(filename).suffix.lower()
        unique_id = str(uuid.uuid4())
        new_filename = f"{unique_id}{file_ext}"
        
        # 确定存储目录
        if subcategory:
            # 使用子分类作为子目录
            storage_dir = self.base_path / "raw" / category / subcategory
        else:
            storage_dir = self.base_path / "raw" / category
        
        # 确保目录存在
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 完整路径
        full_path = storage_dir / new_filename
        
        # 存储路径（相对路径，用于数据库记录）
        if subcategory:
            storage_path = f"raw/{category}/{subcategory}/{new_filename}"
        else:
            storage_path = f"raw/{category}/{new_filename}"
        
        return full_path, storage_path
    
    def save_file(self, file_stream: BinaryIO, filename: str, category: str, 
                  subcategory: str = None, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        保存文件到对象存储
        
        Args:
            file_stream: 文件流（BytesIO或类似对象）
            filename: 原始文件名
            category: 分类
            subcategory: 子分类
            metadata: 文件元数据
            
        Returns:
            存储信息字典
        """
        try:
            # 生成存储路径
            full_path, storage_path = self.generate_file_path(filename, category, subcategory)
            
            # 保存文件
            with open(full_path, 'wb') as f:
                # 将文件流指针重置到开始（如果支持）
                if hasattr(file_stream, 'seek'):
                    file_stream.seek(0)
                shutil.copyfileobj(file_stream, f)
            
            # 获取文件信息
            file_size = full_path.stat().st_size
            file_hash = self._calculate_file_hash(full_path)
            
            # 准备返回信息
            storage_info = {
                'storage_path': storage_path,
                'absolute_path': str(full_path),
                'filename': filename,
                'saved_filename': full_path.name,
                'file_size': file_size,
                'file_hash': file_hash,
                'category': category,
                'subcategory': subcategory,
                'mime_type': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                'saved_time': datetime.now(timezone.utc).isoformat(),
                'metadata': metadata or {}
            }
            
            logger.info(f"文件保存成功: {filename} -> {storage_path}, 大小: {file_size} 字节")
            return storage_info
            
        except Exception as e:
            logger.error(f"文件保存失败: {filename}, 错误: {e}")
            raise StorageError(f"文件保存失败: {str(e)}")
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件哈希（简化版本）"""
        import hashlib
        
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.warning(f"计算文件哈希失败: {e}")
            return ""
    
    def get_file(self, storage_path: str) -> Optional[Path]:
        """
        根据存储路径获取文件
        
        Args:
            storage_path: 存储路径（如 raw/project_info/filename.pdf）
            
        Returns:
            文件路径对象，如果不存在则返回None
        """
        full_path = self.base_path / storage_path
        
        if full_path.exists() and full_path.is_file():
            return full_path
        else:
            logger.warning(f"文件不存在: {storage_path}")
            return None
    
    def delete_file(self, storage_path: str) -> bool:
        """
        删除文件
        
        Args:
            storage_path: 存储路径
            
        Returns:
            是否删除成功
        """
        full_path = self.base_path / storage_path
        
        try:
            if full_path.exists() and full_path.is_file():
                full_path.unlink()
                logger.info(f"文件删除成功: {storage_path}")
                return True
            else:
                logger.warning(f"文件不存在，无法删除: {storage_path}")
                return False
        except Exception as e:
            logger.error(f"文件删除失败: {storage_path}, 错误: {e}")
            return False
    
    def file_exists(self, storage_path: str) -> bool:
        """检查文件是否存在"""
        full_path = self.base_path / storage_path
        return full_path.exists() and full_path.is_file()
    
    def get_file_info(self, storage_path: str) -> Optional[Dict[str, Any]]:
        """获取文件信息"""
        full_path = self.base_path / storage_path
        
        if not full_path.exists() or not full_path.is_file():
            return None
        
        try:
            stat_info = full_path.stat()
            
            return {
                'storage_path': storage_path,
                'absolute_path': str(full_path),
                'filename': full_path.name,
                'file_size': stat_info.st_size,
                'created_time': datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc).isoformat(),
                'modified_time': datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc).isoformat(),
                'is_file': True,
                'extension': full_path.suffix.lower()
            }
        except Exception as e:
            logger.error(f"获取文件信息失败: {storage_path}, 错误: {e}")
            return None
    
    def list_files(self, category: str = None, subcategory: str = None) -> List[Dict[str, Any]]:
        """列出文件"""
        if category and subcategory:
            base_dir = self.base_path / "raw" / category / subcategory
        elif category:
            base_dir = self.base_path / "raw" / category
        else:
            base_dir = self.base_path / "raw"
        
        if not base_dir.exists():
            return []
        
        files = []
        for file_path in base_dir.iterdir():
            if file_path.is_file():
                stat_info = file_path.stat()
                
                # 提取分类信息
                relative_path = file_path.relative_to(self.base_path)
                parts = relative_path.parts
                
                file_category = parts[1] if len(parts) > 1 else None
                file_subcategory = parts[2] if len(parts) > 2 else None
                
                files.append({
                    'filename': file_path.name,
                    'storage_path': str(relative_path),
                    'file_size': stat_info.st_size,
                    'modified_time': datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc).isoformat(),
                    'category': file_category,
                    'subcategory': file_subcategory
                })
        
        return files
    
    def cleanup_temp_files(self, max_age_hours: int = 24):
        """清理临时文件"""
        temp_dir = self.base_path / "temp"
        
        if not temp_dir.exists():
            return
        
        cutoff_time = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        
        for file_path in temp_dir.iterdir():
            if file_path.is_file():
                file_age = file_path.stat().st_mtime
                if file_age < cutoff_time:
                    try:
                        file_path.unlink()
                        logger.info(f"清理临时文件: {file_path.name}")
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {file_path.name}, 错误: {e}")
    
    def get_storage_usage(self) -> Dict[str, Any]:
        """获取存储使用情况"""
        total_size = 0
        file_count = 0
        category_stats = {}
        
        # 统计原始文件
        raw_dir = self.base_path / "raw"
        if raw_dir.exists():
            for category_dir in raw_dir.iterdir():
                if category_dir.is_dir():
                    category_size = 0
                    category_files = 0
                    
                    for file_path in category_dir.rglob('*'):
                        if file_path.is_file():
                            category_size += file_path.stat().st_size
                            category_files += 1
                    
                    total_size += category_size
                    file_count += category_files
                    
                    category_stats[category_dir.name] = {
                        'size': category_size,
                        'file_count': category_files,
                        'size_human': self._human_readable_size(category_size)
                    }
        
        return {
            'total_size': total_size,
            'total_size_human': self._human_readable_size(total_size),
            'file_count': file_count,
            'category_stats': category_stats,
            'base_path': str(self.base_path)
        }
    
    def _human_readable_size(self, size_bytes: int) -> str:
        """将字节数转换为人类可读的格式"""
        if size_bytes == 0:
            return "0B"
        
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        unit_index = 0
        
        while size_bytes >= 1024 and unit_index < len(units) - 1:
            size_bytes /= 1024
            unit_index += 1
        
        return f"{size_bytes:.2f}{units[unit_index]}"


# 全局实例
_storage_manager = None

def get_storage_manager() -> ObjectStorageManager:
    """获取存储管理器全局实例"""
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = ObjectStorageManager()
    return _storage_manager


# 工具函数
def validate_file_extension(filename: str, allowed_extensions: set = None) -> Tuple[bool, str]:
    """验证文件扩展名"""
    if not allowed_extensions:
        return True, "无扩展名限制"
    
    file_ext = Path(filename).suffix.lower()
    if not file_ext:
        return False, "文件无扩展名"
    
    if file_ext not in allowed_extensions:
        return False, f"不支持的文件类型: {file_ext}，支持的类型: {', '.join(allowed_extensions)}"
    
    return True, "文件类型验证通过"


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除不安全字符"""
    # 保留扩展名
    file_ext = Path(filename).suffix
    base_name = Path(filename).stem
    
    # 移除不安全字符
    safe_name = re.sub(r'[^\w\s\-\.]', '_', base_name)
    
    # 限制长度
    if len(safe_name) > 100:
        safe_name = safe_name[:100]
    
    return safe_name + file_ext


def get_file_category_from_path(storage_path: str) -> Tuple[Optional[str], Optional[str]]:
    """从存储路径提取分类信息"""
    parts = storage_path.split('/')
    
    if len(parts) >= 3 and parts[0] == 'raw':
        category = parts[1]
        subcategory = parts[2] if len(parts) > 3 else None
        return category, subcategory
    
    return None, None


if __name__ == "__main__":
    print("对象存储管理器测试")
    print("=" * 60)
    
    # 创建存储管理器
    manager = ObjectStorageManager("test_uploads")
    
    # 测试存储使用情况
    usage = manager.get_storage_usage()
    print(f"存储使用情况:")
    print(f"  基础路径: {usage['base_path']}")
    print(f"  总大小: {usage['total_size_human']}")
    print(f"  文件数量: {usage['file_count']}")
    
    # 测试文件路径生成
    full_path, storage_path = manager.generate_file_path("测试文档.pdf", "project_info", "requirements")
    print(f"\n文件路径生成:")
    print(f"  完整路径: {full_path}")
    print(f"  存储路径: {storage_path}")
    
    # 清理测试目录
    import shutil
    if Path("test_uploads").exists():
        shutil.rmtree("test_uploads")
    
    print("\n✅ 对象存储管理器测试完成")