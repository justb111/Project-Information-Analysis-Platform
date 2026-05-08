import pandas as pd
import numpy as np
from datetime import datetime, date


def _find_column(df, *keywords):
    for col in df.columns:
        col_lower = str(col).lower().strip()
        for kw in keywords:
            if kw in col_lower:
                return col
    return None


def _safe_float(val):
    try:
        v = float(val)
        return v if not np.isnan(v) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    try:
        v = float(val)
        return int(v) if not np.isnan(v) else 0
    except (ValueError, TypeError):
        return 0


def _safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()


def _deviation_color(deviation):
    if deviation >= 30:
        return "danger"
    elif deviation >= 25:
        return "deep-orange"
    elif deviation >= 15:
        return "warning"
    return "normal"


def _deviation_level(deviation):
    if deviation >= 30:
        return "high"
    elif deviation >= 15:
        return "warning"
    return "normal"


def _risk_label(risk):
    return {"high": "高风险", "warning": "预警", "normal": "正常"}.get(risk, "未知")


def _risk_by_progress(progress_pct):
    if progress_pct < 50:
        return "high"
    elif progress_pct < 80:
        return "warning"
    return "normal"


def _risk_by_deviation(deviation):
    if deviation >= 30:
        return "high"
    elif deviation >= 10:
        return "warning"
    return "normal"


def _risk_judgment_text(risk_level, deviation):
    if risk_level == "high":
        if deviation > 50:
            return "进度严重滞后，需紧急追加资源"
        return "进度严重滞后，需追加资源"
    elif risk_level == "warning":
        return "进度滞后，需重点关注"
    return "进度正常"


# ====== 9 阶段泳道映射 ======
PHASE_MAP = {
    "P1": "散件测试",
    "PR0": "硬件设计",
    "PR1": "硬件验证",
    "PR2": "样机试产",
    "STR1": "软件评审", "STR2": "软件评审", "STR3": "软件评审",
    "STR4": "系统测试", "STR4-1": "系统测试", "STR4-2": "系统测试",
    "STR4A": "预量产", "STR5": "预量产", "PIR": "预量产",
    "MPR": "大批量产", "MP": "大批量产", "STR6": "大批量产",
    "MR": "预量产"
}

PHASE_ORDER = ["散件测试", "硬件设计", "硬件验证", "样机试产", "软件评审", "系统测试", "预量产", "大批量产"]

PHASE_LANE_MAP = {
    "散件测试": "散件测试",
    "硬件设计": "硬件设计",
    "硬件验证": "硬件验证",
    "样机试产": "样机试产",
    "软件评审": "软件评审",
    "系统测试": "系统测试",
    "预量产": "预量产",
    "大批量产": "大批量产"
}

PHASE_ICONS = {
    "散件测试": "🔩",
    "硬件设计": "🎨",
    "硬件验证": "🔍",
    "样机试产": "🏭",
    "软件评审": "📝",
    "系统测试": "🧪",
    "预量产": "⚙️",
    "大批量产": "📦",
}

# 小批试产：暂无可忽略或归入预量产
# 特殊任务关键词
SPECIAL_TASK_KEYWORDS = ["专项", "专题", "验证"]


def _normalize_phase(phase_str):
    raw = _safe_str(phase_str)
    if not raw:
        return "其他"
    return PHASE_MAP.get(raw, "其他")


def _parse_project_name(proj_name):
    """解析项目名，返回 (parent_project, is_special)
    
    - CN6-OP → parent=CN6, 子项目=CN6-OP
    - X6879-OP → parent=X6879, 子项目=X6879-OP
    - tOS16.1, tOS16.2 → parent=tOS, 子项目=原项目名
    - 其他 → parent=原项目名, 子项目=原项目名
    """
    name = _safe_str(proj_name)
    if not name:
        return (name, False)

    # tOS 系列
    if name.upper().startswith("TOS"):
        return ("tOS", False)

    # CN6-OP, X6879-OP 等格式
    parts = name.split("-")
    if len(parts) >= 2:
        parent = parts[0]
        # 检查是否属于特殊任务
        for kw in SPECIAL_TASK_KEYWORDS:
            if kw in name:
                return (parent, True)
        return (parent, False)

    # 检查是否属于特殊任务
    for kw in SPECIAL_TASK_KEYWORDS:
        if kw in name:
            return (name, True)

    return (name, False)


