"""人力洞察看板 - 后端API"""
import json
import os
import uuid
import re
import threading
import queue
from datetime import datetime

import pandas as pd
from flask import Blueprint, request, jsonify, Response

from utils import call_ai_api, generate_sse_message

workforce_bp = Blueprint('workforce', __name__, url_prefix='/api/workforce')

TEMP_DIR = os.path.join(os.path.dirname(__file__), 'temp_workforce')
os.makedirs(TEMP_DIR, exist_ok=True)

workforce_cache = {}
workforce_cache_lock = threading.Lock()

COLUMN_KEYWORDS_A = {
    'name': ['姓名', '名字', '人员', 'owner', 'name', 'user', '负责人', '成员'],
    'total': ['总任务', 'total', '任务数', 'case_num', 'task', '用例数', '计划'],
    'done': ['完成', 'done', '已执行', 'finished', '已关闭', '已解决'],
    'team': ['部门', '组', 'team', 'dept', 'group', '小组', '团队', '科室']
}

COLUMN_KEYWORDS_B = {
    'team': ['业务组', '组', '团队', 'team', 'group', '小组', '部门', '科室'],
    'estimated': ['预估', '计划', 'estimate', 'plan', '预算', '预计'],
    'actual': ['实际', 'actual', '真实'],
    'efficiency': ['提效', 'efficiency', '提升', '效率']
}

COLUMN_KEYWORDS_HR = {
    'dept': ['部门', 'dept', 'department', '一级部门', '事业部'],
    'group': ['小组', '组', 'group', '业务组', '团队', '二级部门'],
    'name': ['姓名', '名字', '人员', 'name', '员工', '负责人', '成员'],
    'utilization': ['使用率', '利用率', 'utilization', '负载率', '负荷', '占用率'],
    'tasks': ['任务数', '任务数量', 'tasks', 'case_num', '项目数', 'task_count', '工作项'],
    'project': ['项目', 'project', '主要项目', '项目名称', '关联项目'],
    'leave': ['请假', 'leave', '休假', '请假情况', '请假天数', '缺勤'],
    'task_type': ['任务类型', 'task_type', '工作类型', '类型', '任务分类', '工作类别'],
    'task_name': ['任务名称', 'task_name', '任务名', '工作项', '任务描述', '任务标题'],
    'standard_load': ['标准负载', '标准值', 'standard', '标准负荷', '额定负载', '基准值']
}


def _safe_float(val):
    try:
        v = float(val)
        return v if not pd.isna(v) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    try:
        v = int(float(val))
        return v if not pd.isna(v) else 0
    except (ValueError, TypeError):
        return 0


def _safe_str(val):
    if pd.isna(val):
        return ''
    return str(val).strip()


def _find_column(df, *keywords):
    """智能列名匹配：在DataFrame列名中搜索包含任一关键词的列"""
    for col in df.columns:
        col_lower = str(col).lower().strip().replace(' ', '').replace('_', '').replace('-', '')
        for kw in keywords:
            kw_clean = kw.lower().strip().replace(' ', '').replace('_', '').replace('-', '')
            if kw_clean in col_lower:
                return col
    return None


def _detect_file_type(df):
    """自动检测文件类型：personal_task / team_cost_reduction / hr_dashboard"""
    col_str = ' '.join([str(c).lower() for c in df.columns])

    type_a_score = 0
    for key, keywords in COLUMN_KEYWORDS_A.items():
        for kw in keywords:
            if kw.lower() in col_str:
                type_a_score += 1
                break

    type_b_score = 0
    for key, keywords in COLUMN_KEYWORDS_B.items():
        for kw in keywords:
            if kw.lower() in col_str:
                type_b_score += 1
                break

    hr_score = 0
    for key, keywords in COLUMN_KEYWORDS_HR.items():
        for kw in keywords:
            if kw.lower() in col_str:
                hr_score += 1
                break

    if hr_score >= 4 and hr_score > type_a_score and hr_score > type_b_score:
        return 'hr_dashboard'
    return 'personal_task' if type_a_score >= type_b_score else 'team_cost_reduction'


