"""LLM 调用 — 302.ai 直连，支持流式和非流式，含响应清洗。

复用自旧系统 api_proxy.py 的清洗逻辑。
"""
import json
import re
import threading
import time as _time
from typing import Optional

import requests

from .utils import load_config, log

# ── 共享 HTTP Session（连接复用） ─────────────────────────

_http_session: Optional[requests.Session] = None
_http_lock = threading.Lock()


def get_http_session() -> requests.Session:
    """获取共享的 HTTP Session（线程安全，连接复用）。"""
    global _http_session
    if _http_session is None:
        with _http_lock:
            if _http_session is None:
                s = requests.Session()
                # 增大连接池以应对并发 API 调用
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=4,
                    pool_maxsize=8,
                    max_retries=0,
                )
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _http_session = s
    return _http_session


# ── 网络重试 ──────────────────────────────────────────────

_RETRYABLE_STATUS = {502, 503, 504, 529}
_MAX_RETRIES = 2
_RETRY_BACKOFF = [3, 6]  # 第1次重试等3s，第2次等6s


def _robust_api_post(
    session: requests.Session,
    url: str,
    payload: dict,
    headers: dict,
    timeout: int = 30,
    label: str = "API",
) -> requests.Response:
    """带网络重试的 API POST 请求。

    重试条件：ConnectionError（连接失败）、HTTP 502/503/504/529（服务端临时故障）。
    不重试：Timeout（已等完整超时时间）、HTTP 4xx（客户端错误）。
    """
    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.ConnectionError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF[attempt]
                log("warning", f"[{label}] 连接失败 (第{attempt+1}次), {wait}s 后重试: {e}")
                _time.sleep(wait)
                continue
            log("warning", f"[{label}] 连接失败 (已重试{_MAX_RETRIES}次): {e}")
            raise
        except requests.exceptions.Timeout as e:
            # 已等完整超时时间，不重试
            log("warning", f"[{label}] 请求超时 ({timeout}s): {e}")
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in _RETRYABLE_STATUS:
                last_error = e
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF[attempt]
                    log("warning", f"[{label}] HTTP {status} (第{attempt+1}次), {wait}s 后重试")
                    _time.sleep(wait)
                    continue
            log("warning", f"[{label}] HTTP {status} @ {url}: {e}")
            raise
    raise last_error


# ── 清洗函数（从 api_proxy.py 复用） ─────────────────────


def clean_reasoning_leak(text: str) -> str:
    """清洗 SSE 响应中泄露的推理链、tool_calls 等内容。"""
    if "data: " not in text:
        return text

    full_content = []
    has_tool_calls = False
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            chunk = json.loads(line[6:])
            for c in chunk.get("choices", []):
                delta = c.get("delta", {})
                msg = c.get("message", {})
                content = delta.get("content") or msg.get("content") or ""
                if content:
                    full_content.append(content)
                if delta.get("tool_calls") or msg.get("tool_calls"):
                    has_tool_calls = True
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    if not full_content:
        return text

    joined = "".join(full_content).strip()
    is_leak = False

    # 1. Content + tool_calls = thinking leak
    if has_tool_calls and joined:
        is_leak = True
    # 2. Reasoning chain
    elif joined.startswith("Assistant:") or joined.startswith("assistant:"):
        is_leak = True
    # 3. Tool result JSON echoed
    elif joined.startswith("{") and ('"results"' in joined or '"snippet"' in joined):
        is_leak = True

    if not is_leak:
        return text

    log("debug", f"清洗泄露内容 ({len(joined)} chars): {joined[:100]}...")
    new_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("data: ") and stripped != "data: [DONE]":
            try:
                chunk = json.loads(stripped[6:])
                for c in chunk.get("choices", []):
                    if "delta" in c and "content" in c["delta"]:
                        c["delta"]["content"] = ""
                    if "message" in c and "content" in c["message"]:
                        c["message"]["content"] = ""
                new_lines.append("data: " + json.dumps(chunk, ensure_ascii=False))
            except (json.JSONDecodeError, KeyError, TypeError):
                new_lines.append(line)
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def clean_tool_calls_leak(text: str) -> str:
    """清洗泄露的 <tool_calls> 标签。"""
    if "<tool_calls>" not in text:
        return text
    cleaned = re.sub(r"<tool_calls>.*?</tool_calls>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<tool_calls>.*", "", cleaned, flags=re.DOTALL)
    if cleaned != text:
        log("debug", "清洗泄露的 <tool_calls>")
    return cleaned


def extract_text_from_sse(sse_text: str) -> str:
    """从 SSE 流式响应中提取完整文本。"""
    parts = []
    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            chunk = json.loads(line[6:])
            for c in chunk.get("choices", []):
                delta = c.get("delta", {})
                t = delta.get("content", "")
                if t:
                    parts.append(t)
        except (json.JSONDecodeError, KeyError):
            pass
    return "".join(parts)


def extract_text_from_json(resp_data: dict) -> str:
    """从非流式 JSON 响应中提取文本。"""
    try:
        return resp_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return ""


# ── LLM 调用 ──────────────────────────────────────────────


