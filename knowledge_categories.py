"""
知识库分类系统配置
定义三种知识传输类型的分类结构
"""

# 知识分类配置
KNOWLEDGE_CATEGORIES = {
    # 项目信息知识传输
    'project_info': {
        'name': '项目信息知识',
        'description': '项目相关的配置、预装信息、关键器件、计划文档等',
        'icon': '📋',
        'color': '#3498db',
        'subcategories': [
            {
                'id': 'project_config',
                'name': '项目配置',
                'description': '项目基础配置、环境配置、参数设置等',
                'file_types': ['docx', 'pdf', 'md', 'txt', 'xlsx', 'json', 'yaml', 'yml']
            },
            {
                'id': 'preinstall_info',
                'name': '预装信息',
                'description': '预装软件清单、预装配置、预装规范等',
                'file_types': ['docx', 'pdf', 'md', 'txt', 'xlsx']
            },
            {
                'id': 'key_components',
                'name': '关键器件信息',
                'description': '关键器件清单、规格说明、供应商信息、器件认证等',
                'file_types': ['docx', 'pdf', 'xlsx', 'md', 'txt']
            },
            {
                'id': 'project_plans',
                'name': '项目计划',
                'description': '项目计划、里程碑、时间表、资源计划等',
                'file_types': ['xlsx', 'docx', 'pdf', 'md']
            },
            {
                'id': 'project_docs',
                'name': '项目文档',
                'description': '需求文档、设计文档、技术方案、项目报告等综合文档',
                'file_types': ['docx', 'pdf', 'md', 'txt', 'xlsx']
            }
        ],
        'default_tags': ['项目配置', '预装信息', '关键器件', '项目计划', '项目文档']
    },

    # 项目管理知识传输
    'project_management': {
        'name': '项目管理知识',
        'description': '项目管理全流程的方法论、流程规范、最佳实践等',
        'icon': '📊',
        'color': '#2ecc71',
        'subcategories': [
            {
                'id': 'pm_framework',
                'name': '管理框架',
                'description': '敏捷开发、Scrum、Kanban、瀑布模型等管理框架',
                'file_types': ['docx', 'pdf', 'md', 'xlsx']
            },
            {
                'id': 'pm_process_mgmt',
                'name': '流程管理',
                'description': '开发流程、测试流程、发布流程、变更流程等',
                'file_types': ['docx', 'pdf', 'md', 'xlsx']
            },
            {
                'id': 'pm_quality',
                'name': '质量管理',
                'description': '质量标准、评审流程、缺陷管理、质量度量等',
                'file_types': ['docx', 'pdf', 'xlsx', 'md']
            },
            {
                'id': 'pm_risk_mgmt',
                'name': '风险管理',
                'description': '风险评估、风险应对策略、风险跟踪等',
                'file_types': ['docx', 'pdf', 'xlsx', 'md']
            },
            {
                'id': 'pm_communication',
                'name': '沟通管理',
                'description': '会议管理、报告机制、干系人管理、信息分发等',
                'file_types': ['docx', 'pdf', 'md', 'xlsx']
            },
            {
                'id': 'pm_resource',
                'name': '资源管理',
                'description': '团队管理、资源分配、技能评估、培训计划等',
                'file_types': ['docx', 'pdf', 'xlsx', 'md']
            },
            {
                'id': 'pm_templates',
                'name': '模板工具',
                'description': '项目模板、会议纪要模板、风险评估模板等',
                'file_types': ['docx', 'xlsx', 'pdf', 'md']
            },
            {
                'id': 'pm_best_practices',
                'name': '最佳实践',
                'description': '成功案例、经验总结、教训分享、行业标杆等',
                'file_types': ['docx', 'pdf', 'md', 'xlsx']
            }
        ],
        'default_tags': ['管理框架', '流程', '质量', '风险', '沟通', '资源', '模板', '最佳实践']
    },

    # Jira库规范知识传输
    'jira_spec': {
        'name': 'Jira库规范知识',
        'description': 'Jira验收规范、JQL规则、提交规范、工作流配置等',
        'icon': '🎯',
        'color': '#e74c3c',
        'subcategories': [
            {
                'id': 'jira_acceptance',
                'name': '验收规范',
                'description': '验收标准、验收流程、DoD定义、验收检查清单等',
                'file_types': ['docx', 'pdf', 'md', 'xlsx']
            },
            {
                'id': 'jira_jql_rules',
                'name': 'JQL生成规则',
                'description': 'JQL查询规则、常用查询模板、复杂查询示例等',
                'file_types': ['md', 'json', 'txt', 'docx', 'pdf', 'xlsx']
            },
            {
                'id': 'jira_submit_standard',
                'name': '提交规范',
                'description': 'Issue提交规范、标题格式、描述模板、字段填写规范等',
                'file_types': ['docx', 'pdf', 'md', 'json', 'xlsx']
            },
            {
                'id': 'jira_workflows',
                'name': '工作流规范',
                'description': 'Jira工作流定义、状态流转规则、审批流程等',
                'file_types': ['docx', 'pdf', 'md', 'json', 'xlsx']
            },
            {
                'id': 'jira_fields',
                'name': '字段规范',
                'description': '自定义字段、字段选项、验证规则、字段配置等',
                'file_types': ['docx', 'pdf', 'md', 'json', 'xlsx']
            },
            {
                'id': 'jira_permissions',
                'name': '权限配置',
                'description': '权限方案、角色定义、访问控制、安全策略等',
                'file_types': ['docx', 'pdf', 'md', 'json', 'xlsx']
            }
        ],
        'default_tags': ['验收规范', 'JQL规则', '提交规范', '工作流', '字段', '权限']
    }
}

