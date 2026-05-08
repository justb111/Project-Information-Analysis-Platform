"""交付路线图看板 - 后端API

支持多Sheet多功能看板：自动检测Sheet类型（路线图矩阵/人力模型/风险看板/简单列表），
提取结构化数据，聚合为组合级（Portfolio）视图，输出可视化数据与AI分析。
"""
import json
import os
import uuid
import re
import threading
from datetime import datetime

import pandas as pd
from flask import Blueprint, request, jsonify, Response

from utils import call_ai_api, generate_sse_message

delivery_bp = Blueprint('delivery', __name__, url_prefix='/api/delivery')

TEMP_DIR = os.path.join(os.path.dirname(__file__), 'temp_delivery')
os.makedirs(TEMP_DIR, exist_ok=True)

delivery_cache = {}
delivery_cache_lock = threading.Lock()

COLUMN_KEYWORDS = {
    'project': ['项目', 'project', '产品线', '产品', '项目名称', '所属项目', '项目名'],
    'deliverable': ['交付物', '交付件', '交付内容', 'deliverable', '交付', '交付项', '输出物'],
    'milestone': ['里程碑', 'milestone', '节点', '关键节点', '阶段节点', '重要节点', '当前节点'],
    'phase': ['阶段', 'phase', '阶段名称', '项目阶段', '阶段划分', '开发阶段'],
    'planned_date': ['计划日期', '计划时间', '计划完成', '计划', 'planned', 'plan date', '预计完成', '计划提测时间', '开始时间', '开始日期', '预计转维'],
    'actual_date': ['实际日期', '实际时间', '实际完成', '实际', 'actual', 'actual date', '完成日期', '实际提测', '实际上线'],
    'owner': ['负责人', 'owner', '责任人', '责任方', '责任部门', '承接人', '承担人', 'DPM', 'dpm', 'TPM', 'tpm', 'SPM', 'spm', 'STPM', '项目经理', '主管'],
    'status': ['状态', 'status', '完成状态', '进度状态', '当前状态', '任务状态', '完成', '是否测完', '任务完成'],
    'risk': ['风险', '风险等级', '风险级别', 'risk', '风险状态', '风险标识', '风险状态'],
    'completion': ['完成率', '进度', 'completion', 'progress', '完成百分比', '完成度', '进度百分比', '完成比例', '测试进度'],
    'dept': ['部门', '团队', 'department', 'team', 'group', '所属部门', '责任部门', '归属部门', '三级部门', '四级部门'],
    'priority': ['优先级', 'priority', '优先级别', '紧急程度', '优先等级'],
    'remark': ['备注', 'remark', '说明', 'note', '注释', '补充说明', '描述'],
    'date': ['日期', 'date', '时间', '时间节点', '提测时间']
}

# ========== Sheet 类型检测 ==========

SHEET_TYPE_SIGNATURES = {
    'roadmap': ['roadmap', '路线图', '项目类型', '产品线', '主辅测', 'DPM', 'STPM', 'HTPM', '当前节点', '下一节点时间'],
    'manpower': ['人力模型', '工时模型', '三级部门', '四级部门', '主测', '辅测', '跟测', '测试类型', '交付部门'],
    'risk_kanban': ['风险看板', '风险状态', '风险预警', '总体状态', '测试进度'],
    'staffing': ['编制人数', '编制执行人力', '软测', '硬测'],
    'release_plan': ['版本计划', '外发版本', '计划提测时间', '提测时间'],
    'maintenance': ['转维', '未转维', '后续产品线', 'DPM分配'],
    'project_count': ['规划数', '项目数', '在跑项目'],
    'tech_roadmap': ['技术项目', '专测人力', '技术项目分布'],
    'simple_list': []  # fallback
}


def _sheet_has_any_keyword(df_raw, keywords, max_rows=8):
    """检查Sheet前几行是否包含任一关键词"""
    for i in range(min(max_rows, len(df_raw))):
        row = df_raw.iloc[i]
        for val in row:
            if pd.isna(val):
                continue
            s = str(val).strip().lower().replace(' ', '').replace('_', '').replace('-', '')
            for kw in keywords:
                k = kw.lower().strip().replace(' ', '').replace('_', '').replace('-', '')
                if k in s:
                    return True
    return False


def detect_sheet_type(df_raw, sheet_name):
    """检测Sheet的数据类型"""
    sn = sheet_name.lower().replace(' ', '').replace('（', '(').replace('）', ')')

    # 看板 is empty
    if len(df_raw) == 0 or df_raw.shape[1] == 0:
        return 'empty'

    # Check by name first (fast)
    if '编制人数' in sn:
        return 'staffing'
    if '风险看板' in sn:
        return 'risk_kanban'
    if '未转维' in sn or 'dpm分配' in sn:
        return 'maintenance'
    if '版本计划' in sn or '外发版本' in sn:
        return 'release_plan'
    if '规划数' in sn or '项目数' in sn:
        return 'project_count'
    if '技术项目' in sn:
        return 'tech_roadmap'
    if 'log路径' in sn:
        return 'simple_list'
    if '工时模型' in sn or '人力模型' in sn or '模型分析' in sn:
        return 'manpower'
    if 'roadmap' in sn or '路线图' in sn:
        return 'roadmap'

    # Check by content signatures
    for stype, sigs in SHEET_TYPE_SIGNATURES.items():
        if stype in ('simple_list', 'empty'):
            continue
        if _sheet_has_any_keyword(df_raw, sigs):
            return stype

    return 'simple_list'


# ========== 专有解析器 ==========

