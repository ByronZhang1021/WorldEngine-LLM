"""嵌入向量工具 — 供 memory_retrieval 和 memory_pipeline 共用。"""
import math
import time as _time

import requests

from .utils import log, load_config
from .llm import get_http_session, _robust_api_post


def get_embedding(text: str) -> list[float]:
    """获取文本的嵌入向量（复用共享连接池 + 网络重试）。"""
    config = load_config()
    api_cfg = config["api"]
    model = config["models"].get("embedding", {}).get("model", "Qwen/Qwen3-Embedding-8B")

    url = f"{api_cfg['base_url']}/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }

    session = get_http_session()
    t0 = _time.time()
    resp = _robust_api_post(
        session, url, {"model": model, "input": text}, headers,
        timeout=30, label="Embedding",
    )
    result = resp.json()

    elapsed = _time.time() - t0
    usage = result.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Embedding", model, elapsed, total_tokens, 0)

    return result["data"][0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