def _parse_personal_task(df):
    """解析Type A - 个人任务明细数据"""
    name_col = _find_column(df, *COLUMN_KEYWORDS_A['name']) or df.columns[0]
    total_col = _find_column(df, *COLUMN_KEYWORDS_A['total'])
    done_col = _find_column(df, *COLUMN_KEYWORDS_A['done'])
    team_col = _find_column(df, *COLUMN_KEYWORDS_A['team'])

    rows = []
    for _, row in df.iterrows():
        name = _safe_str(row.get(name_col, ''))
        if not name:
            continue
        total = _safe_float(row.get(total_col, 0)) if total_col else 0
        done = _safe_float(row.get(done_col, 0)) if done_col else 0
        team = _safe_str(row.get(team_col, '')) if team_col else ''

        if total == 0:
            completion_rate = 0
        else:
            completion_rate = round(done / total * 100, 1)

        if completion_rate < 70:
            status = '不饱和'
        elif completion_rate <= 90:
            status = '正常'
        else:
            status = '饱和'

        rows.append({
            'name': name,
            'total': int(total) if total == int(total) else total,
            'done': int(done) if done == int(done) else done,
            'completion_rate': completion_rate,
            'status': status,
            'team': team
        })

    rows.sort(key=lambda x: x['completion_rate'])

    unsaturated = [r for r in rows if r['status'] == '不饱和']
    normal = [r for r in rows if r['status'] == '正常']
    overloaded = [r for r in rows if r['status'] == '饱和']

    team_groups = {}
    for r in rows:
        t = r['team'] or '未分组'
        if t not in team_groups:
            team_groups[t] = []
        team_groups[t].append(r)

    team_avg = {}
    for t, members in team_groups.items():
        rates = [m['completion_rate'] for m in members]
        team_avg[t] = round(sum(rates) / len(rates), 1) if rates else 0

    resource_gaps = {t: avg for t, avg in team_avg.items() if avg > 90}

    return {
        'type': 'personal_task',
        'column_mapping': {
            'name': str(name_col),
            'total': str(total_col) if total_col else None,
            'done': str(done_col) if done_col else None,
            'team': str(team_col) if team_col else None
        },
        'data': rows,
        'summary': {
            'total_persons': len(rows),
            'avg_completion_rate': round(sum(r['completion_rate'] for r in rows) / len(rows), 1) if rows else 0,
            'unsaturated_count': len(unsaturated),
            'normal_count': len(normal),
            'overloaded_count': len(overloaded),
            'unsaturated': [{'name': r['name'], 'rate': r['completion_rate'], 'team': r['team']} for r in unsaturated],
            'overloaded': [{'name': r['name'], 'rate': r['completion_rate'], 'team': r['team']} for r in overloaded],
            'team_avg': team_avg,
            'resource_gaps': resource_gaps
        }
    }


def _parse_team_cost_reduction(df):
    """解析Type B - 团队会签降本数据"""
    team_col = _find_column(df, *COLUMN_KEYWORDS_B['team']) or df.columns[0]
    estimated_col = _find_column(df, *COLUMN_KEYWORDS_B['estimated'])
    actual_col = _find_column(df, *COLUMN_KEYWORDS_B['actual'])
    efficiency_col = _find_column(df, *COLUMN_KEYWORDS_B['efficiency'])

    rows = []
    for _, row in df.iterrows():
        team = _safe_str(row.get(team_col, ''))
        if not team:
            continue
        estimated = _safe_float(row.get(estimated_col, 0)) if estimated_col else 0
        actual = _safe_float(row.get(actual_col, 0)) if actual_col else 0
        efficiency = _safe_float(row.get(efficiency_col, 0)) if efficiency_col else 0

        if estimated == 0:
            savings_rate = 0
        else:
            savings_rate = round((estimated - actual) / estimated * 100, 1)

        rows.append({
            'team': team,
            'estimated': estimated,
            'actual': actual,
            'savings_rate': savings_rate,
            'efficiency_rate': efficiency
        })

    rows.sort(key=lambda x: x['efficiency_rate'], reverse=True)

    best_team = rows[0] if rows else None
    worst_team = rows[-1] if rows else None

    return {
        'type': 'team_cost_reduction',
        'column_mapping': {
            'team': str(team_col),
            'estimated': str(estimated_col) if estimated_col else None,
            'actual': str(actual_col) if actual_col else None,
            'efficiency': str(efficiency_col) if efficiency_col else None
        },
        'data': rows,
        'summary': {
            'total_teams': len(rows),
            'avg_efficiency': round(sum(r['efficiency_rate'] for r in rows) / len(rows), 1) if rows else 0,
            'avg_savings_rate': round(sum(r['savings_rate'] for r in rows) / len(rows), 1) if rows else 0,
            'best_team': {'name': best_team['team'], 'efficiency': best_team['efficiency_rate']} if best_team else None,
            'worst_team': {'name': worst_team['team'], 'efficiency': worst_team['efficiency_rate']} if worst_team else None
        }
    }


