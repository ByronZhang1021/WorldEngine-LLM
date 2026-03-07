"""预定事件系统 — 管理 NPC 的计划、约定、日程。"""
import uuid
from datetime import datetime, timedelta
from .utils import load_state, save_state, log, load_config

# 去重窗口（分钟）：时间差在此范围内且参与者有交集的事件视为重复
_DEDUP_WINDOW_MINUTES = 30


def _get_default_flexible_window() -> int:
    """从 config 读取默认事件等待窗口，默认 30 分钟。"""
    return load_config().get("world", {}).get("default_flexible_window", 30)


# ─── CRUD ───────────────────────────────────────────

def _events(state: dict | None = None) -> list:
    """获取 state 中的 scheduled_events 列表（引用）。"""
    if state is None:
        state = load_state()
    if "scheduled_events" not in state:
        state["scheduled_events"] = []
    return state["scheduled_events"]


def get_all_events() -> list[dict]:
    """获取所有事件（副本）。"""
    return list(_events())


def get_upcoming_events(
    character: str,
    from_time: str | None = None,
    to_time: str | None = None,
) -> list[dict]:
    """获取某角色在时间范围内的待处理事件。

    Args:
        character: 角色名
        from_time: 起始时间 ISO（默认当前时间）
        to_time: 结束时间 ISO（可选）
    """
    state = load_state()
    events = _events(state)
    current_time = state.get("current_time", "")

    if from_time is None:
        from_time = current_time

    results = []
    for evt in events:
        if evt.get("status", "pending") != "pending":
            continue
        if character not in evt.get("participants", []):
            continue
        evt_time = evt.get("time", "")
        if evt_time < from_time:
            continue
        if to_time and evt_time > to_time:
            continue
        results.append(evt)

    # 按时间排序
    results.sort(key=lambda e: e.get("time", ""))
    return results


def _is_duplicate_event(
    time: str, participants: list[str], description: str,
    location: str, existing_events: list[dict],
) -> str | None:
    """检查是否已存在相似的 pending 事件。

    判断条件（全部满足才算重复）：
    1. 状态为 pending
    2. 时间差在 ±_DEDUP_WINDOW_MINUTES 分钟内
    3. 参与者有交集（至少一个共同参与者）
    4. 地点相同（如果两者都有地点）或描述有重叠关键词

    Returns:
        重复事件的 ID，如果没有重复则返回 None
    """
    try:
        new_dt = datetime.fromisoformat(time)
    except Exception:
        return None

    new_set = set(participants)
    # 从描述中提取关键词（去掉常见虚词，取长度>=2的片段）
    desc_chars = set(description)

    for evt in existing_events:
        if evt.get("status", "pending") != "pending":
            continue

        # 时间差检查
        try:
            evt_dt = datetime.fromisoformat(evt.get("time", ""))
            if abs((new_dt - evt_dt).total_seconds()) > _DEDUP_WINDOW_MINUTES * 60:
                continue
        except Exception:
            continue

        # 参与者交集检查
        evt_set = set(evt.get("participants", []))
        if not (new_set & evt_set):
            continue

        # 地点或描述相似度检查
        evt_loc = evt.get("location", "")
        if location and evt_loc and location == evt_loc:
            # 同时间 + 同参与者 + 同地点 → 明确重复
            log("debug", f"去重命中(地点): 新={description} vs 旧=[{evt['id']}]{evt.get('description', '')}")
            return evt["id"]

        # 描述关键词重叠检查（简单的字符重叠率）
        evt_desc = evt.get("description", "")
        if description and evt_desc:
            # 计算共同字符比例
            common = desc_chars & set(evt_desc)
            shorter = min(len(description), len(evt_desc))
            if shorter > 0 and len(common) / shorter > 0.5:
                log("debug", f"去重命中(描述): 新={description} vs 旧=[{evt['id']}]{evt_desc}")
                return evt["id"]

    return None


