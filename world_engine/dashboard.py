"""Dashboard — FastAPI + 纯 HTML/CSS/JS 浏览器管理界面。

标签页：会话 | 事件 | 角色 | 地图 | 设置
启动：python -m world_engine.dashboard
"""
import json
import re
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .utils import (
    log, load_config, reload_config, load_state, save_state,
    read_file, write_file, read_json, write_json,
    PROJECT_DIR, DATA_DIR, WORLD_DIR, CHARACTERS_DIR, LOCATIONS_PATH, RULES_PATH,
    CONFIG_PATH, STATE_PATH, EVENTS_DIR, LORE_PATH,
    SESSIONS_DIR, ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR,
)

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

HTML_PATH = Path(__file__).parent / "dashboard.html"


# ── 工具 ──────────────────────────────────────────────────




def _parse_locations() -> list[dict]:
    """从当前世界的 locations.json 加载地点列表。
    兼容旧格式（description 字符串）和新格式（entries 数组含 TTL）。
    """
    if not LOCATIONS_PATH.exists():
        return []
    raw = read_json(LOCATIONS_PATH)
    for loc in raw:
        if "entries" not in loc:
            # 旧格式：从 description 字符串转换
            desc = loc.pop("description", "")
            loc["entries"] = [{"text": desc, "ttl": "永久", "created": ""}] if desc else []
        else:
            loc.pop("description", None)
        # 保留 sub_locations（没有就给空数组）
        if "sub_locations" not in loc:
            loc["sub_locations"] = []
    return raw


def _save_locations(locations: list[dict]):
    """保存地点到 locations.json。
    保留 entries 数组（含 TTL/created），同时生成 description 供 location.py 兼容读取。
    """
    clean = []
    for loc in locations:
        entries = loc.get("entries", [])
        clean_entries = []
        for e in entries:
            text = e.get("text", "").strip()
            if text:
                clean_entries.append({
                    "text": text,
                    "ttl": e.get("ttl", "永久"),
                    "created": e.get("created", ""),
                })
        desc = "\n".join(e["text"] for e in clean_entries)
        item = {
            "name": loc.get("name", ""),
            "x": loc.get("x", 0),
            "y": loc.get("y", 0),
            "description": desc,
            "entries": clean_entries,
        }
        # 保留 sub_locations
        sub_locs = loc.get("sub_locations", [])
        if sub_locs:
            item["sub_locations"] = sub_locs
        clean.append(item)
    write_json(LOCATIONS_PATH, clean)


from datetime import datetime as _dt


def _load_characters() -> list[dict]:
    """加载所有角色数据（从 ChromaDB）。"""
    from .utils import SECTION_KEYS
    from . import chroma_store
    char_names = chroma_store.get_all_characters()
    chars = []
    for name in char_names:
        char = {"name": name}
        for key in SECTION_KEYS:
            entries = chroma_store.get_entries(name, key)
            char[key] = [
                {"text": e.get("content", ""), "ttl": e.get("ttl", "永久"), "created": e.get("created", "")}
                for e in entries if e.get("content", "").strip()
            ]
        chars.append(char)
    return chars


def _load_sessions(directory: Path) -> list[dict]:
    """加载某目录下所有会话。"""
    sessions = []
    if not directory.exists():
        return sessions
    for f in sorted(directory.glob("*.json")):
        try:
            data = read_json(f)
            data["_file"] = f.name
            sessions.append(data)
        except Exception:
            pass
    return sessions


def _load_events() -> list[dict]:
    """加载事件列表（从 events 目录扫描）。"""
    if not EVENTS_DIR.exists():
        return []
    events = []
    for f in sorted(EVENTS_DIR.glob("*.json")):
        try:
            events.append(read_json(f))
        except Exception:
            pass
    return events


# ── API ───────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/state")
def api_state():
    return load_state()


