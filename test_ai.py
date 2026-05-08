import requests
import os
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("AI_BASE_URL", "https://hk-intra-paas.transsion.com/tranai-proxy/v1") + "/chat/completions"
api_key = os.getenv("AI_API_KEY")
if not api_key:
    print("❌ 请在 .env 文件中设置 AI_API_KEY")
    exit(1)

headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
payload = {
    "model": "gpt-5.4-thinking-high",
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": False,
    "max_tokens": 10,
}
response = requests.post(url, headers=headers, json=payload, timeout=60)
print(response.status_code, response.text)