def parse_roadmap_sheet(df_raw, header_row=7):
    """解析路线图矩阵Sheet"""
    if len(df_raw) <= header_row:
        return {'type': 'roadmap', 'data': [], 'projects': [], 'phases': []}

    # Header columns
    raw_headers = []
    for c in df_raw.iloc[header_row]:
        raw_headers.append(_safe_str(c))

    # Dynamic column mapping based on header values
    col_map = {}

    # Detect layout: 主管评估 sheets have 产品线 at col 0, 研测工时 sheets don't
    first_col_h = raw_headers[0].lower().replace(' ', '').replace('_', '').replace('-', '') if len(raw_headers) > 0 else ''
    has_product_line = '产品线' in first_col_h

    if has_product_line:
        col_map['proj_line'] = 0
        col_map['proj_type'] = 1
        col_map['proj_name'] = 2
    else:
        col_map['proj_type'] = 0
        col_map['proj_name'] = 1

    for j, h in enumerate(raw_headers):
        h_clean = h.lower().replace(' ', '').replace('_', '').replace('-', '')
        if '主辅测' in h_clean or '辅测' in h_clean:
            col_map['test_role'] = j
        elif 'tos' in h_clean and '版本' in h_clean:
            col_map['os_ver'] = j
        elif '测试策略' in h_clean or '策略' in h_clean:
            col_map['strategy'] = j
        elif h_clean in ('stpm',) or 'stpm' in h_clean:
            col_map['stpm'] = j
        elif h_clean in ('dpm',) or 'dpm' in h_clean:
            col_map['dpm'] = j
        elif '当前节点' in h_clean:
            col_map['current_node'] = j
        elif '下一节点时间' in h_clean or '节点时间' in h_clean:
            col_map['next_node_time'] = j

    # Find timeline start column
    timeline_start = max(col_map.get('next_node_time', 9), col_map.get('current_node', 8)) + 1
    if timeline_start < 10:
        timeline_start = 10

    # Parse project rows (data starts after header)
    projects = []
    for i in range(header_row + 1, len(df_raw)):
        row = df_raw.iloc[i]

        def _rc(idx_key, default_idx):
            idx = col_map.get(idx_key, default_idx)
            return _safe_str(row.iloc[idx] if len(row) > idx else '')

        proj_line = _rc('proj_line', 0) if 'proj_line' in col_map else ''
        if 'proj_line' in col_map and not proj_line:
            continue

        proj_type = _rc('proj_type', 1)
        proj_name = _rc('proj_name', 2)
        test_role = _rc('test_role', 3)
        os_ver = _rc('os_ver', 4)
        strategy = _rc('strategy', 5)

        # Skip summary rows (项目类型/项目数/未启动/进行中/已量产 rows)
        if proj_type and not proj_name and proj_line in ('', '项目类型', '产品线'):
            continue
        if not proj_name and proj_type:
            continue

        stpm = _rc('stpm', 6)
        dpm = _rc('dpm', 7)
        current_node = _rc('current_node', 8)
        next_node_time = _rc('next_node_time', 9)

        # Timeline data (week/phase columns)
        timeline = {}
        for j in range(timeline_start, min(len(row), len(raw_headers))):
            col_name = raw_headers[j] if j < len(raw_headers) else f'col_{j}'
            val = _safe_str(row.iloc[j] if j < len(row) else '')
            if col_name and val:
                # Week numbers or phase names
                timeline[col_name] = val

        entry = {
            'project_line': proj_line,
            'project_type': proj_type,
            'project': proj_name,
            'test_role': test_role,
            'os_version': os_ver,
            'strategy': strategy,
            'stpm': stpm,
            'dpm': dpm,
            'current_node': current_node,
            'next_node_time': next_node_time,
            'timeline': timeline
        }

        projects.append(entry)

    # Extract unique phases from timeline headers
    phases = []
    for j in range(timeline_start, min(len(raw_headers), 100)):
        h = raw_headers[j]
        if h:
            phases.append(h)

    # Build standardized data rows — for roadmap, group by DPM or project_type
    data = []
    for p in projects:
        # Use current_node as meaningful context, don't force into status keywords
        node = p['current_node'] or ''
        # Map project_type to a kanban-like group
        ptype = p['project_type'] or '其他项目'
        # Use os_version as phase grouping
        phase_str = p['os_version'] or p['project_type'] or ''
        # Determine a meaningful status-like label from what's available
        if node:
            display_status = node
        elif p['timeline']:
            # Has timeline data = actively tracked
            display_status = '在轨'
        else:
            display_status = ptype

        data.append({
            'project': p['project'],
            'project_type': p['project_type'],
            'deliverable': p['project'],
            'milestone': node,
            'phase': phase_str,
            'owner': p['dpm'] or p['stpm'],
            'status': display_status,
            'risk': '低',
            'completion': 0,
            'planned_date': p['next_node_time'],
            'dpm': p['dpm'],
            'stpm': p['stpm'],
            'test_role': p['test_role'],
            'os_version': p['os_version']
        })

    # Compute distributions for frontend filters
    status_dist = {}
    risk_dist = {}
    phase_dist = {}
    for r in data:
        s = r['status'] or '其他'
        status_dist[s] = (status_dist.get(s, 0) + 1)
        risk = r['risk'] or '低'
        risk_dist[risk] = (risk_dist.get(risk, 0) + 1)
        p = r['phase'] or '未分配'
        phase_dist[p] = (phase_dist.get(p, 0) + 1)

    return {
        'type': 'roadmap',
        'data': data,
        'projects': projects,
        'phases': list(phase_dist.keys()),
        'status_distribution': status_dist,
        'risk_distribution': risk_dist,
        'phase_distribution': phase_dist,
        'slices': {},
        'dept_stats': [],
        'completion_stats': {},
        'summary': {
            'total_projects': len([p for p in projects if p['project']]),
            'total_dpm_count': len(set(p['dpm'] for p in projects if p['dpm'])),
            'total_deliverables': len(data),
            'completed_count': 0,
            'delayed_count': 0,
            'on_going_count': len(data),
            'pending_count': 0,
            'high_risk_count': 0,
            'avg_completion': 0,
            'completion_rate': 0,
            'total_phases': len(phase_dist),
            'total_depts': 0
        }
    }


def parse_manpower_sheet(df_raw):
    """解析人力模型/工时模型Sheet"""
    # Find the header row (contains 三级部门/四级部门 and 主测/辅测/跟测)
    header_row = 0
    for i in range(min(8, len(df_raw))):
        row_str = ' '.join([_safe_str(v) for v in df_raw.iloc[i][:8]])
        if '三级部门' in row_str or '四级部门' in row_str or ('测试类型' in row_str and '主测' in row_str):
            header_row = i
            break
        if '部门' in row_str and '主测' in row_str:
            header_row = i
            break

    if len(df_raw) <= header_row:
        return {'type': 'manpower', 'rows': [], 'depts': [], 'phases': []}

    # Extract phase groups from header rows
    # Phase columns are grouped as: 主测[STR4, STR4二轮, STR4A, FR], 辅测[...], 跟测[...]
    raw_headers = []
    for c in df_raw.iloc[header_row]:
        raw_headers.append(_safe_str(c))

    # Determine column groups
    col_groups = {}  # col_idx -> group_name
    current_group = ''
    for j, h in enumerate(raw_headers):
        h_lower = h.lower().replace(' ', '')
        if '主测' in h_lower:
            current_group = '主测'
        elif '辅测' in h_lower:
            current_group = '辅测'
        elif '跟测' in h_lower:
            current_group = '跟测'
        elif h == '':
            current_group = current_group or ''
        col_groups[j] = current_group

    # Read sub-headers from next row (or previous row for phase names)
    # The rows near header contain phase names and totals
    sub_row_idx = header_row + 1
    sub_headers = {}
    if sub_row_idx < len(df_raw):
        for j, v in enumerate(df_raw.iloc[sub_row_idx]):
            sub_headers[j] = _safe_str(v)

    # Find dept/group columns
    dept_col = None
    group_col = None
    test_type_col = None
    for j, h in enumerate(raw_headers):
        h_lower = h.lower().replace(' ', '')
        if h_lower in ('三级部门', '部门', '交付测试一部', '交付测试二部', '交付测试三部'):
            dept_col = j
            break
        if h_lower in ('四级部门',):
            group_col = j
        if h_lower == '测试类型':
            test_type_col = j

    if dept_col is None:
        # Try to find by scanning
        for j in range(len(raw_headers)):
            for kw in ['部门', 'dept', 'team']:
                if kw in raw_headers[j].lower().replace(' ', ''):
                    dept_col = j
                    break
            if dept_col is not None:
                break

    # Parse data rows
    rows = []
    for i in range(header_row + 1, len(df_raw)):
        row = df_raw.iloc[i]
        dept_name = _safe_str(row.iloc[dept_col]) if dept_col is not None and len(row) > dept_col else ''
        group_name = _safe_str(row.iloc[group_col]) if group_col is not None and len(row) > group_col else ''
        test_type = _safe_str(row.iloc[test_type_col]) if test_type_col is not None and len(row) > test_type_col else ''

        if not dept_name and not group_name:
            continue
        # Skip summary rows (contain totals not dept data)
        if len(dept_name) > 20:
            continue

        entry = {
            'dept': dept_name or group_name,
            'group': group_name,
            'test_type': test_type,
            'by_group': {}
        }

        for j, h in enumerate(raw_headers):
            if j >= len(row):
                break
            if j <= max(dept_col or 0, group_col or 0, test_type_col or 0):
                continue
            val = row.iloc[j]
            if pd.isna(val) or val == '':
                continue
            try:
                num_val = float(val)
                phase_key = sub_headers.get(j, h) if j in sub_headers and sub_headers[j] else h
                group_name_col = col_groups.get(j, '')
                entry['by_group'][f'{group_name_col}_{phase_key}'] = num_val
            except (ValueError, TypeError):
                pass

        rows.append(entry)

    # Extract phase list
    phases = list(dict.fromkeys([sub_headers.get(j, '') for j in range(len(raw_headers)) if sub_headers.get(j, '') and j > (dept_col or 3)]))

    return {
        'type': 'manpower',
        'rows': rows,
        'depts': list(set(r['dept'] for r in rows)),
        'phases': phases,
        'summary': {'total_depts': len(set(r['dept'] for r in rows)), 'total_rows': len(rows)}
    }