@app.post("/api/state")
async def api_save_state(req: Request):
    data = await req.json()
    log("info", f"[Dashboard] 保存世界状态: time={data.get('current_time')}, player={data.get('player_character')}, world={data.get('world_name')}")
    # 检测世界名称变更 → 重命名 media 文件夹
    try:
        old_state = load_state()
        old_name = old_state.get("world_name", "")
        new_name = data.get("world_name", "")
        if old_name and new_name and old_name != new_name:
            log("info", f"[Dashboard] 世界名称变更: {old_name} → {new_name}")
            from .utils import MEDIA_DIR
            old_dir = MEDIA_DIR / old_name
            new_dir = MEDIA_DIR / new_name
            if old_dir.exists() and not new_dir.exists():
                old_dir.rename(new_dir)
    except Exception:
        pass
    # 合并写入：只更新前端修改的字段，不覆盖 Bot 运行时产生的字段
    from .utils import state_transaction
    with state_transaction() as st:
        if "current_time" in data:
            st["current_time"] = data["current_time"]
        if "world_name" in data:
            st["world_name"] = data["world_name"]
        if "player_character" in data:
            st["player_character"] = data["player_character"]
        # day_of_week 由时间自动计算，从前端删除后在这里重新计算
        if "current_time" in data:
            try:
                from datetime import datetime as _dt
                _dow = ['周一','周二','周三','周四','周五','周六','周日']
                st["day_of_week"] = _dow[_dt.fromisoformat(data["current_time"]).weekday()]
            except Exception:
                pass
    return {"ok": True}


@app.get("/api/config")
def api_config():
    return reload_config()


@app.post("/api/config")
async def api_save_config(req: Request):
    data = await req.json()
    log("info", f"[Dashboard] 保存全局配置")
    write_json(CONFIG_PATH, data)
    reload_config()
    return {"ok": True}


@app.get("/api/sessions")
def api_sessions():
    return {
        "active": _load_sessions(ACTIVE_SESSIONS_DIR),
        "archive": _load_sessions(ARCHIVE_SESSIONS_DIR),
    }


@app.get("/api/events")
def api_events():
    return _load_events()


@app.get("/api/scheduled-events")
def api_scheduled_events():
    """获取所有预定事件（从 state.json 的 scheduled_events）。"""
    from .events import get_all_events
    return get_all_events()


@app.post("/api/scheduled-events")
async def api_add_scheduled_event(req: Request):
    """新增预定事件。"""
    body = await req.json()
    from .events import add_event
    time = body.get("time", "")
    if not time:
        return JSONResponse({"error": "缺少 time"}, 400)
    eid = add_event(
        time=time,
        participants=body.get("participants", []),
        description=body.get("description", ""),
        created_by=body.get("created_by", "dashboard"),
        location=body.get("location", ""),
        sub_location=body.get("sub_location", ""),
        flexible_window=body.get("flexible_window", 30),
    )
    log("info", f"[Dashboard] 新增预定事件: {eid}")
    return {"ok": True, "id": eid}


@app.put("/api/scheduled-events/{event_id}")
async def api_update_scheduled_event(event_id: str, req: Request):
    """更新预定事件。"""
    body = await req.json()
    from .events import update_event
    updates = {k: v for k, v in body.items() if k not in ("id", "action")}
    ok = update_event(event_id=event_id, **updates)
    if not ok:
        return JSONResponse({"error": f"未找到事件 {event_id}"}, 404)
    log("info", f"[Dashboard] 更新预定事件: {event_id}")
    return {"ok": True}


@app.delete("/api/scheduled-events/{event_id}")
async def api_delete_scheduled_event(event_id: str):
    """删除预定事件。"""
    from .events import delete_event
    ok = delete_event(event_id=event_id)
    if not ok:
        return JSONResponse({"error": f"未找到事件 {event_id}"}, 404)
    log("info", f"[Dashboard] 删除预定事件: {event_id}")
    return {"ok": True}

@app.get("/api/characters")
def api_characters():
    return _load_characters()


