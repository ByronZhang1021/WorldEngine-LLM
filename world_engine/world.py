"""世界管理 — 时间推进、角色活动生成、离屏事件模拟。

阶段四：完整世界模拟。
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from .utils import log, load_config, load_state, save_state, state_transaction, read_file, read_json, write_json, CHARACTERS_DIR, LOCATIONS_PATH, EVENTS_DIR, PROMPTS_DIR, parse_character_file, load_character
from .llm import chat_json


# 星期映射
_WEEKDAY_MAP = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _fuzzy_match_location(name: str, all_names: list[str]) -> str | None:
    """模糊匹配地点名：精确匹配失败时，尝试子串匹配、编辑距离、LLM 语义匹配。

    返回最佳匹配的地点名，或 None（无足够相似的匹配）。
    """
    if not name or not all_names:
        return None

    # 1) 精确匹配（调用方通常已经做过，这里兜底）
    if name in all_names:
        return name

    # 2) 子串匹配：name 是某个地点名的子串，或某个地点名是 name 的子串
    contains_matches = []
    for loc in all_names:
        if name in loc or loc in name:
            contains_matches.append(loc)
    if len(contains_matches) == 1:
        # 唯一匹配，直接返回
        return contains_matches[0]
    elif len(contains_matches) > 1:
        # 多个子串匹配，取最短编辑距离的
        best = min(contains_matches, key=lambda loc: _edit_distance(name, loc))
        return best

    # 3) 编辑距离匹配（阈值：名字长度的 40%）
    threshold = max(2, len(name) * 2 // 5)
    best_loc = None
    best_dist = threshold + 1
    for loc in all_names:
        dist = _edit_distance(name, loc)
        if dist < best_dist:
            best_dist = dist
            best_loc = loc
    if best_loc and best_dist <= threshold:
        return best_loc

    # 4) LLM 语义匹配（最终兜底：处理 NPC 用非标准名称的情况）
    llm_match = _llm_match_location(name, all_names)
    if llm_match:
        return llm_match

    return None


def _llm_match_location(name: str, all_names: list[str]) -> str | None:
    """当字符串模糊匹配失败时，使用 LLM 进行语义地点匹配。

    处理 NPC 在对话中使用非标准地名的情况，
    如 "云海花园小区" → "云海市中心公寓"。
    """
    try:
        locations_list = "\n".join(f"- {n}" for n in all_names)
        prompt = (
            f"对话中提到了地点「{name}」，但这不是系统中的标准地点名称。\n"
            f"请从以下标准地点列表中找到最可能对应的地点：\n\n"
            f"{locations_list}\n\n"
            f"只有当你有较高把握时才匹配，不要勉强匹配。\n"
            f"返回 JSON: {{\"match\": \"完整标准地点名\"}} 或 {{\"match\": null}}"
        )
        result = chat_json(
            [{"role": "user", "content": prompt}],
            config_key="analysis",
            label="地点匹配",
        )
        match = result.get("match")
        if match and match in all_names:
            log("info", f"LLM 地点匹配: '{name}' → '{match}'")
            return match
        elif match:
            log("warning", f"LLM 地点匹配结果 '{match}' 不在地点列表中，忽略")
    except Exception as e:
        log("warning", f"LLM 地点匹配失败: {e}")

    return None


def _edit_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离（Levenshtein）。"""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(
                prev[j + 1] + 1,   # 删除
                curr[j] + 1,       # 插入
                prev[j] + (0 if c1 == c2 else 1),  # 替换
            ))
        prev = curr
    return prev[-1]



def _next_event_id() -> int:
    """从 events 目录中扫描最大 ID +1。"""
    if not EVENTS_DIR.exists():
        return 1
    max_id = 0
    for path in EVENTS_DIR.glob("*.json"):
        try:
            num = int(path.stem)
            if num > max_id:
                max_id = num
        except ValueError:
            pass
    return max_id + 1

DEFAULT_PLAYER = ""


def get_player_character() -> str:
    """获取当前玩家扮演的角色名。"""
    state = load_state()
    player = state.get("player_character", "")
    if player:
        return player
    # 未设置时取 characters 的第一个角色名，或使用默认值
    chars = state.get("characters", {})
    if chars:
        return next(iter(chars))
    return DEFAULT_PLAYER


def set_player_character(name: str):
    """设置玩家扮演的角色。"""
    state = load_state()
    state["player_character"] = name
    save_state(state)
    log("info", f"玩家角色切换为: {name}")





def get_current_time() -> str:
    """获取当前虚拟时间字符串。"""
    state = load_state()
    return state.get("current_time", "2026-01-01T08:00:00")


def get_day_of_week() -> str:
    """获取当前是星期几。"""
    vtime = get_current_time()
    try:
        dt = datetime.fromisoformat(vtime)
        return _WEEKDAY_MAP[dt.weekday()]
    except Exception:
        return ""


