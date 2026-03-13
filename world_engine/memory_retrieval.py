"""记忆智能检索 — hybrid search + rerank，基于 ChromaDB。

流程:
  记忆总数 ≤ threshold → 全量返回
  记忆总数 > threshold →
    Tier 1: 最近 24 世界小时的记忆（始终纳入）
    Stage 1: ChromaDB 向量检索 + jieba 关键词混合召回 → top recall_top_k
    Stage 2: rerank 精排 → top final_top_k
    合并 → 去重返回
"""
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Optional

import jieba

from .embedding import get_embedding, cosine_similarity
from .utils import log, load_config, load_state
from .memory import load_all_entries
from . import chroma_store


# ── Rerank 引擎 ──────────────────────────────────────────

_local_reranker = None
_local_reranker_name = None
_reranker_lock = threading.Lock()


def _get_local_reranker(model_name: str):
    """懒加载本地 reranker 模型。"""
    global _local_reranker, _local_reranker_name
    with _reranker_lock:
        if _local_reranker is not None and _local_reranker_name == model_name:
            return _local_reranker
        log("info", f"加载本地 Reranker 模型: {model_name}")
        t0 = _time.time()
        try:
            from sentence_transformers import CrossEncoder
            _local_reranker = CrossEncoder(model_name)
            _local_reranker_name = model_name
            elapsed = _time.time() - t0
            log("info", f"本地 Reranker 加载完成: {model_name} ({elapsed:.1f}s)")
            return _local_reranker
        except Exception as e:
            log("warning", f"本地 Reranker 加载失败: {e}")
            raise


def _rerank_cloud(query: str, documents: list[str], model: str) -> list[dict]:
    """调用云端 rerank API。"""
    from .llm import get_http_session, _robust_api_post

    config = load_config()
    api_cfg = config["api"]
    url = f"{api_cfg['base_url']}/v1/rerank"
    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "return_documents": False,
    }

    session = get_http_session()
    t0 = _time.time()
    resp = _robust_api_post(session, url, payload, headers, timeout=30, label="Rerank")
    result = resp.json()
    elapsed = _time.time() - t0

    usage = result.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Rerank", model, elapsed, total_tokens, 0)

    return result.get("results", [])


def _rerank_local(query: str, documents: list[str], model_name: str) -> list[dict]:
    """使用本地模型 rerank。"""
    reranker = _get_local_reranker(model_name)
    t0 = _time.time()
    pairs = [(query, doc) for doc in documents]
    scores = reranker.predict(pairs)
    elapsed = _time.time() - t0

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Rerank(local)", model_name, elapsed, 0, 0)

    results = [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)]
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


def _rerank_local_api(query: str, documents: list[str]) -> list[dict]:
    """通过本地 API 服务（如 llama-server --rerank）进行精排。"""
    import requests as _requests

    config = load_config()
    retrieval_cfg = config.get("memory_retrieval", {})
    base_url = retrieval_cfg.get("local_api_base", "http://localhost:8082").rstrip("/")
    model = retrieval_cfg.get("local_rerank_model", "default")
    url = f"{base_url}/v1/rerank"

    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "return_documents": False,
    }

    t0 = _time.time()
    resp = _requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    elapsed = _time.time() - t0

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Rerank(local_api)", model, elapsed, 0, 0)

    return result.get("results", [])


def _rerank(query: str, documents: list[str]) -> Optional[list[dict]]:
    """根据配置选择 rerank 方式。返回 None 表示关闭 rerank。"""
    config = load_config()
    retrieval_cfg = config.get("memory_retrieval", {})
    mode = retrieval_cfg.get("rerank_mode", "cloud")

    if mode == "off":
        return None

    if mode == "local":
        model = retrieval_cfg.get("local_rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        return _rerank_local(query, documents, model)

    if mode == "local_api":
        return _rerank_local_api(query, documents)

    # cloud
    model = retrieval_cfg.get("rerank_model", "Qwen/Qwen3-Reranker-8B")
    return _rerank_cloud(query, documents, model)


# ── 关键词 ──────────────────────────────────────────────


def _extract_keywords(text: str, all_char_names: list[str], all_locations: list[str]) -> set[str]:
    """从文本中提取关键词：jieba 分词 + 人名/地点精确匹配。"""
    words = set(w for w in jieba.cut(text) if len(w) >= 2)
    for name in all_char_names:
        if name in text:
            words.add(name)
    for loc in all_locations:
        if loc in text:
            words.add(loc)
    return words


def _keyword_score(memory_text: str, keywords: set[str]) -> float:
    """计算记忆与关键词的匹配分数。"""
    if not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in memory_text)
    return min(hits / 5, 1.0)


# ── 主检索函数 ──────────────────────────────────────────