def _get_lane_from_raw_stage(raw_stage):
    """将原始阶段名映射到泳道名"""
    return PHASE_MAP.get(_safe_str(raw_stage), "其他")


def parse_progress_excel(filepath):
    xl = pd.ExcelFile(filepath)
    if "阶段计划进度" in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name="阶段计划进度")
    else:
        print(f"[progress_analyzer] 未找到'阶段计划进度'工作表，使用第一个工作表: {xl.sheet_names[0]}")
        df = pd.read_excel(xl, sheet_name=xl.sheet_names[0])
    df = df.dropna(how="all")
    df = df.reset_index(drop=True)

    cols = list(df.columns)
    print(f"[progress_analyzer] 原始列名: {cols}")

    col_project = _find_column(df, "项目", "project", "项目名称", "项目名")
    col_dpm = _find_column(df, "dpm", "负责人", "manager", "owner", "责任人", "项目经理")
    col_phase = _find_column(df, "阶段", "phase", "泳道", "stage")
    col_progress = _find_column(df, "进度", "progress", "完成进度", "完成率", "完成%")

    print(f"[progress_analyzer] 列检测: 项目={col_project}, DPM={col_dpm}, 阶段={col_phase}, 进度={col_progress}")

    if col_project is None:
        raise ValueError("未找到项目列")

    col_planned = _find_column(df, "用例数", "计划用例", "planned", "计划数")
    col_executed = _find_column(df, "已执行", "executed", "实际用例", "已执行用例", "完成数")
    col_deadline = _find_column(df, "截止日期", "deadline", "计划完成", "完成日期")
    col_deviation = _find_column(df, "偏差", "deviation", "偏差率")
    col_department = _find_column(df, "部门", "department", "dept", "组别", "分组")
    col_person = _find_column(df, "姓名", "人员", "name", "person", "成员")
    col_risk = _find_column(df, "风险", "risk", "问题", "issue")
    col_effort_planned = _find_column(df, "预估人力", "人力需求", "计划人力", "effort", "计划工时", "预估人力需求")
    col_effort_remaining = _find_column(df, "剩余人力", "remaining", "剩余工时", "剩余人力需求")
    col_plan_name = _find_column(df, "计划名称", "计划名", "plan", "任务名称")
    col_creation_time = _find_column(df, "创建时间", "创建", "creation", "create_time")
    col_start_date = _find_column(df, "开始日期", "start", "开始时间", "计划开始")

    # 过滤掉"合计"汇总行
    df = df[df[col_project].apply(_safe_str) != "合计"].copy()
    df = df.reset_index(drop=True)

    # --- Core Data: Progress ---
    if col_progress is not None:
        progress_raw = df[col_progress].apply(_safe_float)
        if progress_raw.max() <= 2.0:
            progress_vals = np.round(progress_raw * 100, 2)
        else:
            progress_vals = progress_raw
    elif col_planned is not None and col_executed is not None:
        planned = df[col_planned].apply(_safe_int)
        executed = df[col_executed].apply(_safe_int)
        progress_vals = np.where(planned > 0, (executed / planned * 100), 0.0)
        progress_vals = np.round(progress_vals, 2)
    else:
        progress_vals = np.zeros(len(df))

    # --- Deviation ---
    if col_deviation is not None:
        deviation_vals = df[col_deviation].apply(_safe_float)
    else:
        deviation_vals = np.round(100.0 - progress_vals, 2)
        deviation_vals = np.clip(deviation_vals, 0, 100)

    if col_planned is not None and col_executed is not None:
        planned = df[col_planned].apply(_safe_int)
        executed = df[col_executed].apply(_safe_int)
    else:
        planned = pd.Series([0] * len(df))
        executed = pd.Series([0] * len(df))

    raw_phase_col = col_phase
    df["_project"] = df[col_project].apply(_safe_str)
    df["_planned"] = planned
    df["_executed"] = executed
    df["_progress"] = progress_vals
    df["_deviation"] = deviation_vals
    df["_raw_stage"] = df[raw_phase_col].apply(_safe_str) if raw_phase_col is not None else ""
    df["_lane"] = df["_raw_stage"].apply(_get_lane_from_raw_stage) if raw_phase_col is not None else "其他"
    df["_dpm"] = df[col_dpm].apply(_safe_str) if col_dpm is not None else ""
    df["_risk_text"] = df[col_risk].apply(_safe_str) if col_risk is not None else ""
    df["_effort_planned"] = df[col_effort_planned].apply(_safe_float) if col_effort_planned is not None else 0
    df["_effort_remaining"] = df[col_effort_remaining].apply(_safe_float) if col_effort_remaining is not None else 0
    df["_creation_time"] = df[col_creation_time].apply(_safe_str) if col_creation_time is not None else ""
    df["_start_date"] = df[col_start_date].apply(_safe_str) if col_start_date is not None else ""
    df["_deadline"] = df[col_deadline].apply(_safe_str) if col_deadline is not None else ""

    # 解析父级项目
    df["_parent_project"] = df["_project"].apply(lambda x: _parse_project_name(x)[0])
    df["_is_special"] = df["_project"].apply(lambda x: _parse_project_name(x)[1])

    # --- Project-level aggregation ---
    project_groups = df.groupby("_project")
    project_progress_list = []
    total_planned_all = 0
    total_executed_all = 0
    project_risk_counts = {"normal": 0, "warning": 0, "high": 0}

    for proj_name, group in project_groups:
        p_sum = group["_planned"].sum()
        e_sum = group["_executed"].sum()
        avg_progress = round(group["_progress"].mean(), 2)
        avg_deviation = round(group["_deviation"].mean(), 2)
        risk = _risk_by_progress(avg_progress)
        project_risk_counts[risk] += 1
        total_planned_all += p_sum
        total_executed_all += e_sum

        lanes_in_project = group["_lane"].value_counts()
        main_lane = lanes_in_project.index[0] if len(lanes_in_project) > 0 else "其他"

        dpms_in_project = group["_dpm"].value_counts()
        main_dpm = dpms_in_project.index[0] if len(dpms_in_project) > 0 else ""

        risk_descriptions = []
        if col_risk is not None:
            risk_descriptions = group["_risk_text"].dropna().replace("", np.nan).dropna().unique().tolist()

        raw_stages = group["_raw_stage"].dropna().unique() if raw_phase_col is not None else []
        raw_stage = str(raw_stages[0]) if len(raw_stages) > 0 else "其他"

        test_progress = round(e_sum / p_sum * 100, 1) if p_sum > 0 else 0

        delay_plans = []
        if col_plan_name is not None:
            for _, row in group.iterrows():
                if row["_progress"] < 50 and _safe_str(row[col_plan_name]):
                    delay_plans.append(str(row[col_plan_name]).strip())
        main_delay_plans = ";".join(delay_plans[:5]) if delay_plans else "-"

        risk_judgment = _risk_judgment_text(risk, avg_deviation)

        parent_proj = group["_parent_project"].iloc[0]
        is_special = bool(group["_is_special"].any())

        project_progress_list.append({
            "project": proj_name,
            "phase": raw_stage,
            "lane": main_lane,
            "raw_stage": raw_stage,
            "progress": avg_progress,
            "test_progress": test_progress,
            "deviation": avg_deviation,
            "main_delay_plans": main_delay_plans,
            "risk_judgment": risk_judgment,
            "risk": risk,
            "risk_label": _risk_label(risk),
            "planned": int(p_sum),
            "executed": int(e_sum),
            "effort_planned": round(group["_effort_planned"].sum(), 1) if col_effort_planned is not None else 0,
            "effort_remaining": round(group["_effort_remaining"].sum(), 1) if col_effort_remaining is not None else 0,
            "manager": main_dpm,
            "tasks_count": len(group),
            "risks": risk_descriptions[:5],
            "parent_project": parent_proj,
            "is_special": is_special
        })

    project_progress_list.sort(key=lambda x: x["deviation"], reverse=True)

    # --- 构建新数据结构 ---

    # 1. Swimlane (9 个泳道)
    swimlane = {}
    for lane_name in PHASE_ORDER:
        swimlane[lane_name] = []
    swimlane["其他"] = []

    for proj in project_progress_list:
        p = proj["lane"]
        if p in swimlane:
            swimlane[p].append(proj)
        else:
            swimlane["其他"].append(proj)

    swimlane = {k: v for k, v in swimlane.items() if v}

    # 2. 剩余人力需求全量排序（按 DPM 聚合）
    remaining_effort_all = []
    dpm_effort_summary = {}
    if col_dpm is not None and col_effort_remaining is not None:
        dpm_remaining = df.groupby("_dpm")["_effort_remaining"].sum()
        dpm_planned = df.groupby("_dpm")["_effort_planned"].sum()
        for dpm_name in dpm_remaining.index:
            dpm_name = _safe_str(dpm_name)
            if not dpm_name:
                continue
            rem_sum = dpm_remaining[dpm_name]
            plan_sum = dpm_planned.get(dpm_name, 0)
            completion_rate = round((plan_sum - rem_sum) / plan_sum * 100, 1) if plan_sum > 0 else 0
            project_count = len(df[df["_dpm"] == dpm_name]["_parent_project"].unique())
            remaining_effort_all.append({
                "dpm": dpm_name,
                "remaining": round(rem_sum, 1),
                "planned": round(plan_sum, 1),
                "completion_rate": completion_rate,
                "project_count": project_count
            })
            dpm_effort_summary[dpm_name] = {
                "planned": round(plan_sum, 1),
                "remaining": round(rem_sum, 1),
                "completion_rate": completion_rate,
                "project_count": project_count
            }
        remaining_effort_all.sort(key=lambda x: x["remaining"], reverse=True)

    # 3. DPM → 项目映射 (dpm_to_projects)
    dpm_to_projects = {}
    if col_dpm is not None:
        for dpm_name, group in df.groupby("_dpm"):
            dpm_name = _safe_str(dpm_name)
            if not dpm_name:
                continue
            # 父级项目列表（去重）
            parent_projects = group["_parent_project"].unique().tolist()
            parent_projects = [p for p in parent_projects if p]

            # 特殊任务列表
            special_df = group[group["_is_special"] == True]
            special_tasks = []
            for _, row in special_df.iterrows():
                special_tasks.append({
                    "name": _safe_str(row["_project"]),
                    "remaining_effort": round(float(row["_effort_remaining"]), 1) if col_effort_remaining is not None else 0,
                    "lane": _safe_str(row["_lane"]),
                    "raw_stage": _safe_str(row["_raw_stage"]),
                    "progress": round(float(row["_progress"]), 2),
                    "deviation": round(float(row["_deviation"]), 2)
                })

            dpm_to_projects[dpm_name] = {
                "parent_projects": parent_projects,
                "special_tasks": special_tasks
            }

    # 4. 项目树 (project_tree) — 大项目 → 子项目列表
    project_tree = {}
    # 按 parent_project 分组
    for parent_name, group in df.groupby("_parent_project"):
        parent_name = _safe_str(parent_name)
        if not parent_name:
            continue
        # 子项目按原始项目名分组聚合
        sub_projects = []
        for sub_name, sub_group in group.groupby("_project"):
            sub_name = _safe_str(sub_name)
            if not sub_name:
                continue
            p_sum = sub_group["_planned"].sum()
            e_sum = sub_group["_executed"].sum()
            avg_progress = round(sub_group["_progress"].mean(), 2)
            avg_deviation = round(sub_group["_deviation"].mean(), 2)
            risk = _risk_by_deviation(avg_deviation)
            lanes = sub_group["_lane"].value_counts()
            main_lane = lanes.index[0] if len(lanes) > 0 else "其他"
            raw_stages = sub_group["_raw_stage"].dropna().unique()
            raw_stage = str(raw_stages[0]) if len(raw_stages) > 0 else "其他"
            test_progress = round(e_sum / p_sum * 100, 1) if p_sum > 0 else 0
            delay_plans = []
            if col_plan_name is not None:
                for _, row in sub_group.iterrows():
                    if row["_progress"] < 50 and _safe_str(row[col_plan_name]):
                        delay_plans.append(str(row[col_plan_name]).strip())
            main_delay_plans = ";".join(delay_plans[:5]) if delay_plans else "-"

            sub_projects.append({
                "name": sub_name,
                "lane": main_lane,
                "stage": raw_stage,
                "test_progress": test_progress,
                "deviation": avg_deviation,
                "main_plans": main_delay_plans,
                "risk": risk,
                "progress": avg_progress,
                "planned": int(p_sum),
                "executed": int(e_sum),
                "effort_planned": round(sub_group["_effort_planned"].sum(), 1) if col_effort_planned is not None else 0,
                "effort_remaining": round(sub_group["_effort_remaining"].sum(), 1) if col_effort_remaining is not None else 0,
                "creation_time": sub_group["_creation_time"].iloc[0] if col_creation_time is not None else "",
                "start_date": sub_group["_start_date"].iloc[0] if col_start_date is not None else "",
                "deadline": sub_group["_deadline"].iloc[0] if col_deadline is not None else "",
                "dpm": sub_group["_dpm"].iloc[0] if col_dpm is not None else ""
            })

        if sub_projects:
            project_tree[parent_name] = {
                "sub_projects": sub_projects
            }

    # 5. 泳道项目映射 (lane_projects) — 泳道 → 父级项目列表（去重）
    # 使用数据中的实际阶段名（raw_stage）作为泳道名
    lane_projects = {}
    raw_phase_order = df["_raw_stage"].value_counts().index.tolist() if raw_phase_col is not None else []
    for phase_name in raw_phase_order:
        phase_df = df[df["_raw_stage"] == phase_name]
        if not phase_df.empty:
            parent_set = set()
            for p in phase_df["_parent_project"].unique():
                pp = _safe_str(p)
                if pp:
                    parent_set.add(pp)
            lane_projects[phase_name] = {
                "parent_projects": sorted(list(parent_set))
            }

    # --- Build risks list for risk panel ---
    risks_list = []
    for proj in project_progress_list:
        if proj["risk"] in ("high", "warning"):
            tags_list = []
            if proj["deviation"] >= 30:
                tags_list.append(f"偏差{proj['deviation']}% ⚠️ 严重滞后")
            elif proj["deviation"] >= 15:
                tags_list.append(f"偏差{proj['deviation']}% 进度滞后")
            if proj["risks"]:
                for r in proj["risks"][:3]:
                    tags_list.append(f"风险: {r}")

            risk_item = {
                "project": proj["project"],
                "lane": proj["lane"],
                "deviation": proj["deviation"],
                "progress": proj["progress"],
                "level": proj["risk"],
                "risk_label": proj["risk_label"],
                "relatedPerson": proj["manager"],
                "personLoad": 0,
                "tasks_count": proj["tasks_count"],
                "risk_tags": tags_list,
                "suggestion": _generate_risk_suggestion(proj)
            }
            risks_list.append(risk_item)

    # --- Summary ---
    total_projects = len(project_progress_list)
    total_tasks = sum(p["tasks_count"] for p in project_progress_list)
    normal_count = project_risk_counts["normal"]
    warning_count = project_risk_counts["warning"]
    high_count = project_risk_counts["high"]
    health_rate = round(
        (normal_count * 100 + warning_count * 50) / total_projects if total_projects > 0 else 0
    )
    unique_dpms = df["_dpm"].dropna().replace("", np.nan).dropna().unique()
    team_size = len(unique_dpms)

    normal_pct = round(normal_count / total_projects * 100) if total_projects > 0 else 0
    warning_pct = round(warning_count / total_projects * 100) if total_projects > 0 else 0
    high_pct = round(high_count / total_projects * 100) if total_projects > 0 else 0

    dpm_freq = df["_dpm"].value_counts()
    manager_name = str(dpm_freq.index[0]) if len(dpm_freq) > 0 and pd.notna(dpm_freq.index[0]) else "未指定"

    active_phases = [p for p in PHASE_ORDER if p in swimlane]

    print(f"[progress_analyzer] === 数据校验 ===")
    print(f"[progress_analyzer] 项目总数: {total_projects}")
    print(f"[progress_analyzer] 任务总数: {total_tasks}")
    print(f"[progress_analyzer] DPM人数: {team_size}")
    print(f"[progress_analyzer] 泳道: {list(swimlane.keys())}")
    print(f"[progress_analyzer] 正常/预警/高风险: {normal_count}/{warning_count}/{high_count}")
    print(f"[progress_analyzer] 健康度: {health_rate}%")
    print(f"[progress_analyzer] 负责人: {manager_name}")
    if project_progress_list:
        sample = project_progress_list[0]
        print(f"[progress_analyzer] 样本项目: {sample['project']}, 进度={sample['progress']}%, 泳道={sample['lane']}")

    summary = {
        "total_projects": total_projects,
        "normal": normal_count,
        "warning": warning_count,
        "high_risk": high_count,
        "health_rate": health_rate,
        "team_size": team_size,
        "total_planned": int(total_planned_all),
        "total_executed": int(total_executed_all),
        "total_tasks": total_tasks,
        "update_date": date.today().isoformat(),
        "normal_pct": normal_pct,
        "warning_pct": warning_pct,
        "high_pct": high_pct,
        "manager_name": manager_name,
        "active_phases": len(active_phases)
    }

    # 保留兼容旧前端的字段
    dept_load_list = []
    hr_members = []
    if col_dpm is not None:
        dpm_groups = df.groupby("_dpm")
        for dpm_name, group in dpm_groups:
            dpm_name = _safe_str(dpm_name)
            if not dpm_name:
                continue
            ep_sum = group["_effort_planned"].sum()
            er_sum = group["_effort_remaining"].sum()
            load_val = round((er_sum / ep_sum * 100) if ep_sum > 0 else 0, 2)
            dept_load_list.append({
                "dpm": dpm_name,
                "headcount": len(group),
                "load": load_val,
                "members": []
            })
            hr_members.append({
                "name": dpm_name,
                "department": "项目部",
                "load": load_val
            })
        dept_load_list.sort(key=lambda x: x["load"], reverse=True)
        hr_members.sort(key=lambda x: x["load"], reverse=True)

    # --- Trend ---
    trend_list = []
    if col_deadline is not None:
        date_col = col_deadline
        df_temp = df.copy()
        df_temp[date_col] = pd.to_datetime(df_temp[date_col], errors="coerce")
        df_temp = df_temp.dropna(subset=[date_col])
        if not df_temp.empty:
            df_temp["_date"] = df_temp[date_col].dt.date
            trend_groups = df_temp.groupby("_date").agg(
                planned_sum=("_planned", "sum"),
                executed_sum=("_executed", "sum")
            ).reset_index()
            for _, row in trend_groups.iterrows():
                trend_list.append({
                    "date": str(row["_date"]),
                    "planned": int(row["planned_sum"]),
                    "executed": int(row["executed_sum"])
                })
            trend_list.sort(key=lambda x: x["date"])

    return {
        "summary": summary,
        "project_progress": project_progress_list,
        "swimlane": swimlane,
        "dept_load": dept_load_list,
        "hr_members": hr_members,
        "trend": trend_list,
        "risks": risks_list,
        "remaining_effort_all": remaining_effort_all,
        "dpm_effort_summary": dpm_effort_summary,
        "dpm_to_projects": dpm_to_projects,
        "project_tree": project_tree,
        "lane_projects": lane_projects,
        "phase_order": raw_phase_order
    }


