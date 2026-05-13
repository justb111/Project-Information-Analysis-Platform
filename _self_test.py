"""
自测脚本：验证 JQL 生成 + 阻塞测试/MP Block 检测
"""
import sys, os, json, re, uuid
sys.path.insert(0, os.path.dirname(__file__))

os.environ['AI_API_KEY'] = 'sk_0f04e27baf7fd49de98314bc793b943e2514b72afaf9f67af8676a2'
os.environ['AI_MODEL'] = 'gpt-5.4'

from risk_agent import RiskAnalysisAgent, _insert_before_order_by, _get_project_jql_clause
from e import _has_blocking_label, _has_mp_block, fetch_all_issues

# ── Test 1: ORDER BY placement ──
print("="*60)
print("【Test 1】_insert_before_order_by")
base = "(project = X) AND type = Bug ORDER BY created ASC"
result = _insert_before_order_by(base, "summary ~ 'test' AND created >= startOfMonth()")
print(f"  Base:     {base}")
print(f"  Insert:   summary ~ 'test' AND created >= startOfMonth()")
print(f"  Result:   {result}")
assert "ORDER BY created ASC" in result
assert result.index("ORDER") > result.index("startOfMonth()"), "条件应该出现在 ORDER BY 之前!"
print("  ✅ ORDER BY 位置正确")

# ── Test 2: JQL template injection ──
print("\n" + "="*60)
print("【Test 2】_get_project_jql_clause")
clause = _get_project_jql_clause("X6856")
print(f"  X6856 → {clause}")
assert clause is not None
assert "project = X6856" in clause
assert "ORDER BY" in clause
print("  ✅ 模版提取成功")

# ── Test 3: Blocking label detection ──
print("\n" + "="*60)
print("【Test 3】阻塞测试标签检测（排除不阻塞）")
test_cases = [
    (["阻塞测试"], True),
    (["阻塞"], True),
    (["不阻塞"], False),
    (["非阻塞"], False),
    (["阻塞测试", "performance"], True),
    (["regression"], False),
    (None, False),
]
for labels, expected in test_cases:
    result = _has_blocking_label(labels)
    status = "✅" if result == expected else "❌"
    print(f"  {status} _has_blocking_label({labels}) = {result} (期望 {expected})")

# ── Test 4: MP Block detection ──
print("\n" + "="*60)
print("【Test 4】MP Block 检测（兼容各种 Jira 返回格式）")
mp_cases = [
    ("MP Block", True),
    ("Not MP Block", False),
    ("", False),
    (None, False),
    ({"value": "MP Block"}, True),
    ({"value": "Not MP Block"}, False),
    ([{"value": "MP Block"}], True),
    ([], False),
]
for val, expected in mp_cases:
    result = _has_mp_block(val)
    status = "✅" if result == expected else "❌"
    print(f"  {status} _has_mp_block({repr(val)[:50]}) = {result} (期望 {expected})")

# ── Test 5: 实际 Jira 数据验证 ──
print("\n" + "="*60)
print("【Test 5】实际 Jira 数据：阻塞测试 & MP Block 提取")
print("  正在拉取数据（最近30天Bug），请稍候...")

try:
    issues = fetch_all_issues(
        jql="(project = X6856-tOS16-Aee OR project = X6856-tOS16 OR project = tOS16.2) AND type = Bug AND created >= -30d",
        max_fetch=2000
    )
    print(f"  ✅ 获取到 {len(issues)} 条问题")

    blk_ids = []
    mpb_ids = []
    for issue in issues:
        fields = issue.get("fields", {})
        labels = fields.get("labels", [])
        cf = fields.get("customfield_15400", "")
        key = issue.get("key", "")

        if _has_blocking_label(labels):
            blk_ids.append((key, labels, cf))
        if _has_mp_block(cf) or any("mp block" in l.lower() for l in labels):
            mpb_ids.append((key, labels, cf))

    print(f"\n  🧱 阻塞测试标签问题数: {len(blk_ids)}")
    for key, lbs, cf in blk_ids[:5]:
        print(f"     {key}  labels={lbs}  must_resolve={cf}")

    print(f"\n  🚫 MP Block 问题数: {len(mpb_ids)}")
    for key, lbs, cf in mpb_ids[:5]:
        print(f"     {key}  labels={lbs}  must_resolve={cf}")

    # 至少各输出一个供用户验证
    if blk_ids:
        print(f"\n  🔍 验证用阻塞测试Bug ID: {blk_ids[0][0]}")
    if mpb_ids:
        print(f"  🔍 验证用MP Block Bug ID: {mpb_ids[0][0]}")

except Exception as e:
    print(f"  ❌ 错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("自测完成")
