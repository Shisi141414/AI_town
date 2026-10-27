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
    pass


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
        return data["choices"][0]["message"]["content"].strip()
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
    except requests.RequestException as e:
        raise LLMError(f"网络错误: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    try:
        return _extract_text(p, r.json())
    except json.JSONDecodeError as e:
        raise LLMError(f"非 JSON 响应: {r.text[:200]}") from e


def test_profile(p: ApiProfile, model: str | None = None) -> tuple[bool, str]:
    """连接测试：发一条 1 token 的极小请求，返回 (ok, 说明)"""
    m = model or p.default_model or "gpt-4o-mini"
    try:
        t0 = time.time()
        reply = chat(p, m, system="You are a probe.", user="ping",
                     temperature=0.0, max_tokens=8, timeout=12.0)
        dt = time.time() - t0
        return True, f"连通正常({dt:.1f}s, {m}): {reply[:40]}"
    except LLMError as e:
        return False, str(e)
