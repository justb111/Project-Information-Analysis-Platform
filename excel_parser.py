import pandas as pd
import numpy as np
import re
import os
import json
from datetime import datetime
from collections import defaultdict

COLUMN_MAPPING_DB = os.path.join(os.path.dirname(__file__), 'column_mapping_cache.json')

DEFAULT_ALIASES = {
    "project": ["项目", "项目名称", "项目名", "项目标识", "project", "project name", "task", "任务", "任务名称"],
    "progress": ["进度", "进度%", "进度百分比", "完成率", "完成进度", "progress", "completion", "完成%", "完成比例", "完成百分比", "percent complete", "% complete"],
    "start_date": ["开始日期", "任务开始日期", "计划开始日期", "开始时间", "start date", "start", "计划开始"],
    "end_date": ["截止日期", "结束日期", "任务截止日期", "计划结束日期", "完成日期", "end date", "due date", "deadline", "end", "计划结束"],
    "dpm": ["负责人", "责任人", "项目经理", "dpm", "owner", "assignee", "经办人", "承担人", "主管"],
    "department": ["部门", "所属部门", "团队", "组", "dept", "department", "team", "项目组"],
    "effort_planned": ["预估人力", "预估人力需求", "计划工时", "预估工时", "人力投入", "effort", "计划人力", "总工时", "计划人天"],
    "effort_remaining": ["剩余人力", "剩余人力需求", "剩余工时", "实际工时", "实际人力", "remaining effort", "remaining", "剩余人天"],
    "case_count": ["用例数", "测试用例数", "用例", "case count", "test cases", "测试用例", "用例数量"],
    "case_executed": ["已执行", "已执行用例", "执行用例数", "已执行数", "executed", "cases executed"],
    "phase": ["阶段", "项目阶段", "当前阶段", "phase", "stage", "状态", "项目状态", "开发阶段"],
    "status": ["状态", "项目状态", "任务状态", "status", "当前状态"],
    "manager": ["项目经理", "项目负责人", "主管", "manager", "负责人"],
    "priority": ["优先级", "优先等级", "priority", "级别", "重要程度"],
    "risk_level": ["风险等级", "风险级别", "risk level", "风险", "风险状态"],
}

NUMERIC_FIELDS = {"progress", "effort_planned", "effort_remaining", "case_count", "case_executed"}
DATE_FIELDS = {"start_date", "end_date"}
TEXT_FIELDS = {"project", "dpm", "department", "phase", "status", "manager", "priority", "risk_level"}


def _normalize_col(name):
    name = str(name).strip()
    name = re.sub(r'[\s\-_\.]+', ' ', name)
    name = name.lower()
    return name


def _build_alias_lookup():
    lookup = {}
    for key, aliases in DEFAULT_ALIASES.items():
        for alias in aliases:
            norm = _normalize_col(alias)
            lookup[norm] = key
    return lookup


ALIAS_LOOKUP = _build_alias_lookup()


def infer_columns(df):
    mapping = {}
    confidence = {}

    for col in df.columns:
        col_str = str(col).strip()
        norm = _normalize_col(col_str)

        if norm in ALIAS_LOOKUP:
            target = ALIAS_LOOKUP[norm]
            mapping[col] = target
            confidence[col] = 0.95
            continue

        for key, aliases in DEFAULT_ALIASES.items():
            for alias in aliases:
                alias_norm = _normalize_col(alias)
                if norm == alias_norm or norm.startswith(alias_norm) or alias_norm.startswith(norm):
                    mapping[col] = key
                    confidence[col] = 0.85
                    break
            if col in mapping:
                break

        if col not in mapping:
            for key, aliases in DEFAULT_ALIASES.items():
                for alias in aliases:
                    alias_norm = _normalize_col(alias)
                    if alias_norm in norm or norm in alias_norm:
                        mapping[col] = key
                        confidence[col] = 0.7
                        break
                if col in mapping:
                    break

    for col in df.columns:
        if col not in mapping:
            detected = _detect_by_data(df[col])
            if detected:
                mapping[col] = detected
                confidence[col] = 0.5

    return mapping, confidence


def _detect_by_data(series):
    series = series.dropna()
    if len(series) < 3:
        return None

    sample = series.astype(str).str.strip()
    nums = pd.to_numeric(series, errors='coerce').dropna()

    if len(nums) > len(series) * 0.5:
        if nums.between(0, 1).all() and nums.nunique() > 2:
            return "progress"
        if nums.nunique() > 5 and nums.max() > 100:
            return "effort_planned"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "start_date"

    try:
        parsed = pd.to_datetime(series, errors='coerce', dayfirst=True)
        if parsed.notna().sum() > len(series) * 0.5:
            return "start_date"
    except:
        pass

    if len(sample) > 2:
        avg_len = sample.str.len().mean()
        if avg_len > 8:
            return "project"

    return None


