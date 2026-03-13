"""ChromaDB 统一存储层 — 角色记忆的持久化和语义检索。

每个世界一个 ChromaDB 实例（persist_directory = WORLD_DIR / "chromadb"），
角色+板块通过 metadata 区分。
"""
import shutil
import threading
from pathlib import Path
from typing import Optional

import chromadb

from .utils import log, WORLD_DIR, SECTION_KEYS

_lock = threading.Lock()
_client: Optional[chromadb.ClientAPI] = None
_collection: Optional[chromadb.Collection] = None

CHROMA_DIR_NAME = "chromadb"


def _chroma_path() -> Path:
    return WORLD_DIR / CHROMA_DIR_NAME


def _get_collection() -> chromadb.Collection:
    """获取或创建 memories collection（懒初始化）。"""
    global _client, _collection
    with _lock:
        if _collection is not None:
            return _collection
        path = _chroma_path()
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
        _collection = _client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
        log("info", f"ChromaDB 已加载: {path} ({_collection.count()} 条记录)")
        return _collection


def reload_chroma():
    """重新加载 ChromaDB（存档切换后调用）。"""
    global _client, _collection
    with _lock:
        _collection = None
        if _client is not None:
            try:
                _client.clear_system_cache()
            except Exception:
                pass
            try:
                del _client
            except Exception:
                pass
        _client = None
    # Windows 上 SQLite 文件锁需要 GC 回收后才释放
    import gc
    gc.collect()
    log("info", "ChromaDB 已重置，下次访问将重新加载")


def _make_id(character: str, section: str, index: int) -> str:
    return f"{character}__{section}__{index:04d}"


def _parse_id(entry_id: str) -> tuple[str, str, int]:
    parts = entry_id.split("__")
    return parts[0], parts[1], int(parts[2])


# ── CRUD ──────────────────────────────────────────────────


def add_entries(character: str, section: str, entries: list[dict]):
    """批量添加条目到 ChromaDB。

    entries: [{"content": "...", "ttl": "...", "created": "...", "hit_count": 0}, ...]
    """
    if not entries:
        return
    col = _get_collection()
    # 找出该角色该板块当前最大 index
    existing = col.get(where={"character": character, "section": section})
    max_idx = -1
    for eid in existing["ids"]:
        _, _, idx = _parse_id(eid)
        max_idx = max(max_idx, idx)

    ids = []
    documents = []
    metadatas = []
    for i, entry in enumerate(entries):
        content = entry.get("content", "").strip()
        if not content:
            continue
        idx = max_idx + 1 + i
        ids.append(_make_id(character, section, idx))
        documents.append(content)
        metadatas.append({
            "character": character,
            "section": section,
            "ttl": entry.get("ttl", "永久"),
            "created": entry.get("created", ""),
            "hit_count": entry.get("hit_count", 0),
        })

    if ids:
        col.add(ids=ids, documents=documents, metadatas=metadatas)


def get_entries(character: str, section: Optional[str] = None) -> list[dict]:
    """获取角色的条目列表。

    返回: [{"id": "...", "content": "...", "ttl": "...", "created": "...", "hit_count": 0, "_section": "..."}, ...]
    """
    col = _get_collection()
    where_filter: dict = {"character": character}
    if section:
        where_filter["section"] = section

    # chromadb where 只支持单条件或 $and/$or
    if len(where_filter) > 1:
        where_filter = {"$and": [{"character": character}, {"section": section}]}

    result = col.get(where=where_filter)
    entries = []
    for i, eid in enumerate(result["ids"]):
        meta = result["metadatas"][i]
        entries.append({
            "id": eid,
            "content": result["documents"][i],
            "ttl": meta.get("ttl", "永久"),
            "created": meta.get("created", ""),
            "hit_count": meta.get("hit_count", 0),
            "_section": meta.get("section", ""),
        })
    # 按 id 排序保持稳定顺序
    entries.sort(key=lambda e: e["id"])
    return entries


def update_entry(entry_id: str, content: str, metadata_updates: Optional[dict] = None):
    """更新一条记录的内容和/或元数据。"""
    col = _get_collection()
    update_kwargs: dict = {"ids": [entry_id], "documents": [content]}
    if metadata_updates:
        # 读取现有 metadata 并合并
        existing = col.get(ids=[entry_id])
        if existing["ids"]:
            meta = dict(existing["metadatas"][0])
            meta.update(metadata_updates)
            update_kwargs["metadatas"] = [meta]
    col.update(**update_kwargs)


def delete_entry(entry_id: str):
    """删除一条记录。"""
    col = _get_collection()
    col.delete(ids=[entry_id])