def _is_topic_duplicate_event(
    participants: list[str], description: str,
    existing_events: list[dict],
) -> str | None:
    """宽松主题去重：忽略时间差，只看参与者和描述相似度。

    用于拦截 LLM 在不同 turn 中对同一件事用不同时间重复创建的情况。
    例如第一轮 NPC 提议"周六去医院"建了事件，第二轮又建了"今天下午去医院"。

    判断条件（全部满足才算重复）：
    1. 状态为 pending
    2. 参与者完全相同（集合相等）
    3. 描述的字符重叠率 > 0.6

    Returns:
        重复事件的 ID，如果没有重复则返回 None
    """
    new_set = set(participants)
    desc_chars = set(description)

    for evt in existing_events:
        if evt.get("status", "pending") != "pending":
            continue

        # 参与者必须完全相同
        evt_set = set(evt.get("participants", []))
        if new_set != evt_set:
            continue

        # 描述相似度检查
        evt_desc = evt.get("description", "")
        if description and evt_desc:
            common = desc_chars & set(evt_desc)
            shorter = min(len(description), len(evt_desc))
            if shorter > 0 and len(common) / shorter > 0.6:
                log("debug", f"主题去重命中: 新={description} vs 旧=[{evt['id']}]{evt_desc}")
                return evt["id"]

    return None


def add_event(
    time: str,
    participants: list[str],
    description: str,
    created_by: str,
    location: str = "",
    sub_location: str = "",
    flexible_window: int = 30,
) -> str:
    """添加新事件，返回事件 ID。自带去重：如果已存在相似事件则跳过。"""
    state = load_state()
    current_time = state.get("current_time", "")

    # 过去时间修正：如果事件时间已过，自动推到下一天同一时间
    if time and current_time:
        try:
            evt_dt = datetime.fromisoformat(time)
            now_dt = datetime.fromisoformat(current_time)
            if evt_dt < now_dt:
                evt_dt += timedelta(days=1)
                new_time = evt_dt.strftime("%Y-%m-%dT%H:%M:%S")
                log("warning", f"预定事件时间修正: '{description}' 时间 {time} 已过（当前 {current_time}），"
                    f"自动推迟到 {new_time}")
                time = new_time
        except (ValueError, TypeError):
            pass

    existing = _events(state)

    # 去重检查
    dup_id = _is_duplicate_event(time, participants, description, location, existing)
    if dup_id:
        # 合并参与者：将新事件的参与者补入已有事件
        for evt in existing:
            if evt["id"] == dup_id:
                old_set = set(evt.get("participants", []))
                new_set = set(participants)
                merged = list(old_set | new_set)
                if merged != evt.get("participants", []):
                    evt["participants"] = merged
                    save_state(state)
                    log("info", f"预定事件去重+合并参与者: [{dup_id}] 参与者更新为 {merged}")
                else:
                    log("info", f"预定事件去重: 跳过添加 '{description}' @ {time}，已有相似事件 [{dup_id}]")
                break
        return dup_id

    event_id = f"evt_{uuid.uuid4().hex[:8]}"
    created_at = state.get("current_time", "")
    event = {
        "id": event_id,
        "time": time,
        "participants": participants,
        "location": location,
        "sub_location": sub_location,
        "description": description,
        "created_by": created_by,
        "created_at": created_at,
        "flexible_window": flexible_window,
        "status": "pending",
    }
    existing.append(event)
    save_state(state)
    log("info", f"预定事件添加: [{event_id}] {description} @ {time} | 参与: {', '.join(participants)}")
    return event_id


def update_event(event_id: str = "", match: str = "", **updates) -> bool:
    """更新事件。可通过 event_id 精确匹配或 match 关键词模糊匹配。

    Returns:
        True 如果找到并更新了事件
    """
    state = load_state()
    events = _events(state)
    target = None

    if event_id:
        for evt in events:
            if evt["id"] == event_id:
                target = evt
                break
    elif match:
        for evt in events:
            if evt.get("status", "pending") != "pending":
                continue
            if match in evt.get("description", ""):
                target = evt
                break

    if target is None:
        log("warning", f"预定事件更新失败: 未找到 id={event_id} match={match}")
        return False

    # 可更新的字段
    for key in ("time", "location", "sub_location", "description",
                "participants", "flexible_window"):
        if key in updates:
            target[key] = updates[key]

    save_state(state)
    log("info", f"预定事件更新: [{target['id']}] {updates}")
    return True


