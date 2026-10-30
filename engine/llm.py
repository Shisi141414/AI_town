"""LLM 客户端 —— OpenAI / Anthropic 双上游格式统一封装

不依赖官方 SDK（省一个依赖 + 避免版本坑），直接 requests 调 HTTP：
- OpenAI 格式:   POST {base_url}/chat/completions
- Anthropic 格式: POST {base_url}/v1/messages, 头部 x-api-key + anthropic-version

两者都要求网关允许 CORS 不存在——因为请求由 Python 后端发出，天然无跨域问题。
"""
from __future__ import annotations

import json
import time

import requests

from .models import ApiProfile

UA = "ai-town/0.1"
TIMEOUT_CONNECT = 8


class LLMError(Exception):
    def __init__(self,message,status=None,retryable=True):
        super().__init__(message)
        self.status=status
        self.retryable=retryable


def _headers(p: ApiProfile) -> dict:
    if p.format == "anthropic":
        return {
            "x-api-key": p.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": UA,
        }
    return {
        "Authorization": f"Bearer {p.api_key}",
        "content-type": "application/json",
        "User-Agent": UA,
    }


def _payload(p: ApiProfile, model: str, system: str, user: str,
             temperature: float, max_tokens: int) -> dict:
    if p.format == "anthropic":
        return {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def _extract_text(p: ApiProfile, data: dict) -> str:
    try:
        if p.format == "anthropic":
            parts = [b.get("text", "") for b in data["content"]
                     if b.get("type") == "text"]
            return "".join(parts).strip()
        message=data["choices"][0]["message"]
        content=message.get("content")
        if isinstance(content,list):
            content="".join(part.get("text","") for part in content if isinstance(part,dict))
        if isinstance(content,str) and content.strip():
            return content.strip()
        # 推理模型兜底：最终回答为空（思考阶段耗尽输出额度）时，
        # 取思考过程的末尾——数值类调用的结论通常就在末尾，由上层解析器钳制。
        for field in ("reasoning_content", "reasoning"):
            thinking = message.get(field)
            if isinstance(thinking, str) and thinking.strip():
                return thinking.strip()[-600:]
        raise LLMError("模型暂未输出最终回答，可能仍在推理或输出额度不足")
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"响应结构异常: {e}: {json.dumps(data, ensure_ascii=False)[:300]}") from e


def _url(p: ApiProfile) -> str:
    base = p.base_url.rstrip("/")
    if p.format == "anthropic":
        return f"{base}/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat(p: ApiProfile, model: str, system: str, user: str,
         temperature: float = 0.8, max_tokens: int = 400,
         timeout: float = 25.0) -> str:
    """单轮对话。任何网络/协议错误统一抛 LLMError，由调用方降级。"""
    url = _url(p)
    try:
        r = requests.post(url, headers=_headers(p),
                          json=_payload(p, model, system, user,
                                        temperature, max_tokens),
                          timeout=(TIMEOUT_CONNECT, timeout))
    except requests.ConnectTimeout as e:
        raise LLMError("连接模型服务超时，请检查地址和网络") from e
    except requests.ReadTimeout as e:
        raise LLMError(f"等待模型回答超时（{timeout:g}秒），可增加等待时间") from e
    except requests.ConnectionError as e:
        raise LLMError("无法连接模型服务，请检查地址和网络") from e
    except requests.RequestException as e:
        raise LLMError("模型请求发送失败，请检查连接设置") from e
    if r.status_code != 200:
        raise LLMError(f"模型服务返回HTTP {r.status_code}", status=r.status_code, retryable=r.status_code in (408,409,425,429) or r.status_code>=500)
    try:
        return _extract_text(p, r.json())
    except json.JSONDecodeError as e:
        raise LLMError("模型服务返回了无法识别的响应格式") from e


def test_profile(p: ApiProfile, model: str | None = None,
                 timeout: float = 90.0, temperature: float = 0.8) -> tuple[bool, str]:
    """沿用正式调用的等待时间和输出额度，避免推理模型被极小探测误判。"""
    m = (model or p.default_model or "").strip()
    if not m:
        return False, "未选择测试模型，请设置档案默认模型或为居民绑定模型"
    try:
        t0 = time.monotonic()
        chat(p, m, system="这是连接测试。请简短回答。", user="请只回复 OK。",
             temperature=temperature, max_tokens=2048, timeout=timeout)
        dt = time.monotonic() - t0
        return True, f"连通正常（{dt:.1f}秒，模型：{m}）"
    except LLMError as e:
        message = f"测试模型 {m}：{e}"
        return False, message.replace(p.api_key, "[密钥已隐藏]") if p.api_key else message