@app.post("/api/character/{name}/{field}")
async def api_save_character(name: str, field: str, req: Request):
    from .utils import SECTION_KEYS
    from . import chroma_store
    body = await req.json()
    if field not in SECTION_KEYS:
        return JSONResponse({"error": f"unknown field: {field}"}, 400)
    entries = body.get("entries", [])

    normalized = [
        {"content": e.get("text", "").strip(), "ttl": e.get("ttl", "永久"), "created": e.get("created", ""), "hit_count": 0}
        for e in entries if e.get("text", "").strip()
    ]
    chroma_store.replace_section(name, field, normalized)
    log("info", f"[Dashboard] 保存角色 {name}/{field}: {len(normalized)} 条")

    # 如果是新角色，自动注册到 state.json
    state = load_state()
    chars = state.setdefault("characters", {})
    if name not in chars:
        locs = _parse_locations()
        default_loc = locs[0]["name"] if locs else "未知"
        chars[name] = {"location": default_loc, "sub_location": "", "activity": ""}
        save_state(state)
        log("info", f"[Dashboard] 新角色 '{name}' 已注册到 state.json，位置: {default_loc}")

    return {"ok": True}


@app.post("/api/characters/bulk")
async def api_save_characters_bulk(req: Request):
    """批量保存所有角色数据（一次请求替代 N×4 次）。"""
    from .utils import SECTION_KEYS
    from . import chroma_store
    body = await req.json()
    characters = body.get("characters", [])
    saved_count = 0
    for char in characters:
        name = char.get("name", "")
        if not name:
            continue
        for field in SECTION_KEYS:
            entries = char.get(field, [])
            normalized = [
                {"content": e.get("text", "").strip(), "ttl": e.get("ttl", "永久"), "created": e.get("created", ""), "hit_count": 0}
                for e in entries if e.get("text", "").strip()
            ]
            chroma_store.replace_section(name, field, normalized)
        saved_count += 1
    log("info", f"[Dashboard] 批量保存角色: {saved_count} 个")
    return {"ok": True, "count": saved_count}


@app.delete("/api/character/{name}")
async def api_delete_character(name: str):
    log("info", f"[Dashboard] 删除角色: {name}")
    from . import chroma_store
    chroma_store.delete_entries_by_filter(name)
    # 从 state.json 中移除角色状态
    try:
        state = load_state()
        chars = state.get("characters", {})
        if name in chars:
            del chars[name]
            save_state(state)
            log("info", f"[Dashboard] 已从 state.json 移除角色 '{name}'")
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/locations")
def api_locations():
    return _parse_locations()


@app.post("/api/locations")
async def api_save_locations(req: Request):
    data = await req.json()
    _save_locations(data)
    log("info", f"[Dashboard] 保存地点: {len(data)} 个")
    # 刷新 Bot 侧的地点缓存
    try:
        from .location import reload_location_manager
        reload_location_manager()
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/lore")
def api_lore():
    """获取当前世界的 lore.json。"""
    from .utils import load_lore
    return load_lore()


@app.post("/api/lore")
async def api_save_lore(req: Request):
    """保存当前世界的 lore.json。"""
    data = await req.json()
    write_json(LORE_PATH, data)
    # 刷新缓存
    from .utils import reload_lore
    reload_lore()
    log("info", f"[Dashboard] 保存世界设定 (lore)")
    return {"ok": True}


@app.get("/api/models")
def api_models():
    """代理获取 302.ai 可用模型列表，按类型分类。"""
    config = load_config()
    try:
        base_url = config.get("api", {}).get("base_url", "")
        api_key = config.get("api", {}).get("api_key", "")
        resp = requests.get(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8
        )
        if resp.status_code != 200:
            return {}
        all_ids = [m["id"] for m in resp.json().get("data", [])]
    except Exception:
        return {}

    # 按关键词分类
    embedding_kw = ["embed", "bge", "bce-embedding"]
    image_kw = ["image", "dall-e", "flux", "stable-diffusion", "midjourney", "minimax-image", "minimaxi-image"]

    def match(mid, keywords):
        ml = mid.lower()
        return any(k in ml for k in keywords)

    embedding = sorted([m for m in all_ids if match(m, embedding_kw)])
    image = sorted([m for m in all_ids if match(m, image_kw)])
    # chat = 排除掉 embedding/image 后的剩余模型
    non_chat = set(embedding + image)
    chat = sorted([m for m in all_ids if m not in non_chat])

    return {
        "chat": chat,
        "analysis": chat,
        "embedding": embedding,
        "image": image,
    }


# ── Archive (saves/) ─────────────────────────────────────

import shutil

