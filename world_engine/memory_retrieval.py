"""记忆智能检索 — 为对话生成提供 hybrid search + rerank。

流程:
  记忆总数 ≤ threshold → 全量返回
  记忆总数 > threshold →
    Tier 1: 最近 24 世界小时的记忆（始终纳入）
    Stage 1: embedding(0.7) + jieba关键词(0.3) 混合召回 → top recall_top_k
    Stage 2: rerank 精排 → top final_top_k
    合并 → 去重返回
"""
import json
from datetime import datetime, timedelta
from typing import Optional

import jieba
import requests

from .embedding import get_embedding, cosine_similarity
from .utils import log, load_config, load_state, CHARACTERS_DIR
from .memory import load_all_entries, load_emb_cache, save_emb_cache, inject_embeddings
from .llm import get_http_session, _robust_api_post


def _rerank(query: str, documents: list[str], model: str) -> list[dict]:
    """调用 rerank API 对文档重新排序（复用共享连接池）。

    返回: [{"index": 原始索引, "relevance_score": 分数}, ...]
    """
    import time as _time
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
    resp = _robust_api_post(
        session, url, payload, headers,
        timeout=30, label="Rerank",
    )
    result = resp.json()

    elapsed = _time.time() - t0
    usage = result.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.record_llm_call("Rerank", model, elapsed, total_tokens, 0)

    return result.get("results", [])


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


def _ensure_embeddings(meta: list, character: str):
    """确保所有记忆条目都有 embedding（使用独立缓存文件）。"""
    # 先从缓存加载已有的
    inject_embeddings(meta, character)

    # 找出还没有 embedding 的
    needs = [m for m in meta if "embedding" not in m]
    if not needs:
        return

    try:
        cache = load_emb_cache(character)
        for m in needs:
            emb = get_embedding(m["content"])
            m["embedding"] = emb
            cache[m["content"]] = emb
        save_emb_cache(character, cache)
        log("info", f"为 {character} 计算了 {len(needs)} 条记忆的 embedding")
    except Exception as e:
        log("warning", f"计算 embedding 失败 [{character}]: {e}")


def retrieve_memories(
    character: str,
    conversation_context: str,
    present_chars: list[str],
    current_location: str,
    all_char_names: Optional[list[str]] = None,
    all_locations: Optional[list[str]] = None,
) -> dict[str, str]:
    """智能检索角色记忆，按 visibility 分组返回。

    Args:
        character: 角色名
        conversation_context: 最近对话上下文（用于构建搜索 query）
        present_chars: 在场角色名列表
        current_location: 当前地点
        all_char_names: 所有角色名（用于关键词提取）
        all_locations: 所有地点名（用于关键词提取）

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
    rerank_model = retrieval_cfg.get("rerank_model", "jina-reranker-v2-base-multilingual")

    # 读取记忆
    meta = load_all_entries(character)
    if not meta:
        return {"public": "", "private": ""}

    # 所有记忆内容
    all_contents = [m.get("content", "") for m in meta]

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
    tier1_indices = set()
    if now_str:
        try:
            now = datetime.fromisoformat(now_str[:19])
            cutoff = now - timedelta(hours=24)
            for i, m in enumerate(meta):
                created = m.get("created", "")
                if created:
                    try:
                        ct = datetime.fromisoformat(created[:19])
                        if ct >= cutoff:
                            tier1_indices.add(i)
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Stage 1: 混合召回 ──
    # 排除 Tier 1 已纳入的
    candidates = [(i, m) for i, m in enumerate(meta) if i not in tier1_indices]

    if not candidates:
        # 全部都是 Tier 1
        result = _split_by_visibility(meta)
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            pub_n = result["public"].count("\n") + (1 if result["public"].strip() else 0)
            pri_n = result["private"].count("\n") + (1 if result["private"].strip() else 0)
            tl.log_memory_retrieval(character, len(meta), pub_n + pri_n, len(tier1_indices), 0, "全部Tier1")
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

    # 计算 embedding
    _ensure_embeddings(meta, character)

    try:
        query_emb = get_embedding(query)
    except Exception as e:
        # 回退到最近 N 条，而非全量注入，防止 token 爆炸
        fallback_max = retrieval_cfg.get("fallback_max", 30)
        log("warning", f"query embedding 失败: {e}，回退最近 {fallback_max} 条注入")
        recent_meta = meta[-fallback_max:]
        return _split_by_visibility(recent_meta)

    # 混合打分
    scored = []
    for i, m in candidates:
        content = m.get("content", "")

        # embedding 分数
        emb_score = 0.0
        if "embedding" in m:
            emb_score = cosine_similarity(query_emb, m["embedding"])

        # 关键词分数
        kw_score = _keyword_score(content, keywords)

        # 综合分数
        score = emb_weight * emb_score + kw_weight * kw_score
        scored.append((score, i, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    stage1_results = scored[:recall_top_k]

    min_score = retrieval_cfg.get("min_score", 0.3)

    # ── Stage 2: Rerank 精排 ──
    if len(stage1_results) > final_top_k:
        try:
            docs = [m.get("content", "") for _, _, m in stage1_results]
            rerank_results = _rerank(query, docs, rerank_model)

            # 按 rerank 分数排序，过滤低于 min_score 的
            rerank_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
            reranked_indices = [
                r["index"] for r in rerank_results[:final_top_k]
                if r.get("relevance_score", 0) >= min_score
            ]

            final_indices = tier1_indices.copy()
            for ri in reranked_indices:
                _, orig_i, _ = stage1_results[ri]
                final_indices.add(orig_i)

        except Exception as e:
            log("warning", f"rerank 失败: {e}，使用 Stage 1 结果")
            final_indices = tier1_indices.copy()
            for _, i, _ in stage1_results[:final_top_k]:
                final_indices.add(i)
    else:
        final_indices = tier1_indices.copy()
        for _, i, _ in stage1_results:
            final_indices.add(i)

    # 按原始顺序 + visibility 分组
    selected = [meta[i] for i in sorted(final_indices)]
    result = _split_by_visibility(selected)

    pub_count = result["public"].count("\n") + (1 if result["public"].strip() else 0)
    pri_count = result["private"].count("\n") + (1 if result["private"].strip() else 0)
    retrieved_count = len(final_indices) - len(tier1_indices)
    log("info", f"记忆检索 [{character}]: 注入 {pub_count}+{pri_count} 条（Tier1: {len(tier1_indices)}, 检索: {retrieved_count}）")

    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.log_memory_retrieval(character, len(meta), pub_count + pri_count,
                                 len(tier1_indices), retrieved_count, "智能检索")
    return result


def _split_by_visibility(entries: list[dict]) -> dict[str, str]:
    """将记忆条目按 visibility 分成 public 和 private 两组文本。"""
    public_lines = []
    private_lines = []
    for m in entries:
        content = m.get("content", "")
        if not content.strip():
            continue
        # 根据 _section 判断 visibility
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