def _generate_risk_suggestion(proj):
    deviation = proj["deviation"]
    if deviation >= 30:
        return f"项目{proj['project']}偏差{deviation}%，严重滞后，建议立即开展专项攻关，增加人力投入，重新评估项目计划"
    elif deviation >= 15:
        return f"项目{proj['project']}进度滞后(偏差{deviation}%)，建议加强进度跟踪，识别瓶颈环节，适当调配资源"
    return ""


def analyze_with_intelligence(filepath):
    try:
        from excel_parser import parse_excel_intelligent

        result = parse_excel_intelligent(filepath)
        if result.get("success"):
            metrics = result["metrics"]

            summary = {
                "total_projects": metrics.get("total_projects", 0),
                "normal": metrics.get("normal", 0),
                "warning": metrics.get("warning", 0),
                "high_risk": metrics.get("high_risk", 0),
                "health_rate": metrics.get("health_rate", 0),
                "team_size": metrics.get("team_size", 0),
                "total_tasks": 0,
                "manager_name": metrics.get("manager_name", "未指定"),
                "active_phases": metrics.get("active_phases", 0),
                "update_date": date.today().isoformat(),
                "normal_pct": round(metrics.get("normal", 0) / max(metrics.get("total_projects", 1), 1) * 100),
                "warning_pct": round(metrics.get("warning", 0) / max(metrics.get("total_projects", 1), 1) * 100),
                "high_pct": round(metrics.get("high_risk", 0) / max(metrics.get("total_projects", 1), 1) * 100),
                "total_planned": metrics.get("total_planned_cases", 0),
                "total_executed": metrics.get("total_executed_cases", 0)
            }

            project_progress_list = []
            for p in metrics.get("project_progress", []):
                project_progress_list.append({
                    "project": p["project"],
                    "progress": p["progress"],
                    "risk": p["risk"],
                    "risk_label": _risk_label(p["risk"]),
                    "lane": p.get("phase", "其他"),
                    "manager": p.get("manager", ""),
                    "deviation": 0,
                    "planned": 0,
                    "executed": 0,
                    "tasks_count": 0,
                    "risk_desc": ""
                })

            swimlane = {}
            for p in project_progress_list:
                lane = p["lane"]
                if lane not in swimlane:
                    swimlane[lane] = []
                swimlane[lane].append(p)

            dept_load_list = [{"dpm": d["dpm"], "load": d["load"]} for d in metrics.get("dept_load", [])]

            return {
                "summary": summary,
                "project_progress": project_progress_list,
                "swimlane": swimlane,
                "dept_load": dept_load_list,
                "hr_members": [],
                "trend": [],
                "risks": [],
                "remaining_effort_top3": [],
                "remaining_effort_all": [],
                "dpm_effort_summary": {},
                "dpm_to_projects": {},
                "project_tree": {},
                "lane_projects": {},
                "_column_mapping": result.get("column_mapping", {}),
                "_columns_detected": result.get("columns_detected", [])
            }

        print(f"[intelligent] 智能解析失败: {result.get('error', '未知错误')}，回退到标准解析")
        return None
    except ImportError:
        print("[intelligent] excel_parser 模块不可用，回退到标准解析")
        return None
    except Exception as e:
        print(f"[intelligent] 智能解析异常: {e}，回退到标准解析")
        return None