def _parse_file(filepath, filename):
    """解析上传的文件，支持多Sheet"""
    ext = os.path.splitext(filename)[1].lower()
    result = {}

    if ext == '.csv':
        df = pd.read_csv(filepath, encoding='utf-8')
        if df.shape[1] <= 1:
            df = pd.read_csv(filepath, encoding='gbk')
        df = df.dropna(how='all').reset_index(drop=True)
        file_type = _detect_file_type(df)
        if file_type == 'hr_dashboard':
            parsed = _parse_hr_dashboard(df)
        elif file_type == 'personal_task':
            parsed = _parse_personal_task(df)
        else:
            parsed = _parse_team_cost_reduction(df)
        parsed['sheet_name'] = 'Sheet1'
        result['Sheet1'] = parsed
    else:
        xl = pd.ExcelFile(filepath)
        for sheet in xl.sheet_names:
            df = pd.read_excel(xl, sheet_name=sheet)
            df = df.dropna(how='all').reset_index(drop=True)
            if df.empty:
                continue
            file_type = _detect_file_type(df)
            if file_type == 'hr_dashboard':
                parsed = _parse_hr_dashboard(df)
            elif file_type == 'personal_task':
                parsed = _parse_personal_task(df)
            else:
                parsed = _parse_team_cost_reduction(df)
            parsed['sheet_name'] = sheet
            result[sheet] = parsed

    return result


def _get_load_status(utilization):
    if utilization <= 90:
        return '空闲'
    elif utilization <= 110:
        return '正常'
    elif utilization <= 120:
        return '低负载'
    else:
        return '高负载'


def _compute_available_manpower(total_persons, avg_utilization):
    return max(0, round(total_persons * (1 - avg_utilization / 100)))


