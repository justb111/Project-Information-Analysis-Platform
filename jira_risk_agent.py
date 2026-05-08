import requests
import json
import base64
import os
from dotenv import load_dotenv
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# ========== 1. 配置 Jira 连接信息（请通过 .env 文件设置） ==========
JIRA_CONFIG = {'server': os.getenv("JIRA_URL", "http://jira.transsion.com")}
JIRA_USERNAME = os.getenv("JIRA_USERNAME")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD")

if not JIRA_USERNAME or not JIRA_PASSWORD:
    print("❌ 错误: 请在 .env 文件中设置 JIRA_USERNAME 和 JIRA_PASSWORD")
    exit(1)

# ========== 2. 配置 AI 服务信息（请通过 .env 文件设置） ==========
AI_API_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1/chat/completions")
AI_API_KEY = os.getenv("AI_API_KEY")

if not AI_API_KEY:
    print("❌ 错误: 请在 .env 文件中设置 AI_API_KEY")
    exit(1)

# ========== 3. 指定要分析的项目 ==========
PROJECT_KEY = "X6840"   # 例如 "PROJ"，在 Jira 中项目的 key

# ========== 4. 从 Jira 获取项目数据 ==========
# 构造 Basic Auth 认证头（直接使用用户名和密码）
auth_str = f"{JIRA_USERNAME}:{JIRA_PASSWORD}"
auth_bytes = auth_str.encode("ascii")
base64_auth = base64.b64encode(auth_bytes).decode("ascii")

headers_jira = {
    "Authorization": f"Basic {base64_auth}",
    "Content-Type": "application/json"
}

# 获取指定项目的所有未关闭问题（示例：状态不为 Closed/Resolved 的）
# 你可以根据需求调整 JQL 查询语句
jql = f"project = {PROJECT_KEY}"
url = f"{JIRA_CONFIG['server']}/rest/api/2/search"
params = {
    "jql": jql,
    "fields": "summary,status,priority,issuetype,assignee,created",
    "maxResults": 50   # 最多获取 50 条，可根据需要调整
}

response = requests.get(url, headers=headers_jira, params=params, verify=False)

if response.status_code != 200:
    print(f"获取 Jira 数据失败: {response.status_code}")
    print(response.text)
    exit()

issues = response.json().get("issues", [])
if not issues:
    print("该项目没有未关闭的问题。")
    exit()

# 提取关键信息，用于 AI 分析
issue_summaries = []
for issue in issues:
    fields = issue["fields"]
    summary = fields["summary"]
    status = fields["status"]["name"]
    priority = fields["priority"]["name"] if fields.get("priority") else "无"
    issuetype = fields["issuetype"]["name"]
    assignee = fields["assignee"]["displayName"] if fields.get("assignee") else "未分配"
    created = fields["created"][:10]  # 只取日期部分
    issue_summaries.append(f"- {summary} (类型:{issuetype}, 状态:{status}, 优先级:{priority}, 负责人:{assignee}, 创建日期:{created})")

# 将所有问题文本合并
issues_text = "\n".join(issue_summaries)

# ========== 5. 调用 AI 分析风险 ==========
prompt = f"""
你是一个项目风险分析师。请根据以下 Jira 中未关闭的任务列表，评估该项目的风险状态（高风险、中风险、低风险），并给出简要的分析理由和建议。

任务列表：
{issues_text}

请输出格式：
风险等级：
分析理由：
建议：
"""

headers_ai = {
    "Authorization": f"Bearer {AI_API_KEY}",
    "Content-Type": "application/json"
}

payload_ai = {
    "model": "gpt-5.4-thinking-high",   # 如果是其他模型，改成对应的名称
    "messages": [
        {"role": "system", "content": "你是一个专业的项目风险分析师。"},
        {"role": "user", "content": prompt}
    ],
    "temperature": 0.7
}

ai_response = requests.post(AI_API_URL, headers=headers_ai, json=payload_ai)

if ai_response.status_code != 200:
    print(f"调用 AI 失败: {ai_response.status_code}")
    print(ai_response.text)
    exit()

result = ai_response.json()
# 根据返回结构提取内容（OpenAI 格式）
analysis = result["choices"][0]["message"]["content"]

# ========== 6. 输出结果 ==========
print("=" * 50)
print("项目风险分析结果")
print("=" * 50)
print(analysis)