def _make_request(
    messages: list[dict],
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: Optional[int] = None,
    stream: bool = False,
) -> dict | str:
    """发送 LLM 请求（使用共享连接池）。

    非流式返回 dict（完整 JSON 响应），流式返回 str（SSE 文本）。
    所有参数由上层函数从 config_key 解析后传入。
    """
    config = load_config()
    api_cfg = config["api"]

    url = f"{api_cfg['base_url']}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }

    session = get_http_session()
    resp = _robust_api_post(
        session, url, payload, headers,
        timeout=300, label=f"LLM/{payload['model']}",
    )
    # 强制 UTF-8：SSE 响应 (text/event-stream) 可能不带 charset，
    # requests 会默认 ISO-8859-1 导致中文乱码
    resp.encoding = "utf-8"
    resp_body = resp.text

    if stream:
        # 清洗 SSE 响应
        resp_body = clean_reasoning_leak(resp_body)
        resp_body = clean_tool_calls_leak(resp_body)
        return resp_body
    else:
        return json.loads(resp_body)


def _resolve_config(config_key: str, model: Optional[str] = None,
                    temperature: Optional[float] = None,
                    top_p: Optional[float] = None) -> tuple[str, float, float]:
    """从 config_key 解析 model/temperature/top_p，支持参数覆盖。"""
    config = load_config()
    cfg = config["models"].get(config_key, {})
    return (
        model or cfg.get("model", "grok-4-1-fast-non-reasoning"),
        temperature if temperature is not None else cfg.get("temperature", 0.65),
        top_p if top_p is not None else cfg.get("top_p", 0.9),
    )


def chat(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    label: str = "",
    config_key: str = "secondary_story",
) -> str:
    """非流式聊天，返回纯文本回复。

    config_key: 配置项名，默认 secondary_story（辅助剧情模型）。
    """
    _model, _temp, _top_p = _resolve_config(config_key, model, temperature)
    log("debug", f"LLM chat: model={_model} msgs={len(messages)}")

    t0 = _time.time()
    try:
        result = _make_request(
            messages=messages,
            model=_model,
            temperature=_temp,
            top_p=_top_p,
            max_tokens=max_tokens,
            stream=False,
        )
        elapsed = _time.time() - t0
        text = extract_text_from_json(result)

        # 提取 token 用量
        usage = result.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # 上报给 TurnLogger
        if label:
            from .utils import get_turn_logger
            tl = get_turn_logger()
            if tl:
                tl.record_llm_call(label, _model, elapsed, prompt_tokens, completion_tokens)

        log("debug", f"LLM 回复: {text[:100]}...")
        return text
    except Exception as e:
        log("warning", f"LLM 调用失败: {type(e).__name__}: {e}")
        raise


def chat_stream(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    label: str = "",
    config_key: str = "secondary_story",
) -> str:
    """聊天，返回完整文本（非流式）。

    config_key: 配置项名，默认 secondary_story（辅助剧情模型）。
    """
    _model, _temp, _top_p = _resolve_config(config_key, model, temperature)
    log("debug", f"LLM chat_stream: model={_model} msgs={len(messages)}")

    t0 = _time.time()
    try:
        resp = _make_request(
            messages=messages,
            model=_model,
            temperature=_temp,
            top_p=_top_p,
            max_tokens=max_tokens,
            stream=False,
        )
        elapsed = _time.time() - t0

        # 从 JSON 响应中提取文本
        text = resp["choices"][0]["message"]["content"] or ""
        text = clean_tool_calls_leak(text)

        # 清洗可能泄露的 <think> 推理链
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # 提取 usage
        u = resp.get("usage", {})
        prompt_tokens = u.get("prompt_tokens", 0)
        completion_tokens = u.get("completion_tokens", 0)

        # 上报给 TurnLogger
        if label:
            from .utils import get_turn_logger
            tl = get_turn_logger()
            if tl:
                tl.record_llm_call(label, _model, elapsed, prompt_tokens, completion_tokens)

        log("debug", f"LLM 回复: {text[:100]}...")
        return text
    except Exception as e:
        log("warning", f"LLM 调用失败: {type(e).__name__}: {e}")
        raise


def chat_json(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 5000,
    _retries: int = 2,
    label: str = "",
    config_key: str = "analysis",
) -> dict | list:
    """调用 LLM 并解析 JSON 响应。

    默认用 analysis 配置（指令分析模型，低温度）。
    可通过 config_key 切换到其他配置（如 secondary_story）。
    支持自动重试（LLM 偶尔返回不合法 JSON）。
    """
    _model, _temp, _top_p = _resolve_config(config_key, model, temperature)

    last_error = None
    for attempt in range(_retries + 1):
        try:
            text = chat(messages, model=_model, temperature=_temp, max_tokens=max_tokens, label=label, config_key=config_key)

            # 处理可能的 markdown 代码块包裹
            if text.strip().startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text.strip())
                text = re.sub(r"\n?```$", "", text)

            # 清除 LLM 常见的尾逗号错误（如 [1, 2,] 或 {"a": 1,}）
            text = re.sub(r',\s*([}\]])', r'\1', text)

            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = e
            log("warning", f"chat_json JSON 解析失败 (第{attempt+1}次): {e}\n原始内容: {text[:200]}")
            if attempt < _retries:
                _time.sleep(1)  # 退避：给 LLM 缓冲时间
                continue
        except Exception as e:
            last_error = e
            log("warning", f"chat_json 调用失败 (第{attempt+1}次): {e}")
            if attempt < _retries:
                _time.sleep(1)  # 退避
                continue

    raise last_error

