import json


def format_sse(event, data):
    """
    格式化SSE事件
    
    Args:
        event: 事件类型，如 'thinking', 'answer', 'data', 'error'
        data: 要发送的数据，可以是字符串或字典
    
    Returns:
        格式化的SSE字符串
    """
    if isinstance(data, dict):
        data_str = json.dumps(data, ensure_ascii=False)
    else:
        data_str = str(data)
    
    return f"event: {event}\ndata: {data_str}\n\n"