def _parse_hr_dashboard(df):
    """解析 HR Dashboard 格式：部门/小组/人员/使用率/任务数/项目/请假/任务类型/标准负载"""
    dept_col = _find_column(df, *COLUMN_KEYWORDS_HR['dept']) or df.columns[0]
    group_col = _find_column(df, *COLUMN_KEYWORDS_HR['group'])
    name_col = _find_column(df, *COLUMN_KEYWORDS_HR['name']) or df.columns[1]
    utilization_col = _find_column(df, *COLUMN_KEYWORDS_HR['utilization'])
    tasks_col = _find_column(df, *COLUMN_KEYWORDS_HR['tasks'])
    project_col = _find_column(df, *COLUMN_KEYWORDS_HR['project'])
    leave_col = _find_column(df, *COLUMN_KEYWORDS_HR['leave'])
    task_type_col = _find_column(df, *COLUMN_KEYWORDS_HR['task_type'])
    task_name_col = _find_column(df, *COLUMN_KEYWORDS_HR['task_name'])
    standard_load_col = _find_column(df, *COLUMN_KEYWORDS_HR['standard_load'])

    persons = []
    for _, row in df.iterrows():
        name = _safe_str(row.get(name_col, ''))
        if not name:
            continue
        dept = _safe_str(row.get(dept_col, '')) or '未分配部门'
        group = _safe_str(row.get(group_col, '')) if group_col else ''
        utilization = _safe_float(row.get(utilization_col, 0)) if utilization_col else 0
        # 阈值10：0.646→64.6%, 2.4→240%；而53.6/64.6已是百分比则不动
        if utilization <= 10:
            utilization = round(utilization * 100, 1)
        tasks = _safe_int(row.get(tasks_col, 0)) if tasks_col else 0
        project = _safe_str(row.get(project_col, '')) if project_col else ''

        leave = _safe_str(row.get(leave_col, '')) if leave_col else ''
        task_type = _safe_str(row.get(task_type_col, '')) if task_type_col else ''
        task_name = _safe_str(row.get(task_name_col, '')) if task_name_col else ''
        standard_load = _safe_float(row.get(standard_load_col, 0)) if standard_load_col else 0

        # 相对标准值的负荷状态
        if standard_load > 0:
            if utilization > standard_load:
                load_status = '超标'
            elif utilization < standard_load * 0.8:
                load_status = '未达标'
            else:
                load_status = '正常'
        else:
            load_status = _get_load_status(utilization)

        persons.append({
            'name': name,
            'dept': dept,
            'group': group or dept,
            'utilization': round(utilization, 1),
            'tasks': tasks,
            'project': project,
            'status': _get_load_status(utilization),
            'available': _compute_available_manpower(1, utilization),
            'leave': leave,
            'task_type': task_type,
            'task_name': task_name,
            'standard_load': standard_load,
            'load_status': load_status
        })

    # 构建层级：dept → group → persons
    depts_order = []
    depts_map = {}
    for p in persons:
        d = p['dept']
        if d not in depts_map:
            depts_map[d] = {}
            depts_order.append(d)
        g = p['group']
        if g not in depts_map[d]:
            depts_map[d][g] = []
        depts_map[d][g].append(p)

    # 构建 departments 输出
    departments = []
    for dept in depts_order:
        groups = depts_map[dept]
        group_list = []
        for gname, members in groups.items():
            avg_util = round(sum(m['utilization'] for m in members) / len(members), 1) if members else 0
            projects = list(dict.fromkeys([m['project'] for m in members if m['project']]))
            leave_count = sum(1 for m in members if m['leave'])
            group_list.append({
                'name': gname,
                'total_persons': len(members),
                'avg_utilization': avg_util,
                'available_manpower': _compute_available_manpower(len(members), avg_util),
                'status': _get_load_status(avg_util),
                'total_tasks': sum(m['tasks'] for m in members),
                'projects': projects,
                'leave_count': leave_count,
                'members': members
            })

        group_list.sort(key=lambda g: g['avg_utilization'], reverse=True)
        dept_avg_util = round(sum(g['avg_utilization'] for g in group_list) / len(group_list), 1) if group_list else 0
        dept_total_persons = sum(g['total_persons'] for g in group_list)
        dept_leave_count = sum(g['leave_count'] for g in group_list)

        departments.append({
            'name': dept,
            'total_persons': dept_total_persons,
            'avg_utilization': dept_avg_util,
            'available_manpower': _compute_available_manpower(dept_total_persons, dept_avg_util),
            'status': _get_load_status(dept_avg_util),
            'leave_count': dept_leave_count,
            'groups': group_list
        })

    # 所有人员（全量）
    all_persons = sorted(persons, key=lambda p: p['utilization'], reverse=True)

    # 人力资源分布表数据
    hr_distribution = []
    for p in persons:
        hr_distribution.append({
            'group': p['group'],
            'name': p['name'],
            'task_type': p['task_type'] or '-',
            'tasks': p['tasks'],
            'load_status': p['load_status'],
            'task_name': p['task_name'] or '-',
            'utilization': p['utilization'],
            'status': p['status']
        })

    # 计算全局指标
    global_avg_util = round(sum(p['utilization'] for p in persons) / len(persons), 1) if persons else 0
    global_available = _compute_available_manpower(len(persons), global_avg_util)
    normal_count = sum(1 for p in persons if p['status'] == '正常')
    idle_count = sum(1 for p in persons if p['status'] == '空闲')
    low_load_count = sum(1 for p in persons if p['status'] == '低负载')
    high_load_count = sum(1 for p in persons if p['status'] == '高负载')
    overload_count = sum(1 for p in persons if p['load_status'] == '超标')
    below_count = sum(1 for p in persons if p['load_status'] == '未达标')

    return {
        'type': 'hr_dashboard',
        'departments': departments,
        'all_persons': all_persons,
        'hr_distribution': hr_distribution,
        'dept_names': depts_order,
        'summary': {
            'total_persons': len(persons),
            'avg_utilization': global_avg_util,
            'available_manpower': global_available,
            'status': _get_load_status(global_avg_util),
            'idle_count': idle_count,
            'normal_count': normal_count,
            'low_load_count': low_load_count,
            'high_load_count': high_load_count,
            'overload_count': overload_count,
            'below_count': below_count,
            'total_departments': len(departments),
            'total_groups': sum(len(d['groups']) for d in departments)
        },
        'column_mapping': {
            'dept': str(dept_col),
            'group': str(group_col) if group_col else None,
            'name': str(name_col),
            'utilization': str(utilization_col) if utilization_col else None,
            'tasks': str(tasks_col) if tasks_col else None,
            'project': str(project_col) if project_col else None,
            'leave': str(leave_col) if leave_col else None,
            'task_type': str(task_type_col) if task_type_col else None,
            'task_name': str(task_name_col) if task_name_col else None,
            'standard_load': str(standard_load_col) if standard_load_col else None
        }
    }


