"""
文本润色模块
使用阿里云 DashScope OpenAI 兼容接口 (qwen-turbo-latest) 对语音识别文本进行润色
去除口头禅、重复内容，修正语序，保留核心语义
"""

import json
import urllib.request

POLISH_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
POLISH_MODEL = "qwen-turbo-latest"

SYSTEM_PROMPT = (
    "你是文本润色助手。用户会给你一段语音识别产生的原始文本，"
    "其中可能包含重复内容、口头禅（嗯、呃、那个、然后、就是说等）、语句不通顺等问题。\n"
    "请你将其整理为简洁清晰、语义完整的文本。要求：\n"
    "1）保留用户的核心意思，不要添加新内容；\n"
    "2）去掉所有口头禅和重复；\n"
    "3）修正语序使其通顺；\n"
    "4）只输出润色后的文本，不要输出任何解释。"
)


def polish_text(raw: str, api_key: str) -> str:
    """调用 LLM 润色语音识别文本，失败时返回原始文本"""
    if not raw.strip():
        return raw

    payload = {
        "model": POLISH_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw},
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        req = urllib.request.Request(
            POLISH_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            polished = result["choices"][0]["message"]["content"].strip()
            return polished if polished else raw
    except Exception as e:
        print(f"⚠️ 润色失败，返回原始文本: {e}")
        return raw