def parse_risk_kanban(df_raw):
    """解析风险看板Sheet"""
    # Find header row
    header_row = 0
    for i in range(min(6, len(df_raw))):
        row_str = ' '.join([_safe_str(v) for v in df_raw.iloc[i][:8]])
        if '序号' in row_str and '项目' in row_str:
            header_row = i
            break

    if len(df_raw) <= header_row + 1:
        return {'type': 'risk_kanban', 'items': [], 'summary': {}}

    raw_headers = [_safe_str(c) for c in df_raw.iloc[header_row]]

    # Map columns
    col_map = {}
    for j, h in enumerate(raw_headers):
        h_lower = h.lower().replace(' ', '').replace('_', '')
        if not h_lower:
            continue
        if '序号' in h_lower:
            col_map['seq'] = j
        elif h_lower == '项目' or '项目名' in h_lower:
            col_map['project'] = j
        elif 'tos' in h_lower or '版本' in h_lower:
            col_map['os_ver'] = j
        elif '当前节点' in h_lower:
            col_map['node'] = j
        elif '截止时间' in h_lower or '时间' in h_lower:
            col_map['deadline'] = j
        elif '风险状态' in h_lower or '风险' in h_lower:
            col_map['risk_level'] = j
        elif h_lower == '风险' or '风险描述' in h_lower:
            col_map['risk_desc'] = j

    # Find progress columns (names of team members)
    team_cols = []
    for j in range(6, len(raw_headers)):
        h = raw_headers[j]
        if h and h not in ('', '风险状态', '风险', '风险描述') and j not in col_map.values():
            team_cols.append((j, h))

    items = []
    for i in range(header_row + 1, len(df_raw)):
        row = df_raw.iloc[i]
        seq = _safe_str(row.iloc[col_map.get('seq', 0)]) if 'seq' in col_map and len(row) > col_map['seq'] else ''
        if not seq.isdigit() and seq != '':
            continue

        project = _safe_str(row.iloc[col_map['project']]) if 'project' in col_map and len(row) > col_map['project'] else ''

        if not project:
            continue

        risk_level = ''
        if col_map.get('risk_level') and len(row) > col_map['risk_level']:
            risk_level = _safe_str(row.iloc[col_map['risk_level']])

        risk_desc = ''
        if col_map.get('risk_desc') and len(row) > col_map['risk_desc']:
            risk_desc = _safe_str(row.iloc[col_map['risk_desc']])

        # Team progress
        team_progress = {}
        for j, name in team_cols:
            if j < len(row):
                val = _safe_str(row.iloc[j])
                if val and val != '':
                    team_progress[name] = val

        items.append({
            'project': project,
            'os_version': _safe_str(row.iloc[col_map.get('os_ver', 0)]) if 'os_ver' in col_map and len(row) > col_map['os_ver'] else '',
            'current_node': _safe_str(row.iloc[col_map.get('node', 0)]) if 'node' in col_map and len(row) > col_map['node'] else '',
            'risk_level': _normalize_risk(risk_level if risk_level else '低'),
            'risk_description': risk_desc,
            'team_progress': team_progress
        })

    return {
        'type': 'risk_kanban',
        'items': items,
        'summary': {
            'total': len(items),
            'high_risk': len([i for i in items if i['risk_level'] == '高']),
            'mid_risk': len([i for i in items if i['risk_level'] == '中']),
            'low_risk': len([i for i in items if i['risk_level'] == '低'])
        },
        'data': [{
            'project': i['project'],
            'deliverable': i['project'],
            'status': '有风险' if i['risk_level'] in ('高', '中') else '进行中',
            'risk': i['risk_level'],
            'milestone': i['current_node']
        } for i in items],
        'status_distribution': {
            '有风险': len([i for i in items if i['risk_level'] in ('高', '中')]),
            '进行中': len([i for i in items if i['risk_level'] == '低'])
        },
        'risk_distribution': {
            '高': len([i for i in items if i['risk_level'] == '高']),
            '中': len([i for i in items if i['risk_level'] == '中']),
            '低': len([i for i in items if i['risk_level'] == '低'])
        },
        'phase_distribution': {},
        'phases': [],
        'slices': {},
        'dept_stats': [],
        'completion_stats': {}
    }


def parse_staffing_sheet(df_raw):
    """解析编制人数Sheet"""
    data_rows = []
    for i in range(1, len(df_raw)):
        row = df_raw.iloc[i]
        dept3 = _safe_str(row.iloc[2] if len(row) > 2 else '')
        dept4 = _safe_str(row.iloc[3] if len(row) > 3 else '')
        sw_staff = _safe_float(row.iloc[6] if len(row) > 6 else 0)
        hw_staff = _safe_float(row.iloc[7] if len(row) > 7 else 0)

        if not dept3 and not dept4:
            continue

        data_rows.append({
            'dept_3': dept3,
            'dept_4': dept4 or dept3,
            'sw_staff': sw_staff,
            'hw_staff': hw_staff,
            'total': sw_staff + hw_staff
        })

    return {
        'type': 'staffing',
        'rows': data_rows,
        'summary': {
            'total_sw': round(sum(r['sw_staff'] for r in data_rows), 2),
            'total_hw': round(sum(r['hw_staff'] for r in data_rows), 2),
            'total': round(sum(r['total'] for r in data_rows), 2)
        }
    }


# ========== 通用解析函数（保留兼容） ==========

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
    for col in df.columns:
        col_lower = str(col).lower().strip().replace(' ', '').replace('_', '').replace('-', '')
        for kw in keywords:
            kw_clean = kw.lower().strip().replace(' ', '').replace('_', '').replace('-', '')
            if kw_clean in col_lower:
                return col
    return None


def _detect_header_row(filepath, sheet_name, max_rows=10):
    """自动检测表头行"""
    try:
        df_raw = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
        best_row = 0
        best_score = 0

        for row_idx in range(min(max_rows, len(df_raw))):
            row = df_raw.iloc[row_idx]
            score = 0
            for val in row:
                if pd.isna(val):
                    continue
                val_str = str(val).strip()
                if len(val_str) > 200:
                    continue
                val_clean = val_str.lower().replace(' ', '').replace('_', '').replace('-', '')
                if not val_clean or val_clean.startswith('unnamed'):
                    continue
                for field, keywords in COLUMN_KEYWORDS.items():
                    for kw in keywords:
                        kw_clean = kw.lower().strip().replace(' ', '').replace('_', '').replace('-', '')
                        if kw_clean in val_clean:
                            score += 1
                            break
            if score > best_score:
                best_score = score
                best_row = row_idx

        if best_score > 0 and (best_row != 0 or best_score >= 2):
            return best_row
        return 0
    except Exception:
        return 0


def _normalize_date(val):
    if pd.isna(val):
        return ''
    val_str = str(val).strip()
    if not val_str:
        return ''
    try:
        if val_str.isdigit() and len(val_str) == 8:
            return f'{val_str[:4]}-{val_str[4:6]}-{val_str[6:8]}'
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日',
                     '%m-%d', '%m/%d', '%Y-%m', '%Y/%m']:
            try:
                dt = datetime.strptime(val_str, fmt)
                if fmt in ('%m-%d', '%m/%d'):
                    return dt.strftime('%m-%d')
                elif fmt in ('%Y-%m', '%Y/%m'):
                    return dt.strftime('%Y-%m')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue
    except Exception:
        pass
    try:
        dt = pd.Timestamp(val_str)
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return val_str