def delete_event(event_id: str = "", match: str = "") -> bool:
    """删除事件。可通过 event_id 或 match 关键词。"""
    state = load_state()
    events = _events(state)

    for i, evt in enumerate(events):
        if event_id and evt["id"] == event_id:
            removed = events.pop(i)
            save_state(state)
            log("info", f"预定事件删除: [{removed['id']}] {removed.get('description', '')}")
            return True
        if match and match in evt.get("description", "") and evt.get("status", "pending") == "pending":
            removed = events.pop(i)
            save_state(state)
            log("info", f"预定事件删除: [{removed['id']}] {removed.get('description', '')}")
            return True

    log("warning", f"预定事件删除失败: 未找到 id={event_id} match={match}")
    return False


def complete_event(event_id: str) -> bool:
    """标记事件为已完成。"""
    return _set_status(event_id, "completed")


def miss_event(event_id: str) -> bool:
    """标记事件为已错过。"""
    return _set_status(event_id, "missed")


def _set_status(event_id: str, status: str) -> bool:
    state = load_state()
    for evt in _events(state):
        if evt["id"] == event_id:
            evt["status"] = status
            save_state(state)
            log("info", f"预定事件 {status}: [{event_id}] {evt.get('description', '')}")
            return True
    return False


def mark_skipped(event_id: str, character: str, reason: str = "") -> bool:
    """记录某角色主动跳过（放弃）一个预定事件。

    在事件数据中添加 skipped_by 列表，供 cleanup_expired 检测"被放鸽子"。
    """
    state = load_state()
    for evt in _events(state):
        if evt["id"] == event_id and evt.get("status", "pending") == "pending":
            skipped_by = evt.setdefault("skipped_by", [])
            if not any(s["character"] == character for s in skipped_by):
                skipped_by.append({"character": character, "reason": reason})
                save_state(state)
                log("info", f"预定事件跳过: [{event_id}] {character} — {reason}")
            return True
    return False


# ─── 自动处理 ──────────────────────────────────────

def _check_participant_showed_up(
    name: str, evt_time: str, evt_location: str, window: int,
    state: dict, player: str,
    chains: dict[str, list[dict]] | None = None,
) -> bool:
    """检查某参与者是否在事件时间窗口内到达了事件地点。

    NPC: 检查活动链中是否有覆盖事件时间段且地点匹配的活动。
    玩家: 检查 location_since（到达当前位置的时间）是否在事件窗口内。
    
    Args:
        chains: 可选，来自 simulate_time_period 内存中的活动链字典。
                在大跳转中活动链尚未写入 state 时使用。
    
    地点匹配策略（宽松，只看主地点）：
    - 事件无地点 → NPC 直接视为到场；玩家检查是否跟其他参与者同地点
    - 精确匹配 location
    - 互相包含（处理不同粒度的地点名）
    """
    from datetime import timedelta

    try:
        evt_dt = datetime.fromisoformat(evt_time)
        window_start = evt_dt - timedelta(minutes=window)
        window_end = evt_dt + timedelta(minutes=window)
    except Exception:
        return False

    cs = state.get("characters", {}).get(name, {})

    def _loc_match(char_location: str) -> bool:
        """宽松地点匹配：只看主地点。"""
        if not char_location:
            return False
        if char_location == evt_location:
            return True
        # 互相包含（处理 "学校食堂" vs "云海实验中学" 等不同粒度）
        if evt_location in char_location or char_location in evt_location:
            return True
        return False

    if name == player:
        # 玩家：需要检查是否在事件时间窗口内就在事件地点
        player_loc = cs.get("location", "")
        location_since = cs.get("location_since", "")

        # 事件没有指定地点 → 检查玩家是否跟其他参与者在同一位置
        if not evt_location:
            return True  # 无地点事件，玩家默认到场

        if not _loc_match(player_loc):
            return False  # 当前不在事件地点

        # 有 location_since 记录：检查到达时间是否在事件窗口内
        if location_since:
            try:
                since_dt = datetime.fromisoformat(location_since)
                # 玩家在事件窗口结束之后才到达 → 没赶上
                if since_dt > window_end:
                    return False
            except Exception:
                pass

        return True

    # 事件没有指定地点 → NPC 直接视为到场
    if not evt_location:
        return True

    # NPC: 优先检查传入的活动链（大跳转时 state 里还没有）
    chain = (chains or {}).get(name, []) or cs.get("activity_chain", [])
    if chain:
        ws = window_start.isoformat()
        we = window_end.isoformat()
        for act in chain:
            act_start = act.get("start", "")
            act_end = act.get("end", "")
            act_loc = act.get("location", "")
            if act_start < we and act_end > ws and _loc_match(act_loc):
                return True

    # 没有活动链或活动链中没有匹配 → 检查当前位置
    return _loc_match(cs.get("location", ""))


