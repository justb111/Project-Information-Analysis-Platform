import requests

url = "https://hk-intra-paas.transsion.com/tranai-proxy/v1/chat/completions"
api_key = "sk_0f04e27baf7fd49de98314bc793b943e2514b72afaf9f67af8676a2"
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
payload = {
    "model": "gpt-5.4-thinking-high",
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": False,
    "max_tokens": 10,   # 限制输出长度
}
response = requests.post(url, headers=headers, json=payload, timeout=60)
print(response.status_code, response.text)