def _normalize_completion(val):
    v = _safe_float(val)
    if v == 0 and isinstance(val, str) and '%' in val:
        try:
            v = float(val.replace('%', ''))
        except (ValueError, TypeError):
            pass
    if v > 1 and v <= 100:
        return round(v, 1)
    elif v > 0 and v <= 1:
        return round(v * 100, 1)
    return round(v, 1)


def _normalize_status(val):
    val_str = _safe_str(val).lower()
    complete_keywords = ['完成', '已完成', '完结', '已结项', 'closed', 'done', 'complete', 'completed', 'finished', '已量产']
    progress_keywords = ['进行中', 'in progress', 'in_progress', 'ongoing', 'working', '开发中', '实施中', '在跑']
    delay_keywords = ['延期', '延迟', '滞后', 'delay', 'delayed', 'overdue', '超期', '逾期']
    pending_keywords = ['待开始', '未开始', '未启动', 'pending', 'todo', 'to do', 'planned', '计划中', '排队']
    risk_keywords = ['有风险', '风险', 'at risk', 'warning', 'issue']

    if not val_str:
        return '待开始'
    for kw in complete_keywords:
        if kw in val_str:
            return '已完成'
    for kw in delay_keywords:
        if kw in val_str:
            return '已延期'
    for kw in risk_keywords:
        if kw in val_str:
            return '有风险'
    for kw in progress_keywords:
        if kw in val_str:
            return '进行中'
    for kw in pending_keywords:
        if kw in val_str:
            return '待开始'
    return val_str.capitalize()


def _normalize_risk(val):
    val_str = _safe_str(val).lower()
    high_kw = ['高', '严重', 'blocker', 'critical', 'high', 'p0', 'p1', '重大', '高风险']
    mid_kw = ['中', 'medium', 'mid', 'major', 'p2', '一般', '中风险']
    low_kw = ['低', 'low', 'minor', 'p3', '正常', '无', '低风险']
    if not val_str:
        return '低'
    for kw in high_kw:
        if kw in val_str:
            return '高'
    for kw in mid_kw:
        if kw in val_str:
            return '中'
    for kw in low_kw:
        if kw in val_str:
            return '低'
    try:
        v = float(val_str)
        if v >= 3:
            return '高'
        elif v >= 2:
            return '中'
        else:
            return '低'
    except (ValueError, TypeError):
        pass
    return '低'


def _clean_sheet(df):
    df = df.dropna(how='all').reset_index(drop=True)
    if df.empty:
        return df
    df = df.dropna(axis=1, how='all')
    return df


def _detect_column_mapping(df):
    mapping = {}
    used_cols = set()

    def _find_best_column(field, keywords):
        candidates = []
        for col in df.columns:
            col_lower = str(col).lower().strip().replace(' ', '').replace('_', '').replace('-', '')
            for kw in keywords:
                kw_clean = kw.lower().strip().replace(' ', '').replace('_', '').replace('-', '')
                if kw_clean in col_lower:
                    non_null_count = df[col].notna().sum()
                    total_count = len(df)
                    density = non_null_count / max(total_count, 1)
                    candidates.append((col, density, non_null_count))
                    break
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[1], -x[2]))
        best_col = str(candidates[0][0])
        if best_col in used_cols:
            for col, density, count in candidates:
                if str(col) not in used_cols:
                    return str(col)
        return best_col

    for field, keywords in COLUMN_KEYWORDS.items():
        col = _find_best_column(field, keywords)
        if col:
            mapping[field] = col
            used_cols.add(col)

    return mapping


def _parse_phases(df, mapping):
    if 'phase' in mapping:
        phases = df[mapping['phase']].dropna().unique()
        return [_safe_str(p) for p in phases if _safe_str(p)]
    return []


def _extract_slice_data(df, mapping):
    result = {'all': []}
    if 'phase' in mapping:
        phase_col = mapping['phase']
        for phase, group in df.groupby(phase_col):
            phase_name = _safe_str(phase)
            if phase_name:
                result[f'phase_{phase_name}'] = group
    if 'status' in mapping:
        status_col = mapping['status']
        for status, group in df.groupby(status_col):
            status_name = _normalize_status(status)
            if status_name:
                result[f'status_{status_name}'] = group
    if 'dept' in mapping:
        dept_col = mapping['dept']
        for dept, group in df.groupby(dept_col):
            dept_name = _safe_str(dept)
            if dept_name:
                result[f'dept_{dept_name}'] = group
    result['all'] = df
    return result


def _format_date_for_display(val):
    d = _normalize_date(val)
    return d if d else '-'