SAVES_DIR = DATA_DIR / "saves"
SAVES_DIR.mkdir(parents=True, exist_ok=True)


def _list_saves() -> list[dict]:
    """扫描 saves/ 目录，返回存档列表。"""
    result = []
    if not SAVES_DIR.exists():
        return result
    for d in sorted(SAVES_DIR.iterdir()):
        if d.is_dir():
            # 优先从 state.json 的 world_name 获取名称
            state_path = d / "state.json"
            if state_path.exists():
                state = read_json(state_path)
                name = state.get("world_name", d.name)
            else:
                name = d.name
            result.append({"id": d.name, "name": name})
    return result


def _resolve_save_dir(aid: str) -> Path:
    """解析存档 id 到实际路径。_current → current/。"""
    if aid == "_current":
        return WORLD_DIR
    return SAVES_DIR / aid


def _copy_data_to(dest: Path):
    """把当前世界目录下的可存档数据复制到 dest。"""
    dest.mkdir(parents=True, exist_ok=True)
    for fname in ("state.json", "locations.json", "lore.json", "temp_characters.json"):
        src = WORLD_DIR / fname
        if src.exists():
            shutil.copy2(src, dest / fname)
    # events/
    src_events = EVENTS_DIR
    dst_events = dest / "events"
    if dst_events.exists():
        shutil.rmtree(dst_events)
    if src_events.exists():
        shutil.copytree(src_events, dst_events)
    # chromadb/
    from . import chroma_store
    chroma_store.copy_chroma_to(dest)
    # sessions/
    src_sess = SESSIONS_DIR
    dst_sess = dest / "sessions"
    if dst_sess.exists():
        shutil.rmtree(dst_sess)
    if src_sess.exists():
        shutil.copytree(src_sess, dst_sess)


def _copy_data_from(src: Path):
    """从 src 恢复数据到当前世界目录（先清空再复制）。"""
    # 先清空当前世界目录的数据
    for fname in ("state.json", "locations.json", "lore.json", "temp_characters.json"):
        target = WORLD_DIR / fname
        if target.exists():
            target.unlink()
    if EVENTS_DIR.exists():
        shutil.rmtree(EVENTS_DIR)
    if SESSIONS_DIR.exists():
        shutil.rmtree(SESSIONS_DIR)
    # 确保基本目录结构
    ACTIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    # 创建默认空文件（防止 API 报错）
    if not (WORLD_DIR / "state.json").exists():
        default_time = load_config().get("world", {}).get("start_time", "2026-01-01T08:00:00")
        write_json(WORLD_DIR / "state.json", {"world_name": "", "current_time": default_time, "characters": {}})
    if not LOCATIONS_PATH.exists():
        write_json(LOCATIONS_PATH, [])
    # 从存档复制
    for fname in ("state.json", "locations.json", "lore.json", "temp_characters.json"):
        sf = src / fname
        if sf.exists():
            shutil.copy2(sf, WORLD_DIR / fname)
    # events/
    src_events = src / "events"
    if src_events.exists():
        if EVENTS_DIR.exists():
            shutil.rmtree(EVENTS_DIR)
        shutil.copytree(src_events, EVENTS_DIR)
    # chromadb/
    from . import chroma_store
    chroma_store.copy_chroma_from(src)
    # sessions/
    src_sess = src / "sessions"
    if src_sess.exists():
        if SESSIONS_DIR.exists():
            shutil.rmtree(SESSIONS_DIR)
        shutil.copytree(src_sess, SESSIONS_DIR)
        ACTIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/archives/list")
def api_archives_list():
    return _list_saves()