def _build_hr_ai_prompt(hr_data):
    """构建HR Dashboard AI分析提示词"""
    s = hr_data['summary']
    depts = hr_data['departments']
    persons = hr_data['all_persons']

    lines = ['【重要：数据已在下方提供，请直接进行分析并输出完整报告。不要询问更多数据。】']
    lines.append('\n你是一位资深的人力资源与组织效能分析专家。请基于以下完整人力数据，输出一份专业的人力洞察分析报告，包含：')

    lines.append(f"""
## 人力总览

- **总人数**：{s['total_persons']}人
- **总部门数**：{s['total_departments']}个
- **总小组数**：{s['total_groups']}个
- **平均人力利用率**：{s['avg_utilization']}%
- **可调用人力**：{s['available_manpower']}人
- **整体负荷状态**：{s['status']}

### 负荷分布
- 空闲（≤90%）：{s['idle_count']}人
- 正常（90%-110%）：{s['normal_count']}人
- 低负载（110%-120%）：{s['low_load_count']}人
- 高负载（>120%）：{s['high_load_count']}人
- 超标准负载：{s['overload_count']}人
- 未达标准负载：{s['below_count']}人
""")

    # 各部门详细数据
    lines.append('\n## 各部门详情\n')
    for d in depts:
        lines.append(f"""
### {d['name']}
- 总人力：{d['total_persons']}人
- 平均利用率：{d['avg_utilization']}%
- 可调用人力：{d['available_manpower']}人
- 负荷状态：{d['status']}
- 请假人数：{d['leave_count']}人""")
        for g in d['groups']:
            lines.append(f"  - {g['name']}：利用率{g['avg_utilization']}%、可调用{g['available_manpower']}人、状态{g['status']}、{g['total_persons']}人")

    # 高负载/空闲人员
    high_load_persons = [p for p in persons if p['status'] == '高负载']
    idle_persons = [p for p in persons if p['status'] == '空闲']

    if high_load_persons:
        lines.append('\n## 高负载人员（>120%，需重点关注）\n')
        for p in high_load_persons[:10]:
            lines.append(f"- {p['name']}（{p['dept']}/{p['group']}）：利用率{p['utilization']}%")
    if idle_persons:
        lines.append('\n## 空闲人员（≤90%，可调配）\n')
        for p in idle_persons[:10]:
            lines.append(f"- {p['name']}（{p['dept']}/{p['group']}）：利用率{p['utilization']}%")

    lines.append('\n\n请输出结构化报告：1. 整体人力状况评估 2. 各部门分析 3. 关键风险与建议 4. 可优化空间')
    return '\n'.join(lines)


