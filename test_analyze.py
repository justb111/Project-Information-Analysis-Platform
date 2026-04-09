import sys
import os

# 添加当前目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from e import analyze_project

# 测试分析函数
print("开始测试分析函数...")
try:
    result = analyze_project("X6840", "分析风险")
    print("分析函数调用成功！")
    print(f"分析结果长度: {len(result.get('analysis', ''))}")
    print(f"分析结果: {result.get('analysis', '')}")
except Exception as e:
    print(f"分析函数调用失败: {e}")
    import traceback
    print(f"详细错误: {traceback.format_exc()}")