@app.post("/api/archives/new")
async def api_archives_new(req: Request):
    """创建空白存档（不复制当前数据）。"""
    body = await req.json()
    name = body.get("name", "未命名")
    folder = name
    dest = SAVES_DIR / folder
    if dest.exists():
        return JSONResponse({"error": f"存档 '{name}' 已存在"}, 409)
    dest.mkdir(parents=True, exist_ok=True)
    # 创建空白目录结构
    (dest / "characters").mkdir(exist_ok=True)
    (dest / "events").mkdir(exist_ok=True)
    (dest / "sessions" / "active").mkdir(parents=True, exist_ok=True)
    (dest / "sessions" / "archive").mkdir(parents=True, exist_ok=True)
    # 空白 state.json
    default_time = load_config().get("world", {}).get("start_time", "2026-01-01T08:00:00")
    write_json(dest / "state.json", {
        "world_name": name,
        "current_time": default_time,
        "characters": {},
    })
    # 空白 locations.json
    write_json(dest / "locations.json", [])
    # 空白 lore.json
    write_json(dest / "lore.json", {
        "world_premise": "",
        "era": "",
        "tone": "",
        "glossary": {},
    })
    log("info", f"[Dashboard] 创建空白存档: {name}")
    return {"ok": True, "id": folder}


@app.post("/api/archives/save")
async def api_archives_save(req: Request):
    """保存当前世界数据为存档。"""
    body = await req.json()
    name = body.get("name", "未命名")
    folder = name
    dest = SAVES_DIR / folder
    if dest.exists():
        return JSONResponse({"error": f"存档 '{name}' 已存在"}, 409)
    dest.mkdir(parents=True, exist_ok=True)
    # 把当前世界数据完整复制到存档
    _copy_data_to(dest)
    # 把用户输入的存档名称写入 state.json 的 world_name
    state_path = dest / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        state["world_name"] = name
        write_json(state_path, state)
    log("info", f"[Dashboard] 保存当前为存档: {name}")
    return {"ok": True, "id": folder}


@app.get("/api/archives/{aid}")
def api_archive_get(aid: str):
    d = _resolve_save_dir(aid)
    if not d.exists():
        return JSONResponse({"error": "not found"}, 404)
    result = {}
    sp = d / "state.json"
    result["state"] = read_json(sp) if sp.exists() else {}

    # 角色数据：_current 世界从 ChromaDB 读取，存档优先 ChromaDB 回退 JSON
    chars = []
    from .utils import SECTION_KEYS
    chroma_dir = d / "chromadb"
    if chroma_dir.exists():
        import chromadb as _chromadb
        try:
            client = _chromadb.PersistentClient(path=str(chroma_dir))
            collection = client.get_or_create_collection(name="memories")
            all_data = collection.get()
            # 按角色分组
            char_map = {}
            for i, eid in enumerate(all_data["ids"]):
                meta = all_data["metadatas"][i]
                name = meta.get("character", "")
                section = meta.get("section", "")
                content = all_data["documents"][i]
                if name not in char_map:
                    char_map[name] = {"name": name}
                    for key in SECTION_KEYS:
                        char_map[name][key] = []
                if section in SECTION_KEYS:
                    char_map[name][section].append({
                        "text": content,
                        "ttl": meta.get("ttl", "永久"),
                        "created": meta.get("created", ""),
                    })
            chars = sorted(char_map.values(), key=lambda c: c["name"])
        except Exception:
            pass

    result["characters"] = chars
    loc_path = d / "locations.json"
    locs_raw = read_json(loc_path) if loc_path.exists() else []
    for loc in locs_raw:
        desc = loc.pop("description", "")
        if "entries" not in loc:
            loc["entries"] = [{"text": desc}] if desc else []
        if "sub_locations" not in loc:
            loc["sub_locations"] = []
    result["locations"] = locs_raw
    # events/
    events_dir = d / "events"
    result["events"] = []
    if events_dir.exists():
        for f in sorted(events_dir.glob("*.json")):
            try:
                result["events"].append(read_json(f))
            except Exception:
                pass
    sess = {"active": [], "archive": []}
    for sub in ("active", "archive"):
        sd = d / "sessions" / sub
        if sd.exists():
            for f in sorted(sd.glob("*.json")):
                try:
                    data = read_json(f)
                    data["_file"] = f.name
                    sess[sub].append(data)
                except Exception:
                    pass
    result["sessions"] = sess
    # lore
    lore_path = d / "lore.json"
    result["lore"] = read_json(lore_path) if lore_path.exists() else {}
    return result