def _build_ai_prompt_combined(personal_data=None, team_data=None):
    """构建AI分析提示词"""
    parts = ['【重要：数据已在下方提供，请直接进行分析并输出完整报告。不要询问更多数据，不要要求提供原始数据，不要以任何方式表示需要更多信息。直接基于现有数据输出报告。】']
    parts.append('\n你是一位资深的人力资源分析专家。请基于以下已提供的完整数据，直接输出一份专业的人力洞察分析报告。')

    if personal_data:
        s = personal_data['summary']
        parts.append(f"""
## 个人任务负载数据

### 数据概况
- 总人数：{s['total_persons']}人
- 平均完成率：{s['avg_completion_rate']}%
- 不饱和人数：{s['unsaturated_count']}人（完成率<70%）
- 正常人数：{s['normal_count']}人
- 饱和/超负荷人数：{s['overloaded_count']}人（完成率>90%）

### 不饱和人员列表
{chr(10).join([f"- {p['name']}（完成率{p['rate']}%，部门：{p['team']}）" for p in s['unsaturated']]) if s['unsaturated'] else '- 无不饱和人员'}

### 饱和/超负荷人员列表
{chr(10).join([f"- {p['name']}（完成率{p['rate']}%，部门：{p['team']}）" for p in s['overloaded']]) if s['overloaded'] else '- 无超负荷人员'}

### 部门/小组平均完成率
{chr(10).join([f"- {t}：平均完成率{avg}%" for t, avg in s['team_avg'].items()]) if s.get('team_avg') else '- 无分组信息'}

### 资源缺口（平均完成率>90%的小组需增援）
{chr(10).join([f"- {t}（平均完成率{avg}%）" for t, avg in s.get('resource_gaps', {}).items()]) if s.get('resource_gaps') else '- 无资源缺口'}
""")

    if team_data:
        s = team_data['summary']
        parts.append(f"""
## 团队会签降本数据

### 数据概况
- 总团队数：{s['total_teams']}个
- 平均提效率：{s['avg_efficiency']}%
- 平均人力节省率：{s['avg_savings_rate']}%

### 各团队明细
{chr(10).join([f"- {r['team']}：预估{r['estimated']}人，实际{r['actual']}人，节省率{r['savings_rate']}%，提效率{r['efficiency_rate']}%" for r in team_data['data']])}

### 提效最佳团队
{'无数据' if not s['best_team'] else f"- {s['best_team']['name']}（提效率{s['best_team']['efficiency']}%）"}

### 提效最差团队
{'无数据' if not s['worst_team'] else f"- {s['worst_team']['name']}（提效率{s['worst_team']['efficiency']}%）"}
""")

    if personal_data and team_data:
        parts.append(f"""
## 综合分析要求

你同时收到了个人任务负载数据和团队会签降本数据，请进行**结合分析**：

1. 识别提效最差的团队中是否有成员属于不饱和或超负荷人员
2. 给出跨团队的任务调配建议
3. 分析人力负载与团队提效之间的关联关系
""")

    # 确定报告章节标题
    if personal_data and team_data:
        section_title = '综合分析（个人负载 + 团队降本）'
    elif personal_data:
        section_title = '个人负载分析'
    elif team_data:
        section_title = '团队降本分析'
    else:
        section_title = '数据分析'

    parts.append(f"""
## 报告要求

请生成一份专业的人力资源分析报告，包含以下部分：

### 一、数据总览
简要说明数据规模、总体指标。

### 二、{section_title}
- 核心指标解读
- 异常点清单（不饱和/超负荷人员 或 提效最差/最佳团队）

### 三、关键发现
- 列举最重要的3-5个发现
- 引用具体人名或团队名及数据

### 四、资源调配建议
- 针对不饱和人员和超负荷人员，给出具体任务调整建议
- 例如：将XX的部分任务调整给YY，以平衡负载
- 针对提效差的团队，给出改进建议

### 五、总结
- 一句话总结整体状况

请用Markdown格式输出，使用专业但清晰易懂的语言。""")

    return '\n'.join(parts)


# ========== API 路由 ==========