def clean_data(df, column_mapping):
    df = df.copy()

    df = df.apply(lambda col: col.ffill() if col.dtype == object else col)

    for col, target in column_mapping.items():
        if target in NUMERIC_FIELDS:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        if target in DATE_FIELDS:
            try:
                parsed = pd.to_datetime(df[col], errors='coerce', dayfirst=True)
                if parsed.notna().sum() > 0:
                    df[col] = parsed
            except:
                pass

    return df


def aggregate_metrics(df, column_mapping):
    metrics = {}

    rev_map = {}
    for col, target in column_mapping.items():
        rev_map.setdefault(target, []).append(col)

    def best_col(target):
        cols = rev_map.get(target, [])
        if not cols:
            return None
        return cols[0]

    pcol = best_col("project")
    dpmcol = best_col("dpm")
    progress_col = best_col("progress")
    phase_col = best_col("phase")
    dept_col = best_col("department")
    ep_col = best_col("effort_planned")
    er_col = best_col("effort_remaining")
    cc_col = best_col("case_count")
    ce_col = best_col("case_executed")
    sd_col = best_col("start_date")
    ed_col = best_col("end_date")

    if pcol:
        metrics["project_count"] = df[pcol].nunique()

    if progress_col and pcol:
        risk_counts = {"normal": 0, "warning": 0, "high": 0}
        project_progress = []
        for idx, (proj_name, group) in enumerate(df.groupby(pcol)):
            prog_vals = group[progress_col].dropna()
            if len(prog_vals) == 0:
                continue
            # 与 progress_analyzer._scale_progress 保持一致的逐值缩放逻辑：
            # 值≤2视为小数格式需*100，值>2视为已百分比尺度保持不变
            prog_vals = prog_vals.apply(lambda v: round(v * 100, 2) if v <= 2.0 else round(v, 2))
            avg_prog = float(prog_vals.mean())
            pct = round(avg_prog, 1)
            if pct < 50:
                risk = "high"
            elif pct < 80:
                risk = "warning"
            else:
                risk = "normal"
            risk_counts[risk] += 1
            entry = {"project": str(proj_name), "progress": pct, "risk": risk, "deviation": 0}
            if phase_col:
                phases = group[phase_col].dropna().unique()
                if len(phases) > 0:
                    entry["phase"] = str(phases[0])
            if dpmcol:
                dpms = group[dpmcol].dropna().unique()
                if len(dpms) > 0:
                    entry["manager"] = str(dpms[0])
            project_progress.append(entry)

        metrics["project_progress"] = project_progress
        metrics["risk_counts"] = risk_counts
        metrics["total_projects"] = len(project_progress)
        metrics["normal"] = risk_counts["normal"]
        metrics["warning"] = risk_counts["warning"]
        metrics["high_risk"] = risk_counts["high"]
        metrics["health_rate"] = round(
            (risk_counts["normal"] * 100 + risk_counts["warning"] * 50) / max(len(project_progress), 1)
        )

    if dpmcol and ep_col and er_col:
        load_data = []
        for dpm_name, group in df.groupby(dpmcol):
            ep = group[ep_col].sum()
            er = group[er_col].sum()
            if ep > 0:
                load = round(float(er / ep * 100), 1)
            else:
                load = 0
            if ep > 0 or er > 0:
                load_data.append({"dpm": str(dpm_name), "load": load, "planned": int(ep), "remaining": int(er)})
        metrics["dept_load"] = load_data
        if load_data:
            metrics["avg_load"] = round(sum(d["load"] for d in load_data) / len(load_data), 1)
        else:
            metrics["avg_load"] = 0
    else:
        metrics["dept_load"] = []
        metrics["avg_load"] = 0

    if dept_col:
        dept_list = df[dept_col].dropna().unique()
        metrics["team_size"] = len(dept_list) if len(dept_list) > 0 else (df[dpmcol].nunique() if dpmcol else 0)
    elif dpmcol:
        metrics["team_size"] = df[dpmcol].nunique()
    else:
        metrics["team_size"] = 0

    if cc_col:
        metrics["total_planned_cases"] = int(df[cc_col].sum())
    if ce_col:
        metrics["total_executed_cases"] = int(df[ce_col].sum())

    if sd_col and ed_col:
        try:
            df_temp = df.copy()
            df_temp["_start"] = pd.to_datetime(df_temp[sd_col], errors='coerce', dayfirst=True)
            df_temp["_end"] = pd.to_datetime(df_temp[ed_col], errors='coerce', dayfirst=True)
            valid = df_temp.dropna(sub=["_start", "_end"])
            if len(valid) > 0:
                total_days = (valid["_end"] - valid["_start"]).dt.days.sum()
                metrics["total_days"] = int(total_days)
        except:
            pass

    if pcol:
        dpm_freq = df[dpmcol].value_counts() if dpmcol else None
        metrics["manager_name"] = str(dpm_freq.index[0]) if dpm_freq is not None else "未指定"

    try:
        if phase_col:
            phases = df[phase_col].dropna().unique()
            metrics["active_phases"] = len(phases)
        elif pcol:
            metrics["active_phases"] = min(len(metrics.get("project_progress", [])), 4)
    except:
        metrics["active_phases"] = 1

    return metrics


