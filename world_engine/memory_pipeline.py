"""对话后处理管道 — 每轮对话后异步分析：记忆操作、情绪更新、活动变更、图片生成。"""
import concurrent.futures
import threading
from typing import Optional

from .embedding import get_embedding, cosine_similarity
from .utils import log, load_config, load_state, save_state, state_transaction, read_file, CHARACTERS_DIR, PROMPTS_DIR, parse_character_file, char_file_path, load_character
from .llm import chat_json
from .memory import load_all_entries, execute_memory_ops
from . import chroma_store


def search_relevant_memories(character: str, query: str, top_n: int = 5) -> list[dict]:
    """搜索与查询最相关的记忆条目（使用 ChromaDB 向量检索）。"""
    try:
        query_emb = get_embedding(query)
    except Exception as e:
        log("warning", f"获取 query embedding 失败: {e}")
        return []

    results = chroma_store.query_similar(
        character, query, n_results=top_n,
        query_embedding=query_emb,
    )
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "_section": r["metadata"].get("section", ""),
            **r["metadata"],
        }
        for r in results
    ]


def run_post_conversation(character: str, conversation_text: str, *, player_mode: bool = False, allow_image: bool | None = None):
    """为角色运行对话后处理管道：记忆 + 情绪 + 活动变更。

    player_mode: 为 True 时只处理记忆和情绪，跳过活动变更和图片生成。
    allow_image: 显式控制是否允许图片生成。None 时跟随 player_mode 逻辑（非 player_mode 才生图）。
    """
    log("info", f"对话后处理启动 [{character}]")

    char_data = load_character(character)
    parsed = parse_character_file(char_data)

    # 合并公开+私密动态记忆作为完整记忆
    parts = []
    for section_label, section_key in [("【公开动态】", "public_dynamic"), ("【私密动态】", "private_dynamic")]:
        text = parsed.get(section_key, "").strip()
        if text:
            parts.append(f"{section_label}\n{text}")
    memory = "\n\n".join(parts) if parts else "（暂无记忆）"

    state = load_state()
    char_state = state.get("characters", {}).get(character, {})
    current_emotion = char_state.get("emotion", "")
    current_activity = f"{char_state.get('activity', '未知')}（在 {char_state.get('location', '未知')}，直到 {char_state.get('until', '未知')}）"

    prompt_template = read_file(PROMPTS_DIR / "post_conversation.md")
    prompt = prompt_template.format(
        character=character,
        current_time=state.get("current_time", ""),
        conversation=conversation_text[-2000:],
        current_memory=memory,
        current_emotion=current_emotion or "（无）",
        current_activity=current_activity,
    )

    result = None
    try:
        result = chat_json([{"role": "user", "content": prompt}], config_key="analysis", label="记忆分析")
        ops = result.get("operations", [])

        if ops:
            log("info", f"记忆操作 [{character}]: {len(ops)} 个操作")
            execute_memory_ops(character, ops)
        else:
            log("debug", f"对话后处理 [{character}]: 无记忆操作")

        emotion = result.get("emotion", "")
        if emotion is not None:
            with state_transaction() as st:
                cs = st.setdefault("characters", {}).setdefault(character, {})
                cs["emotion"] = emotion if emotion else ""
            if emotion:
                log("info", f"情绪更新 [{character}]: {emotion}")

        # 预定事件处理（两侧都处理，去重机制会自动合并参与者）
        events = result.get("events", [])
        if events:
            from .events import process_event_operations
            log("info", f"预定事件检测 [{character}]: {len(events)} 个操作")
            process_event_operations(events, character, state.get("current_time", ""))
            try:
                from .utils import get_turn_logger
                _tl = get_turn_logger()
                if _tl:
                    _tl.log_scheduled_event(events, source=f"对话记忆分析/{character}")
            except Exception:
                pass

        # 活动变更：带 change_reason 立即重新生成活动链（玩家角色跳过）
        if not player_mode and result.get("activity_changed"):
            change_reason = result.get("change_reason", "")
            log("info", f"活动变更 [{character}]: {change_reason or '(无原因)'}")

            try:
                from .world import generate_activity_chain
                from datetime import datetime, timedelta

                state = load_state()
                current_time = state.get("current_time", "")
                old_location = state.get("characters", {}).get(character, {}).get("location", "未知")

                # 清空 until，避免活动链 prompt 中的 busy_until 规则阻止 NPC 立即行动
                with state_transaction() as st:
                    cs = st.get("characters", {}).get(character, {})
                    if cs:
                        cs["until"] = current_time

                # 生成未来 30 分钟的短链，带上变更原因
                dt = datetime.fromisoformat(current_time)
                end_time = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")

                activities = generate_activity_chain(
                    character, current_time, end_time,
                    event_context=change_reason,
                )

                if activities:
                    act = activities[0]
                    new_location = act.get("location", old_location)

                    # 确定 sub_location：优先取活动链返回值，否则用默认子地点
                    from .location import discover_location, discover_sub_location, get_default_sub_location
                    new_sub_location = act.get("sub_location", "")
                    if not new_sub_location:
                        new_sub_location = get_default_sub_location(new_location)

                    # 使用事务保证原子性
                    with state_transaction() as st:
                        cs = st.setdefault("characters", {}).setdefault(character, {})
                        cs["location"] = new_location
                        cs["sub_location"] = new_sub_location
                        cs["activity"] = act.get("activity", "")
                        cs["activity_start"] = act.get("start", current_time)
                        cs["until"] = act.get("end", "")
                        cs.pop("activity_chain", None)

                    # 自动发现新地点和子地点
                    discover_location(character, new_location)
                    if new_sub_location:
                        discover_sub_location(character, new_location, new_sub_location)

                    if new_location != old_location:
                        log("info", f"活动变更导致移动 [{character}]: {old_location} → {new_location}")
                    else:
                        log("info", f"活动变更 [{character}]: 留在 {old_location}，新活动: {act.get('activity', '')}")
                else:
                    # 活动链生成失败，回退到旧逻辑：重置 until
                    with state_transaction() as st:
                        cs = st.get("characters", {}).get(character, {})
                        cs["until"] = st.get("current_time", "")
                    log("warning", f"活动变更 [{character}]: 活动链生成失败，仅重置 until")

            except Exception as e:
                log("warning", f"活动变更处理失败 [{character}]: {e}")
                # 回退到旧逻辑
                try:
                    with state_transaction() as st:
                        cs = st.get("characters", {}).get(character, {})
                        cs["until"] = st.get("current_time", "")
                except Exception:
                    pass

        # 记录到 TurnLogger
        try:
            from .utils import get_turn_logger
            tl = get_turn_logger()
            if tl:
                tl.log_memory_pipeline(
                    character, prompt, result, ops,
                    emotion=emotion if emotion else ""
                )
        except Exception:
            pass

    except Exception as e:
        log("warning", f"对话后处理失败 [{character}]: {e}")

    # === 图片生成（在所有记忆/情绪/活动处理完毕后执行）===
    _should_try_image = allow_image if allow_image is not None else (not player_mode)
    try:
        if _should_try_image and result and result.get("generate_image"):
            image_desc = result.get("image_description", "")
            image_chars = result.get("image_characters", [])
            if image_desc and image_chars:
                log("info", f"图片生成触发 [{character}]: {image_desc[:50]}")
                from .tools import _generate_scene_image_sync
                image_path = _generate_scene_image_sync(
                    description=image_desc,
                    characters=image_chars,
                    conversation=conversation_text[-500:],
                )
                if image_path:
                    log("info", f"图片生成完成 [{character}]: {image_path}")
                    return image_path
                else:
                    log("warning", f"图片生成返回空 [{character}]")
    except Exception as e:
        log("warning", f"图片生成失败 [{character}]: {e}")

    return None