@workforce_bp.route('/upload', methods=['POST'])
def upload_file():
    """上传文件并解析"""
    session_id = str(uuid.uuid4())
    file = request.files.get('file')
    if not file:
        return jsonify({"success": False, "error": "未上传文件"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        return jsonify({"success": False, "error": "仅支持 .xlsx、.xls、.csv 格式"}), 400

    tmp_path = os.path.join(TEMP_DIR, f"{session_id}{ext}")
    file.save(tmp_path)

    try:
        parsed = _parse_file(tmp_path, file.filename)
        sheet_names = list(parsed.keys())
        first_sheet = sheet_names[0] if sheet_names else None

        with workforce_cache_lock:
            workforce_cache[session_id] = {
                'filepath': tmp_path,
                'filename': file.filename,
                'sheets': parsed,
                'sheet_names': sheet_names,
                'active_sheet': first_sheet
            }

        first_data = parsed.get(first_sheet) if first_sheet else None
        resp = {
            "success": True,
            "session_id": session_id,
            "sheet_names": sheet_names,
            "active_sheet": first_sheet,
            "file_type": first_data['type'] if first_data else None,
            "summary": first_data['summary'] if first_data else None,
            "data": first_data.get('data', []),
            "column_mapping": first_data['column_mapping'] if first_data else {}
        }
        if first_data and first_data.get('type') == 'hr_dashboard':
            resp['departments'] = first_data.get('departments', [])
            resp['dept_names'] = first_data.get('dept_names', [])
            resp['all_persons'] = first_data.get('all_persons', [])
            resp['hr_distribution'] = first_data.get('hr_distribution', [])
        return jsonify(resp)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"}), 400


@workforce_bp.route('/switch-sheet', methods=['POST'])
def switch_sheet():
    """切换工作表"""
    data = request.get_json()
    session_id = data.get('session_id')
    sheet_name = data.get('sheet_name')

    with workforce_cache_lock:
        entry = workforce_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效"}), 404
        if sheet_name not in entry['sheets']:
            return jsonify({"success": False, "error": f"工作表 {sheet_name} 不存在"}), 400
        entry['active_sheet'] = sheet_name
        sheet_data = entry['sheets'][sheet_name]

    resp = {
        "success": True,
        "active_sheet": sheet_name,
        "file_type": sheet_data['type'],
        "summary": sheet_data['summary'],
        "data": sheet_data.get('data', []),
        "column_mapping": sheet_data['column_mapping']
    }
    if sheet_data.get('type') == 'hr_dashboard':
        resp['departments'] = sheet_data.get('departments', [])
        resp['dept_names'] = sheet_data.get('dept_names', [])
        resp['all_persons'] = sheet_data.get('all_persons', [])
        resp['hr_distribution'] = sheet_data.get('hr_distribution', [])
    return jsonify(resp)


@workforce_bp.route('/data/<session_id>', methods=['GET'])
def get_data(session_id):
    """获取已解析的数据"""
    with workforce_cache_lock:
        entry = workforce_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效或已过期"}), 404
        sheet_name = entry['active_sheet']
        sheet_data = entry['sheets'][sheet_name]

    resp = {
        "success": True,
        "session_id": session_id,
        "sheet_names": entry['sheet_names'],
        "active_sheet": sheet_name,
        "file_type": sheet_data['type'],
        "summary": sheet_data['summary'],
        "data": sheet_data.get('data', []),
        "column_mapping": sheet_data['column_mapping']
    }
    if sheet_data.get('type') == 'hr_dashboard':
        resp['departments'] = sheet_data.get('departments', [])
        resp['dept_names'] = sheet_data.get('dept_names', [])
        resp['all_persons'] = sheet_data.get('all_persons', [])
        resp['hr_distribution'] = sheet_data.get('hr_distribution', [])
    return jsonify(resp)


@workforce_bp.route('/analyze/<session_id>', methods=['GET'])
def analyze(session_id):
    """AI分析报告 - SSE流式输出"""
    personal_session_id = request.args.get('personal_session_id')
    feedback = request.args.get('feedback')

    with workforce_cache_lock:
        entry = workforce_cache.get(session_id)
        personal_entry = workforce_cache.get(personal_session_id) if personal_session_id else None
        if not entry:
            return jsonify({"success": False, "error": "session_id无效或已过期"}), 404

        active_sheet = entry['active_sheet']
        sheet_data = entry['sheets'][active_sheet]
        personal_sheet_data = None
        if personal_entry:
            personal_sheet = personal_entry['active_sheet']
            personal_sheet_data = personal_entry['sheets'][personal_sheet]

    prompt = _build_ai_prompt_combined(
        personal_data=personal_sheet_data or (sheet_data if sheet_data['type'] == 'personal_task' else None),
        team_data=sheet_data if sheet_data['type'] == 'team_cost_reduction' else None
    )

    if sheet_data['type'] == 'hr_dashboard':
        prompt = _build_hr_ai_prompt(sheet_data)

    if feedback:
        prompt = f"【用户的改进意见】\n{feedback}\n\n请根据以上改进意见重新生成报告。\n\n原数据和分析提示：\n{prompt}"

    chat_messages = [
        {"role": "system", "content": "你是一位资深的人力资源分析专家，擅长数据分析、人力资源规划和团队效能优化。注意：用户已经提供了完整的结构化数据在对话中，请直接基于这些数据进行分析和报告输出，不要询问更多数据或要求提供原始数据。"},
        {"role": "user", "content": prompt}
    ]

    def generate():
        try:
            ai_response = call_ai_api(chat_messages, stream=True, temperature=0.3, max_tokens=8192)
            if ai_response is None:
                yield generate_sse_message('error', 'AI服务调用失败')
                return
            for line in ai_response.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8')
                if not line.startswith('data: '):
                    continue
                chunk_data = line[6:]
                if chunk_data == '[DONE]':
                    break
                try:
                    chunk = json.loads(chunk_data)
                    choices = chunk.get('choices')
                    if not choices or not isinstance(choices, list) or len(choices) == 0:
                        continue
                    delta = choices[0].get('delta', {})
                    if not delta:
                        continue
                    content = delta.get('content', '')
                    if content:
                        yield generate_sse_message('answer', content)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            yield generate_sse_message('error', f'AI分析异常: {str(e)}')
        finally:
            yield generate_sse_message('done', '分析完成')

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'Connection': 'keep-alive',
                             'X-Accel-Buffering': 'no'})


