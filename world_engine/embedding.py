"""嵌入向量工具 — 支持云端 API 和本地模型两种模式。"""
import math
import threading
import time as _time
from typing import Optional

from .utils import log, load_config

# ── 本地模型单例 ──────────────────────────────────────────

_local_model = None
_local_model_name = None
_local_lock = threading.Lock()


def _get_local_model(model_name: str):
    """懒加载本地 embedding 模型（线程安全单例）。"""
    global _local_model, _local_model_name
    with _local_lock:
        if _local_model is not None and _local_model_name == model_name:
            return _local_model
        log("info", f"加载本地 Embedding 模型: {model_name}")
        t0 = _time.time()
        try:
            from sentence_transformers import SentenceTransformer
            _local_model = SentenceTransformer(model_name, trust_remote_code=True)
            _local_model_name = model_name
            elapsed = _time.time() - t0
            log("info", f"本地 Embedding 模型加载完成: {model_name} ({elapsed:.1f}s)")
            return _local_model
        except Exception as e:
            log("warning", f"本地 Embedding 模型加载失败: {e}")
            raise


# ── 云端 API ──────────────────────────────────────────────


def _get_embedding_cloud(text: str, config: dict) -> list[float]:
    """通过云端 API 获取嵌入向量。"""
    from .llm import get_http_session, _robust_api_post

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


# ── 本地 API（llama-server 等）───────────────────────


def _get_embedding_local_api(text: str, config: dict) -> list[float]:
    """通过本地 API 服务（如 llama-server）获取嵌入向量。"""
    import requests as _requests

    emb_cfg = config["models"].get("embedding", {})
    base_url = emb_cfg.get("local_api_base", "http://localhost:8081").rstrip("/")
    model = emb_cfg.get("local_model", "default")
    url = f"{base_url}/v1/embeddings"

    t0 = _time.time()
    resp = _requests.post(url, json={"model": model, "input": text}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    elapsed = _time.time() - t0

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Embedding(local_api)", model, elapsed, 0, 0)

    return result["data"][0]["embedding"]


# ── 本地模型 ──────────────────────────────────────────────


def _get_embedding_local(text: str, config: dict) -> list[float]:
    """通过本地模型获取嵌入向量。"""
    model_name = config["models"].get("embedding", {}).get("local_model", "Qwen/Qwen3-Embedding-0.6B")
    model = _get_local_model(model_name)

    t0 = _time.time()
    embedding = model.encode(text, normalize_embeddings=True)
    elapsed = _time.time() - t0

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Embedding(local)", model_name, elapsed, 0, 0)

    return embedding.tolist()


def _get_embeddings_local_batch(texts: list[str], config: dict) -> list[list[float]]:
    """批量获取本地嵌入向量。"""
    model_name = config["models"].get("embedding", {}).get("local_model", "Qwen/Qwen3-Embedding-0.6B")
    model = _get_local_model(model_name)

    t0 = _time.time()
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)
    elapsed = _time.time() - t0

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Embedding(local,batch)", model_name, elapsed, 0, 0)

    return [e.tolist() for e in embeddings]


# ── 统一接口 ──────────────────────────────────────────────


def get_embedding_mode() -> str:
    """获取当前 embedding 模式：'cloud'、'local' 或 'local_api'。"""
    config = load_config()
    return config.get("models", {}).get("embedding", {}).get("mode", "cloud")


def get_embedding(text: str) -> list[float]:
    """获取文本的嵌入向量（根据配置自动选择云端、本地或本地API）。"""
    config = load_config()
    mode = config.get("models", {}).get("embedding", {}).get("mode", "cloud")
    if mode == "local":
        return _get_embedding_local(text, config)
    if mode == "local_api":
        return _get_embedding_local_api(text, config)
    return _get_embedding_cloud(text, config)


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """批量获取嵌入向量。本地模式利用批量推理加速，其他模式逐条调用。"""
    if not texts:
        return []
    config = load_config()
    mode = config.get("models", {}).get("embedding", {}).get("mode", "cloud")
    if mode == "local":
        return _get_embeddings_local_batch(texts, config)
    if mode == "local_api":
        return [_get_embedding_local_api(t, config) for t in texts]
    # 云端逐条
    return [_get_embedding_cloud(t, config) for t in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
