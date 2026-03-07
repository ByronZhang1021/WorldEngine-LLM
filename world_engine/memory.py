"""记忆管理 — add/update/forget 操作角色 JSON 的 private_dynamic / public_dynamic sections。"""
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .utils import (
    log, read_json, write_json, load_state, save_state,
    CHARACTERS_DIR, SECTION_KEYS,
    load_character, save_character, char_file_path,
)

_memory_lock = threading.Lock()


def _world_now() -> str:
    """获取世界当前时间（来自 state.json），若无则用系统时间。"""
    state = load_state()
    t = state.get("current_time", "")
    if t:
        return t[:19]
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ── 加载所有记忆条目（供检索系统使用）────────────────────────

def load_all_entries(character: str) -> list[dict]:
    """加载角色所有板块的记忆条目（带 section 信息）。
    
    自动将 'text' 字段规范化为 'content'，确保下游代码统一使用 .get("content")。
    """
    data = load_character(character)
    entries = []
    for key in SECTION_KEYS:
        for entry in data.get(key, []):
            e = dict(entry)
            e["_section"] = key
            # 统一字段名：text → content
            if "text" in e and "content" not in e:
                e["content"] = e.pop("text")
            entries.append(e)
    return entries


# ── Embedding 缓存（独立文件）────────────────────────────

def _emb_cache_path(character: str) -> Path:
    """获取 embedding 缓存文件路径。"""
    return CHARACTERS_DIR / f".{character}.emb_cache.json"


def load_emb_cache(character: str) -> dict:
    """加载 embedding 缓存。返回 {content: [floats]}。"""
    path = _emb_cache_path(character)
    if path.exists():
        return read_json(path)
    return {}


def save_emb_cache(character: str, cache: dict):
    """保存 embedding 缓存。"""
    write_json(_emb_cache_path(character), cache)


def inject_embeddings(meta: list, character: str):
    """将缓存中的 embedding 注入到 meta 条目中（内存操作，不写文件）。"""
    cache = load_emb_cache(character)
    for m in meta:
        content = m.get("content", "")
        if content in cache:
            m["embedding"] = cache[content]


# ── TTL 解析 ─────────────────────────────────────────────

def parse_ttl(ttl_str: str) -> str:
    """将人类可读的 TTL 转为存储格式。返回 '永久', 'Nh', 或 'Nm'。"""
    ttl_str = str(ttl_str).strip()
    if ttl_str == "永久":
        return "永久"
    if re.match(r"^\d+$", ttl_str):
        val = int(ttl_str)
        return f"{val}h" if val > 0 else "永久"
    m = re.match(r"^(\d+)\s*(天|小时|分钟|h|d|m)$", ttl_str)
    if not m:
        log("warning", f"无效 TTL 格式: '{ttl_str}'，默认使用 '永久'")
        return "永久"
    val, unit = int(m.group(1)), m.group(2)
    if unit in ("天", "d"):
        return f"{val * 24}h"
    if unit in ("分钟", "m"):
        return f"{val}m"
    return f"{val}h"


# ── 记忆 CRUD ─────────────────────────────────────────────

def add_memory(
    character: str,
    content: str,
    ttl: str,
    visibility: str = "private",
):
    """为角色添加记忆。"""
    with _memory_lock:
        section = "public_dynamic" if visibility == "public" else "private_dynamic"
        data = load_character(character)
        entries = data.setdefault(section, [])

        ttl_stored = parse_ttl(ttl)
        entries.append({
            "content": content,
            "ttl": ttl_stored,
            "created": _world_now(),
            "hit_count": 0,
        })

        save_character(character, data)

        tag = "公开" if visibility == "public" else "私密"
        log("info", f"记忆 add [{character}] ({tag}): {content[:50]}")

    _increment_add_count(character)


def _increment_add_count(character: str):
    """递增角色新增记忆计数，超过阈值触发合并。"""
    state = load_state()
    chars = state.setdefault("characters", {})
    cs = chars.setdefault(character, {})
    count = cs.get("memory_add_count", 0) + 1
    if count >= 20:
        cs["memory_add_count"] = 0
        save_state(state)
        log("info", f"记忆合并触发 [{character}]: 新增已达 {count} 条")
        try:
            merge_memories(character)
        except Exception as e:
            log("warning", f"记忆合并失败 [{character}]: {e}")
    else:
        cs["memory_add_count"] = count
        save_state(state)