def cleanup_expired(
    current_time: str | None = None,
    chains: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """清理已过期的待处理事件（标记为 missed/completed）。

    增强：检测"被放鸽子"情况，为被爽约的 NPC 生成记忆和情绪。

    Args:
        chains: 可选，来自 simulate_time_period 内存中的活动链字典。
                在大跳转中调用时传入，确保活动链还没被丢弃。

    Returns:
        被标记为 missed 的事件列表
    """
    from .world import get_player_character

    state = load_state()
    if current_time is None:
        current_time = state.get("current_time", "")

    player = ""
    try:
        player = get_player_character()
    except Exception:
        pass

    missed = []
    for evt in _events(state):
        if evt.get("status", "pending") != "pending":
            continue
        evt_time = evt.get("time", "")
        window = evt.get("flexible_window", _get_default_flexible_window())
        try:
            evt_dt = datetime.fromisoformat(evt_time)
            cur_dt = datetime.fromisoformat(current_time)
            from datetime import timedelta
            if cur_dt <= evt_dt + timedelta(minutes=window):
                continue  # 还没过期
        except Exception:
            continue

        # 事件已过期 — 检查每个参与者是否到场
        participants = evt.get("participants", [])
        evt_location = evt.get("location", "")
        skipped_by_names = {s["character"] for s in evt.get("skipped_by", [])}

        showed_up = []
        no_show = []
        for name in participants:
            if name in skipped_by_names:
                no_show.append(name)  # 主动跳过的
            elif _check_participant_showed_up(name, evt_time, evt_location, window, state, player, chains=chains):
                showed_up.append(name)
            else:
                no_show.append(name)

        # 标记状态
        from .utils import get_turn_logger
        tl = get_turn_logger()

        if showed_up and not no_show:
            evt["status"] = "completed"
            log("info", f"预定事件完成: [{evt['id']}] {evt.get('description', '')}")
            if tl:
                tl.log_custom("预定事件完成",
                    f"[{evt['id']}] {evt.get('description', '')}\n"
                    f"到场: {', '.join(showed_up)}")
            # 事件完成后截断参与者的活动链，使其重新生成后续活动
            for name in showed_up:
                if name == player:
                    continue
                try:
                    _cs = state.get("characters", {}).get(name, {})
                    if current_time and _cs:
                        _cs["until"] = current_time
                        log("info", f"预定事件完成 → 活动链过期 [{name}]: 下次 tick 重新生成")
                except Exception:
                    pass
        else:
            evt["status"] = "missed"
            evt["showed_up"] = showed_up
            evt["no_show"] = no_show
            missed.append(dict(evt))
            log("info",
                f"预定事件过期: [{evt['id']}] {evt.get('description', '')} "
                f"| 到场: {showed_up} | 缺席: {no_show}")
            if tl:
                tl.log_custom("预定事件过期",
                    f"[{evt['id']}] {evt.get('description', '')}\n"
                    f"到场: {', '.join(showed_up) or '无'}\n"
                    f"缺席: {', '.join(no_show) or '无'}")

        # 为被放鸽子的人生成记忆
        if showed_up and no_show:
            _handle_stood_up(evt, showed_up, no_show, state)

    if missed or any(e.get("status") in ("completed", "missed") for e in _events(state)):
        save_state(state)
    return missed


def _handle_stood_up(
    evt: dict, showed_up: list[str], no_show: list[str], state: dict,
):
    """为被放鸽子的角色生成记忆和情绪变化。"""
    from .world import get_player_character
    try:
        player = get_player_character()
    except Exception:
        player = ""

    desc = evt.get("description", "")
    evt_time = evt.get("time", "")
    time_display = evt_time[5:16].replace("T", " ") if evt_time else ""
    no_show_names = "、".join(no_show)

    # 查找跳过原因（如果有）
    skip_reasons = {s["character"]: s.get("reason", "") for s in evt.get("skipped_by", [])}

    for name in showed_up:
        if name == player:
            continue  # 玩家的反应由玩家自己控制

        # 构造记忆内容（只记事实，情绪由后续 LLM 自然处理）
        memory_content = f"{time_display}和{no_show_names}约好了{desc}，但对方没有来"

        # 写入记忆
        try:
            from .memory import add_memory
            add_memory(
                name,
                content=memory_content,
                ttl="7天",
                visibility="private",
            )
            log("info", f"爽约记忆写入 [{name}]: {memory_content}")
        except Exception as e:
            log("warning", f"爽约记忆写入失败 [{name}]: {e}")

        # 截断活动链 — 强制过期，下次 check_activity_expiry 会重新生成
        try:
            cs = state.get("characters", {}).get(name, {})
            current_time = state.get("current_time", "")
            if current_time:
                cs["until"] = current_time  # 活动立即过期
                cs["activity"] = f"等{no_show_names}没等到，准备离开"
                log("info", f"爽约活动截断 [{name}]: 活动链将在下次 tick 重新生成")
        except Exception as e:
            log("warning", f"爽约活动截断失败 [{name}]: {e}")

    # Turn 日志记录爽约详情
    from .utils import get_turn_logger
    tl = get_turn_logger()
    if tl:
        tl.log_custom("爽约检测",
            f"[{evt.get('id', '')}] {desc}\n"
            f"时间: {time_display}\n"
            f"到场等待: {', '.join(showed_up)}\n"
            f"缺席: {no_show_names}\n"
            f"处理: 写入记忆 + 活动链截断")


def format_events_for_prompt(character: str, from_time: str, to_time: str) -> str:
    """格式化某角色在指定时间段内的事件，用于注入 activity_chain prompt。

    包含事件 ID、参与者、地点等完整信息，供 LLM 做"去不去"的判断。
    """
    events = get_upcoming_events(character, from_time, to_time)
    if not events:
        return ""

    lines = []
    for evt in events:
        eid = evt.get("id", "")
        t = evt.get("time", "").replace("T", " ")[5:16]  # "01-08 10:00"
        loc = evt.get("location", "")
        sub = evt.get("sub_location", "")
        desc = evt.get("description", "")
        window = evt.get("flexible_window", _get_default_flexible_window())
        others = [p for p in evt.get("participants", []) if p != character]

        loc_str = f"{loc}/{sub}" if sub else loc
        parts = [f"- [{eid}] {t}:"]
        if loc_str:
            parts.append(f"去{loc_str}")
        parts.append(f"（{desc}）")
        if others:
            parts.append(f"与{'、'.join(others)}")
        parts.append(f"[等{window}分钟]")
        lines.append(" ".join(parts))

    return "\n".join(lines)


def _find_event(event_id: str = "", match: str = "") -> dict | None:
    """查找事件（不修改），用于在操作前获取参与者信息。"""
    state = load_state()
    for evt in _events(state):
        if event_id and evt["id"] == event_id:
            return dict(evt)
        if match and match in evt.get("description", "") and evt.get("status", "pending") == "pending":
            return dict(evt)
    return None


def _notify_other_participants(
    actor: str, evt: dict, action: str, detail: str = "",
):
    """通知事件的其他参与者：有人取消/修改了约定。

    - 写入记忆
    - 强制过期活动链（下次 tick 会重新生成）
    """
    desc = evt.get("description", "")
    evt_time = evt.get("time", "")
    time_display = evt_time[5:16].replace("T", " ") if evt_time else ""
    others = [p for p in evt.get("participants", []) if p != actor]

    if not others:
        return

    notified = []
    for name in others:
        # 写入记忆
        try:
            from .memory import add_memory
            if action == "delete":
                content = f"{actor}取消了{time_display}的约定（{desc}）"
            else:
                content = f"{actor}修改了{time_display}的约定（{desc}）"
            add_memory(name, content=content, ttl="3天", visibility="private")
            log("info", f"预定变更通知 [{name}]: {content}")
            notified.append(f"{name}: {content}")
        except Exception as e:
            log("warning", f"预定变更通知失败 [{name}]: {e}")

        # 强制过期活动链
        try:
            state = load_state()
            cs = state.get("characters", {}).get(name, {})
            current_time = state.get("current_time", "")
            if current_time and cs:
                cs["until"] = current_time
                save_state(state)
                log("info", f"预定变更 → 活动链过期 [{name}]")
        except Exception as e:
            log("warning", f"活动链过期失败 [{name}]: {e}")

    # Turn 日志
    if notified:
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            action_label = '取消' if action == 'delete' else '修改'
            tl.log_custom(f"预定事件{action_label}通知",
                f"{actor} {action_label}了 [{evt.get('id', '')}] {desc}\n"
                f"通知对象:\n" + "\n".join(f"  - {n}" for n in notified))


def process_event_operations(
    operations: list[dict],
    character: str,
    current_time: str = "",
) -> None:
    """处理 LLM 输出的事件操作列表。

    Args:
        operations: [{"action": "add/update/delete", ...}, ...]
        character: 当前角色（作为 created_by）
        current_time: 当前虚拟时间
    """
    if not operations or not isinstance(operations, list):
        return

    for op in operations:
        if not isinstance(op, dict):
            continue
        action = op.get("action", "")

        if action == "add":
            time = op.get("time", "")
            if not time:
                log("warning", "事件添加失败: 缺少 time")
                continue
            participants = op.get("participants", [character])
            if character not in participants:
                participants.append(character)

            # ─── NPC 创建包含玩家的事件：主题去重拦截 ───
            # 防止 LLM 对同一件事在不同 turn 中用不同时间重复创建
            try:
                from .world import get_player_character
                player = get_player_character()
            except Exception:
                player = ""

            if player and player in participants and character != player:
                state = load_state()
                existing = _events(state)
                topic_dup = _is_topic_duplicate_event(
                    participants, op.get("description", ""), existing,
                )
                if topic_dup:
                    log("warning",
                        f"NPC主题去重拦截: [{character}] 试图添加 '{op.get('description', '')}' @ {time}，"
                        f"但已有同主题事件 [{topic_dup}]，跳过")
                    continue

            add_event(
                time=time,
                participants=participants,
                description=op.get("description", ""),
                created_by=character,
                location=op.get("location", ""),
                sub_location=op.get("sub_location", ""),
                flexible_window=op.get("flexible_window", 30),
            )

        elif action == "update":
            event_id = op.get("id", "")
            match = op.get("match", "")
            # 先获取原事件信息（用于通知）
            evt_before = _find_event(event_id=event_id, match=match)
            updates = {k: v for k, v in op.items()
                       if k not in ("action", "id", "match")}
            if update_event(event_id=event_id, match=match, **updates) and evt_before:
                _notify_other_participants(
                    character, evt_before, "update",
                    detail=str(updates),
                )

        elif action == "delete":
            event_id = op.get("id", "")
            match = op.get("match", "")
            # 先获取事件信息（删了就没了）
            evt_before = _find_event(event_id=event_id, match=match)
            if delete_event(event_id=event_id, match=match) and evt_before:
                _notify_other_participants(
                    character, evt_before, "delete",
                )

        else:
            log("warning", f"未知事件操作: {action}")