def retrieve_memories(
    character: str,
    conversation_context: str,
    present_chars: list[str],
    current_location: str,
    all_char_names: Optional[list[str]] = None,
    all_locations: Optional[list[str]] = None,
) -> dict[str, str]:
    """智能检索角色记忆，按 visibility 分组返回。

    Returns:
        {"public": "公开记忆文本", "private": "私密记忆文本"}
    """
    config = load_config()
    retrieval_cfg = config.get("memory_retrieval", {})
    threshold = retrieval_cfg.get("threshold", 20)
    recall_top_k = retrieval_cfg.get("recall_top_k", 30)
    final_top_k = retrieval_cfg.get("final_top_k", 15)
    emb_weight = retrieval_cfg.get("embedding_weight", 0.7)
    kw_weight = retrieval_cfg.get("keyword_weight", 0.3)

    # 读取记忆
    meta = load_all_entries(character)
    if not meta:
        return {"public": "", "private": ""}

    # 数量 ≤ threshold → 全量返回
    if len(meta) <= threshold:
        result = _split_by_visibility(meta)
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            pub_n = result["public"].count("\n") + (1 if result["public"].strip() else 0)
            pri_n = result["private"].count("\n") + (1 if result["private"].strip() else 0)
            tl.log_memory_retrieval(character, len(meta), pub_n + pri_n, 0, 0, "全量注入")
        return result

    log("info", f"记忆检索 [{character}]: {len(meta)} 条记忆，启用智能检索")

    # ── Tier 1: 最近 24 世界小时的记忆 ──
    state = load_state()
    now_str = state.get("current_time", "")
    tier1_ids = set()
    if now_str:
        try:
            now = datetime.fromisoformat(now_str[:19])
            cutoff = now - timedelta(hours=24)
            for m in meta:
                created = m.get("created", "")
                if created:
                    try:
                        ct = datetime.fromisoformat(created[:19])
                        if ct >= cutoff:
                            tier1_ids.add(m["id"])
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Stage 1: 混合召回 ──
    candidates = [m for m in meta if m["id"] not in tier1_ids]

    if not candidates:
        result = _split_by_visibility(meta)
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            pub_n = result["public"].count("\n") + (1 if result["public"].strip() else 0)
            pri_n = result["private"].count("\n") + (1 if result["private"].strip() else 0)
            tl.log_memory_retrieval(character, len(meta), pub_n + pri_n, len(tier1_ids), 0, "全部Tier1")
        return result

    # 构建搜索 query
    query = conversation_context[:1000]
    if current_location:
        query += f"\n地点：{current_location}"
    if present_chars:
        query += f"\n在场：{', '.join(present_chars)}"

    # 提取关键词
    all_char_names = all_char_names or []
    all_locations = all_locations or []
    keywords = _extract_keywords(query, all_char_names, all_locations)

    # 使用 ChromaDB 向量检索
    try:
        query_emb = get_embedding(query)
    except Exception as e:
        fallback_max = retrieval_cfg.get("fallback_max", 30)
        log("warning", f"query embedding 失败: {e}，回退最近 {fallback_max} 条注入")
        recent_meta = meta[-fallback_max:]
        return _split_by_visibility(recent_meta)

    # ChromaDB 向量检索（排除 tier1）
    chroma_results = chroma_store.query_similar(
        character, query, n_results=recall_top_k,
        query_embedding=query_emb,
    )

    # 混合打分：embedding 分数 + 关键词分数
    scored = []
    chroma_distances = {r["id"]: r["distance"] for r in chroma_results}
    for m in candidates:
        content = m.get("content", "")
        # ChromaDB cosine distance → similarity = 1 - distance
        emb_score = 1.0 - chroma_distances.get(m["id"], 1.0)
        kw_score = _keyword_score(content, keywords)
        score = emb_weight * emb_score + kw_weight * kw_score
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    stage1_results = scored[:recall_top_k]

    min_score = retrieval_cfg.get("min_score", 0.3)

    # ── Stage 2: Rerank 精排 ──
    final_ids = set(tier1_ids)
    if len(stage1_results) > final_top_k:
        try:
            rerank_results = _rerank(query, [m.get("content", "") for _, m in stage1_results])
            if rerank_results is not None:
                rerank_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                reranked_indices = [
                    r["index"] for r in rerank_results[:final_top_k]
                    if r.get("relevance_score", 0) >= min_score
                ]
                for ri in reranked_indices:
                    if ri < len(stage1_results):
                        final_ids.add(stage1_results[ri][1]["id"])
            else:
                # rerank off，直接取 stage1 前 final_top_k
                for _, m in stage1_results[:final_top_k]:
                    final_ids.add(m["id"])
        except Exception as e:
            log("warning", f"rerank 失败: {e}，使用 Stage 1 结果")
            for _, m in stage1_results[:final_top_k]:
                final_ids.add(m["id"])
    else:
        for _, m in stage1_results:
            final_ids.add(m["id"])

    # 按原始顺序 + visibility 分组
    selected = [m for m in meta if m["id"] in final_ids]
    result = _split_by_visibility(selected)

    pub_count = result["public"].count("\n") + (1 if result["public"].strip() else 0)
    pri_count = result["private"].count("\n") + (1 if result["private"].strip() else 0)
    retrieved_count = len(final_ids) - len(tier1_ids)
    log("info", f"记忆检索 [{character}]: 注入 {pub_count}+{pri_count} 条（Tier1: {len(tier1_ids)}, 检索: {retrieved_count}）")

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.log_memory_retrieval(character, len(meta), pub_count + pri_count,
                                 len(tier1_ids), retrieved_count, "智能检索")
    return result


def _split_by_visibility(entries: list[dict]) -> dict[str, str]:
    """将记忆条目按 visibility 分成 public 和 private 两组文本。"""
    public_lines = []
    private_lines = []
    for m in entries:
        content = m.get("content", "")
        if not content.strip():
            continue
        section = m.get("_section", "")
        is_public = section.startswith("public")
        if is_public:
            public_lines.append(f"- {content}")
        else:
            private_lines.append(f"- {content}")
    return {
        "public": "\n".join(public_lines),
        "private": "\n".join(private_lines),
    }