def parse_excel_intelligent(filepath):
    try:
        df = pd.read_excel(filepath, engine='openpyxl')
    except Exception as e:
        return {"success": False, "error": f"无法读取Excel文件: {str(e)}"}

    if df.empty:
        return {"success": False, "error": "Excel文件为空"}

    column_mapping, confidence = infer_columns(df)

    if not column_mapping:
        return {
            "success": False,
            "error": "未能识别任何列，请确保包含项目名称、进度等字段",
            "columns_detected": list(df.columns)
        }

    df = clean_data(df, column_mapping)
    metrics = aggregate_metrics(df, column_mapping)

    return {
        "success": True,
        "column_mapping": column_mapping,
        "confidence": {str(k): v for k, v in confidence.items()},
        "metrics": metrics,
        "columns_detected": list(df.columns),
        "row_count": len(df),
        "_raw_df": df
    }


def save_column_mapping(mapping):
    try:
        existing = {}
        if os.path.exists(COLUMN_MAPPING_DB):
            try:
                with open(COLUMN_MAPPING_DB, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except:
                existing = {}
        existing.update(mapping)
        with open(COLUMN_MAPPING_DB, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except:
        pass


def get_cached_mapping():
    if os.path.exists(COLUMN_MAPPING_DB):
        try:
            with open(COLUMN_MAPPING_DB, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def update_alias(user_col, mapped_field):
    aliases = DEFAULT_ALIASES.get(mapped_field, [])
    if user_col not in aliases:
        DEFAULT_ALIASES.setdefault(mapped_field, []).append(user_col)
    norm = _normalize_col(user_col)
    ALIAS_LOOKUP[norm] = mapped_field
    save_column_mapping({user_col: mapped_field})


def build_ai_prompt(data):
    summary = data.get("summary", {})
    # 总项目数按父级项目计算（total_projects已在parser中按_parent_project去重）
    total = summary.get("total_projects", 0)
    normal = summary.get("normal", 0)
    warning = summary.get("warning", 0)
    high_risk = summary.get("high_risk", 0)
    avg_load = summary.get("avg_load", 0)
    team_size = summary.get("team_size", 0)
    health = summary.get("health_rate", 0)

    dept_load = data.get("dept_load", [])
    remaining_effort_all = data.get("remaining_effort_all", [])
    project_tree = data.get("project_tree", {})

    # 从 project_tree 展开为独立任务行（每行一个独立任务，不按项目名聚合）
    all_tasks = []
    for parent_name, tree in project_tree.items():
        for sp in tree.get("sub_projects", []):
            all_tasks.append(sp)

    # 若 project_tree 为空（如智能解析路径），回退到 project_progress 聚合数据
    if not all_tasks:
        project_progress = data.get("project_progress", [])
        for idx, p in enumerate(project_progress):
            all_tasks.append({
                "name": p.get("project", "?"),
                "stage": p.get("phase", p.get("lane", "?")),
                "progress": p.get("progress", 0),
                "test_progress": p.get("test_progress", p.get("progress", 0)),
                "deviation": p.get("deviation", 0),
                "risk": p.get("risk", "normal"),
                "effort_planned": p.get("effort_planned", 0),
                "effort_remaining": p.get("effort_remaining", 0),
                "dpm": p.get("manager", ""),
                "deadline": p.get("deadline", ""),
                "main_plans": "-",
                "start_date": ""
            })

    # 构建任务级表格数据行（列必须与AI输出要求的表头严格对齐）
    project_rows = []
    for idx, task in enumerate(all_tasks):
        row_num = idx + 1
        task_name = task.get("name", "?")
        stage = task.get("stage", task.get("lane", "?"))
        progress_val = task.get("progress", 0)
        test_progress = task.get("test_progress", progress_val)
        deviation = task.get("deviation", 0)
        risk = task.get("risk", "normal")
        effort_planned = task.get("effort_planned", 0)
        effort_remaining = task.get("effort_remaining", 0)
        dpm = task.get("dpm", "")
        deadline = task.get("deadline", "")
        plan_name = task.get("main_plans", "")
        start_date = task.get("start_date", "")

        if risk == "high":
            risk_text = "高风险"
        elif risk == "warning":
            risk_text = "预警"
        else:
            risk_text = "正常"
        if deviation >= 0:
            deviation_text = f"滞后{deviation}%"
        else:
            deviation_text = f"超前{abs(deviation)}%"
        project_rows.append(
            f"[行{row_num}] {task_name} | {stage} | {progress_val}% | {deviation_text} | {dpm} | {deadline} | {risk_text}"
        )

    # 人力负载详情（全量排序）
    load_details = ""
    if remaining_effort_all:
        load_lines = []
        for d in remaining_effort_all:
            dpm_name = d.get("dpm", "?")
            planned = d.get("planned", 0)
            remaining = d.get("remaining", 0)
            rate = d.get("completion_rate", 0)
            proj_count = d.get("project_count", 0)
            load_lines.append(f"  - {dpm_name}: 预估{planned}人天, 剩余{remaining}人天, 完成率{rate}%, {proj_count}个项目")
        load_details = "\n".join(load_lines)
    elif dept_load:
        dept_sorted = sorted(dept_load, key=lambda x: x["load"], reverse=True)
        load_lines = [f"  - {d['dpm']}: 负载率{d['load']}%" for d in dept_sorted]
        load_details = "\n".join(load_lines)

    # 人力汇总
    total_effort_planned = sum(d.get("planned", 0) for d in remaining_effort_all)
    total_effort_remaining = sum(d.get("remaining", 0) for d in remaining_effort_all)
    overall_completion = round((total_effort_planned - total_effort_remaining) / total_effort_planned * 100, 1) if total_effort_planned > 0 else 0

    project_table = "\n".join(project_rows) if project_rows else "无项目数据"
    task_count = len(all_tasks)

    prompt = f"""项目进度风险分析数据：

数据来源：《阶段计划进度.xlsx》（上传时解析）
说明：进度数据为**执行进度**（如用例完成率）。执行进度偏差 = 应完成时间进度 - 实际执行进度，正数=滞后（执行慢于时间计划），负数=超前（执行快于时间计划）。

【概览】
- 总任务数：{task_count}
- 总项目数：{total}
- 正常项目：{normal}个
- 预警项目：{warning}个
- 高风险项目：{high_risk}个
- 整体健康度：{health}%
- 团队规模：{team_size}人
- 平均负载率：{avg_load}%
- 总预估人力：{total_effort_planned}人天
- 总剩余人力：{total_effort_remaining}人天
- 整体完成率：{overall_completion}%

【人力需求详情（按DPM负责人排序）】
{load_details or "无详细人力数据"}

【各任务数据（每行一个独立任务，未按项目聚合）】
任务名 | 当前阶段 | 执行进度(%) | 执行进度偏差(%) | 负责人 | 截止日期 | 风险等级
{project_table}

请根据以上任务数据，生成一份风险分析报告。报告必须包含一个 Markdown 表格，表头严格按照以下顺序和名称（共7列，与输入数据列一一对应）：
| 任务名（不含行号前缀） | 当前阶段 | 执行进度(%) | 执行进度偏差(%) | 负责人 | 截止日期 | 当前风险点与总体判断 |

【重要约束——必须遵守】
1. **仅基于以上提供的数据回答**，不得添加任何外部信息或假设
2. **禁止使用以下模糊词语**："好像"、"可能"、"也许"、"大概"、"似乎"、"一般来说"、"通常"
3. **必须引用具体数据**：例如"第3行数据显示XX任务进度为0%"、"根据概览，健康度为XX%"
4. **未在数据中明确体现的指标**，明确标注"数据中未提供"
5. 偏差正数=滞后（应完成时间进度 - 实际执行进度），负数=超前。用"滞后XX%"或"超前XX%"表示
6. 表格之后补充核心风险总结（2-3条）和改进建议（2-3条）
7. 语言简洁专业，使用陈述句，避免推测性表述
8. **【关键】表格中"当前风险点与总体判断"列严禁重复项目名和具体数值**，每行用一句话概括核心风险即可。例如：进度严重滞后需紧急干预 / 进展正常 / 尚未开始需尽快启动"""
    return prompt