def _decrement_add_count(character: str):
    """递减角色新增记忆计数（删除记忆时调用），避免因删除后净增量不足而误触发合并。"""
    state = load_state()
    chars = state.setdefault("characters", {})
    cs = chars.setdefault(character, {})
    count = cs.get("memory_add_count", 0)
    if count > 0:
        cs["memory_add_count"] = count - 1
        save_state(state)


def update_memory(character: str, match: str, content: str, ttl: str, visibility: str = ""):
    """更新角色的记忆（在 dynamic sections 中查找）。

    如果 visibility 指定了新的可见性（public/private），记忆会被移动到对应 section。
    """
    with _memory_lock:
        data = load_character(character)

        # 在 dynamic sections 中查找
        for section in ("private_dynamic", "public_dynamic"):
            entries = data.get(section, [])
            for i, entry in enumerate(entries):
                entry_text = entry.get("text", "") or entry.get("content", "")
                if match in entry_text:
                    old = entry_text
                    # 统一写入 content 键，清理旧 text 键
                    entry["content"] = content
                    entry.pop("text", None)
                    entry["ttl"] = parse_ttl(ttl)
                    entry["created"] = _world_now()

                    # 如果 visibility 指定了新 section 且与当前不同，移动条目
                    target_section = ""
                    if visibility == "public":
                        target_section = "public_dynamic"
                    elif visibility == "private":
                        target_section = "private_dynamic"

                    if target_section and target_section != section:
                        entries.pop(i)
                        data.setdefault(target_section, []).append(entry)
                        log("info", f"记忆 update [{character}]: '{old[:30]}' → '{content[:30]}' (移动到 {target_section})")
                    else:
                        log("info", f"记忆 update [{character}]: '{old[:30]}' → '{content[:30]}'")

                    save_character(character, data)
                    return

        log("warning", f"记忆 update [{character}]: 找不到匹配 '{match}'")


def forget_memory(character: str, match: str):
    """删除角色的记忆（在 dynamic sections 中查找）。"""
    with _memory_lock:
        data = load_character(character)

        for section in ("private_dynamic", "public_dynamic"):
            entries = data.get(section, [])
            for i, entry in enumerate(entries):
                entry_text = entry.get("text", "") or entry.get("content", "")
                if match in entry_text:
                    old = entry_text
                    entries.pop(i)
                    save_character(character, data)
                    log("info", f"记忆 forget [{character}]: '{old[:50]}'")
                    _decrement_add_count(character)
                    return

        log("warning", f"记忆 forget [{character}]: 找不到匹配 '{match}'")


def execute_memory_ops(character: str, operations: list[dict]):
    """执行一批记忆操作（来自 LLM 分析结果）。"""
    for op in operations:
        action = op.get("action", "")
        content = op.get("content", "")
        match_kw = op.get("match", "")
        ttl = op.get("ttl", "永久")
        visibility = op.get("visibility", "private")

        try:
            if action == "add" and content:
                add_memory(character, content, ttl, visibility=visibility)
            elif action == "update" and content and match_kw:
                update_memory(character, match_kw, content, ttl, visibility=visibility)
            elif action == "forget" and match_kw:
                forget_memory(character, match_kw)
        except Exception as e:
            log("warning", f"记忆操作失败 [{character}] {action}: {e}")


# ── 记忆清理（TTL 过期）──────────────────────────────────

def cleanup_expired(character: str) -> int:
    """清理角色过期的记忆条目。返回清理条数。"""
    with _memory_lock:
        data = load_character(character)
        now = datetime.strptime(_world_now(), "%Y-%m-%dT%H:%M:%S")
        total_removed = 0

        for section in ("private_dynamic", "public_dynamic"):
            entries = data.get(section, [])
            keep = []
            for entry in entries:
                ttl = entry.get("ttl", "永久")
                if ttl == "永久":
                    keep.append(entry)
                    continue
                created = datetime.strptime(entry["created"], "%Y-%m-%dT%H:%M:%S")
                val = re.match(r"^(\d+)(h|m)$", ttl)
                if not val:
                    keep.append(entry)
                    continue
                amount, unit = int(val.group(1)), val.group(2)
                if unit == "h":
                    expire = created + timedelta(hours=amount)
                else:
                    expire = created + timedelta(minutes=amount)
                if now >= expire:
                    total_removed += 1
                else:
                    keep.append(entry)
            data[section] = keep

        if total_removed > 0:
            save_character(character, data)
            log("info", f"记忆清理 [{character}]: 移除 {total_removed} 条过期记忆")
        return total_removed