def advance_time(minutes: int, exclude_from_activity: set[str] | None = None):
    """推进虚拟时间，并触发世界模拟。
    
    Args:
        exclude_from_activity: 不需要重新生成活动的角色集合（正在对话中的角色）。
    """
    state = load_state()
    vtime = state.get("current_time", "2026-01-01T08:00:00")
    old_time = vtime

    try:
        dt = datetime.fromisoformat(vtime)
        dt += timedelta(minutes=minutes)
        state["current_time"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
        state["day_of_week"] = _WEEKDAY_MAP[dt.weekday()]

        # 情绪衰减：大幅跳转时重置情绪
        if minutes >= 120:
            for cs in state.get("characters", {}).values():
                cs.pop("emotion", None)

        save_state(state)
        log("info", f"时间推进 {minutes} 分钟 → {state['current_time']} {state['day_of_week']}")

        if minutes >= _get_large_jump_minutes():
            # 大幅跳转（≥阈值）：活动链 + 重叠检测 + 场景生成
            # 注意：cleanup_expired 已在 simulate_time_period 内部调用（活动链还在时）
            try:
                simulate_time_period(old_time, state["current_time"], minutes)
            except Exception as e:
                log("warning", f"世界模拟失败: {e}")
        else:
            # 小幅跳转：检查活动到期 + 轻量世界模拟
            check_activity_expiry(exclude=exclude_from_activity)
            try:
                simulate_small_jump(old_time, state["current_time"])
            except Exception as e:
                log("warning", f"小幅跳转模拟失败: {e}")

        # 清理过期记忆
        try:
            from .memory import cleanup_all_characters
            cleanup_all_characters()
        except Exception as e:
            log("warning", f"记忆清理失败: {e}")

        # 清理过期预定事件（大跳转时已在 simulate_time_period 中处理）
        if minutes < _get_large_jump_minutes():
            try:
                from .events import cleanup_expired
                missed = cleanup_expired(state["current_time"])
                if missed:
                    log("info", f"预定事件过期: {len(missed)} 个")
            except Exception as e:
                log("warning", f"预定事件清理失败: {e}")

    except Exception as e:
        log("warning", f"时间推进失败: {e}")


def set_time(time_str: str):
    """设置虚拟时间为指定值。"""
    state = load_state()
    try:
        dt = datetime.fromisoformat(time_str)
        state["current_time"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
        state["day_of_week"] = _WEEKDAY_MAP[dt.weekday()]
        save_state(state)
        log("info", f"时间设置为 {state['current_time']} {state['day_of_week']}")
        check_activity_expiry()
    except Exception as e:
        log("warning", f"时间设置失败: {e}")


def move_user(location: str, sub_location: str = ""):
    """移动用户到指定地点。"""
    state = load_state()
    player = get_player_character()
    user_state = state.get("characters", {}).get(player, {})
    old_location = user_state.get("location", "未知")
    user_state["location"] = location
    user_state["activity"] = ""
    # 记录到达时间：用于预定事件的到场判断
    current_time = state.get("current_time", "")
    if location != old_location:
        user_state["location_since"] = current_time
    # 设置子地点：如果没有指定，使用默认子地点
    from .location import discover_location, get_default_sub_location, discover_sub_location
    if not sub_location:
        sub_location = get_default_sub_location(location)
    user_state["sub_location"] = sub_location
    save_state(state)
    log("info", f"用户移动: {old_location} → {location}/{sub_location}")
    # 自动发现新地点和子地点
    discover_location(player, location)
    if sub_location:
        discover_sub_location(player, location, sub_location)


def get_time_display() -> str:
    """获取时间显示字符串。"""
    state = load_state()
    vtime = state.get("current_time", "")
    dow = state.get("day_of_week", "")
    player_cs = state.get("characters", {}).get(get_player_character(), {})
    user_loc = player_cs.get("location", "未知")
    user_sub = player_cs.get("sub_location", "")
    loc_display = f"{user_loc}/{user_sub}" if user_sub else user_loc
    return f"🕐 {vtime} {dow}\n📍 {loc_display}"


def get_all_character_locations() -> str:
    """获取所有角色位置（/where 命令用）。"""
    state = load_state()
    chars = state.get("characters", {})
    lines = [f"🕐 {state.get('current_time', '')} {state.get('day_of_week', '')}", ""]
    for name, cs in chars.items():
        loc = cs.get("location", "未知")
        sub = cs.get("sub_location", "")
        loc_display = f"{loc}/{sub}" if sub else loc
        act = cs.get("activity", "")
        since = cs.get("activity_start", "")
        until = cs.get("until", "")
        since_str = since[11:16] if since else "?"
        until_str = until[11:16] if until else "?"
        act_str = f" — {act}" if act else ""
        time_str = f" ({since_str}~{until_str})" if act else ""
        lines.append(f"📍 {name}: {loc_display}{act_str}{time_str}")
    return "\n".join(lines)


def get_all_activity_chains() -> str:
    """获取所有角色的完整活动链（/activities 命令用）。"""
    state = load_state()
    chars = state.get("characters", {})
    lines = [f"🕐 {state.get('current_time', '')} {state.get('day_of_week', '')}", ""]
    for name, cs in chars.items():
        chain = cs.get("activity_chain", [])
        lines.append(f"👤 {name}")
        if not chain:
            act = cs.get("activity", "")
            if act:
                since = cs.get("activity_start", "")
                until = cs.get("until", "")
                since_str = since[11:16] if since else "?"
                until_str = until[11:16] if until else "?"
                loc = cs.get("location", "未知")
                sub = cs.get("sub_location", "")
                loc_display = f"{loc}/{sub}" if sub else loc
                lines.append(f"  [{since_str}~{until_str}] 📍{loc_display} — {act}")
            else:
                lines.append("  （无活动计划）")
        else:
            for i, act in enumerate(chain):
                start = act.get("start", "")
                end = act.get("end", "")
                start_str = start[11:16] if start else "?"
                end_str = end[11:16] if end else "?"
                loc = act.get("location", "")
                sub = act.get("sub_location", "")
                loc_display = f"{loc}/{sub}" if sub and loc else (loc or "")
                activity = act.get("activity", "")
                marker = "▶" if _is_current_activity(act, state.get("current_time", "")) else " "
                lines.append(f"  {marker}[{start_str}~{end_str}] 📍{loc_display} — {activity}")
        lines.append("")
    return "\n".join(lines)


def _is_current_activity(act: dict, current_time: str) -> bool:
    """判断活动是否是当前正在进行的。"""
    try:
        start = act.get("start", "")
        end = act.get("end", "")
        if start and end and current_time:
            return start <= current_time < end
    except Exception:
        pass
    return False


# ── LLM 驱动的时间推进 ─────────────────────────────────────


def auto_advance_time(
    user_msg: str, assistant_msg: str,
    present_npcs: list[str] | None = None,
    conversation: str = "",
) -> dict:
    """每轮对话后，判断时间推进和移动（含群组移动、NPC 离场）。

    返回 dict:
        minutes, reason, narration, destination,
        leader (领头人), companions (同行角色列表),
        npc_departures (NPC 离场列表)
    """
    state = load_state()
    current_time = state.get("current_time", "")
    day_of_week = state.get("day_of_week", "")
    player = get_player_character()
    player_state = state.get("characters", {}).get(player, {})
    current_location = player_state.get("location", "未知")
    current_sub_location = player_state.get("sub_location", "")

    distances = _get_distances(current_location, player)

    # 在场角色信息
    if present_npcs:
        present_str = "、".join(present_npcs)
    else:
        present_str = "无其他角色"

    # 对话内容：优先用完整 conversation，否则用 user_msg/assistant_msg
    if conversation:
        conv_text = conversation
    else:
        conv_text = f"用户: {user_msg}\n回复: {assistant_msg}"

    template = read_file(PROMPTS_DIR / "time_advance.md")
    prompt = template.format(
        current_time=current_time,
        day_of_week=day_of_week,
        player_character=player,
        current_location=current_location,
        current_sub_location=current_sub_location or "无",
        present_characters=present_str,
        distances=distances,
        conversation=conv_text,
        large_jump_minutes=_get_large_jump_minutes(),
    )

    default_result = {
        "minutes": 3, "reason": "", "narration": "",
        "destination": None, "sub_destination": "",
        "leader": "", "companions": [],
        "npc_departures": [],
    }

    try:
        result = chat_json([{"role": "user", "content": prompt}], config_key="analysis", label="时间推进")
        minutes = int(result.get("minutes", 3))
        reason = result.get("reason", "")
        narration = result.get("narration", "")
        destination = result.get("destination")
        sub_destination = result.get("sub_destination", "")
        leader = result.get("leader", "")
        companions = result.get("companions", [])

        if destination == "null" or destination is None:
            destination = None
        if not isinstance(companions, list):
            companions = []

        # ── target_time 精确计算 ──────────────────────────
        # 当 LLM 返回了 target_time（具体目标时间点），用代码精确计算分钟差值
        target_time_str = result.get("target_time")
        if target_time_str and target_time_str != "null":
            try:
                dt_now = datetime.fromisoformat(current_time)
                dt_target = datetime.fromisoformat(target_time_str)
                if dt_target == dt_now:
                    # 目标时间等于当前时间：已在目标时间点，不需要推进
                    log("info", f"target_time {target_time_str} 等于当前时间 {current_time}，"
                        f"已在目标时间，使用 LLM 原始值 {minutes}分钟")
                elif dt_target < dt_now:
                    # target_time 严格在过去：可能是 LLM 把"晚上12点"写成了当天 00:00
                    # 尝试加一天修正（和 /time HH:MM 命令逻辑一致）
                    dt_target_next = dt_target + timedelta(days=1)
                    log("warning", f"target_time {target_time_str} 不在当前时间 {current_time} 之后，"
                        f"尝试加一天修正为 {dt_target_next.strftime('%Y-%m-%dT%H:%M:%S')}")
                    dt_target = dt_target_next
                    calculated_minutes = int((dt_target - dt_now).total_seconds() / 60)
                    log("info", f"target_time 精确计算: {current_time} → {dt_target.strftime('%Y-%m-%dT%H:%M:%S')} = {calculated_minutes}分钟 (LLM原始值: {minutes}分钟)")
                    minutes = calculated_minutes
                else:
                    calculated_minutes = int((dt_target - dt_now).total_seconds() / 60)
                    log("info", f"target_time 精确计算: {current_time} → {dt_target.strftime('%Y-%m-%dT%H:%M:%S')} = {calculated_minutes}分钟 (LLM原始值: {minutes}分钟)")
                    minutes = calculated_minutes
            except (ValueError, TypeError) as e:
                log("warning", f"target_time 解析失败: {target_time_str} ({e})，使用 LLM 原始值 {minutes}分钟")

        minutes = max(1, min(minutes, 1440))
        log("info", f"自动时间推进: {minutes}分钟 ({reason})")

        # 验证目的地
        if destination:
            from .location import get_location_manager, get_known_locations, get_default_sub_location

            # 确定领头人（默认玩家）
            effective_leader = leader if leader else player

            # 验证目的地在领头人的已知地点中
            leader_known = get_known_locations(effective_leader)
            loc_mgr = get_location_manager()

            if destination not in loc_mgr.all_locations():
                # 模糊匹配：LLM 可能输出简称/缩写
                fuzzy = _fuzzy_match_location(destination, list(loc_mgr.all_locations().keys()))
                if fuzzy:
                    log("info", f"移动判断: 目的地 '{destination}' 模糊匹配为 '{fuzzy}'")
                    destination = fuzzy
                else:
                    log("warning", f"移动判断: 目的地 '{destination}' 不存在且无法模糊匹配，忽略")
                    destination = None
                    sub_destination = ""
                    leader = ""
                    companions = []
            elif destination not in leader_known:
                log("warning", f"移动判断: 领头人 {effective_leader} 不知道 '{destination}'，忽略")
                destination = None
                sub_destination = ""
                leader = ""
                companions = []
            else:
                # 验证子地点：如果无效，使用默认子地点
                if sub_destination:
                    loc_obj = loc_mgr.get(destination)
                    valid_subs = [s.name for s in (loc_obj.sub_locations if loc_obj else [])]
                    if valid_subs and sub_destination not in valid_subs:
                        sub_destination = get_default_sub_location(destination)
                else:
                    sub_destination = get_default_sub_location(destination)
                if companions:
                    log("info", f"群组移动: {effective_leader} 带 {companions} → {destination}/{sub_destination}")
                else:
                    log("info", f"移动判断: → {destination}/{sub_destination}")

        # 解析 NPC 离场
        npc_departures = result.get("npc_departures", [])
        if not isinstance(npc_departures, list):
            npc_departures = []

        # 验证 NPC 离场的目的地
        validated_departures = []
        if npc_departures:
            from .location import get_location_manager as _dep_loc_mgr, get_known_locations as _dep_known, get_default_sub_location as _dep_default_sub
            dep_loc_mgr = _dep_loc_mgr()
            for dep in npc_departures:
                if not isinstance(dep, dict):
                    continue
                dep_name = dep.get("name", "")
                dep_dest = dep.get("destination")
                dep_companions = dep.get("companions", [])
                dep_reason = dep.get("reason", "")
                if not dep_name:
                    continue
                # 验证是在场 NPC
                if present_npcs and dep_name not in present_npcs:
                    log("warning", f"NPC 离场: {dep_name} 不在在场角色中，忽略")
                    continue
                # 验证目的地
                if dep_dest and dep_dest != "null":
                    if dep_dest not in dep_loc_mgr.all_locations():
                        # 模糊匹配
                        fuzzy = _fuzzy_match_location(dep_dest, list(dep_loc_mgr.all_locations().keys()))
                        if fuzzy:
                            log("info", f"NPC 离场: {dep_name} 目的地 '{dep_dest}' 模糊匹配为 '{fuzzy}'")
                            dep_dest = fuzzy
                        else:
                            log("warning", f"NPC 离场: {dep_name} 目的地 '{dep_dest}' 不存在，设为 null")
                            dep_dest = None
                    elif dep_dest not in _dep_known(dep_name):
                        log("warning", f"NPC 离场: {dep_name} 不知道 '{dep_dest}'，设为 null")
                        dep_dest = None
                else:
                    dep_dest = None
                # 验证 companions
                if not isinstance(dep_companions, list):
                    dep_companions = []
                validated_departures.append({
                    "name": dep_name,
                    "destination": dep_dest,
                    "companions": dep_companions,
                    "reason": dep_reason,
                    "busy_until": dep.get("busy_until"),
                })
                log("info", f"NPC 离场: {dep_name} → {dep_dest or '未知目的地'} ({dep_reason})")

        # 对话中的 NPC 不重新生成活动（但即将离开的 NPC 除外）
        departing_names = {d["name"] for d in validated_departures}
        for d in validated_departures:
            departing_names.update(d.get("companions", []))
        in_conversation = set(present_npcs) if present_npcs else set()
        in_conversation -= departing_names  # 离开的 NPC 不排除，让活动链更新
        advance_time(minutes, exclude_from_activity=in_conversation)

        # 记录到 TurnLogger
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            tl.log_time_advance(prompt, result, minutes, reason, narration, destination)

        return {
            "minutes": minutes, "reason": reason, "narration": narration,
            "destination": destination, "sub_destination": sub_destination,
            "leader": leader, "companions": companions,
            "npc_departures": validated_departures,
        }
    except Exception as e:
        log("warning", f"自动时间推进失败: {e}，默认 3 分钟")
        in_conversation = set(present_npcs) if present_npcs else set()
        advance_time(3, exclude_from_activity=in_conversation)
        return default_result


# ── 角色活动生成 ────────────────────────────────────────────


def _get_distances(current_location: str, character: str = "", characters: list[str] | None = None) -> str:
    """获取从当前位置到可用地点的距离列表。

    Args:
        character: 可选，传入单个角色名时只列出该角色已知的地点。
        characters: 可选，传入多个角色名时取已知地点的并集。
    """
    from .location import get_location_manager, get_known_locations, get_known_sub_locations
    loc_mgr = get_location_manager()
    current_loc = loc_mgr.get(current_location)

    # 确定过滤集合
    if characters:
        # 多角色：取并集
        known = set()
        for c in characters:
            known.update(get_known_locations(c))
    elif character:
        known = set(get_known_locations(character))
    else:
        known = None  # None = 不过滤

    def _get_visible_subs(loc_name: str, loc_obj) -> list[str]:
        """获取角色可见的子地点名列表（含★标记）。"""
        if not loc_obj.sub_locations:
            return []
        all_sub_names = [s.name for s in loc_obj.sub_locations]
        default_flags = {s.name: s.is_default for s in loc_obj.sub_locations}
        # 确定可见集合
        if characters:
            visible = set()
            for c in characters:
                visible.update(get_known_sub_locations(c, loc_name))
        elif character:
            visible = set(get_known_sub_locations(character, loc_name))
        else:
            visible = set(all_sub_names)
        # 保持原始顺序，加★标记
        return [
            s + ("★" if default_flags.get(s) else "")
            for s in all_sub_names if s in visible
        ]

    parts = []
    # 当前地点的子地点（0距离）
    if current_loc:
        visible_subs = _get_visible_subs(current_location, current_loc)
        if visible_subs:
            parts.append(f"  {current_location}（当前位置）: 0分钟  [子地点: {', '.join(visible_subs)}]")
    for name, loc in loc_mgr.all_locations().items():
        if name == current_location:
            continue
        if known is not None and name not in known:
            continue
        if current_loc:
            travel = current_loc.travel_minutes_to(loc)
            line = f"  {name}: 步行约{travel}分钟"
        else:
            line = f"  {name}"
        # 附加可见子地点信息
        visible_subs = _get_visible_subs(name, loc)
        if visible_subs:
            line += f"  [子地点: {', '.join(visible_subs)}]"
        parts.append(line)
    return "\n".join(parts) if parts else "（无可用地点）"




def generate_activity(character: str):
    """让 LLM 为角色生成下一个活动（短链生成，只取第一个）。
    
    只生成未来 30 分钟的短链并取第一个活动。
    不会把完整链存入 state，避免"钉死"NPC 未来计划。
    完整活动链只在大跳转（simulate_time_period）中使用。
    """
    state = load_state()
    current_time = state.get("current_time", "2026-01-01T08:00:00")
    char_state = state.get("characters", {}).get(character, {})

    # 只生成未来 30 分钟的短链，取第一个活动即可
    try:
        dt = datetime.fromisoformat(current_time)
        end_time = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        end_time = current_time

    activities = generate_activity_chain(character, current_time, end_time)

    if activities:
        act = activities[0]
        char_state["location"] = act.get("location", char_state.get("location", "未知"))
        char_state["sub_location"] = act.get("sub_location", "")
        char_state["activity"] = act.get("activity", "")
        char_state["activity_start"] = act.get("start", current_time)
        char_state["until"] = act.get("end", "")
        # 不存完整 activity_chain，只保留当前活动
        char_state.pop("activity_chain", None)
        save_state(state)
        log("info", f"活动生成 [{character}]: {char_state['location']}/{char_state.get('sub_location', '')} — {char_state['activity']} (到 {char_state['until']})")


def _validate_activity_chain(
    activities: list[dict], start_time: str, end_time: str,
    character: str, fallback_location: str = "",
    fallback_activity: str = "",
) -> list[dict]:
    """校验并修复 LLM 生成的活动链。

    修复项：
    - 过滤缺少必需字段的活动
    - 修复无效时间格式
    - 确保 start < end
    - 验证并修复 location / sub_location 合法性
    - 按时间排序
    - 修复时间空隙和边界
    - 空链时返回 fallback
    """
    if not activities or not isinstance(activities, list):
        log("warning", f"活动链校验 [{character}]: 空活动链，使用 fallback")
        return [{
            "start": start_time, "end": end_time,
            "location": fallback_location or "未知",
            "activity": fallback_activity or "待着",
        }]

    valid = []
    for i, act in enumerate(activities):
        if not isinstance(act, dict):
            log("warning", f"活动链校验 [{character}]: 第{i}个活动不是 dict，跳过")
            continue

        # 必需字段检查
        s = act.get("start", "")
        e = act.get("end", "")
        loc = act.get("location", "")
        if not s or not e or not loc:
            log("warning", f"活动链校验 [{character}]: 第{i}个活动缺少必需字段 "
                f"(start={s!r}, end={e!r}, location={loc!r})，跳过")
            continue

        # 时间格式验证
        try:
            dt_s = datetime.fromisoformat(s)
            dt_e = datetime.fromisoformat(e)
        except ValueError:
            log("warning", f"活动链校验 [{character}]: 第{i}个活动时间格式无效 "
                f"(start={s!r}, end={e!r})，跳过")
            continue

        # start < end
        if dt_s >= dt_e:
            log("warning", f"活动链校验 [{character}]: 第{i}个活动 start >= end "
                f"({s} >= {e})，跳过")
            continue

        # 地点合法性验证
        from .location import get_location_manager, get_default_sub_location
        loc_mgr = get_location_manager()
        if loc not in loc_mgr.all_locations():
            log("warning", f"活动链校验 [{character}]: 第{i}个活动地点 '{loc}' 不存在，"
                f"修正为 '{fallback_location}'")
            act["location"] = fallback_location or "未知"

        # 子地点合法性验证
        sub_loc = act.get("sub_location", "")
        actual_loc = act["location"]
        loc_obj = loc_mgr.get(actual_loc)
        if sub_loc:
            if loc_obj and loc_obj.sub_locations:
                valid_subs = [s.name for s in loc_obj.sub_locations]
                if sub_loc not in valid_subs:
                    default_sub = get_default_sub_location(actual_loc)
                    log("warning", f"活动链校验 [{character}]: 第{i}个活动子地点 '{sub_loc}' "
                        f"不属于 '{actual_loc}'，修正为 '{default_sub}'")
                    act["sub_location"] = default_sub
        else:
            # sub_location 为空但该地点有子地点时，自动补全默认子地点
            if loc_obj and loc_obj.sub_locations:
                default_sub = get_default_sub_location(actual_loc)
                if default_sub:
                    act["sub_location"] = default_sub
                    log("debug", f"活动链校验 [{character}]: 第{i}个活动缺少子地点，"
                        f"自动补全为 '{default_sub}'")

        valid.append(act)

    if not valid:
        log("warning", f"活动链校验 [{character}]: 所有活动都无效，使用 fallback")
        return [{
            "start": start_time, "end": end_time,
            "location": fallback_location or "未知",
            "activity": fallback_activity or "待着",
        }]

    # 按 start 排序
    valid.sort(key=lambda a: a["start"])

    # 修复边界：第一个活动的 start 对齐到 start_time
    if valid[0]["start"] > start_time:
        log("debug", f"活动链校验 [{character}]: 第一个活动 start ({valid[0]['start']}) "
            f"晚于时段开始 ({start_time})，向前扩展")
        valid[0]["start"] = start_time
    elif valid[0]["start"] < start_time:
        valid[0]["start"] = start_time

    # 修复边界：最后一个活动的 end 对齐到 end_time
    if valid[-1]["end"] < end_time:
        log("debug", f"活动链校验 [{character}]: 最后一个活动 end ({valid[-1]['end']}) "
            f"早于时段结束 ({end_time})，向后扩展")
        valid[-1]["end"] = end_time
    elif valid[-1]["end"] > end_time:
        valid[-1]["end"] = end_time

    # 修复中间的时间空隙：扩展前一个活动的 end 到下一个的 start
    for i in range(len(valid) - 1):
        curr_end = valid[i]["end"]
        next_start = valid[i + 1]["start"]
        if curr_end < next_start:
            log("debug", f"活动链校验 [{character}]: 活动 {i} 和 {i+1} 之间有时间空隙 "
                f"({curr_end} ~ {next_start})，扩展前一个活动")
            valid[i]["end"] = next_start
        elif curr_end > next_start:
            # 时间重叠，截断前一个
            valid[i]["end"] = next_start

    return valid


def generate_activity_chain(
    character: str, start_time: str, end_time: str,
    event_context: str = "",
    override_location: str = "",
) -> list[dict]:
    """为角色生成覆盖整个时间段的活动链。

    Args:
        event_context: 可选，刚发生的事件描述（用于事件后重新规划）。
        override_location: 可选，覆盖当前位置（用于事件改变了角色位置时）。
    """
    state = load_state()
    day_of_week = state.get("day_of_week", "周四")
    char_state = state.get("characters", {}).get(character, {})
    current_location = override_location or char_state.get("location", "未知")
    current_sub_location = char_state.get("sub_location", "")
    current_activity = char_state.get("activity", "")
    current_emotion = char_state.get("emotion", "")
    busy_until = char_state.get("until", "")

    try:
        dt_s = datetime.fromisoformat(start_time)
        dt_e = datetime.fromisoformat(end_time)
        total_min = int((dt_e - dt_s).total_seconds() / 60)
        hours, mins = divmod(total_min, 60)
        if hours > 0:
            duration_desc = f"{hours}小时{mins}分钟" if mins else f"{hours}小时"
        else:
            duration_desc = f"{mins}分钟"
    except Exception:
        duration_desc = "若干时间"

    _sections = parse_character_file(load_character(character))
    # 完整设定：公开设定 + 私密设定
    profile_parts = [_sections["public_base"]]
    if _sections["private_base"].strip():
        profile_parts.append(f"【私密设定】\n{_sections['private_base']}")
    profile = "\n".join(profile_parts)
    # 完整记忆：公开动态 + 私密动态
    memory_parts = []
    if _sections["public_dynamic"].strip():
        memory_parts.append(f"【公开动态】\n{_sections['public_dynamic']}")
    if _sections["private_dynamic"].strip():
        memory_parts.append(f"【私密动态】\n{_sections['private_dynamic']}")
    memory = "\n".join(memory_parts) if memory_parts else ""
    distances = _get_distances(current_location, character)

    # 角色信息：同地点的可见，其他的只列名字
    player = get_player_character()
    all_chars = state.get("characters", {})
    same_loc_parts = []
    other_name_parts = []
    for name, cs in all_chars.items():
        if name == character:
            continue
        loc = cs.get("location", "未知")
        act = cs.get("activity", "")
        if loc == current_location:
            same_loc_parts.append(f"  {name}" + (f" — {act}" if act else ""))
        else:
            other_name_parts.append(f"  {name}")

    chars_info_lines = []
    if same_loc_parts:
        chars_info_lines.append("在场（你能看到的）：")
        chars_info_lines.extend(same_loc_parts)
    if other_name_parts:
        chars_info_lines.append("不在场（你可以根据对他们的了解推测位置）：")
        chars_info_lines.extend(other_name_parts)
    other_chars_locations = "\n".join(chars_info_lines) if chars_info_lines else "无"

    # 构建事件上下文（为空时不显示）
    event_context_parts = []
    if event_context:
        event_context_parts.append(f"**⚠️ 刚发生的事件（请据此调整后续计划）：**\n{event_context}")

    # 注入预定事件
    from .events import format_events_for_prompt
    scheduled = format_events_for_prompt(character, start_time, end_time)
    if scheduled:
        event_context_parts.append(f"**📅 预定事件（时间段内必须安排）：**\n{scheduled}")

    event_context_text = "\n\n".join(event_context_parts)
    if event_context_text:
        event_context_text = f"\n{event_context_text}\n"

    template = read_file(PROMPTS_DIR / "activity_chain.md")

    # 世界设定（轻量注入，仅 premise + era + tone）
    from .utils import load_lore
    _lore = load_lore()
    _world_ctx_parts = []
    if _lore.get("world_premise"):
        _world_ctx_parts.append(f"世界概述：{_lore['world_premise']}")
    if _lore.get("era"):
        _world_ctx_parts.append(f"时代背景：{_lore['era']}")
    if _lore.get("tone"):
        _world_ctx_parts.append(f"叙事基调：{_lore['tone']}")
    world_context = "\n".join(_world_ctx_parts)

    prompt = template.format(
        start_time=start_time,
        end_time=end_time,
        duration_desc=duration_desc,
        day_of_week=day_of_week,
        character=character,
        current_location=current_location,
        current_sub_location=current_sub_location or "无",
        current_activity=current_activity or "无",
        busy_until=busy_until or "无",
        current_emotion=current_emotion or "正常",
        profile=profile,
        memory=memory,
        distances=distances,
        other_chars_locations=other_chars_locations,
        event_context=event_context_text,
        world_context=world_context,
    )

    try:
        result = chat_json([{"role": "user", "content": prompt}], config_key="analysis", label="活动链")
        raw_activities = result.get("activities", [])

        # 校验并修复 LLM 输出
        activities = _validate_activity_chain(
            raw_activities, start_time, end_time, character,
            fallback_location=current_location,
            fallback_activity=current_activity or "待着",
        )

        log("info", f"活动链 [{character}]: {len(activities)} 个活动")

        # 记录到 TurnLogger
        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            tl.log_activity_chain(character, prompt, result, activities)

        # 处理 NPC 主动跳过的预定事件
        skipped = result.get("skipped_events", [])
        if skipped and isinstance(skipped, list):
            from .events import mark_skipped
            for skip in skipped:
                if isinstance(skip, dict) and skip.get("id"):
                    mark_skipped(skip["id"], character, skip.get("reason", ""))
                    log("info", f"预定事件跳过 [{character}]: {skip['id']} — {skip.get('reason', '')}")
            if tl:
                tl.log_scheduled_event(
                    [{"action": "skip", "id": s.get("id", ""), "description": s.get("reason", "")}
                     for s in skipped if isinstance(s, dict)],
                    source=f"活动链/{character}",
                )

        return activities
    except Exception as e:
        log("warning", f"活动链失败 [{character}]: {e}")
        return [{
            "start": start_time, "end": end_time,
            "location": current_location,
            "activity": current_activity or "待着",
        }]


def check_activity_expiry(exclude: set[str] | None = None):
    """检查所有 NPC 的活动是否到期，到期的重新生成（用于小幅跳转）。
    
    Args:
        exclude: 需要跳过的角色名集合（例如正在对话中的 NPC）。
    """
    state = load_state()
    current_time = state.get("current_time", "")

    try:
        now = datetime.fromisoformat(current_time)
    except Exception:
        return

    for name, cs in state.get("characters", {}).items():
        if name == get_player_character():
            continue
        if exclude and name in exclude:
            continue

        until = cs.get("until")
        if not until:
            # 没有 until，需要生成活动
            log("info", f"角色 {name} 没有活动计划，生成中...")
            generate_activity(name)
            continue

        try:
            until_dt = datetime.fromisoformat(until)
            if now >= until_dt:
                log("info", f"角色 {name} 的活动已到期 ({until})，重新生成...")
                generate_activity(name)
        except Exception:
            continue


# ── 世界模拟 ──────────────────────────────────────────────

# 大幅跳转阈值（分钟），同时用于小幅跳转的重叠检查冷却时间
def _get_large_jump_minutes() -> int:
    """从 config 读取大幅跳转阈值，默认 60 分钟。"""
    return load_config().get("world", {}).get("large_jump_minutes", 60)


# 最小重叠时长（分钟），低于此值跳过
_MIN_OVERLAP_MINUTES = 5


def _apply_event_impact(
    impact: dict, summary: str, event_end: str, new_time: str,
    chains: dict, location_resolver: callable,
):
    """处理事件影响：为受影响角色重新生成后续活动链（公共函数）。

    Args:
        impact: {角色名: 影响原因} — LLM 返回的 activity_impact
        summary: 事件摘要
        event_end: 事件结束时间（截断点）
        new_time: 总时间段的结束时间
        chains: 活动链字典（会被原地修改）
        location_resolver: 函数 (npc_name) -> str，返回该角色的 override 位置
    """
    if not impact or not isinstance(impact, dict) or not new_time:
        return
    for npc_name, reason in impact.items():
        if npc_name not in chains or not reason:
            continue
        kept = [a for a in chains[npc_name] if a.get("end", "") <= event_end]
        override_loc = location_resolver(npc_name)
        ctx = f"{summary}。影响：{reason}"
        log("info", f"事件影响 [{npc_name}]: {reason}，重新生成 {event_end} 之后的活动链 (位置: {override_loc})")
        try:
            new_acts = generate_activity_chain(
                npc_name, event_end, new_time,
                event_context=ctx,
                override_location=override_loc,
            )
            chains[npc_name] = kept + new_acts
            log("info", f"活动修正 [{npc_name}]: 重新安排 {len(new_acts)} 个后续活动")
        except Exception as e:
            log("warning", f"重新生成活动链失败 [{npc_name}]: {e}")


def _process_phone_call(
    caller: str, target: str, call_start: str, call_end: str,
    activity: str, caller_location: str,
    chains: dict, state: dict, tl, player: str,
    new_time: str = "",
) -> bool:
    """处理电话场景（公共函数，大小幅跳转共用）。

    流程：互动判断 → 场景生成 → 事件记录 → 记忆分析 → 事件影响。
    返回 True 表示场景成功生成。
    """
    try:
        dt_s = datetime.fromisoformat(call_start)
        dt_e = datetime.fromisoformat(call_end)
        call_minutes = max(1, int((dt_e - dt_s).total_seconds() / 60))
    except Exception:
        call_minutes = 10

    caller_sec = parse_character_file(load_character(caller))
    target_sec = parse_character_file(load_character(target))
    target_state = state.get("characters", {}).get(target, {})
    target_activity = target_state.get("activity", "")

    char_info = (
        f"- {caller}（主叫方）: {activity}\n"
        f"  设定摘要: {caller_sec['public_base'][:100]}\n"
        f"- {target}（被叫方，在{target_state.get('location', '未知')}）: {target_activity}\n"
        f"  设定摘要: {target_sec['public_base'][:100]}"
    )

    # 互动判断
    try:
        check_template = read_file(PROMPTS_DIR / "offscreen_events.md")
        check_prompt = check_template.format(
            old_time=call_start, new_time=call_end, minutes=call_minutes,
            location=f"电话（{caller}→{target}）",
            location_desc="电话通话",
            char_info=char_info,
        )
        check_result = chat_json([{"role": "user", "content": check_prompt}], label="互动判断")
        should_interact = check_result.get("interact", False)
        reason = check_result.get("reason", "")

        if tl:
            tl.log_custom("电话互动判断", f"{caller}→{target} | 互动: {should_interact} | 理由: {reason}")

        if not should_interact:
            log("info", f"电话判断 [{caller}→{target}]: 不接通 - {reason}")
            return False
        log("info", f"电话判断 [{caller}→{target}]: 接通 - {reason}")
    except Exception as e:
        log("warning", f"电话互动判断失败 [{caller}→{target}]: {e}，默认不生成")
        return False

    # 场景生成
    try:
        caller_state_data = state.get("characters", {}).get(caller, {})
        caller_emotion = caller_state_data.get("emotion", "")
        caller_detail = f"--- {caller}（主叫方）---\n当前活动：{activity}\n"
        if caller_emotion:
            caller_detail += f"当前情绪：{caller_emotion}\n"
        caller_detail += f"公开设定：\n{caller_sec['public_base']}\n"
        caller_detail += f"私密设定：\n{caller_sec['private_base']}\n"
        if caller_sec['public_dynamic'].strip():
            caller_detail += f"公开动态（外在状态）：\n{caller_sec['public_dynamic']}\n"
        if caller_sec['private_dynamic'].strip():
            caller_detail += f"私密动态（内心记忆）：\n{caller_sec['private_dynamic']}\n"

        target_emotion = target_state.get("emotion", "")
        target_detail = f"--- {target}（被叫方）---\n当前活动：{target_activity}\n"
        if target_emotion:
            target_detail += f"当前情绪：{target_emotion}\n"
        target_detail += f"公开设定：\n{target_sec['public_base']}\n"
        target_detail += f"私密设定：\n{target_sec['private_base']}\n"
        if target_sec['public_dynamic'].strip():
            target_detail += f"公开动态（外在状态）：\n{target_sec['public_dynamic']}\n"
        if target_sec['private_dynamic'].strip():
            target_detail += f"私密动态（内心记忆）：\n{target_sec['private_dynamic']}\n"

        scene_template = read_file(PROMPTS_DIR / "offscreen_scene.md")
        scene_prompt = scene_template.format(
            old_time=call_start, new_time=call_end, minutes=call_minutes,
            location=f"电话通话（{caller}拨打给{target}）",
            location_desc="电话通话",
            characters_detail=caller_detail + "\n" + target_detail,
            remaining_schedules="", available_locations="",
        )

        scene_result = chat_json([{"role": "user", "content": scene_prompt}], config_key="primary_story", label="离屏场景")
        scene = scene_result.get("scene", "")
        summary = scene_result.get("summary", "")
        impact = scene_result.get("activity_impact", {})

        log("info", f"电话场景 [{caller}→{target}] ({call_start[11:16]}~{call_end[11:16]}): {summary}")

        if tl:
            tl.log_offscreen_scene(
                f"电话: {caller}→{target}", f"{call_start[11:16]}~{call_end[11:16]}",
                scene_prompt, scene_result, scene, summary, [], impact or {}
            )

        if not scene.strip():
            log("warning", f"电话场景内容为空 [{caller}→{target}]，跳过")
            return False

        # 记录事件
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        event_id = _next_event_id()
        write_json(EVENTS_DIR / f"{event_id}.json", {
            "id": event_id,
            "time": call_start, "end_time": call_end,
            "location": f"电话（{caller}→{target}）",
            "characters": [caller, target],
            "summary": summary, "scene": scene,
            "type": "phone_call",
        })

        # 记忆分析
        if scene:
            from .memory_pipeline import run_scene_memory_analysis
            run_scene_memory_analysis([caller, target], scene, player=player)

        # 事件影响
        _apply_event_impact(
            impact, summary, call_end, new_time, chains,
            location_resolver=lambda name: caller_location if name == caller
                else state.get("characters", {}).get(name, {}).get("location", ""),
        )

        return True

    except Exception as e:
        log("warning", f"电话场景生成失败 [{caller}→{target}]: {e}")
        return False


def _generate_overlap_scene(
    npcs: list[str], location: str,
    ov_start: str, ov_end: str, ov_minutes: int,
    chains: dict, state: dict, tl, player: str,
    new_time: str,
) -> bool:
    """为时空重叠生成离屏场景（公共函数，大小幅跳转共用）。

    流程：位置验证 → 场景生成 → 事件记录 → 记忆分析 → 事件影响。
    不包含互动判断（由调用方处理）。返回 True 表示场景成功生成。
    """
    from .location import get_location_manager

    # 位置验证
    for name in npcs:
        still_there = False
        for act in chains.get(name, []):
            if (act.get("start", "") <= ov_start and
                act.get("end", "") > ov_start and
                act.get("location", "") == location):
                still_there = True
                break
        if not still_there:
            log("info", f"跳过离屏场景 [{location}] {npcs}: {name} 已不在 {location}（链已变更）")
            return False

    # 构建角色详情和后续计划
    chars_detail_parts = []
    remaining_parts = []
    for name in npcs:
        _sec = parse_character_file(load_character(name))
        npc_activity = ""
        _sub_loc = ""
        for act in chains.get(name, []):
            if act.get("start", "") <= ov_start and act.get("end", "") > ov_start:
                npc_activity = act.get("activity", "")
                _sub_loc = act.get("sub_location", "")
                break

        _char_state = state.get("characters", {}).get(name, {})
        _emotion = _char_state.get("emotion", "")
        detail = f"--- {name} ---\n"
        detail += f"当前活动：{npc_activity}\n"
        if _sub_loc:
            detail += f"当前子地点：{_sub_loc}\n"
        if _emotion:
            detail += f"当前情绪：{_emotion}\n"
        detail += f"公开设定：\n{_sec['public_base']}\n"
        detail += f"私密设定：\n{_sec['private_base']}\n"
        if _sec['public_dynamic'].strip():
            detail += f"公开动态（外在状态）：\n{_sec['public_dynamic']}\n"
        if _sec['private_dynamic'].strip():
            detail += f"私密动态（内心记忆）：\n{_sec['private_dynamic']}\n"
        chars_detail_parts.append(detail)

        remaining = [
            a for a in chains.get(name, [])
            if a.get("start", "") >= ov_end
        ]
        if remaining:
            sched = "\n".join(
                f"  {a.get('start','')[11:16]}-{a.get('end','')[11:16]} {a.get('location','')} {a.get('activity','')}"
                for a in remaining
            )
            remaining_parts.append(f"{name}:\n{sched}")
        else:
            remaining_parts.append(f"{name}: 无后续计划")

    loc_mgr = get_location_manager()
    loc = loc_mgr.get(location)
    loc_desc = loc.description if loc else ""

    template = read_file(PROMPTS_DIR / "offscreen_scene.md")
    prompt = template.format(
        old_time=ov_start, new_time=ov_end, minutes=ov_minutes,
        location=location, location_desc=loc_desc,
        characters_detail=chr(10).join(chars_detail_parts),
        remaining_schedules=chr(10).join(remaining_parts),
        available_locations=_get_distances(location, characters=npcs),
    )

    try:
        result = chat_json(
            [{"role": "user", "content": prompt}],
            config_key="primary_story",
            label="离屏场景",
        )
        scene = result.get("scene", "")
        summary = result.get("summary", "")
        impact = result.get("activity_impact", {})

        # 处理群组移动
        group_move = result.get("group_movement", {})
        override_map, extra_impacts = _validate_group_movement(group_move, chains, player)
        if extra_impacts:
            if not isinstance(impact, dict):
                impact = {}
            for name, reason in extra_impacts.items():
                if name not in impact:
                    impact[name] = reason
            if tl:
                tl.log_custom("离屏群组移动",
                    f"{group_move.get('leader', '')} 带 {', '.join(group_move.get('companions', []))} → {group_move.get('destination', '')}")

        # 处理预定事件（只调用一次，避免重复创建）
        scene_events = result.get("events", [])
        if scene_events:
            from .events import process_event_operations
            process_event_operations(scene_events, npcs[0], ov_start)
            if tl:
                tl.log_scheduled_event(scene_events, source=f"离屏场景/{location}")

        if tl:
            tl.log_offscreen_scene(
                location, f"{ov_start[11:16]}~{ov_end[11:16]}",
                prompt, result, scene, summary, [], impact or {}
            )

        log("info", f"离屏场景 [{location}] ({ov_start[11:16]}~{ov_end[11:16]}): {summary}")

        if not scene.strip():
            log("warning", f"离屏场景内容为空 [{location}] {npcs}，跳过")
            return False

        # 记录事件
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        event_id = _next_event_id()
        write_json(EVENTS_DIR / f"{event_id}.json", {
            "id": event_id,
            "time": ov_start, "end_time": ov_end,
            "location": location, "characters": npcs,
            "summary": summary, "scene": scene,
        })

        # 记忆分析
        if scene:
            from .memory_pipeline import run_scene_memory_analysis
            run_scene_memory_analysis(npcs, scene, player=player)

        # 事件影响
        _apply_event_impact(
            impact, summary, ov_end, new_time, chains,
            location_resolver=lambda name: override_map.get(name, location),
        )

        return True

    except Exception as e:
        log("warning", f"离屏场景失败 [{location}]: {e}")
        return False


def _has_scheduled_event(
    npcs: list[str], location: str, start: str, end: str,
) -> bool:
    """检查这组角色在指定地点和时间段是否有共同的 pending 预定事件。"""
    from .events import get_all_events
    npc_set = set(npcs)
    for evt in get_all_events():
        if evt.get("status", "pending") != "pending":
            continue
        evt_loc = evt.get("location", "")
        # 宽松地点匹配：精确 or 互相包含
        if evt_loc and location:
            if evt_loc != location and evt_loc not in location and location not in evt_loc:
                continue
        elif evt_loc != location:
            continue
        evt_time = evt.get("time", "")
        if not (start <= evt_time <= end):
            continue
        participants = set(evt.get("participants", []))
        # 至少两个重叠角色都是参与者
        if len(npc_set & participants) >= 2:
            return True
    return False


def simulate_small_jump(old_time: str, new_time: str):
    """小幅跳转的轻量世界模拟。

    复用 state 中已有的活动链（不重新生成），检测：
    1. 特殊活动（interact / seek / phone_call）
    2. 时间+地点重叠 → 互动判断 → 场景生成

    使用冷却机制（_LARGE_JUMP_MINUTES）避免同一对角色被反复检测。
    """
    from collections import defaultdict
    from .utils import get_turn_logger

    state = load_state()
    player = get_player_character()
    tl = get_turn_logger()

    npc_names = [n for n in state.get("characters", {}) if n != player]
    if not npc_names:
        return

    # ── 从 state 收集已有的活动链 ──
    chains: dict[str, list[dict]] = {}
    for npc in npc_names:
        cs = state.get("characters", {}).get(npc, {})
        chain = cs.get("activity_chain", [])
        if chain:
            filtered = [
                a for a in chain
                if a.get("end", "") > old_time and a.get("start", "") < new_time
            ]
            if filtered:
                chains[npc] = filtered
        if npc not in chains:
            chains[npc] = [{
                "start": old_time, "end": new_time,
                "location": cs.get("location", "未知"),
                "activity": cs.get("activity", ""),
            }]

    # ── 冷却机制 ──
    cooldowns = state.get("overlap_cooldown", {})

    def _is_on_cooldown(npcs: list[str]) -> bool:
        key = ",".join(sorted(npcs))
        last_check = cooldowns.get(key, "")
        if not last_check:
            return False
        try:
            dt_last = datetime.fromisoformat(last_check)
            dt_now = datetime.fromisoformat(new_time)
            return (dt_now - dt_last).total_seconds() / 60 < _get_large_jump_minutes()
        except Exception:
            return False

    def _set_cooldown(npcs: list[str]):
        key = ",".join(sorted(npcs))
        cooldowns[key] = new_time

    # ── 跟踪 ──
    seen_pairs: set[tuple[str, tuple[str, ...]]] = set()
    busy_intervals: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # 记录原始链引用，用于检测 event_impact 是否修改了链
    original_chain_refs: dict[str, list[dict]] = {npc: acts for npc, acts in chains.items()}

    def _is_any_busy(chars: list[str], start: str, end: str) -> tuple[bool, str]:
        for char in chars:
            for bs, be in busy_intervals.get(char, []):
                if start < be and end > bs:
                    return True, char
        return False, ""

    def _mark_busy(chars: list[str], start: str, end: str):
        for char in chars:
            busy_intervals[char].append((start, end))

    def _target_at_location(target, location, act_start):
        if target in chains:
            for t_act in chains[target]:
                if (t_act.get("location") == location and
                    t_act.get("start", "") <= act_start and
                    t_act.get("end", "") > act_start):
                    return True
            return False
        else:
            return state.get("characters", {}).get(target, {}).get("location") == location

    # ── Step 1: 处理特殊活动 ──
    special_activities = []
    for npc, acts in chains.items():
        for act in acts:
            if act.get("type", "") in ("phone_call", "interact", "seek"):
                special_activities.append({"npc": npc, **act})

    # --- interact / seek（共用逻辑） ---
    for act in special_activities:
        act_type = act.get("type", "")
        if act_type not in ("interact", "seek"):
            continue
        npc = act["npc"]
        target = act.get("interact_target" if act_type == "interact" else "seek_target", "")
        act_start = act.get("start", "")
        act_end = act.get("end", "")
        location = act.get("location", "")

        if not target or target not in state.get("characters", {}):
            continue
        pair_key = (location, tuple(sorted([npc, target])))
        if pair_key in seen_pairs:
            continue
        if _is_on_cooldown([npc, target]):
            continue
        if not any(
            a.get("start", "") <= act_start and a.get("end", "") > act_start and a.get("location", "") == location
            for a in chains.get(npc, [])
        ):
            continue
        if not _target_at_location(target, location, act_start):
            continue

        seen_pairs.add(pair_key)
        busy, _ = _is_any_busy([npc, target], act_start, act_end)
        if busy:
            continue

        success = _process_npc_interaction(
            npc, target, location, act_start, act_end,
            act.get("activity", ""), chains, state, tl, player,
            new_time=new_time,
        )
        if success:
            _mark_busy([npc, target], act_start, act_end)
            _set_cooldown([npc, target])

    # --- phone_call ---
    for act in special_activities:
        if act.get("type") != "phone_call":
            continue
        caller = act["npc"]
        target = act.get("call_target", "")
        call_start = act.get("start", "")
        call_end = act.get("end", "")

        if not target or target not in state.get("characters", {}):
            continue
        pair_key = ("电话", tuple(sorted([caller, target])))
        if pair_key in seen_pairs:
            continue
        if _is_on_cooldown([caller, target]):
            continue
        seen_pairs.add(pair_key)
        busy, _ = _is_any_busy([caller, target], call_start, call_end)
        if busy:
            continue

        success = _process_phone_call(
            caller, target, call_start, call_end,
            act.get("activity", ""), act.get("location", ""),
            chains, state, tl, player, new_time,
        )
        if success:
            _mark_busy([caller, target], call_start, call_end)
            _set_cooldown([caller, target])

    # ── Step 2: 时空重叠检测 + 互动判断 + 场景生成 ──
    overlaps = _find_chain_overlaps(chains)

    # ── Step 2a: 收集候选重叠 ──
    candidates = []
    for ov in overlaps:
        npcs = ov["npcs"]
        location = ov["location"]
        pair_key = (location, tuple(sorted(npcs)))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # 有预定事件的组合跳过冷却检查
        has_scheduled = _has_scheduled_event(npcs, location, ov["start"], ov["end"])
        if not has_scheduled and _is_on_cooldown(npcs):
            continue

        busy, _ = _is_any_busy(npcs, ov["start"], ov["end"])
        if busy:
            continue

        ov["has_scheduled"] = has_scheduled
        candidates.append(ov)

    # ── Step 2b: 并行互动判断 ──
    def _check_small_jump_interaction(ov: dict) -> dict:
        """对单个重叠执行互动判断，返回结果。"""
        npcs = ov["npcs"]
        location = ov["location"]

        try:
            dt_s = datetime.fromisoformat(ov["start"])
            dt_e = datetime.fromisoformat(ov["end"])
            ov_minutes = max(1, int((dt_e - dt_s).total_seconds() / 60))
        except Exception:
            ov_minutes = 15

        char_info_parts = []
        for name in npcs:
            _sec = parse_character_file(load_character(name))
            npc_activity = ""
            for a in chains.get(name, []):
                if a.get("start", "") <= ov["start"] and a.get("end", "") > ov["start"]:
                    npc_activity = a.get("activity", "")
                    break
            char_info_parts.append(
                f"- {name}: {npc_activity or '无特定活动'}\n"
                f"  设定摘要: {_sec['public_base'][:100]}"
            )

        from .location import get_location_manager
        loc_mgr = get_location_manager()
        loc = loc_mgr.get(location)
        loc_desc = loc.description if loc else ""

        try:
            check_template = read_file(PROMPTS_DIR / "offscreen_events.md")
            check_prompt = check_template.format(
                old_time=ov["start"], new_time=ov["end"], minutes=ov_minutes,
                location=location, location_desc=loc_desc,
                char_info="\n".join(char_info_parts),
            )
            check_result = chat_json([{"role": "user", "content": check_prompt}], label="互动判断")
            should_interact = check_result.get("interact", False)
            reason = check_result.get("reason", "")

            if tl:
                tl.log_custom("小幅跳转互动判断",
                    f"地点: {location} | 角色: {', '.join(npcs)} | 互动: {should_interact}"
                    + (f" | 理由: {reason}" if reason else "")
                    + (" | 有预定事件" if ov.get("has_scheduled") else ""))

            _set_cooldown(npcs)
            return {**ov, "should_interact": should_interact, "reason": reason, "minutes": ov_minutes}
        except Exception as e:
            log("warning", f"[小幅跳转] 互动判断失败 [{location}] {npcs}: {e}")
            return {**ov, "should_interact": False, "reason": f"判断失败: {e}", "minutes": ov_minutes}

    check_results = []
    if candidates:
        import concurrent.futures
        log("info", f"[小幅跳转] 并行执行 {len(candidates)} 个互动判断")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(_check_small_jump_interaction, ov): ov for ov in candidates}
            for future in concurrent.futures.as_completed(futures):
                try:
                    check_results.append(future.result())
                except Exception as e:
                    ov = futures[future]
                    log("warning", f"[小幅跳转] 互动判断异常 [{ov.get('location', '')}]: {e}")

    # 按时间排序，确保场景按时间顺序生成
    check_results.sort(key=lambda r: r.get("start", ""))

    # ── Step 2c: 顺序生成场景 ──
    for result in check_results:
        if not result.get("should_interact"):
            continue

        npcs = result["npcs"]
        location = result["location"]
        ov_minutes = result.get("minutes", 15)

        # 再次检查 busy（并行判断期间可能有变化）
        busy, _ = _is_any_busy(npcs, result["start"], result["end"])
        if busy:
            continue

        success = _generate_overlap_scene(
            npcs, location, result["start"], result["end"], ov_minutes,
            chains, state, tl, player, new_time,
        )
        if success:
            _mark_busy(npcs, result["start"], result["end"])

    # 保存冷却状态 + 更新受事件影响的角色状态
    state = load_state()  # 重新加载，确保获取最新数据
    for npc, acts in chains.items():
        if acts and acts is not original_chain_refs.get(npc):
            last = acts[-1]
            cs = state.setdefault("characters", {}).setdefault(npc, {})
            cs["location"] = last.get("location", cs.get("location", "未知"))
            cs["sub_location"] = last.get("sub_location", "")
            cs["activity"] = last.get("activity", "")
            cs["activity_start"] = last.get("start", new_time)
            cs["until"] = last.get("end", new_time)
            log("info", f"[小幅跳转] 事件影响更新 [{npc}]: {cs['location']} - {cs['activity']}")
    state["overlap_cooldown"] = cooldowns
    save_state(state)


def _find_chain_overlaps(chains: dict[str, list[dict]]) -> list[dict]:
    """找出活动链中时间+地点重叠的段。纯算法，无 LLM 调用。

    改进：
    - 过滤重叠时长 < _MIN_OVERLAP_MINUTES 的碎片
    - 合并同一地点、同一角色组的连续重叠
    """
    by_location: dict[str, list[tuple]] = {}
    for npc, acts in chains.items():
        for act in acts:
            loc = act.get("location", "")
            if not loc:
                continue
            by_location.setdefault(loc, []).append(
                (npc, act.get("start", ""), act.get("end", ""))
            )

    overlaps = []
    for location, segments in by_location.items():
        if len(set(s[0] for s in segments)) < 2:
            continue
        for i in range(len(segments)):
            for j in range(i + 1, len(segments)):
                npc_a, start_a, end_a = segments[i]
                npc_b, start_b, end_b = segments[j]
                if npc_a == npc_b:
                    continue
                start = max(start_a, start_b)
                end = min(end_a, end_b)
                if start < end:
                    # 过滤太短的重叠
                    try:
                        dt_s = datetime.fromisoformat(start)
                        dt_e = datetime.fromisoformat(end)
                        if (dt_e - dt_s).total_seconds() / 60 < _MIN_OVERLAP_MINUTES:
                            continue
                    except Exception:
                        pass
                    overlaps.append({
                        "start": start, "end": end,
                        "location": location,
                        "npcs": sorted([npc_a, npc_b]),
                    })

    if not overlaps:
        return []

    # 合并同一地点+同一角色组的连续/重叠条目
    overlaps.sort(key=lambda x: (x["location"], tuple(x["npcs"]), x["start"]))
    merged = []
    for ov in overlaps:
        found = False
        for m in merged:
            if m["location"] == ov["location"]:
                # 同一地点 + 时间有交集 -> 合并
                if ov["start"] <= m["end"] and ov["end"] > m["start"]:
                    m["start"] = min(m["start"], ov["start"])
                    m["end"] = max(m["end"], ov["end"])
                    for npc in ov["npcs"]:
                        if npc not in m["npcs"]:
                            m["npcs"].append(npc)
                    m["npcs"].sort()
                    found = True
                    break
        if not found:
            merged.append(dict(ov))

    return sorted(merged, key=lambda x: x["start"])


def _validate_group_movement(
    group_move: dict, chains: dict, player: str
) -> tuple[dict[str, str], dict[str, str]]:
    """验证离屏场景的群组移动，返回 (override_map, extra_impacts)。

    - override_map: {角色名: 目的地} — 用于替代 activity_impact 中的 override_location
    - extra_impacts: {角色名: 原因} — 需要补充到 activity_impact 中的角色
    """
    if not group_move or not isinstance(group_move, dict):
        return {}, {}

    leader = group_move.get("leader", "")
    companions = group_move.get("companions", [])
    destination = group_move.get("destination", "")

    if not destination or not leader:
        return {}, {}

    if not isinstance(companions, list):
        companions = []

    from .location import get_location_manager, get_known_locations
    loc_mgr = get_location_manager()

    if destination not in loc_mgr.all_locations():
        # 模糊匹配：LLM 可能输出简称/缩写
        fuzzy = _fuzzy_match_location(destination, list(loc_mgr.all_locations().keys()))
        if fuzzy:
            log("info", f"离屏群组移动: 目的地 '{destination}' 模糊匹配为 '{fuzzy}'")
            destination = fuzzy
        else:
            log("warning", f"离屏群组移动: 目的地 '{destination}' 不存在且无法模糊匹配，忽略")
            return {}, {}

    leader_known = get_known_locations(leader)
    if destination not in leader_known:
        log("warning", f"离屏群组移动: 领头人 {leader} 不知道 '{destination}'，忽略")
        return {}, {}

    all_movers = []
    for name in [leader] + companions:
        if name != player and name in chains and name not in all_movers:
            all_movers.append(name)

    if not all_movers:
        return {}, {}

    log("info", f"离屏群组移动: {leader} 带 {companions} → {destination}")

    override_map = {name: destination for name in all_movers}
    extra_impacts = {}
    for name in all_movers:
        if name == leader:
            extra_impacts[name] = f"带人前往{destination}"
        else:
            extra_impacts[name] = f"与{leader}一起前往{destination}"

    return override_map, extra_impacts


def _process_npc_interaction(
    npc: str, target: str, location: str,
    act_start: str, act_end: str, activity: str,
    chains: dict, state: dict, tl, player: str,
    new_time: str = "",
) -> bool:
    """处理 NPC 主动发起的面对面互动（interact/seek 共用）。
    
    流程：互动判断 → 场景生成 → 事件记录 → 记忆分析 → 事件影响处理。
    返回 True 表示场景成功生成，False 表示未生成。
    """
    from .location import get_location_manager

    try:
        dt_s = datetime.fromisoformat(act_start)
        dt_e = datetime.fromisoformat(act_end)
        minutes = max(1, int((dt_e - dt_s).total_seconds() / 60))
    except Exception:
        minutes = 15

    # 收集双方信息
    npc_sec = parse_character_file(load_character(npc))
    target_sec = parse_character_file(load_character(target))

    # 获取目标的当前活动和子地点
    target_activity = ""
    target_sub_location = ""
    for t_act in chains.get(target, []):
        if t_act.get("start", "") <= act_start and t_act.get("end", "") > act_start:
            target_activity = t_act.get("activity", "")
            target_sub_location = t_act.get("sub_location", "")
            break

    char_info = (
        f"- {npc}（主动方）: {activity}\n"
        f"  设定摘要: {npc_sec['public_base'][:100]}\n"
        f"- {target}: {target_activity}\n"
        f"  设定摘要: {target_sec['public_base'][:100]}"
    )

    loc_mgr = get_location_manager()
    loc = loc_mgr.get(location)
    loc_desc = loc.description if loc else ""

    # 互动判断
    try:
        check_template = read_file(PROMPTS_DIR / "offscreen_events.md")
        check_prompt = check_template.format(
            old_time=act_start, new_time=act_end, minutes=minutes,
            location=location, location_desc=loc_desc,
            char_info=char_info,
        )
        check_result = chat_json([{"role": "user", "content": check_prompt}], label="互动判断")
        should_interact = check_result.get("interact", False)
        reason = check_result.get("reason", "")

        if tl:
            tl.log_custom("主动互动判断", f"{npc}→{target} @ {location} | 互动: {should_interact} | 理由: {reason}")

        if not should_interact:
            log("info", f"主动互动 [{npc}→{target} @ {location}]: 不互动 - {reason}")
            return False
        log("info", f"主动互动 [{npc}→{target} @ {location}]: 互动 - {reason}")
    except Exception as e:
        log("warning", f"主动互动判断失败 [{npc}→{target}]: {e}")
        return False

    # 生成场景
    try:
        npcs = [npc, target]
        # 获取主动方的子地点
        npc_sub_location = ""
        for a in chains.get(npc, []):
            if a.get("start", "") <= act_start and a.get("end", "") > act_start:
                npc_sub_location = a.get("sub_location", "")
                break

        chars_detail_parts = []
        remaining_parts = []
        for name in npcs:
            _sec = parse_character_file(load_character(name))
            npc_activity = activity if name == npc else target_activity
            _sub_loc = npc_sub_location if name == npc else target_sub_location
            _char_state = state.get("characters", {}).get(name, {})
            _emotion = _char_state.get("emotion", "")
            detail = f"--- {name} ---\n"
            detail += f"当前活动：{npc_activity}\n"
            if _sub_loc:
                detail += f"当前子地点：{_sub_loc}\n"
            if _emotion:
                detail += f"当前情绪：{_emotion}\n"
            detail += f"公开设定：\n{_sec['public_base']}\n"
            detail += f"私密设定：\n{_sec['private_base']}\n"
            if _sec['public_dynamic'].strip():
                detail += f"公开动态（外在状态）：\n{_sec['public_dynamic']}\n"
            if _sec['private_dynamic'].strip():
                detail += f"私密动态（内心记忆）：\n{_sec['private_dynamic']}\n"
            chars_detail_parts.append(detail)

            # 构建后续计划
            remaining = [
                a for a in chains.get(name, [])
                if a.get("start", "") >= act_end
            ]
            if remaining:
                sched = "\n".join(
                    f"  {a.get('start','')[11:16]}-{a.get('end','')[11:16]} {a.get('location','')} {a.get('activity','')}"
                    for a in remaining
                )
                remaining_parts.append(f"{name}:\n{sched}")
            else:
                remaining_parts.append(f"{name}: 无后续计划")

        scene_template = read_file(PROMPTS_DIR / "offscreen_scene.md")
        scene_prompt = scene_template.format(
            old_time=act_start, new_time=act_end, minutes=minutes,
            location=location, location_desc=loc_desc,
            characters_detail=chr(10).join(chars_detail_parts),
            remaining_schedules=chr(10).join(remaining_parts),
            available_locations=_get_distances(location, characters=npcs),
        )

        scene_result = chat_json([{"role": "user", "content": scene_prompt}], config_key="primary_story", label="离屏场景")
        scene = scene_result.get("scene", "")
        summary = scene_result.get("summary", "")
        impact = scene_result.get("activity_impact", {})

        # 处理群组移动
        group_move = scene_result.get("group_movement", {})
        override_map, extra_impacts = _validate_group_movement(group_move, chains, player)
        if extra_impacts:
            if not isinstance(impact, dict):
                impact = {}
            for name, reason in extra_impacts.items():
                if name not in impact:
                    impact[name] = reason
            if tl:
                tl.log_custom("离屏群组移动",
                    f"{group_move.get('leader', '')} 带 {', '.join(group_move.get('companions', []))} → {group_move.get('destination', '')}")

        # 处理预定事件（只调用一次，避免重复创建）
        scene_events_2 = scene_result.get("events", [])
        if scene_events_2:
            from .events import process_event_operations as _peo2
            _peo2(scene_events_2, npc, ov_start)
            if tl:
                tl.log_scheduled_event(scene_events_2, source=f"主动互动/{npc}→{target}")

        log("info", f"主动互动场景 [{npc}→{target} @ {location}]: {summary}")

        if tl:
            tl.log_offscreen_scene(
                f"主动: {npc}→{target}", f"{act_start[11:16]}~{act_end[11:16]}",
                scene_prompt, scene_result, scene, summary, [], impact or {}
            )

        # 兜底：场景为空则跳过记录
        if not scene.strip():
            log("warning", f"主动互动场景内容为空 [{npc}→{target} @ {location}]，跳过事件记录")
            return False

        # 记录事件
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        event_id = _next_event_id()
        write_json(EVENTS_DIR / f"{event_id}.json", {
            "id": event_id,
            "time": act_start, "end_time": act_end,
            "location": location, "characters": sorted(npcs),
            "summary": summary, "scene": scene,
            "type": "npc_initiated",
        })

        # 记忆分析（通过 post_conversation 专业 prompt）
        if scene:
            from .memory_pipeline import run_scene_memory_analysis
            run_scene_memory_analysis([npc, target], scene, player=player)

        # 事件影响：为受影响的角色重新生成后续活动链
        _apply_event_impact(
            impact, summary, act_end, new_time, chains,
            location_resolver=lambda name: override_map.get(name, location),
        )

        return True

    except Exception as e:
        log("warning", f"主动互动场景生成失败 [{npc}→{target}]: {e}")
        return False


def simulate_time_period(old_time: str, new_time: str, minutes: int):
    """模拟一段较长时间内的世界变化。

    1. 为每个 NPC 独立生成活动链（并行）
    2. 处理特殊活动（interact/seek/phone_call）
    3. 算法检测时间+地点重叠
    4. 并行互动判断 + 顺序场景生成（带 busy_intervals + 位置验证）
    5. 处理 activity_impact，为受影响角色重新生成活动链
    6. 更新 state.json
    """
    import concurrent.futures
    from .location import get_location_manager
    from .utils import get_turn_logger

    state = load_state()
    player = get_player_character()
    tl = get_turn_logger()

    npc_names = [n for n in state.get("characters", {}) if n != player]
    if not npc_names:
        return

    # ── Step 1: 并行生成活动链 ──
    chains: dict[str, list[dict]] = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(generate_activity_chain, npc, old_time, new_time): npc
            for npc in npc_names
        }
        for future in concurrent.futures.as_completed(futures):
            npc = futures[future]
            try:
                chains[npc] = future.result()
            except Exception as e:
                log("warning", f"活动链失败 [{npc}]: {e}")
                cs = state.get("characters", {}).get(npc, {})
                chains[npc] = [{
                    "start": old_time, "end": new_time,
                    "location": cs.get("location", "未知"),
                    "activity": cs.get("activity", ""),
                }]

    # ── Step 1.5: 处理主动活动（电话/互动/找人） ──
    # seen_pairs 在这里初始化，Step 1.5 和 Step 3 共享，避免同一对角色重复处理
    seen_pairs: set[tuple[str, tuple[str, ...]]] = set()
    # busy_intervals 跟踪每个角色的已占用时间段，防止同一角色同时出现在多个场景
    from collections import defaultdict
    busy_intervals: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def _is_any_busy(chars: list[str], start: str, end: str) -> tuple[bool, str]:
        """检查任一角色在指定时间段内是否已忙碌。"""
        for char in chars:
            for bs, be in busy_intervals.get(char, []):
                if start < be and end > bs:  # 时间有交集
                    return True, char
        return False, ""

    def _mark_busy(chars: list[str], start: str, end: str):
        """标记角色在指定时间段内忙碌。"""
        for char in chars:
            busy_intervals[char].append((start, end))

    special_activities = []
    for npc, acts in chains.items():
        for act in acts:
            act_type = act.get("type", "")
            if act_type in ("phone_call", "interact", "seek"):
                special_activities.append({"npc": npc, **act})

    # 按类型分组处理
    phone_calls = [a for a in special_activities if a.get("type") == "phone_call"]
    interactions = [a for a in special_activities if a.get("type") == "interact"]
    seeks = [a for a in special_activities if a.get("type") == "seek"]

    if special_activities:
        log("info", f"检测到特殊活动: {len(phone_calls)} 电话, {len(interactions)} 互动, {len(seeks)} 找人")

    def _target_at_location(target, location, act_start):
        """检查目标角色在指定时间是否在指定地点。"""
        if target in chains:
            # NPC：从活动链交叉检查
            for t_act in chains[target]:
                if (t_act.get("location") == location and
                    t_act.get("start", "") <= act_start and
                    t_act.get("end", "") > act_start):
                    return True
            return False
        else:
            # 玩家或无链角色：从 state 读当前位置
            return state.get("characters", {}).get(target, {}).get("location") == location

    # --- 处理 interact（主动互动，同地点）---
    for act in interactions:
        npc = act["npc"]
        target = act.get("interact_target", "")
        act_start = act.get("start", "")
        act_end = act.get("end", "")
        location = act.get("location", "")

        if not target or target not in state.get("characters", {}):
            log("warning", f"互动目标无效: {npc} → {target}")
            continue

        # 位置验证：检查 NPC 自身是否仍在预期地点（链可能被 activity_impact 修改）
        npc_still_there = any(
            a.get("start", "") <= act_start and a.get("end", "") > act_start and a.get("location", "") == location
            for a in chains.get(npc, [])
        )
        if not npc_still_there:
            log("info", f"主动互动 [{npc}→{target}]: {npc} 已不在 {location}（链已变更），跳过")
            continue

        if not _target_at_location(target, location, act_start):
            log("info", f"主动互动 [{npc}→{target}]: 目标不在 {location}，跳过")
            continue

        # 去重：同一地点同一对角色只处理一次
        pair_key = (location, tuple(sorted([npc, target])))
        if pair_key in seen_pairs:
            log("info", f"主动互动 [{npc}→{target} @ {location}]: 已处理过，跳过")
            continue
        seen_pairs.add(pair_key)

        # 忙碌检查：任一角色在该时间段已有场景则跳过
        busy, who = _is_any_busy([npc, target], act_start, act_end)
        if busy:
            log("info", f"主动互动 [{npc}→{target} @ {location}]: {who} 在该时段已忙，跳过")
            continue

        success = _process_npc_interaction(
            npc, target, location, act_start, act_end,
            act.get("activity", ""), chains, state, tl, player,
            new_time=new_time,
        )
        if success:
            _mark_busy([npc, target], act_start, act_end)

    # --- 处理 seek（去找人，跨地点）---
    for act in seeks:
        npc = act["npc"]
        target = act.get("seek_target", "")
        act_start = act.get("start", "")
        act_end = act.get("end", "")
        location = act.get("location", "")

        if not target or target not in state.get("characters", {}):
            log("warning", f"找人目标无效: {npc} → {target}")
            continue

        # 位置验证：检查 NPC 自身是否仍在预期地点
        npc_still_there = any(
            a.get("start", "") <= act_start and a.get("end", "") > act_start and a.get("location", "") == location
            for a in chains.get(npc, [])
        )
        if not npc_still_there:
            log("info", f"找人 [{npc}→{target}]: {npc} 已不在 {location}（链已变更），跳过")
            continue

        if not _target_at_location(target, location, act_start):
            log("info", f"找人 [{npc}→{target} @ {location}]: 目标不在，扑空")
            continue

        log("info", f"找人 [{npc}→{target} @ {location}]: 目标在场")

        # 去重：同一地点同一对角色只处理一次
        pair_key = (location, tuple(sorted([npc, target])))
        if pair_key in seen_pairs:
            log("info", f"找人 [{npc}→{target} @ {location}]: 已处理过，跳过")
            continue
        seen_pairs.add(pair_key)

        # 忙碌检查
        busy, who = _is_any_busy([npc, target], act_start, act_end)
        if busy:
            log("info", f"找人 [{npc}→{target} @ {location}]: {who} 在该时段已忙，跳过")
            continue

        success = _process_npc_interaction(
            npc, target, location, act_start, act_end,
            act.get("activity", ""), chains, state, tl, player,
            new_time=new_time,
        )
        if success:
            _mark_busy([npc, target], act_start, act_end)

    # --- 处理 phone_call（使用公共函数） ---
    phone_calls_processed = []
    for act in phone_calls:
        if act.get("call_target"):
            phone_calls_processed.append({
                "caller": act["npc"],
                "target": act["call_target"],
                "start": act.get("start", ""),
                "end": act.get("end", ""),
                "activity": act.get("activity", ""),
                "location": act.get("location", ""),
            })

    if phone_calls_processed:
        log("info", f"处理 {len(phone_calls_processed)} 个电话活动")

    for call in phone_calls_processed:
        caller = call["caller"]
        target = call["target"]
        call_start = call["start"]
        call_end = call["end"]

        if target not in state.get("characters", {}):
            log("warning", f"电话目标不存在: {caller} → {target}")
            continue

        pair_key = ("电话", tuple(sorted([caller, target])))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        busy, who = _is_any_busy([caller, target], call_start, call_end)
        if busy:
            log("info", f"电话 [{caller}→{target}]: {who} 在该时段已忙，跳过")
            continue

        success = _process_phone_call(
            caller, target, call_start, call_end,
            call["activity"], call["location"],
            chains, state, tl, player, new_time,
        )
        if success:
            _mark_busy([caller, target], call_start, call_end)

    # ── Step 2: 检测重叠 ──
    overlaps = _find_chain_overlaps(chains)
    if overlaps:
        log("info", f"发现 {len(overlaps)} 个时间+地点重叠")

    # ── Step 3: 为每个重叠生成场景（并行互动判断 + 顺序场景生成） ──
    loc_mgr = get_location_manager()

    # seen_pairs 已在 Step 1.5 初始化，这里共享使用

    # ── 3a: 过滤 seen_pairs + busy_intervals，收集待检查的重叠 ──
    candidates = []
    for ov in overlaps:
        npcs = ov["npcs"]
        location = ov["location"]
        pair_key = (location, tuple(sorted(npcs)))
        if pair_key in seen_pairs:
            log("info", f"跳过重复离屏场景 [{location}] {npcs} ({ov['start'][11:16]}~{ov['end'][11:16]})")
            continue
        seen_pairs.add(pair_key)

        # 忙碌过滤：任一角色在该时段已忙（来自 Step 1.5），跳过
        busy, who = _is_any_busy(npcs, ov["start"], ov["end"])
        if busy:
            log("info", f"跳过离屏场景 [{location}] {npcs}: {who} 在该时段已忙（Step 1.5）")
            continue

        # 有预定事件的组合跳过互动判断
        ov["has_scheduled"] = _has_scheduled_event(npcs, location, ov["start"], ov["end"])
        candidates.append(ov)

    # ── 3b: 并行互动判断（纯判断，无副作用） ──
    def _check_overlap_interaction(ov: dict) -> dict:
        """对单个重叠执行互动判断，返回结果。"""
        npcs = ov["npcs"]
        location = ov["location"]
        ov_start = ov["start"]
        ov_end = ov["end"]

        try:
            dt_s = datetime.fromisoformat(ov_start)
            dt_e = datetime.fromisoformat(ov_end)
            ov_minutes = max(1, int((dt_e - dt_s).total_seconds() / 60))
        except Exception:
            ov_minutes = 30

        char_info_parts = []
        for name in npcs:
            _sec = parse_character_file(load_character(name))
            npc_activity = ""
            for act in chains.get(name, []):
                if act.get("start", "") <= ov_start and act.get("end", "") > ov_start:
                    npc_activity = act.get("activity", "")
                    break
            char_info_parts.append(
                f"- {name}: {npc_activity or '无特定活动'}\n"
                f"  设定摘要: {_sec['public_base'][:100]}"
            )

        loc = loc_mgr.get(location)
        loc_desc = loc.description if loc else ""

        try:
            check_template = read_file(PROMPTS_DIR / "offscreen_events.md")
            check_prompt = check_template.format(
                old_time=ov_start, new_time=ov_end, minutes=ov_minutes,
                location=location, location_desc=loc_desc,
                char_info="\n".join(char_info_parts),
            )
            check_result = chat_json([{"role": "user", "content": check_prompt}], label="互动判断")
            should_interact = check_result.get("interact", False)
            reason = check_result.get("reason", "")

            if tl:
                tl.log_custom("互动判断", f"地点: {location} | 角色: {', '.join(npcs)} | "
                              f"互动: {should_interact} | 理由: {reason}")

            return {**ov, "should_interact": should_interact, "reason": reason, "minutes": ov_minutes}
        except Exception as e:
            log("warning", f"互动判断失败 [{location}] {npcs}: {e}，默认生成场景")
            return {**ov, "should_interact": True, "reason": "判断失败，默认互动", "minutes": ov_minutes}

    check_results = []
    if candidates:
        log("info", f"并行执行 {len(candidates)} 个互动判断")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(_check_overlap_interaction, ov): ov for ov in candidates}
            for future in concurrent.futures.as_completed(futures):
                try:
                    check_results.append(future.result())
                except Exception as e:
                    ov = futures[future]
                    log("warning", f"互动判断异常 [{ov.get('location', '')}]: {e}")

        # 按开始时间排序，保证顺序处理时优先处理早的重叠
        check_results.sort(key=lambda x: x["start"])

    # ── 3c: 顺序冲突解决 + 场景生成（使用公共函数） ──
    for ov in check_results:
        npcs = ov["npcs"]
        location = ov["location"]
        ov_start = ov["start"]
        ov_end = ov["end"]
        ov_minutes = ov["minutes"]

        if not ov["should_interact"]:
            log("info", f"离屏判断 [{location}] {npcs}: 不互动 - {ov['reason']}")
            continue

        log("info", f"离屏判断 [{location}] {npcs}: 互动 - {ov['reason']}")

        busy, who = _is_any_busy(npcs, ov_start, ov_end)
        if busy:
            log("info", f"跳过离屏场景 [{location}] {npcs}: {who} 在该时段已忙")
            continue

        success = _generate_overlap_scene(
            npcs, location, ov_start, ov_end, ov_minutes,
            chains, state, tl, player, new_time,
        )
        if success:
            _mark_busy(npcs, ov_start, ov_end)

    # ── Step 3.5: 清理过期预定事件（活动链还在内存中，可以正确判断到场） ──
    try:
        from .events import cleanup_expired
        missed = cleanup_expired(new_time, chains=chains)
        if missed:
            log("info", f"预定事件过期: {len(missed)} 个")
    except Exception as e:
        log("warning", f"预定事件清理失败: {e}")

    # ── Step 4: 更新 state.json ──
    from .location import discover_location, discover_sub_location
    state = load_state()
    discoveries = []  # 收集需要发现的 (角色, 地点) 对
    sub_discoveries = []  # 收集需要发现的 (角色, 地点, 子地点) 三元组
    for npc_name, acts in chains.items():
        if not acts:
            continue
        last = acts[-1]
        cs = state.setdefault("characters", {}).setdefault(npc_name, {})
        cs["location"] = last.get("location", cs.get("location", "未知"))
        cs["sub_location"] = last.get("sub_location", "")
        cs["activity"] = last.get("activity", "")
        cs["activity_start"] = last.get("start", old_time)
        # 保留 busy_until：取原值和链结束时间中更晚的
        chain_end = last.get("end", new_time)
        old_until = cs.get("until", "")
        cs["until"] = max(chain_end, old_until) if old_until else chain_end
        # 大跳转完成后不存活动链，只保留当前活动
        cs.pop("activity_chain", None)
        # 收集活动链中经过的所有地点和子地点
        for act in acts:
            loc = act.get("location", "")
            if loc:
                discoveries.append((npc_name, loc))
                sub_loc = act.get("sub_location", "")
                if sub_loc:
                    sub_discoveries.append((npc_name, loc, sub_loc))
    save_state(state)
    # 统一调用 discover（在 save_state 之后，避免数据竞争）
    for npc_name, loc in discoveries:
        discover_location(npc_name, loc)
    for npc_name, loc, sub_loc in sub_discoveries:
        discover_sub_location(npc_name, loc, sub_loc)
    log("info", f"世界模拟完成：{len(npc_names)} 个 NPC，{len(overlaps)} 个重叠")

    # 记录总结
    if tl:
        tl.log_world_sim_summary(len(npc_names), len(overlaps), minutes, old_time, new_time)