def delete_entries_by_filter(character: str, section: Optional[str] = None):
    """删除角色的所有条目，或指定板块的条目。"""
    col = _get_collection()
    where_filter: dict = {"character": character}
    if section:
        where_filter = {"$and": [{"character": character}, {"section": section}]}
    # 先 get 再 delete（chromadb delete 需要 ids 或 where）
    result = col.get(where=where_filter)
    if result["ids"]:
        col.delete(ids=result["ids"])


def replace_section(character: str, section: str, entries: list[dict]):
    """替换角色某板块的全部条目（Dashboard 保存用）。"""
    delete_entries_by_filter(character, section)
    add_entries(character, section, entries)


# ── 语义检索 ──────────────────────────────────────────────


def query_similar(
    character: str,
    query_text: str,
    n_results: int = 30,
    where_filter: Optional[dict] = None,
    query_embedding: Optional[list[float]] = None,
) -> list[dict]:
    """语义检索角色记忆。

    可以传入 query_text 或 query_embedding。
    返回: [{"id": ..., "content": ..., "distance": ..., "metadata": {...}}, ...]
    """
    col = _get_collection()

    base_filter = {"character": character}
    if where_filter:
        combined = [base_filter]
        if isinstance(where_filter, dict) and "$and" in where_filter:
            combined.extend(where_filter["$and"])
        else:
            combined.append(where_filter)
        final_filter = {"$and": combined}
    else:
        final_filter = base_filter

    query_kwargs: dict = {
        "n_results": n_results,
        "where": final_filter,
    }
    if query_embedding:
        query_kwargs["query_embeddings"] = [query_embedding]
    else:
        query_kwargs["query_texts"] = [query_text]

    try:
        result = col.query(**query_kwargs)
    except Exception as e:
        log("warning", f"ChromaDB query 失败: {e}")
        return []

    entries = []
    if result["ids"] and result["ids"][0]:
        for i, eid in enumerate(result["ids"][0]):
            entries.append({
                "id": eid,
                "content": result["documents"][0][i],
                "distance": result["distances"][0][i] if result.get("distances") else 0,
                "metadata": result["metadatas"][0][i],
            })
    return entries


# ── 存档 ──────────────────────────────────────────────────


def _rmtree_retry(path: Path, retries: int = 10, delay: float = 0.5):
    """删除目录，Windows 上遇到文件锁时自动重试。"""
    import time as _time
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt < retries - 1:
                import gc
                gc.collect()
                _time.sleep(delay)
                log("info", f"[ChromaDB] rmtree 重试 {attempt + 2}/{retries}...")
            else:
                raise


def copy_chroma_to(dest: Path):
    """将当前世界的 ChromaDB 复制到目标路径（存档保存用）。"""
    src = _chroma_path()
    dst = dest / CHROMA_DIR_NAME
    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        # 先确保数据已写入磁盘
        _flush()
        shutil.copytree(src, dst)


def copy_chroma_from(src: Path):
    """从源路径恢复 ChromaDB 到当前世界目录（存档加载用）。"""
    src_chroma = src / CHROMA_DIR_NAME
    dst = _chroma_path()
    # 先关闭当前连接
    reload_chroma()
    if dst.exists():
        _rmtree_retry(dst)
    if src_chroma.exists():
        shutil.copytree(src_chroma, dst)


def _flush():
    """确保 ChromaDB 数据写入磁盘。"""
    # PersistentClient 自动持久化，这里只是确保 collection 已初始化
    with _lock:
        pass


# ── 迁移工具 ──────────────────────────────────────────────


def import_character_json(character: str, data: dict):
    """将角色 JSON 数据导入 ChromaDB。

    data: {"public_base": [...], "private_base": [...], ...}
    """
    for section in SECTION_KEYS:
        entries = data.get(section, [])
        if not entries:
            continue
        # 统一字段名
        normalized = []
        for e in entries:
            content = e.get("content", "") or e.get("text", "")
            if not content.strip():
                continue
            normalized.append({
                "content": content.strip(),
                "ttl": e.get("ttl", "永久"),
                "created": e.get("created", ""),
                "hit_count": e.get("hit_count", 0),
            })
        if normalized:
            add_entries(character, section, normalized)
    log("info", f"角色 {character} 已导入 ChromaDB")


def get_all_characters() -> list[str]:
    """获取 ChromaDB 中所有角色名。"""
    col = _get_collection()
    result = col.get()
    chars = set()
    for meta in result["metadatas"]:
        chars.add(meta["character"])
    return sorted(chars)