def parse_simple_sheet(df):
    """解析简单列表型Sheet（原有逻辑）"""
    df = _clean_sheet(df)
    if df.empty:
        return {'type': 'simple_list', 'data': [], 'summary': {}, 'phases': [], 'slices': {},
                'status_distribution': {}, 'phase_distribution': {}, 'risk_distribution': {},
                'dept_stats': [], 'completion_stats': {}, 'column_mapping': {}}

    mapping = _detect_column_mapping(df)

    rows = []
    for _, row in df.iterrows():
        entry = {}
        entry['project'] = _safe_str(row.get(mapping.get('project', ''), '')) if mapping.get('project') else ''
        entry['deliverable'] = _safe_str(row.get(mapping.get('deliverable', ''), '')) if mapping.get('deliverable') else ''
        entry['milestone'] = _safe_str(row.get(mapping.get('milestone', ''), '')) if mapping.get('milestone') else ''
        entry['phase'] = _safe_str(row.get(mapping.get('phase', ''), '')) if mapping.get('phase') else ''

        if mapping.get('planned_date'):
            entry['planned_date'] = _normalize_date(row.get(mapping['planned_date'], ''))
        else:
            entry['planned_date'] = ''

        if mapping.get('actual_date'):
            entry['actual_date'] = _normalize_date(row.get(mapping['actual_date'], ''))
        else:
            entry['actual_date'] = ''

        entry['planned_display'] = _format_date_for_display(entry.get('planned_date', ''))
        entry['actual_display'] = _format_date_for_display(entry.get('actual_date', ''))

        entry['owner'] = _safe_str(row.get(mapping.get('owner', ''), '')) if mapping.get('owner') else ''
        entry['dept'] = _safe_str(row.get(mapping.get('dept', ''), '')) if mapping.get('dept') else ''

        raw_status = row.get(mapping.get('status', ''), '') if mapping.get('status') else ''
        entry['status'] = _normalize_status(raw_status)
        raw_risk = row.get(mapping.get('risk', ''), '') if mapping.get('risk') else ''
        entry['risk'] = _normalize_risk(raw_risk)
        raw_priority = row.get(mapping.get('priority', ''), '') if mapping.get('priority') else ''
        entry['priority'] = _safe_str(raw_priority) if raw_priority else ''
        raw_completion = row.get(mapping.get('completion', ''), '') if mapping.get('completion') else ''
        entry['completion'] = _normalize_completion(raw_completion)
        entry['remark'] = _safe_str(row.get(mapping.get('remark', ''), '')) if mapping.get('remark') else ''

        if not entry['deliverable'] and not entry['project'] and not entry['milestone']:
            continue
        rows.append(entry)

    if not rows:
        return {'type': 'simple_list', 'data': [], 'summary': {}, 'phases': [], 'slices': {},
                'status_distribution': {}, 'phase_distribution': {}, 'risk_distribution': {},
                'dept_stats': [], 'completion_stats': {}, 'column_mapping': mapping}

    phases = list(dict.fromkeys([r['phase'] for r in rows if r['phase']]))
    status_dist = {}
    for r in rows:
        s = r['status'] or '未设置'
        status_dist[s] = status_dist.get(s, 0) + 1

    phase_dist = {}
    for r in rows:
        p = r['phase'] or '未分配阶段'
        phase_dist[p] = phase_dist.get(p, 0) + 1

    risk_dist = {}
    for r in rows:
        risk = r['risk'] or '未评估'
        risk_dist[risk] = risk_dist.get(risk, 0) + 1

    completion_vals = [r['completion'] for r in rows if r['completion'] > 0]

    dept_data = {}
    for r in rows:
        d = r['dept'] or '未分配'
        if d not in dept_data:
            dept_data[d] = {'count': 0, 'delayed': 0, 'completed': 0, 'completions': []}
        dept_data[d]['count'] += 1
        if r['status'] == '已延期':
            dept_data[d]['delayed'] += 1
        if r['status'] == '已完成':
            dept_data[d]['completed'] += 1
        if r['completion'] > 0:
            dept_data[d]['completions'].append(r['completion'])

    dept_stats = []
    for d, info in dept_data.items():
        avg_c = round(sum(info['completions']) / len(info['completions']), 1) if info['completions'] else 0
        dept_stats.append({
            'name': d, 'total': info['count'], 'delayed': info['delayed'],
            'completed': info['completed'], 'avg_completion': avg_c,
            'completion_rate': round(info['completed'] / info['count'] * 100, 1) if info['count'] > 0 else 0
        })
    dept_stats.sort(key=lambda x: x['avg_completion'])

    gantt_data = []
    for r in rows:
        if r['planned_date'] or r['actual_date']:
            gantt_data.append({
                'deliverable': r['deliverable'] or r['milestone'] or r['project'],
                'phase': r['phase'], 'planned_date': r['planned_date'],
                'actual_date': r['actual_date'], 'status': r['status'],
                'risk': r['risk'], 'owner': r['owner'], 'completion': r['completion']
            })

    slices = {'status': {}, 'phase': {}, 'risk': {}, 'dept': {}}
    for s in set(r['status'] for r in rows):
        slices['status'][s] = [r for r in rows if r['status'] == s]
    for p in set(r['phase'] for r in rows):
        slices['phase'][p] = [r for r in rows if r['phase'] == p]
    for risk in set(r['risk'] for r in rows):
        slices['risk'][risk] = [r for r in rows if r['risk'] == risk]

    total = len(rows)
    completed_count = sum(1 for r in rows if r['status'] == '已完成')
    delayed_count = sum(1 for r in rows if r['status'] == '已延期')
    on_going_count = sum(1 for r in rows if r['status'] == '进行中')
    high_risk_count = sum(1 for r in rows if r['risk'] == '高')
    avg_completion = round(sum(completion_vals) / len(completion_vals), 1) if completion_vals else 0

    summary = {
        'total_deliverables': total, 'completed_count': completed_count,
        'delayed_count': delayed_count, 'on_going_count': on_going_count,
        'pending_count': total - completed_count - delayed_count - on_going_count,
        'high_risk_count': high_risk_count, 'avg_completion': avg_completion,
        'completion_rate': round(completed_count / total * 100, 1) if total > 0 else 0,
        'total_phases': len(phases), 'total_depts': len(dept_stats)
    }

    return {
        'type': 'simple_list', 'column_mapping': mapping, 'data': rows,
        'phases': phases, 'summary': summary, 'gantt_data': gantt_data,
        'slices': slices, 'status_distribution': status_dist,
        'phase_distribution': phase_dist, 'risk_distribution': risk_dist,
        'dept_stats': dept_stats,
        'completion_stats': {'avg': avg_completion, 'values': completion_vals}
    }


# ========== Portfolio 组合分析 ==========

def _build_portfolio(sheets_data):
    """跨Sheet组合分析：汇总路线图、人力模型、风险看板数据"""
    portfolio = {
        'projects': [],
        'dpm_workload': {},
        'manpower': {'depts': [], 'phases': [], 'by_dept': {}},
        'risk_items': [],
        'staffing': {},
        'maintenance_items': [],
        'release_plan': [],
        'summary': {}
    }

    all_dpm_projects = {}

    for sheet_name, sd in sheets_data.items():
        stype = sd.get('type', '')

        if stype == 'roadmap':
            projects = sd.get('projects', [])
            for p in projects:
                if p['project']:
                    entry = {
                        'project': p['project'],
                        'type': p['project_type'],
                        'dpm': p['dpm'],
                        'stpm': p['stpm'],
                        'current_node': p['current_node'],
                        'os_version': p['os_version'],
                        'test_role': p['test_role'],
                        'sheet': sheet_name,
                        'timeline': p.get('timeline', {})
                    }
                    portfolio['projects'].append(entry)

                    # DPM workload
                    dpm_name = p['dpm'] or '未分配'
                    if dpm_name not in all_dpm_projects:
                        all_dpm_projects[dpm_name] = []
                    all_dpm_projects[dpm_name].append(p['project'])

        elif stype == 'manpower':
            rows = sd.get('rows', [])
            for r in rows:
                d = r['dept']
                if d not in portfolio['manpower']['by_dept']:
                    portfolio['manpower']['by_dept'][d] = {'dept': d, 'groups': []}
                portfolio['manpower']['by_dept'][d]['groups'].append(r)
            for d in sd.get('depts', []):
                if d not in portfolio['manpower']['depts']:
                    portfolio['manpower']['depts'].append(d)

        elif stype == 'risk_kanban':
            portfolio['risk_items'] = sd.get('items', [])

        elif stype == 'staffing':
            portfolio['staffing'] = sd.get('summary', {})

        elif stype == 'maintenance':
            # Parse maintenance items from data
            for item in sd.get('data', []):
                portfolio['maintenance_items'].append(item)

        elif stype == 'release_plan':
            for item in sd.get('data', []):
                portfolio['release_plan'].append(item)

    # Build DPM workload list
    dpm_workload_list = []
    for dpm_name, projs in sorted(all_dpm_projects.items()):
        dpm_workload_list.append({
            'name': dpm_name,
            'project_count': len(projs),
            'projects': projs
        })
    dpm_workload_list.sort(key=lambda x: -x['project_count'])
    portfolio['dpm_workload'] = dpm_workload_list

    # Portfolio summary
    total_projects = len(set(p['project'] for p in portfolio['projects']))
    high_risk_count = len([i for i in portfolio['risk_items'] if i.get('risk_level') == '高'])
    mid_risk_count = len([i for i in portfolio['risk_items'] if i.get('risk_level') == '中'])

    portfolio['summary'] = {
        'total_projects': total_projects,
        'total_dpm': len(all_dpm_projects),
        'high_risk_count': high_risk_count,
        'mid_risk_count': mid_risk_count,
        'total_risk_items': len(portfolio['risk_items']),
        'total_manpower_depts': len(portfolio['manpower']['depts']),
        'total_maintenance': len(portfolio['maintenance_items'])
    }

    return portfolio


# ========== 主文件解析入口 ==========