# 文件类型配置
FILE_TYPE_CONFIG = {
    'pdf': {
        'name': 'PDF文档',
        'icon': '📄',
        'parser': 'pdf_parser',
        'supported_features': ['text_extraction', 'page_segmentation', 'table_detection']
    },
    'docx': {
        'name': 'Word文档',
        'icon': '📝',
        'parser': 'docx_parser',
        'supported_features': ['text_extraction', 'heading_detection', 'table_extraction']
    },
    'xlsx': {
        'name': 'Excel表格',
        'icon': '📊',
        'parser': 'excel_parser',
        'supported_features': ['table_extraction', 'sheet_parsing', 'formula_handling']
    },
    'csv': {
        'name': 'CSV文件',
        'icon': '📋',
        'parser': 'csv_parser',
        'supported_features': ['table_extraction', 'delimiter_detection']
    },
    'txt': {
        'name': '文本文件',
        'icon': '📃',
        'parser': 'text_parser',
        'supported_features': ['text_extraction', 'encoding_detection']
    },
    'md': {
        'name': 'Markdown文件',
        'icon': '📑',
        'parser': 'markdown_parser',
        'supported_features': ['text_extraction', 'heading_detection', 'code_block_extraction']
    },
    'jpg': {
        'name': 'JPEG图片',
        'icon': '🖼️',
        'parser': 'image_parser',
        'supported_features': ['ocr_extraction', 'image_analysis']
    },
    'png': {
        'name': 'PNG图片',
        'icon': '🖼️',
        'parser': 'image_parser',
        'supported_features': ['ocr_extraction', 'image_analysis']
    },
    'feishu': {
        'name': '飞书云文档',
        'icon': '📱',
        'parser': 'feishu_parser',
        'supported_features': ['api_integration', 'permission_check', 'real_time_sync'],
        'requires_auth': True
    }
}

# 分类管理器
class CategoryManager:
    """分类管理器"""
    
    def __init__(self):
        self.categories = KNOWLEDGE_CATEGORIES
    
    def get_category_info(self, category_id: str) -> dict:
        """获取分类信息"""
        category = self.categories.get(category_id)
        if not category:
            raise ValueError(f"分类不存在: {category_id}")
        return category
    
    def get_subcategories(self, category_id: str) -> list:
        """获取子分类列表"""
        category = self.get_category_info(category_id)
        return category.get('subcategories', [])
    
    def get_allowed_file_types(self, category_id: str, subcategory_id: str = None) -> list:
        """获取允许的文件类型"""
        if subcategory_id:
            # 获取特定子分类允许的文件类型
            subcategories = self.get_subcategories(category_id)
            for subcat in subcategories:
                if subcat['id'] == subcategory_id:
                    return subcat.get('file_types', [])
            return []
        else:
            # 获取主分类下所有允许的文件类型
            all_file_types = set()
            subcategories = self.get_subcategories(category_id)
            for subcat in subcategories:
                all_file_types.update(subcat.get('file_types', []))
            return list(all_file_types)
    
    def get_default_tags(self, category_id: str) -> list:
        """获取默认标签"""
        category = self.get_category_info(category_id)
        return category.get('default_tags', [])
    
    def get_subcategory_name(self, category_id: str, subcategory_id: str) -> str:
        """获取子分类名称"""
        subcategories = self.get_subcategories(category_id)
        for subcat in subcategories:
            if subcat['id'] == subcategory_id:
                return subcat.get('name', subcategory_id)
        return subcategory_id
    
    def validate_file_type(self, category_id: str, file_type: str, subcategory_id: str = None) -> bool:
        """验证文件类型是否允许"""
        allowed_types = self.get_allowed_file_types(category_id, subcategory_id)
        if not allowed_types:  # 如果没有限制，则允许所有类型
            return True
        return file_type in allowed_types
    
    def get_category_display_info(self, category_id: str) -> dict:
        """获取分类显示信息"""
        category = self.get_category_info(category_id)
        return {
            'id': category_id,
            'name': category['name'],
            'description': category['description'],
            'icon': category.get('icon', '📁'),
            'color': category.get('color', '#95a5a6'),
            'subcategory_count': len(category.get('subcategories', []))
        }
    
    def get_all_categories_display(self) -> list:
        """获取所有分类的显示信息"""
        return [
            self.get_category_display_info(category_id)
            for category_id in self.categories.keys()
        ]