def cleanup_all_characters():
    """清理所有角色的过期记忆。"""
    if not CHARACTERS_DIR.exists():
        return
    total = 0
    for f in CHARACTERS_DIR.iterdir():
        if f.is_file() and f.suffix == ".json" and not f.name.startswith("."):
            total += cleanup_expired(f.stem)
    if total > 0:
        log("info", f"全局记忆清理完成: 共移除 {total} 条")


# ── 记忆合并（LLM 驱动）─────────────────────────────────

def merge_memories(character: str) -> int:
    """LLM 驱动的记忆合并：将内容相近的记忆合并为一条。返回合并次数。"""
    from .llm import chat_json
    from .utils import read_file, PROMPTS_DIR

    # 收集所有 dynamic 条目，分离永久记忆（永久记忆不参与合并）
    data = load_character(character)
    all_entries = []          # 参与合并的非永久条目
    permanent = {}            # section -> [永久条目]，合并后原样保留
    for section in ("private_dynamic", "public_dynamic"):
        permanent[section] = []
        for entry in data.get(section, []):
            if entry.get("ttl") == "永久":
                permanent[section].append(entry)
            else:
                all_entries.append((section, entry))

    if len(all_entries) < 5:
        return 0

    # 按板块分组展示，让 LLM 知道哪些是同板块的
    section_label = {"private_dynamic": "私密动态", "public_dynamic": "公开动态"}
    items_lines = []
    for i, (sec, e) in enumerate(all_entries):
        e_text = e.get("text", "") or e.get("content", "")
        items_lines.append(f"  {i+1}. [{section_label[sec]}] {e_text}（TTL: {e.get('ttl', '永久')}）")
    items = "\n".join(items_lines)
    template = read_file(PROMPTS_DIR / "memory_merge.md")
    prompt = template.format(character=character, items=items)

    total_merged = 0
    try:
        result = chat_json([{"role": "user", "content": prompt}], config_key="analysis_reasoning", label="记忆合并")
        merges = result.get("merges", [])
        if not merges:
            return 0

        with _memory_lock:
            data = load_character(character)
            # 重新收集
            all_entries = []
            for section in ("private_dynamic", "public_dynamic"):
                for entry in data.get(section, []):
                    all_entries.append((section, entry))

            # 处理合并
            remove_set = set()
            for merge in merges:
                remove_indices = merge.get("remove_indices", [])
                keep_indices = merge.get("keep_indices", [])
                merged = merge.get("merged_content", "")
                merged_ttl = merge.get("ttl", "")
                if not remove_indices or not merged:
                    continue

                # 验证：所有涉及的条目必须属于同一板块，拒绝跨板块合并
                all_indices = list(set((keep_indices or []) + remove_indices))
                sections_involved = set()
                for idx in all_indices:
                    idx_0 = idx - 1
                    if 0 <= idx_0 < len(all_entries):
                        sections_involved.add(all_entries[idx_0][0])
                if len(sections_involved) > 1:
                    log("warning", f"记忆合并 [{character}]: 拒绝跨板块合并 {all_indices} (涉及 {sections_involved})")
                    continue

                if keep_indices:
                    ki = keep_indices[0] - 1
                    if 0 <= ki < len(all_entries):
                        all_entries[ki][1]["content"] = merged
                        # 如果 LLM 指定了新的 TTL，更新之
                        if merged_ttl:
                            all_entries[ki][1]["ttl"] = parse_ttl(merged_ttl)

                for ri in remove_indices:
                    ri_0 = ri - 1
                    if 0 <= ri_0 < len(all_entries):
                        remove_set.add(ri_0)
                total_merged += len(remove_indices)

            # 重建 sections：永久记忆放前面 + 合并后的非永久记忆
            for section in ("private_dynamic", "public_dynamic"):
                merged_entries = [
                    entry for i, (sec, entry) in enumerate(all_entries)
                    if sec == section and i not in remove_set
                ]
                data[section] = permanent.get(section, []) + merged_entries
            save_character(character, data)

    except Exception as e:
        log("warning", f"记忆合并失败 [{character}]: {e}")

    if total_merged > 0:
        log("info", f"记忆合并 [{character}]: 合并了 {total_merged} 条")
    return total_merged