def run_scene_memory_analysis(characters: list[str], scene_text: str, player: str = ""):
    """为离屏场景中的每个参与角色运行记忆分析。

    与 run_post_conversation 的区别：
    - 不处理 activity_changed（活动变更由 activity_impact 机制处理）
    - 为每个非玩家角色独立调用一次 memory_analysis
    - 同步执行（在 simulate_time_period 的流程中调用）
    """
    # load_state, save_state, state_transaction 已在模块顶部导入

    if not scene_text or not scene_text.strip():
        return

    for character in characters:
        if character == player:
            continue

        try:
            char_data = load_character(character)
            parsed = parse_character_file(char_data)

            parts = []
            for section_label, section_key in [("【公开动态】", "public_dynamic"), ("【私密动态】", "private_dynamic")]:
                text = parsed.get(section_key, "").strip()
                if text:
                    parts.append(f"{section_label}\n{text}")
            memory = "\n\n".join(parts) if parts else "（暂无记忆）"

            state = load_state()
            char_state = state.get("characters", {}).get(character, {})
            current_emotion = char_state.get("emotion", "")
            current_activity = f"{char_state.get('activity', '未知')}（在 {char_state.get('location', '未知')}）"

            prompt_template = read_file(PROMPTS_DIR / "post_conversation.md")
            prompt = prompt_template.format(
                character=character,
                current_time=state.get("current_time", ""),
                conversation=scene_text[-2000:],
                current_memory=memory,
                current_emotion=current_emotion or "（无）",
                current_activity=current_activity,
            )

            result = chat_json([{"role": "user", "content": prompt}], config_key="analysis", label="记忆分析")
            ops = result.get("operations", [])

            if ops:
                log("info", f"离屏记忆分析 [{character}]: {len(ops)} 个操作")
                execute_memory_ops(character, ops)
            else:
                log("debug", f"离屏记忆分析 [{character}]: 无记忆操作")

            # 情绪更新
            emotion = result.get("emotion", "")
            if emotion is not None:
                with state_transaction() as st:
                    cs = st.setdefault("characters", {}).setdefault(character, {})
                    cs["emotion"] = emotion if emotion else ""
                if emotion:
                    log("info", f"离屏情绪更新 [{character}]: {emotion}")

            # 预定事件处理
            events = result.get("events", [])
            if events:
                from .events import process_event_operations
                log("info", f"离屏事件检测 [{character}]: {len(events)} 个操作")
                process_event_operations(events, character, state.get("current_time", ""))
                try:
                    from .utils import get_turn_logger
                    _tl = get_turn_logger()
                    if _tl:
                        _tl.log_scheduled_event(events, source=f"离屏记忆分析/{character}")
                except Exception:
                    pass

            # 记录到 TurnLogger
            try:
                from .utils import get_turn_logger
                tl = get_turn_logger()
                if tl:
                    tl.log_memory_pipeline(
                        character, prompt, result, ops,
                        emotion=emotion if emotion else ""
                    )
            except Exception:
                pass

        except Exception as e:
            log("warning", f"离屏记忆分析失败 [{character}]: {e}")


def trigger_memory_pipeline(character: str, conversation_text: str, *, player_mode: bool = False, allow_image: bool | None = None) -> concurrent.futures.Future:
    """异步触发对话后处理管道，返回图片生成的 Future。

    Future 的结果：
    - str: 图片本地路径（需要发送）
    - None: 不需要生成图片，或生成失败
    """
    future = concurrent.futures.Future()

    def _run():
        try:
            image_result = run_post_conversation(character, conversation_text, player_mode=player_mode, allow_image=allow_image)
            future.set_result(image_result)
        except Exception:
            if not future.done():
                future.set_result(None)

    threading.Thread(target=_run, daemon=True).start()
    return future