@workforce_bp.route('/combined-analyze', methods=['GET'])
def combined_analyze():
    """综合分析（两个数据源）SSE流式输出"""
    session_a = request.args.get('session_a')
    session_b = request.args.get('session_b')

    with workforce_cache_lock:
        entry_a = workforce_cache.get(session_a) if session_a else None
        entry_b = workforce_cache.get(session_b) if session_b else None

    personal_data = None
    team_data = None

    if entry_a:
        sheet_a = entry_a['active_sheet']
        data_a = entry_a['sheets'][sheet_a]
        if data_a['type'] == 'personal_task':
            personal_data = data_a
        else:
            team_data = data_a

    if entry_b:
        sheet_b = entry_b['active_sheet']
        data_b = entry_b['sheets'][sheet_b]
        if data_b['type'] == 'personal_task':
            personal_data = data_b
        else:
            team_data = data_b

    if not personal_data and not team_data:
        return jsonify({"success": False, "error": "没有可分析的数据"}), 400

    prompt = _build_ai_prompt_combined(
        personal_data=personal_data,
        team_data=team_data
    )

    chat_messages = [
        {"role": "system", "content": "你是一位资深的人力资源分析专家，擅长数据分析、人力资源规划和团队效能优化。注意：用户已经提供了完整的结构化数据在对话中，请直接基于这些数据进行分析和报告输出，不要询问更多数据或要求提供原始数据。"},
        {"role": "user", "content": prompt}
    ]

    def generate():
        try:
            ai_response = call_ai_api(chat_messages, stream=True, temperature=0.3, max_tokens=8192)
            if ai_response is None:
                yield generate_sse_message('error', 'AI服务调用失败')
                return
            for line in ai_response.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8')
                if not line.startswith('data: '):
                    continue
                chunk_data = line[6:]
                if chunk_data == '[DONE]':
                    break
                try:
                    chunk = json.loads(chunk_data)
                    choices = chunk.get('choices')
                    if not choices or not isinstance(choices, list) or len(choices) == 0:
                        continue
                    delta = choices[0].get('delta', {})
                    if not delta:
                        continue
                    content = delta.get('content', '')
                    if content:
                        yield generate_sse_message('answer', content)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            yield generate_sse_message('error', f'AI分析异常: {str(e)}')
        finally:
            yield generate_sse_message('done', '分析完成')

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'Connection': 'keep-alive',
                             'X-Accel-Buffering': 'no'})