def _parse_file(filepath, filename):
    """解析上传的文件，支持多Sheet，自动检测类型并分发到专有解析器"""
    ext = os.path.splitext(filename)[1].lower()
    result = {}
    sheet_types = {}
    raw_sheets = {}

    if ext == '.csv':
        try:
            df = pd.read_csv(filepath, encoding='utf-8')
        except UnicodeDecodeError:
            df = pd.read_csv(filepath, encoding='gbk')
        df = _clean_sheet(df)
        if not df.empty:
            parsed = parse_simple_sheet(df)
            parsed['sheet_name'] = 'Sheet1'
            result['Sheet1'] = parsed
            sheet_types['Sheet1'] = 'simple_list'
    else:
        xl = pd.ExcelFile(filepath)
        for sheet in xl.sheet_names:
            # First read raw for type detection
            df_raw = pd.read_excel(xl, sheet_name=sheet, header=None)

            # Detect sheet type
            stype = detect_sheet_type(df_raw, sheet)
            sheet_types[sheet] = stype
            raw_sheets[sheet] = df_raw

            if stype == 'empty':
                continue

            parsed = None

            if stype == 'roadmap':
                # Find the actual header row
                header_row = _detect_header_row_roadmap(df_raw)
                parsed = parse_roadmap_sheet(df_raw, header_row)

            elif stype in ('manpower',):
                parsed = parse_manpower_sheet(df_raw)

            elif stype == 'risk_kanban':
                parsed = parse_risk_kanban(df_raw)

            elif stype == 'staffing':
                parsed = parse_staffing_sheet(df_raw)

            elif stype == 'simple_list':
                # Use keyword-based header detection
                header_row = _detect_header_row(filepath, sheet)
                df = pd.read_excel(xl, sheet_name=sheet, header=header_row)
                df = _clean_sheet(df)
                if header_row > 0:
                    df_all = pd.read_excel(xl, sheet_name=sheet, header=None)
                    raw_columns = df_all.iloc[header_row].tolist()
                    clean_cols = []
                    seen = {}
                    for i, c in enumerate(raw_columns):
                        if pd.isna(c) or str(c).strip() == '' or str(c).strip().lower().startswith('unnamed'):
                            name = ''
                        else:
                            name = str(c).strip()
                        if name == '':
                            name = f'col_{i}'
                        if name in seen:
                            seen[name] += 1
                            name = f'{name}_{seen[name]}'
                        else:
                            seen[name] = 1
                        clean_cols.append(name)
                    df_all.columns = clean_cols
                    df = df_all.iloc[header_row + 1:].reset_index(drop=True)
                    df = _clean_sheet(df)
                if not df.empty:
                    parsed = parse_simple_sheet(df)
            else:
                # For all other types (release_plan, maintenance, etc.), try simple parsing
                header_row = _detect_header_row(filepath, sheet)
                df = pd.read_excel(xl, sheet_name=sheet, header=header_row)
                df = _clean_sheet(df)
                if header_row > 0:
                    df_all = pd.read_excel(xl, sheet_name=sheet, header=None)
                    raw_columns = df_all.iloc[header_row].tolist()
                    clean_cols = []
                    seen = {}
                    for i, c in enumerate(raw_columns):
                        if pd.isna(c) or str(c).strip() == '' or str(c).strip().lower().startswith('unnamed'):
                            name = ''
                        else:
                            name = str(c).strip()
                        if name == '':
                            name = f'col_{i}'
                        if name in seen:
                            seen[name] += 1
                            name = f'{name}_{seen[name]}'
                        else:
                            seen[name] = 1
                        clean_cols.append(name)
                    df_all.columns = clean_cols
                    df = df_all.iloc[header_row + 1:].reset_index(drop=True)
                    df = _clean_sheet(df)
                if not df.empty:
                    parsed = parse_simple_sheet(df)
                    parsed['type'] = stype

            if parsed:
                parsed['sheet_name'] = sheet
                result[sheet] = parsed

    # Build portfolio
    portfolio = _build_portfolio(result)

    return result, sheet_types, portfolio


def _detect_header_row_roadmap(df_raw):
    """检测路线图Sheet的真正表头行（含产品线/项目/主辅测/DPM等）"""
    for i in range(min(10, len(df_raw))):
        row = df_raw.iloc[i]
        row_str = ' '.join([_safe_str(v) for v in row[:10]])
        if '产品线' in row_str and '项目类型' in row_str and '项目' in row_str:
            return i
        if '项目' in row_str and ('主辅测' in row_str or 'DPM' in row_str or 'STPM' in row_str):
            return i
        if '项目类型' in row_str and '项目' in row_str and '主辅测' in row_str:
            return i
    return 7  # default fallback


# ========== AI Prompt 构建 ==========

def _build_ai_prompt(sheet_data, sheet_name, portfolio=None):
    """构建AI分析提示词，包含组合级数据"""
    stype = sheet_data.get('type', 'simple_list')

    if stype == 'roadmap':
        return _build_roadmap_prompt(sheet_data, sheet_name)
    elif stype == 'risk_kanban':
        return _build_risk_prompt(sheet_data, sheet_name)
    elif stype == 'manpower':
        return _build_manpower_prompt(sheet_data, sheet_name)
    else:
        return _build_simple_prompt(sheet_data, sheet_name)


def _build_roadmap_prompt(sd, sheet_name):
    """路线图AI提示"""
    projects = sd.get('projects', [])
    valid = [p for p in projects if p['project']]
    if not valid:
        return '路线图数据为空，无项目信息。'

    dpm_count = len(set(p['dpm'] for p in valid if p['dpm']))
    node_status = {}
    for p in valid:
        n = p['current_node'] or '未设置'
        node_status[n] = node_status.get(n, 0) + 1

    dpm_projects = {}
    for p in valid:
        d = p['dpm'] or '未分配'
        if d not in dpm_projects:
            dpm_projects[d] = []
        dpm_projects[d].append(p['project'])

    lines = [
        f'## 路线图数据概览（{sheet_name}）',
        f'- 总项目数：{len(valid)} 项',
        f'- DPM/项目经理数：{dpm_count} 人',
        f'- 项目类型分布：',
    ]
    types = {}
    for p in valid:
        t = p['project_type'] or '未知'
        types[t] = types.get(t, 0) + 1
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        lines.append(f'  - {t}：{c} 项')

    lines.append('')
    lines.append('## 当前节点分布')
    for n, c in sorted(node_status.items(), key=lambda x: -x[1]):
        lines.append(f'- {n}：{c} 项')

    lines.append('')
    lines.append('## DPM 项目负载')
    for d, projs in sorted(dpm_projects.items(), key=lambda x: -len(x[1])):
        lines.append(f'- {d}：{len(projs)} 个项目（{", ".join(projs[:5])}{"..." if len(projs) > 5 else ""}）')

    lines.append('')
    lines.append("""
请输出结构化报告，包含以下章节：

### 一、组合级交付概况
评估整体交付组合的健康状况，项目群分布特征。

### 二、DPM负载分析
分析DPM/项目经理的项目负载情况，是否存在过载风险。

### 三、阶段进展分析
各阶段节点分布，关键路径识别。

### 四、风险与瓶颈
识别交付瓶颈和潜在风险点。

### 五、改进建议
针对过载DPM和关键瓶颈给出建议。
""")
    return '\n'.join(lines)


def _build_risk_prompt(sd, sheet_name):
    """风险看板AI提示"""
    items = sd.get('items', [])
    s = sd.get('summary', {})

    lines = [
        f'## 风险看板数据（{sheet_name}）',
        f'- 总项目数：{s.get("total", 0)}',
        f'- 高风险：{s.get("high_risk", 0)}',
        f'- 中风险：{s.get("mid_risk", 0)}',
        f'- 低风险：{s.get("low_risk", 0)}',
        '',
        '## 风险明细',
    ]

    for item in items:
        desc = item.get('risk_description', '')
        desc_short = desc[:200] + '...' if len(desc) > 200 else desc
        team_info = ', '.join([f'{k}:{v}' for k, v in item.get('team_progress', {}).items()])
        lines.append(f'- [{item["risk_level"]}] {item["project"]}')
        if desc_short:
            lines.append(f'  - 风险描述：{desc_short}')
        if team_info:
            lines.append(f'  - 团队进度：{team_info}')

    lines.append('')
    lines.append("""
请分析风险看板数据，输出：

### 一、整体风险状况
### 二、高风险项目深度分析
### 三、风险根因归类
### 四、改进建议
""")
    return '\n'.join(lines)