@app.post("/api/archives/{aid}/save")
async def api_archive_save_data(aid: str, req: Request):
    d = _resolve_save_dir(aid)
    if not d.exists():
        return JSONResponse({"error": "not found"}, 404)
    body = await req.json()
    if "state" in body:
        write_json(d / "state.json", body["state"])
    if "characters" in body:
        # 存档模式下角色数据保存到 ChromaDB
        from .utils import SECTION_KEYS
        import chromadb as _chromadb
        chroma_dir = d / "chromadb"
        chroma_dir.mkdir(parents=True, exist_ok=True)
        client = _chromadb.PersistentClient(path=str(chroma_dir))
        collection = client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
        # 清空旧数据
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        # 写入新数据
        for c in body["characters"]:
            name = c.get("name", "")
            for section in SECTION_KEYS:
                entries = c.get(section, [])
                for i, e in enumerate(entries):
                    text = e.get("text", "").strip()
                    if not text:
                        continue
                    collection.add(
                        ids=[f"{name}__{section}__{i:04d}"],
                        documents=[text],
                        metadatas=[{
                            "character": name,
                            "section": section,
                            "ttl": e.get("ttl", "永久"),
                            "created": e.get("created", ""),
                            "hit_count": 0,
                        }],
                    )
    if "locations" in body:
        clean_locs = []
        for loc in body["locations"]:
            entries = loc.get("entries", [])
            clean_entries = [
                {"text": e.get("text", "").strip(), "ttl": e.get("ttl", "永久"), "created": e.get("created", "")}
                for e in entries if e.get("text", "").strip()
            ]
            desc = "\n".join(e["text"] for e in clean_entries)
            item = {
                "name": loc.get("name", ""), "x": loc.get("x", 0), "y": loc.get("y", 0),
                "description": desc, "entries": clean_entries,
            }
            sub_locs = loc.get("sub_locations", [])
            if sub_locs:
                item["sub_locations"] = sub_locs
            clean_locs.append(item)
        write_json(d / "locations.json", clean_locs)
    if "lore" in body:
        write_json(d / "lore.json", body["lore"])
    log("info", f"[Dashboard] 保存存档数据: {aid}")
    return {"ok": True}


@app.post("/api/archives/apply/{aid}")
def api_archive_apply(aid: str):
    if aid == "_current":
        return {"ok": True}  # already current
    d = _resolve_save_dir(aid)
    if not d.exists():
        return JSONResponse({"error": "not found"}, 404)
    _copy_data_from(d)
    # 刷新内存中的单例缓存
    try:
        from .session import reload_session_manager
        reload_session_manager()
    except Exception:
        pass
    try:
        from .location import reload_location_manager
        reload_location_manager()
    except Exception:
        pass
    try:
        from .utils import reload_lore
        reload_lore()
    except Exception:
        pass
    try:
        from . import chroma_store
        chroma_store.reload_chroma()
    except Exception:
        pass
    log("info", f"[Dashboard] 加载存档到当前: {aid}")
    return {"ok": True}


@app.post("/api/archives/rename")
async def api_archive_rename(req: Request):
    body = await req.json()
    aid = body.get("id", "")
    new_name = body.get("name", "")
    if aid == "_current":
        return JSONResponse({"error": "不可重命名当前世界"}, 400)
    d = SAVES_DIR / aid
    if not d.exists():
        return JSONResponse({"error": "not found"}, 404)
    # 更新 state.json 的 world_name
    state_path = d / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        state["world_name"] = new_name
        write_json(state_path, state)
    else:
        write_json(state_path, {"world_name": new_name})
    new_folder = new_name
    new_path = SAVES_DIR / new_folder
    if new_path.exists() and new_path != d:
        return JSONResponse({"error": "目标名称已存在"}, 409)
    if new_path != d:
        d.rename(new_path)
    log("info", f"[Dashboard] 重命名存档: {aid} → {new_name}")
    return {"ok": True, "id": new_folder}


@app.delete("/api/archives/{aid}")
def api_archive_delete(aid: str):
    if aid == "_current":
        return JSONResponse({"error": "不可删除当前世界"}, 400)
    d = SAVES_DIR / aid
    if d.exists():
        shutil.rmtree(d)
    log("info", f"[Dashboard] 删除存档: {aid}")
    return {"ok": True}


# ── 入口 ──────────────────────────────────────────────────


def start_dashboard(port: int = 8080):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    start_dashboard()
