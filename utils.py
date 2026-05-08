import json
import os
import requests

# AI调用配置（从环境变量读取，保持与主文件一致）
AI_BASE_URL = "https://hk-intra-paas.transsion.com/tranai-proxy/v1"

# AI配置 - 必须通过环境变量设置（全局变量，可能在导入时未设置）
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.4")
X_USER_NO = os.getenv("X_USER_NO", "")
X_USER_NAME = os.getenv("X_USER_NAME", "")
X_USER_DEPT_NAME = os.getenv("X_USER_DEPT_NAME", "")

# 配置验证（仅在直接运行utils时检查）
if __name__ == "__main__":
    if not AI_API_KEY:
        print("❌ 错误: AI_API_KEY 环境变量未设置!")
        print("💡 请配置AI_API_KEY环境变量")
        exit(1)


def call_ai_api(messages, system_prompt=None, temperature=0.7, stream=True, max_retries=3, retry_delay=5, max_tokens=None, tools=None, tool_choice=None):
    """统一的AI API调用函数"""
    import sys
    
    # 在函数内部动态获取环境变量，确保获取最新值
    ai_api_key = os.getenv("AI_API_KEY", AI_API_KEY)
    ai_model = os.getenv("AI_MODEL", AI_MODEL)
    x_user_no = os.getenv("X_USER_NO", X_USER_NO)
    x_user_name = os.getenv("X_USER_NAME", X_USER_NAME)
    x_user_dept_name = os.getenv("X_USER_DEPT_NAME", X_USER_DEPT_NAME)
    
    # 强制使用服务器共享身份，确保所有用户都能访问AI服务
    # 测试表明即使使用空身份也能成功，但为了规范使用服务器身份
    if not x_user_no or x_user_no == "18654794":  # 如果是空值或个人身份，使用服务器身份
        x_user_no = "JIRA_RISK_SERVER"
        x_user_name = "Jira风险分析服务器"
        x_user_dept_name = "公共分析服务"
    
    # 验证关键变量
    if not ai_api_key:
        print(f"[AI_ERROR] AI_API_KEY 未设置! 当前值: {repr(ai_api_key)}", file=sys.stderr)
        return None
    if not x_user_no:
        print(f"[AI_ERROR] X_USER_NO 未设置! 当前值: {repr(x_user_no)}", file=sys.stderr)
    
    url = f"{AI_BASE_URL}/chat/completions"
    
    # 对中文请求头进行编码处理
    def encode_header(value):
        if isinstance(value, str):
            return value.encode('utf-8').decode('latin-1', errors='ignore')
        return value
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ai_api_key}",
        "X-USER-NO": x_user_no,
        "X-USER-NAME": encode_header(x_user_name),
        "X-USER-DEPT-NAME": encode_header(x_user_dept_name),
    }
    
    # 构建消息列表
    if system_prompt:
        all_messages = [{"role": "system", "content": system_prompt}] + messages
    else:
        all_messages = messages
    
    payload = {
        "model": ai_model,
        "messages": all_messages,
        "temperature": temperature,
        "stream": stream,
    }
    
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    # Function Calling 支持
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    
    # 计算请求数据大小
    payload_str = json.dumps(payload, ensure_ascii=False)
    payload_size = len(payload_str.encode('utf-8'))
    
    # 详细的调试信息
    print(f"[AI_DEBUG] 开始AI服务调用，URL: {url}", file=sys.stderr)
    print(f"[AI_DEBUG] 请求数据大小: {payload_size} 字节 ({payload_size/1024:.1f} KB)", file=sys.stderr)
    print(f"[AI_DEBUG] 消息数量: {len(all_messages)}", file=sys.stderr)
    if all_messages:
        content_len = len(all_messages[-1].get('content', '')) if all_messages[-1].get('content') else 0
        print(f"[AI_DEBUG] 最后一条消息内容长度: {content_len} 字符", file=sys.stderr)
    if max_tokens is not None:
        print(f"[AI_DEBUG] 输出max_tokens限制: {max_tokens}", file=sys.stderr)
    print(f"[AI_DEBUG] 流式模式: {stream}, 超时: 600秒", file=sys.stderr)
    print(f"[AI_DEBUG] 请求头: Authorization={headers.get('Authorization', '')[:20]}..., X-USER-NO={headers.get('X-USER-NO', '')}", file=sys.stderr)
    print(f"[AI_DEBUG] 动态获取的变量: AI_API_KEY长度={len(ai_api_key)}, X_USER_NO={x_user_no}, X_USER_NAME长度={len(x_user_name) if x_user_name else 0}", file=sys.stderr)
    
    # 重试机制
    last_exception = None
    last_status_code = None
    last_response_text = None
    
    for attempt in range(max_retries):
        try:
            print(f"[AI_DEBUG] AI调用尝试 {attempt+1}/{max_retries}", file=sys.stderr)
            response = requests.post(url, headers=headers, json=payload, stream=stream, timeout=600)  # 增加到600秒
            
            last_status_code = response.status_code
            print(f"[AI_DEBUG] AI响应状态码: {response.status_code}", file=sys.stderr)
            
            if response.status_code == 200:
                print(f"[AI_DEBUG] AI服务调用成功!", file=sys.stderr)
                return response
            else:
                last_response_text = response.text[:500]
                print(f"[AI_DEBUG] AI响应非200: 状态码={response.status_code}", file=sys.stderr)
                print(f"[AI_DEBUG] 错误响应预览: {response.text[:200]}", file=sys.stderr)
                
                if attempt < max_retries - 1:
                    print(f"[AI_DEBUG] 等待 {retry_delay} 秒后重试...", file=sys.stderr)
                    import time
                    time.sleep(retry_delay)
                else:
                    print(f"[AI_DEBUG] 所有重试失败，最终状态码: {response.status_code}", file=sys.stderr)
                    return None
                    
        except requests.exceptions.Timeout as e:
            last_exception = e
            print(f"[AI_DEBUG] AI请求超时: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                print(f"[AI_DEBUG] 等待 {retry_delay} 秒后重试...", file=sys.stderr)
                import time
                time.sleep(retry_delay)
            else:
                print(f"[AI_DEBUG] 所有重试失败，最终错误: 超时", file=sys.stderr)
                return None
                
        except requests.exceptions.ConnectionError as e:
            last_exception = e
            print(f"[AI_DEBUG] AI连接错误: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                print(f"[AI_DEBUG] 等待 {retry_delay} 秒后重试...", file=sys.stderr)
                import time
                time.sleep(retry_delay)
            else:
                print(f"[AI_DEBUG] 所有重试失败，最终错误: 连接错误", file=sys.stderr)
                return None
                
        except requests.exceptions.RequestException as e:
            last_exception = e
            print(f"[AI_DEBUG] AI请求异常: {type(e).__name__}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                print(f"[AI_DEBUG] 等待 {retry_delay} 秒后重试...", file=sys.stderr)
                import time
                time.sleep(retry_delay)
            else:
                print(f"[AI_DEBUG] 所有重试失败，最终错误: {type(e).__name__}", file=sys.stderr)
                return None
    
    # 所有重试都失败后的汇总
    print(f"[AI_ERROR] AI服务调用最终失败详情:", file=sys.stderr)
    if last_status_code:
        print(f"[AI_ERROR] - 最后状态码: {last_status_code}", file=sys.stderr)
    if last_response_text:
        print(f"[AI_ERROR] - 最后响应: {last_response_text[:300]}", file=sys.stderr)
    if last_exception:
        print(f"[AI_ERROR] - 最后异常: {type(last_exception).__name__}: {last_exception}", file=sys.stderr)
    
    return None


def parse_thinking_answer(full_response):
    """统一的标签解析函数，提取thinking和answer内容"""
    import sys
    print(f"[DEBUG] parse_thinking_answer called, full_response length: {len(full_response)}", file=sys.stderr)
    if len(full_response) > 500:
        print(f"[DEBUG] full_response first 500 chars: {full_response[:500]}", file=sys.stderr)
    else:
        print(f"[DEBUG] full_response: {full_response}", file=sys.stderr)
    
    thinking_start = full_response.find('<thinking>')
    thinking_end = full_response.find('</thinking>')
    answer_start = full_response.find('<answer>')
    answer_end = full_response.find('</answer>')
    
    print(f"[DEBUG] thinking_start: {thinking_start}, thinking_end: {thinking_end}, answer_start: {answer_start}, answer_end: {answer_end}", file=sys.stderr)
    
    thinking_content = ""
    answer_content = ""
    
    # 提取思考过程
    if thinking_start != -1 and thinking_end != -1 and thinking_start < thinking_end:
        thinking_content = full_response[thinking_start + len('<thinking>'):thinking_end]
        # 保留换行符，前端会处理
        # thinking_content = thinking_content.replace('\n', '').replace('\r', '')
        print(f"[DEBUG] thinking_content extracted (length: {len(thinking_content)}): {thinking_content[:200]}", file=sys.stderr)
    else:
        print(f"[DEBUG] No thinking tags found or invalid positions", file=sys.stderr)
    
    # 提取回答内容
    if answer_start != -1 and answer_end != -1 and answer_start < answer_end:
        answer_content = full_response[answer_start + len('<answer>'):answer_end]
        print(f"[DEBUG] answer_content extracted (length: {len(answer_content)}): {answer_content[:200]}", file=sys.stderr)
    else:
        print(f"[DEBUG] No answer tags found, using full response as answer", file=sys.stderr)
        # 如果没有找到answer标签，将整个响应作为answer
        answer_content = full_response
        # 如果也没有thinking标签，尝试将前1/3作为thinking，其余作为answer？
        if thinking_start == -1 and len(full_response) > 200:
            # 尝试找到自然分割点
            split_point = min(200, len(full_response) // 3)
            thinking_content = full_response[:split_point]
            answer_content = full_response[split_point:]
    
    return thinking_content, answer_content


def process_sse_stream(response):
    """统一的SSE流式输出解析函数"""
    full_response = ""
    
    for line in response.iter_lines():
        if not line:
            continue
        line = line.decode('utf-8')
        if not line.startswith('data: '):
            continue
        data = line[6:]
        if data == '[DONE]':
            break
        try:
            chunk = json.loads(data)
            choices = chunk.get('choices', [])
            if not choices or len(choices) == 0:
                continue
            delta = choices[0].get('delta', {})
            content = delta.get('content', '')
            if not content:
                continue
            full_response += content
        except json.JSONDecodeError:
            continue
    
    return full_response


def generate_sse_message(message_type, content):
    """统一的SSE消息生成函数"""
    return f"data: {json.dumps({'type': message_type, 'content': content})}\n\n"


def send_thinking_chars(thinking_content):
    """逐字发送思考内容，带有微小延迟"""
    import time
    for char in thinking_content:
        yield generate_sse_message('thinking', char)
        time.sleep(0.03)  # 30ms延迟，确保逐字效果


def send_answer_chars(answer_content):
    """逐字发送回答内容，带有微小延迟"""
    import time
    for char in answer_content:
        yield generate_sse_message('answer', char)
        time.sleep(0.05)  # 50ms延迟，确保逐字效果