def _build_manpower_prompt(sd, sheet_name):
    """人力模型AI提示"""
    rows = sd.get('rows', [])
    depts = sd.get('depts', [])

    lines = [
        f'## 人力模型数据（{sheet_name}）',
        f'- 涉及部门数：{len(depts)}',
        f'- 数据行数：{len(rows)}',
        '',
        '## 各部门数据',
    ]

    dept_totals = {}
    for r in rows:
        d = r['dept']
        total = sum(v for v in r['by_group'].values() if isinstance(v, (int, float)))
        dept_totals[d] = dept_totals.get(d, 0) + total

    for d, t in sorted(dept_totals.items(), key=lambda x: -x[1]):
        lines.append(f'- {d}：{round(t, 2)} 人天')

    lines.append('')
    lines.append("""
请分析人力模型数据，输出：

### 一、人力投入总览
### 二、部门人力分布
### 三、关键发现
### 四、资源调配建议
""")
    return '\n'.join(lines)


def _build_simple_prompt(sd, sheet_name):
    """简单列表AI提示（兼容旧版）"""
    s = sd['summary']
    rows = sd.get('data', [])
    phases = sd.get('phases', [])
    dept_stats = sd.get('dept_stats', [])
    status_dist = sd.get('status_distribution', {})
    risk_dist = sd.get('risk_distribution', {})
    phase_dist = sd.get('phase_distribution', {})

    if not rows or not s:
        return '没有可分析的数据。'

    high_risk_items = [r for r in rows if r.get('risk') == '高']
    delayed_items = [r for r in rows if r.get('status') == '已延期']

    lines = [
        '【重要：数据已在下方提供，请直接进行分析并输出完整报告。不要询问更多数据。】',
        '',
        '你是一位资深的项目管理与交付路线图分析专家。请基于以下交付路线图数据，输出一份专业的项目交付分析报告。',
        '',
        f'## 数据概览（工作表：{sheet_name}）',
        '',
        f'- 交付物总数：{s["total_deliverables"]} 项',
        f'- 已完成：{s["completed_count"]} 项（{s["completion_rate"]}%）',
        f'- 进行中：{s["on_going_count"]} 项',
        f'- 已延期：{s["delayed_count"]} 项',
        f'- 高风险项：{s["high_risk_count"]} 项',
        f'- 平均完成率：{s["avg_completion"]}%',
        f'- 涉及阶段数：{s["total_phases"]} 个',
        '',
        '## 状态分布',
    ]
    for status, count in sorted(status_dist.items(), key=lambda x: -x[1]):
        lines.append(f'- {status}：{count} 项')
    lines.append('')
    lines.append('## 阶段分布')
    for phase, count in sorted(phase_dist.items(), key=lambda x: -x[1]):
        lines.append(f'- {phase}：{count} 项')
    lines.append('')
    lines.append('## 风险分布')
    for risk, count in sorted(risk_dist.items(), key=lambda x: -x[1]):
        lines.append(f'- {risk}风险：{count} 项')
    lines.append('')
    lines.append('## 阶段列表')
    for p in phases:
        lines.append(f'- {p}')

    if dept_stats:
        lines.append('')
        lines.append('## 部门/团队统计')
        for d in dept_stats:
            lines.append(f'- {d["name"]}：共{d["total"]}项，完成{d["completed"]}项，延期{d["delayed"]}项，平均完成率{d["avg_completion"]}%')

    if high_risk_items:
        lines.append('')
        lines.append('## 高风险项（需重点关注）')
        for item in high_risk_items[:10]:
            lines.append(f'- {item.get("deliverable") or item.get("project")}（{item.get("phase")}）- 负责人：{item.get("owner")}')

    if delayed_items:
        lines.append('')
        lines.append('## 已延期项')
        for item in delayed_items[:10]:
            lines.append(f'- {item.get("deliverable") or item.get("project")}（{item.get("phase")}）- 计划：{item.get("planned_display")} - 负责人：{item.get("owner")}')

    lines.append('')
    lines.append("""
请输出结构化报告，包含以下章节：

### 一、整体交付概况
评估整体交付进展健康状况（正常/需关注/高风险）。

### 二、阶段进展分析
逐阶段分析完成情况、关键堵点。

### 三、风险与延期分析
- 高风险项的风险原因分析
- 延期项的根因与影响评估
- 跨阶段/跨团队依赖风险

### 四、关键发现
列举最重要的3-5个发现，引用具体数据。

### 五、改进建议
针对延期和高风险项给出具体改进措施和建议。

请用Markdown格式输出，使用专业但清晰易懂的语言。""")

    return '\n'.join(lines)


# ========== API 路由 ==========

@delivery_bp.route('/upload', methods=['POST'])
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
        parsed, sheet_types, portfolio = _parse_file(tmp_path, file.filename)
        sheet_names = list(parsed.keys())

        # Auto-select best sheet
        first_sheet = None
        best_score = -1
        priority_fields = {'project', 'deliverable', 'status', 'risk', 'planned_date', 'owner'}
        for s in sheet_names:
            sd = parsed[s]
            if not sd.get('data'):
                continue
            mapping = sd.get('column_mapping', {})
            score = sum(2 for f in priority_fields if f in mapping)
            score += len(mapping)
            if score > best_score:
                best_score = score
                first_sheet = s
        if not first_sheet:
            first_sheet = sheet_names[0] if sheet_names else None

        # Also consider roadmap sheets as default if available
        roadmap_sheets = [s for s in sheet_names if parsed[s].get('type') == 'roadmap']
        if roadmap_sheets and first_sheet not in roadmap_sheets:
            first_sheet = roadmap_sheets[0]

        with delivery_cache_lock:
            delivery_cache[session_id] = {
                'filepath': tmp_path,
                'filename': file.filename,
                'sheets': parsed,
                'sheet_types': sheet_types,
                'sheet_names': sheet_names,
                'active_sheet': first_sheet,
                'portfolio': portfolio
            }

        first_data = parsed.get(first_sheet) if first_sheet else None
        resp = {
            "success": True,
            "session_id": session_id,
            "sheet_names": sheet_names,
            "active_sheet": first_sheet,
            "sheet_types": sheet_types,
            "file_type": first_data.get('type', 'delivery_roadmap') if first_data else 'unknown',
            "portfolio": portfolio,
            "summary": first_data.get('summary', {}) if first_data else {},
            "data": first_data.get('data', []) if first_data else [],
            "phases": first_data.get('phases', []) if first_data else [],
            "projects": first_data.get('projects', []) if first_data else [],
            "column_mapping": first_data.get('column_mapping', {}) if first_data else {},
            "slices": first_data.get('slices', {}) if first_data else {},
            "status_distribution": first_data.get('status_distribution', {}) if first_data else {},
            "phase_distribution": first_data.get('phase_distribution', {}) if first_data else {},
            "risk_distribution": first_data.get('risk_distribution', {}) if first_data else {},
            "dept_stats": first_data.get('dept_stats', []) if first_data else [],
            "gantt_data": first_data.get('gantt_data', []) if first_data else [],
            "completion_stats": first_data.get('completion_stats', {}) if first_data else {}
        }
        return jsonify(resp)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"}), 400