# 文件类型管理器
class FileTypeManager:
    """文件类型管理器"""
    
    def __init__(self):
        self.file_types = FILE_TYPE_CONFIG
    
    def get_file_type_info(self, file_type: str) -> dict:
        """获取文件类型信息"""
        info = self.file_types.get(file_type)
        if not info:
            # 默认配置
            info = {
                'name': f'{file_type.upper()}文件',
                'icon': '📎',
                'parser': 'default_parser',
                'supported_features': ['text_extraction']
            }
        return info
    
    def is_feature_supported(self, file_type: str, feature: str) -> bool:
        """检查是否支持特定功能"""
        info = self.get_file_type_info(file_type)
        return feature in info.get('supported_features', [])
    
    def get_parser_name(self, file_type: str) -> str:
        """获取解析器名称"""
        info = self.get_file_type_info(file_type)
        return info.get('parser', 'default_parser')
    
    def requires_auth(self, file_type: str) -> bool:
        """检查是否需要认证"""
        info = self.get_file_type_info(file_type)
        return info.get('requires_auth', False)


# 全局实例
category_manager = CategoryManager()
file_type_manager = FileTypeManager()


# 工具函数
def get_category_options() -> list:
    """获取分类选项（用于下拉选择）"""
    options = []
    for category_id, category in KNOWLEDGE_CATEGORIES.items():
        options.append({
            'value': category_id,
            'label': f"{category.get('icon', '📁')} {category['name']}",
            'description': category['description']
        })
    return options


def get_subcategory_options(category_id: str) -> list:
    """获取子分类选项"""
    manager = CategoryManager()
    subcategories = manager.get_subcategories(category_id)
    
    options = [{
        'value': '',
        'label': '不指定子分类',
        'description': '使用主分类'
    }]
    
    for subcat in subcategories:
        options.append({
            'value': subcat['id'],
            'label': subcat['name'],
            'description': subcat['description']
        })
    
    return options


def get_file_type_options() -> list:
    """获取文件类型选项"""
    options = []
    for file_type, config in FILE_TYPE_CONFIG.items():
        options.append({
            'value': file_type,
            'label': f"{config.get('icon', '📎')} {config['name']}",
            'description': f"支持: {', '.join(config.get('supported_features', []))}"
        })
    return options


# 示例使用
if __name__ == "__main__":
    print("知识分类系统测试")
    print("=" * 60)
    
    # 测试分类管理器
    print("1. 分类管理器测试:")
    for category_id in ['project_info', 'project_management', 'jira_spec']:
        info = category_manager.get_category_display_info(category_id)
        print(f"   {info['icon']} {info['name']}: {info['description']}")
        print(f"     子分类数量: {info['subcategory_count']}")
    
    # 测试文件类型验证
    print("\n2. 文件类型验证测试:")
    test_cases = [
        ('project_info', 'docx', 'project_info_requirements'),
        ('project_management', 'xlsx', 'pm_templates'),
        ('jira_spec', 'json', 'jira_workflows'),
        ('project_info', 'jpg', 'project_info_design')
    ]
    
    for category_id, file_type, subcategory_id in test_cases:
        is_valid = category_manager.validate_file_type(category_id, file_type, subcategory_id)
        print(f"   {category_id}/{subcategory_id} 接受 {file_type}: {'✅' if is_valid else '❌'}")
    
    # 测试文件类型管理器
    print("\n3. 文件类型管理器测试:")
    for file_type in ['pdf', 'docx', 'xlsx', 'feishu']:
        info = file_type_manager.get_file_type_info(file_type)
        print(f"   {info.get('icon', '📎')} {file_type}: {info['name']}")
        print(f"     解析器: {info.get('parser', 'unknown')}")
        print(f"     需要认证: {'是' if file_type_manager.requires_auth(file_type) else '否'}")
    
    print("\n✅ 分类系统测试完成")