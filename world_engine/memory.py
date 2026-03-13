"""记忆管理 — 基于 ChromaDB 的 add/update/forget 操作。"""
import re
import threading
from datetime import datetime, timedelta

from .utils import log, load_state, save_state
from . import chroma_store

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

    返回: [{"id": ..., "content": ..., "ttl": ..., "created": ..., "_section": ...}, ...]
    """
    return chroma_store.get_entries(character)


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
        ttl_stored = parse_ttl(ttl)
        entry = {
            "content": content,
            "ttl": ttl_stored,
            "created": _world_now(),
            "hit_count": 0,
        }
        chroma_store.add_entries(character, section, [entry])

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
    """递减角色新增记忆计数。"""
    state = load_state()
    chars = state.setdefault("characters", {})
    cs = chars.setdefault(character, {})
    count = cs.get("memory_add_count", 0)
    if count > 0:
        cs["memory_add_count"] = count - 1
        save_state(state)


def update_memory(character: str, match: str, content: str, ttl: str, visibility: str = ""):
    """更新角色的记忆（在 dynamic sections 中查找匹配的条目）。"""
    with _memory_lock:
        for section in ("private_dynamic", "public_dynamic"):
            entries = chroma_store.get_entries(character, section)
            for entry in entries:
                entry_text = entry.get("content", "")
                if match in entry_text:
                    old = entry_text
                    # 确定目标 section
                    target_section = ""
                    if visibility == "public":
                        target_section = "public_dynamic"
                    elif visibility == "private":
                        target_section = "private_dynamic"

                    if target_section and target_section != section:
                        # 跨板块移动：删旧增新
                        chroma_store.delete_entry(entry["id"])
                        chroma_store.add_entries(character, target_section, [{
                            "content": content,
                            "ttl": parse_ttl(ttl),
                            "created": _world_now(),
                            "hit_count": entry.get("hit_count", 0),
                        }])
                        log("info", f"记忆 update [{character}]: '{old[:30]}' → '{content[:30]}' (移动到 {target_section})")
                    else:
                        # 原地更新
                        chroma_store.update_entry(entry["id"], content, {
                            "ttl": parse_ttl(ttl),
                            "created": _world_now(),
                        })
                        log("info", f"记忆 update [{character}]: '{old[:30]}' → '{content[:30]}'")
                    return

        log("warning", f"记忆 update [{character}]: 找不到匹配 '{match}'，fallback 到 add")
        add_memory(character, content, ttl, visibility=visibility or "private")


def forget_memory(character: str, match: str):
    """删除角色的记忆（在 dynamic sections 中查找）。"""
    with _memory_lock:
        for section in ("private_dynamic", "public_dynamic"):
            entries = chroma_store.get_entries(character, section)
            for entry in entries:
                entry_text = entry.get("content", "")
                if match in entry_text:
                    chroma_store.delete_entry(entry["id"])
                    log("info", f"记忆 forget [{character}]: '{entry_text[:50]}'")
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
        now = datetime.strptime(_world_now(), "%Y-%m-%dT%H:%M:%S")
        total_removed = 0

        for section in ("private_dynamic", "public_dynamic"):
            entries = chroma_store.get_entries(character, section)
            for entry in entries:
                ttl = entry.get("ttl", "永久")
                if ttl == "永久":
                    continue
                created = entry.get("created", "")
                if not created:
                    continue
                try:
                    ct = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                val = re.match(r"^(\d+)(h|m)$", ttl)
                if not val:
                    continue
                amount, unit = int(val.group(1)), val.group(2)
                if unit == "h":
                    expire = ct + timedelta(hours=amount)
                else:
                    expire = ct + timedelta(minutes=amount)
                if now >= expire:
                    chroma_store.delete_entry(entry["id"])
                    total_removed += 1

        if total_removed > 0:
            log("info", f"记忆清理 [{character}]: 移除 {total_removed} 条过期记忆")
        return total_removed


def cleanup_all_characters():
    """清理所有角色的过期记忆。"""
    chars = chroma_store.get_all_characters()
    total = 0
    for char in chars:
        total += cleanup_expired(char)
    if total > 0:
        log("info", f"全局记忆清理完成: 共移除 {total} 条")


# ── 记忆合并（LLM 驱动）─────────────────────────────────

def merge_memories(character: str) -> int:
    """LLM 驱动的记忆合并：将内容相近的记忆合并为一条。返回合并次数。"""
    from .llm import chat_json
    from .utils import read_file, PROMPTS_DIR

    # 收集所有 dynamic 条目，分离永久记忆
    all_entries = []      # 参与合并的非永久条目
    permanent = {}        # section -> [永久条目]
    for section in ("private_dynamic", "public_dynamic"):
        permanent[section] = []
        entries = chroma_store.get_entries(character, section)
        for entry in entries:
            if entry.get("ttl") == "永久":
                permanent[section].append(entry)
            else:
                all_entries.append((section, entry))

    if len(all_entries) < 5:
        return 0

    # 按板块分组展示
    section_label = {"private_dynamic": "私密动态", "public_dynamic": "公开动态"}
    items_lines = []
    for i, (sec, e) in enumerate(all_entries):
        items_lines.append(f"  {i+1}. [{section_label[sec]}] {e.get('content', '')}（TTL: {e.get('ttl', '永久')}）")
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
            # 重新收集（避免并发修改）
            all_entries = []
            for section in ("private_dynamic", "public_dynamic"):
                entries = chroma_store.get_entries(character, section)
                for entry in entries:
                    all_entries.append((section, entry))

            remove_ids = set()
            for merge in merges:
                remove_indices = merge.get("remove_indices", [])
                keep_indices = merge.get("keep_indices", [])
                merged = merge.get("merged_content", "")
                merged_ttl = merge.get("ttl", "")
                if not remove_indices or not merged:
                    continue

                # 验证同板块
                all_indices = list(set((keep_indices or []) + remove_indices))
                sections_involved = set()
                for idx in all_indices:
                    idx_0 = idx - 1
                    if 0 <= idx_0 < len(all_entries):
                        sections_involved.add(all_entries[idx_0][0])
                if len(sections_involved) > 1:
                    log("warning", f"记忆合并 [{character}]: 拒绝跨板块合并 {all_indices}")
                    continue

                if keep_indices:
                    ki = keep_indices[0] - 1
                    if 0 <= ki < len(all_entries):
                        entry = all_entries[ki][1]
                        meta_updates = {}
                        if merged_ttl:
                            meta_updates["ttl"] = parse_ttl(merged_ttl)
                        chroma_store.update_entry(entry["id"], merged, meta_updates)

                for ri in remove_indices:
                    ri_0 = ri - 1
                    if 0 <= ri_0 < len(all_entries):
                        remove_ids.add(all_entries[ri_0][1]["id"])
                total_merged += len(remove_indices)

            # 删除被合并的条目
            for entry_id in remove_ids:
                chroma_store.delete_entry(entry_id)

    except Exception as e:
        log("warning", f"记忆合并失败 [{character}]: {e}")

    if total_merged > 0:
        log("info", f"记忆合并 [{character}]: 合并了 {total_merged} 条")
    return total_merged