@delivery_bp.route('/switch-sheet', methods=['POST'])
def switch_sheet():
    """切换工作表"""
    data = request.get_json()
    session_id = data.get('session_id')
    sheet_name = data.get('sheet_name')

    with delivery_cache_lock:
        entry = delivery_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效"}), 404
        if sheet_name not in entry['sheets']:
            return jsonify({"success": False, "error": f"工作表 {sheet_name} 不存在"}), 400
        entry['active_sheet'] = sheet_name
        sheet_data = entry['sheets'][sheet_name]

    resp = {
        "success": True,
        "active_sheet": sheet_name,
        "file_type": sheet_data.get('type', 'delivery_roadmap'),
        "portfolio": entry.get('portfolio', {}),
        "summary": sheet_data.get('summary', {}),
        "data": sheet_data.get('data', []),
        "projects": sheet_data.get('projects', []),
        "phases": sheet_data.get('phases', []),
        "column_mapping": sheet_data.get('column_mapping', {}),
        "slices": sheet_data.get('slices', {}),
        "status_distribution": sheet_data.get('status_distribution', {}),
        "phase_distribution": sheet_data.get('phase_distribution', {}),
        "risk_distribution": sheet_data.get('risk_distribution', {}),
        "dept_stats": sheet_data.get('dept_stats', []),
        "gantt_data": sheet_data.get('gantt_data', []),
        "completion_stats": sheet_data.get('completion_stats', {})
    }
    return jsonify(resp)


@delivery_bp.route('/data/<session_id>', methods=['GET'])
def get_data(session_id):
    """获取已解析的数据"""
    with delivery_cache_lock:
        entry = delivery_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效或已过期"}), 404
        sheet_name = entry['active_sheet']
        sheet_data = entry['sheets'][sheet_name]

    resp = {
        "success": True,
        "session_id": session_id,
        "sheet_names": entry['sheet_names'],
        "active_sheet": sheet_name,
        "sheet_types": entry.get('sheet_types', {}),
        "file_type": sheet_data.get('type', 'delivery_roadmap'),
        "portfolio": entry.get('portfolio', {}),
        "summary": sheet_data.get('summary', {}),
        "data": sheet_data.get('data', []),
        "projects": sheet_data.get('projects', []),
        "phases": sheet_data.get('phases', []),
        "column_mapping": sheet_data.get('column_mapping', {}),
        "slices": sheet_data.get('slices', {}),
        "status_distribution": sheet_data.get('status_distribution', {}),
        "phase_distribution": sheet_data.get('phase_distribution', {}),
        "risk_distribution": sheet_data.get('risk_distribution', {}),
        "dept_stats": sheet_data.get('dept_stats', []),
        "gantt_data": sheet_data.get('gantt_data', []),
        "completion_stats": sheet_data.get('completion_stats', {})
    }
    return jsonify(resp)


@delivery_bp.route('/all-sheets/<session_id>', methods=['GET'])
def get_all_sheets(session_id):
    """获取所有工作表的完整数据（按类型组织），供7板块看板使用"""
    with delivery_cache_lock:
        entry = delivery_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效或已过期"}), 404

        sheets_by_type = {}
        all_data = {}
        for sname, sdata in entry['sheets'].items():
            stype = sdata.get('type', 'unknown')
            if stype not in sheets_by_type:
                sheets_by_type[stype] = []
            sheets_by_type[stype].append(sname)

            # Keep raw + parsed data
            safe = {
                'sheet_name': sname,
                'type': stype,
                'summary': sdata.get('summary', {}),
                'data': sdata.get('data', []),
                'phases': sdata.get('phases', []),
                'projects': sdata.get('projects', []),
                'slices': sdata.get('slices', {}),
                'status_distribution': sdata.get('status_distribution', {}),
                'risk_distribution': sdata.get('risk_distribution', {}),
                'phase_distribution': sdata.get('phase_distribution', {}),
                'dept_stats': sdata.get('dept_stats', []),
                'gantt_data': sdata.get('gantt_data', []),
                'completion_stats': sdata.get('completion_stats', {}),
                'column_mapping': sdata.get('column_mapping', {}),
                'dpm_workload': sdata.get('dpm_workload', []),
                'manpower_data': sdata.get('manpower_data', []),
                'staffing_data': sdata.get('staffing_data', []),
                'sample_rows': sdata.get('data', [])[:5]
            }
            all_data[sname] = safe

    resp = {
        "success": True,
        "sheets_by_type": sheets_by_type,
        "all_sheets": all_data,
        "sheet_names": entry['sheet_names'],
        "sheet_types": entry.get('sheet_types', {}),
        "portfolio": entry.get('portfolio', {}),
        "active_sheet": entry['active_sheet']
    }
    return jsonify(resp)


@delivery_bp.route('/analyze/<session_id>', methods=['GET'])
def analyze(session_id):
    """AI分析报告 - SSE流式输出"""
    feedback = request.args.get('feedback')
    sheet_arg = request.args.get('sheet')

    with delivery_cache_lock:
        entry = delivery_cache.get(session_id)
        if not entry:
            return jsonify({"success": False, "error": "session_id无效或已过期"}), 404

        if sheet_arg and sheet_arg in entry['sheets']:
            sheet_name = sheet_arg
        else:
            sheet_name = entry['active_sheet']
        sheet_data = entry['sheets'][sheet_name]
        portfolio = entry.get('portfolio', {})

    # Build portfolio-level summary first
    portfolio_summary_lines = []
    if portfolio:
        ps = portfolio.get('summary', {})
        if ps.get('total_projects', 0) > 0:
            portfolio_summary_lines = [
                '## Portfolio 组合级概览',
                f'- 项目总数：{ps.get("total_projects", 0)} 个',
                f'- DPM/项目经理数：{ps.get("total_dpm", 0)} 人',
                f'- 高风险项目：{ps.get("high_risk_count", 0)} 个',
                f'- 中风险项目：{ps.get("mid_risk_count", 0)} 个',
                f'- 待转维项目数：{ps.get("total_maintenance", 0)} 个',
                ''
            ]

            # DPM workload
            dpm_list = portfolio.get('dpm_workload', [])
            if dpm_list:
                portfolio_summary_lines.append('## DPM 负载分布')
                for dpm in dpm_list[:15]:
                    portfolio_summary_lines.append(f'- {dpm["name"]}：{dpm["project_count"]} 个项目')
                portfolio_summary_lines.append('')

            # Risk items
            risk_items = portfolio.get('risk_items', [])
            if risk_items:
                portfolio_summary_lines.append('## 风险项目')
                for item in risk_items[:10]:
                    desc = item.get('risk_description', '')
                    short_desc = desc[:100] + '...' if len(desc) > 100 else desc
                    portfolio_summary_lines.append(f'- [{item.get("risk_level","?")}] {item.get("project","?")}')
                    if short_desc:
                        portfolio_summary_lines.append(f'  - {short_desc}')
                portfolio_summary_lines.append('')

    prompt = _build_ai_prompt(sheet_data, sheet_name, portfolio)

    # Prepend portfolio summary if available
    if portfolio_summary_lines:
        prompt = '\n'.join(portfolio_summary_lines) + '\n\n' + prompt

    if feedback:
        prompt = f"【用户的改进意见】\n{feedback}\n\n请根据以上改进意见重新生成报告。\n\n原数据和分析提示：\n{prompt}"

    chat_messages = [
        {"role": "system", "content": "你是一位资深的项目管理与交付路线图分析专家，擅长项目组合管理、交付风险识别和资源优化调度。注意：用户已经提供了完整的结构化数据在对话中，请直接基于这些数据进行分析和报告输出，不要询问更多数据或要求提供原始数据。"},
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
