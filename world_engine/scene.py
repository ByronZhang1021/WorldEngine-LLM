"""场景管理 — 多角色调度、消息路由、用户移动、电话。

阶段三：多角色轮流回复、@定向对话、/call 电话。
"""
import asyncio
import concurrent.futures
import re
import threading
import time as _time
from typing import Optional

from .utils import log, load_state, save_state, read_file, LOCATIONS_PATH, PROMPTS_DIR, start_turn_logger, get_turn_logger, finish_turn_logger
from .session import Session, get_session_manager
from .character import generate_reply
from .llm import chat_json
from .location import get_location_manager
from .memory_pipeline import trigger_memory_pipeline
from .world import (
    auto_advance_time,
    move_user,
    advance_time,
    check_activity_expiry,
    get_player_character,
)

# ── Turn 门控 ─────────────────────────────────────────────
# 确保上一轮的后台任务（记忆管道等）完成后才处理新消息，
# 避免 state 不一致（情绪、记忆、活动被并发读写）。
_turn_gate = threading.Event()
_turn_gate.set()  # 初始打开
_TURN_GATE_TIMEOUT = 120  # 最长等待秒数，防止死锁

# 电话挂断/拒接信号词
_HANGUP_SIGNALS = [
    "挂断", "挂了", "先挂了", "挂掉", "电话挂", "不说了", "先这样",
    "嘟嘟嘟", "挂断了电话", "挂掉了电话", "结束通话",
    "拒接", "拒绝接听", "按了拒接", "没有接听", "不接",
    "拉黑", "未接通", "无人接听", "关机",
]


def _process_npc_departures(
    npc_departures: list[dict],
    location: str,
    session,
    results: list[tuple[str, str]],
):
    """处理 NPC 自主离场（共用于普通对话和电话场景）。

    Args:
        npc_departures: auto_advance_time 返回的 npc_departures 列表。
        location: 玩家当前所在的大地点名称。
        session: 当前 Session（可为 None）。
        results: 结果消息列表，离场通知会 append 到这里。
    """
    if not npc_departures:
        return

    from .location import (
        discover_location as _disc_loc,
        get_default_sub_location as _dep_sub,
        discover_sub_location as _disc_sub,
    )
    from .world import generate_activity as _gen_act

    from datetime import datetime

    state = load_state()
    current_time = state.get("current_time", "")
    departed_npcs = set()

    # 获取当前时间的短格式（供 session 消息使用）
    try:
        _dt = datetime.fromisoformat(current_time)
        time_short = _dt.strftime("%H:%M")
    except Exception:
        time_short = ""

    for dep in npc_departures:
        dep_name = dep["name"]
        dep_dest = dep.get("destination")
        dep_companions = dep.get("companions", [])
        dep_reason = dep.get("reason", "")
        dep_busy_until = dep.get("busy_until")

        # 防护：如果离场目的地就是当前位置，跳过（LLM 误判）
        if dep_dest and dep_dest == location:
            log("info", f"NPC 离场忽略: {dep_name} 的目的地 '{dep_dest}' 就是当前位置，跳过")
            continue

        all_departing = [dep_name] + [c for c in dep_companions if c != dep_name]

        for npc in all_departing:
            npc_state = state.get("characters", {}).get(npc)
            if npc_state is None:
                continue

            if dep_dest:
                # 有明确目的地：直接移动
                sub = _dep_sub(dep_dest)
                npc_state["location"] = dep_dest
                npc_state["sub_location"] = sub
                _disc_loc(npc, dep_dest)
                if sub:
                    _disc_sub(npc, dep_dest, sub)
                log("info", f"NPC 离场执行: {npc} → {dep_dest}/{sub}")
            else:
                log("info", f"NPC 离场执行: {npc} → 由活动链决定")

            # 设置活动和忙到时间
            if dep_busy_until:
                # 有推断的忙到时间：设置 until，避免活动链提前重新生成
                npc_state["activity"] = dep_reason or ""
                npc_state["until"] = dep_busy_until
                log("info", f"NPC 离场: {npc} busy_until={dep_busy_until} ({dep_reason})")
            else:
                # 无法推断：清空，让 generate_activity 决定
                npc_state["activity"] = ""
                npc_state.pop("until", None)

            departed_npcs.add(npc)

            # 从 session 移除
            if session and npc in session.participants:
                session.remove_participant(npc)
                session.add_message("system", f"{npc} 离开了", time_short)
                log("info", f"NPC {npc} 从 session {session.id} 移除")

        # 用户通知
        if dep_companions:
            all_names = "、".join(all_departing)
            results.append(("system", f"📤 {all_names} 一起离开了。（{dep_reason}）"))
        else:
            results.append(("system", f"📤 {dep_name} 离开了。（{dep_reason}）"))

        tl = get_turn_logger()
        if tl:
            tl.log_custom("NPC 离场", f"{'、'.join(all_departing)} 离开了 {location}。原因: {dep_reason}。目的地: {dep_dest or '未知'}")

    save_state(state)

    # 为无明确目的地的离场 NPC 生成新活动（确定去哪）
    for npc in departed_npcs:
        npc_st = state.get("characters", {}).get(npc, {})
        if not npc_st.get("activity"):
            try:
                _gen_act(npc)
            except Exception as e:
                log("warning", f"NPC 离场后活动生成失败 [{npc}]: {e}")

    # 检查 session 是否只剩玩家一人
    if session and len(session.participants) < 2:
        session_mgr = get_session_manager()
        session_mgr.close(session.id)


def get_characters_at(location: str) -> list[str]:
    """获取指定地点的所有角色（不含用户）。"""
    state = load_state()
    chars = state.get("characters", {})
    return [
        name for name, cs in chars.items()
        if cs.get("location") == location and name != get_player_character()
    ]


def get_user_location() -> str:
    """获取用户当前位置。"""
    state = load_state()
    return state.get("characters", {}).get(get_player_character(), {}).get("location", "未知")


def parse_at_mention(text: str) -> Optional[str]:
    """解析 @角色名，返回角色名或 None。"""
    m = re.match(r"^@(\S+)\s*", text)
    if m:
        return m.group(1)
    return None


def strip_at_mention(text: str) -> str:
    """去掉消息开头的 @角色名。"""
    return re.sub(r"^@\S+\s*", "", text)


# 交通方式速度倍率（相对于步行）
_TRAVEL_SPEED = {
    "步行": 1.0,
    "骑车": 3.0,
    "公交": 5.0,
    "开车": 6.0,
    "打车": 6.0,
}

def _calc_travel_note(from_loc: str, to_loc: str, travel_mode: str = "") -> str:
    """计算旅行时间并返回显示文本，如"（步行约15分钟）"。"""
    try:
        from .location import get_location_manager
        lm = get_location_manager()
        loc_from = lm.get(from_loc)
        loc_to = lm.get(to_loc)
        if not loc_from or not loc_to:
            return ""
        mode = travel_mode or "步行"
        speed = _TRAVEL_SPEED.get(mode, 1.0)
        travel_min = loc_from.travel_minutes_to(loc_to, speed)
        return f"（{mode}约{travel_min}分钟）"
    except Exception:
        return ""



def _get_all_character_names() -> list[str]:
    """获取所有角色名（不含用户）。"""
    state = load_state()
    return [n for n in state.get("characters", {}) if n != get_player_character()]




# ── 多角色回复调度 ──────────────────────────────────────────


def decide_responders(user_text: str, npcs: list[str], session: Session) -> list[str]:
    """让 LLM 判断哪些角色需要回复，以及回复顺序。"""
    if len(npcs) <= 1:
        return npcs

    recent = session.messages[-5:] if session.messages else []
    context = "\n".join(f"  {m['speaker']}: {m['text'][:80]}" for m in recent)

    template = read_file(PROMPTS_DIR / "multi_responder.md")
    prompt = template.format(
        npcs=', '.join(npcs),
        player_character=get_player_character(),
        user_text=user_text,
        context=context,
    )

    try:
        result = chat_json([{"role": "user", "content": prompt}], label="多角色调度")
        responders = result.get("responders", npcs)
        valid = [r for r in responders if r in npcs]

        # 记录到 TurnLogger
        tl = get_turn_logger()
        if tl:
            tl.log_responder_decision(prompt, result, valid or npcs[:1])

        if valid:
            return valid
    except Exception as e:
        log("warning", f"回复调度失败: {e}")

    return npcs[:1]


# ── 对话前处理 ─────────────────────────────────────────────


def pre_conversation(
    user_text: str,
    location: str,
    sub_location: str,
    npcs: list[str],
    session: Session | None = None,
    phone_target: str = "",
) -> dict:
    """DM 系统 — 行为裁定 + 环境旁白 + 场景记忆。

    返回 dict:
        narration: str    — 环境旁白文本（空串=不需要）
        adjudication: str — 行为裁定结果（空串=不需要裁定）
        dm_context: str   — 更新后的 DM 便签
    """
    from datetime import datetime

    result = {"narration": "", "adjudication": "", "dm_context": ""}

    state = load_state()
    current_time = state.get("current_time", "")
    player = get_player_character()
    try:
        dt = datetime.fromisoformat(current_time)
        day_of_week = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][dt.weekday()]
    except Exception:
        day_of_week = ''

    loc_mgr = get_location_manager()
    loc = loc_mgr.get(location)
    loc_desc = loc.description if loc else ""

    # 子地点描述
    sub_loc_desc = ""
    if loc and sub_location and loc.sub_locations:
        for sl in loc.sub_locations:
            if sl.name == sub_location:
                sub_loc_desc = sl.description
                break

    # 玩家角色完整设定
    from .utils import load_character, parse_character_file
    chars = state.get("characters", {})
    player_cs = chars.get(player, {})

    try:
        p_data = load_character(player)
        p_sec = parse_character_file(p_data)
    except Exception:
        p_sec = {}

    player_lines = []
    if p_sec.get("public_base"):
        player_lines.append(f"公开设定：{p_sec['public_base']}")
    if p_sec.get("private_base"):
        player_lines.append(f"私密设定：{p_sec['private_base']}")
    if p_sec.get("public_dynamic"):
        player_lines.append(f"公开动态：{p_sec['public_dynamic']}")
    if p_sec.get("private_dynamic"):
        player_lines.append(f"私密动态：{p_sec['private_dynamic']}")
    if player_cs.get("activity"):
        player_lines.append(f"当前活动：{player_cs['activity']}")
    if player_cs.get("emotion"):
        player_lines.append(f"当前情绪：{player_cs['emotion']}")
    player_all = "\n".join(player_lines) if player_lines else "无"

    # 在场 NPC 完整设定
    npc_parts = []
    if npcs:
        for npc_name in npcs:
            try:
                n_data = load_character(npc_name)
                n_sec = parse_character_file(n_data)
            except Exception:
                n_sec = {}
            npc_cs = chars.get(npc_name, {})
            lines = [f"--- {npc_name} ---"]
            if n_sec.get("public_base"):
                lines.append(f"公开设定：{n_sec['public_base']}")
            if n_sec.get("private_base"):
                lines.append(f"私密设定：{n_sec['private_base']}")
            if n_sec.get("public_dynamic"):
                lines.append(f"公开动态：{n_sec['public_dynamic']}")
            if n_sec.get("private_dynamic"):
                lines.append(f"私密动态：{n_sec['private_dynamic']}")
            if npc_cs.get("activity"):
                lines.append(f"当前活动：{npc_cs['activity']}")
            if npc_cs.get("emotion"):
                lines.append(f"当前情绪：{npc_cs['emotion']}")
            npc_parts.append("\n".join(lines))
    npc_details = "\n\n".join(npc_parts) if npc_parts else "无"

    # 读取 DM 便签
    dm_ctx = state.get("dm_context", {}).get(player, "")

    # 读取当前地点的临时角色
    from .utils import load_temp_characters
    all_temp = load_temp_characters()
    local_temp = {name: info for name, info in all_temp.items()
                  if info.get("location") == location}
    if local_temp:
        tc_lines = []
        for name, info in local_temp.items():
            desc = info.get("description", "")
            tc_state = info.get("state", "")
            sub = info.get("sub_location", "")
            line = f"- {name}"
            if sub:
                line += f"（在 {sub}）"
            if desc:
                line += f"：{desc}"
            if tc_state:
                line += f"。当前状态：{tc_state}"
            tc_lines.append(line)
        temp_characters_text = "\n".join(tc_lines)
    else:
        temp_characters_text = "无"

    # 最近对话上下文
    context = ""
    if session and session.messages:
        recent = session.messages[-6:]
        context = "\n".join(f"  {m['speaker']}: {m['text'][:80]}" for m in recent)

    # 电话通话上下文
    phone_context = ""
    if phone_target:
        try:
            pt_data = load_character(phone_target)
            pt_sec = parse_character_file(pt_data)
        except Exception:
            pt_sec = {}
        pt_cs = chars.get(phone_target, {})
        pt_lines = [f"--- {phone_target}（电话对方） ---"]
        if pt_sec.get("public_base"):
            pt_lines.append(f"公开设定：{pt_sec['public_base']}")
        if pt_sec.get("private_base"):
            pt_lines.append(f"私密设定：{pt_sec['private_base']}")
        if pt_sec.get("public_dynamic"):
            pt_lines.append(f"公开动态：{pt_sec['public_dynamic']}")
        if pt_sec.get("private_dynamic"):
            pt_lines.append(f"私密动态：{pt_sec['private_dynamic']}")
        if pt_cs.get("activity"):
            pt_lines.append(f"当前活动：{pt_cs['activity']}")
        if pt_cs.get("emotion"):
            pt_lines.append(f"当前情绪：{pt_cs['emotion']}")
        phone_context = f"⚠️ 玩家正在与 {phone_target} 通电话。玩家的言行（包括括号中的行为委托）默认指向电话对方 {phone_target}，除非明确指向物理空间中的角色。\n\n电话对方信息：\n" + "\n".join(pt_lines)

    template = read_file(PROMPTS_DIR / "pre_conversation.md")

    # 世界设定（DM 视角，含 secret）
    from .utils import format_lore_for_prompt
    world_lore = format_lore_for_prompt(include_secrets=True)
    if world_lore:
        world_lore = f"**世界设定：**\n{world_lore}"

    prompt = template.format(
        current_time=current_time,
        day_of_week=day_of_week,
        location=location,
        location_desc=loc_desc or "无描述",
        sub_location=sub_location or "无",
        sub_location_desc=sub_loc_desc or "无描述",
        world_lore=world_lore or "",
        player_character=player,
        player_all_settings=player_all,
        npc_details=npc_details,
        temp_characters=temp_characters_text,
        phone_context=phone_context or "无（不在通话中）",
        user_text=user_text,
        context=context or "（无历史对话）",
        dm_context=dm_ctx or "（无）",
    )

    try:
        llm_result = chat_json(
            [{"role": "user", "content": prompt}],
            config_key="secondary_story",
            label="DM",
        )
        narration = llm_result.get("narration", "")
        adjudication = llm_result.get("adjudication", "")
        new_dm_context = llm_result.get("dm_context", "")

        # 清除 LLM 可能自带的标签前缀（代码后续会统一添加）
        import re as _re
        narration = _re.sub(r'^\[旁白\]\s*', '', narration).strip() if narration else ""
        adjudication = _re.sub(r'^\[DM\]\s*', '', adjudication).strip() if adjudication else ""

        tl = get_turn_logger()
        if tl:
            tl.log_dm(prompt, llm_result, adjudication, narration, new_dm_context)

        if narration:
            log("info", f"DM 旁白: {narration[:80]}")
            result["narration"] = narration.strip()
        if adjudication:
            log("info", f"DM 裁定: {adjudication[:80]}")
            result["adjudication"] = adjudication.strip()
        result["dm_context"] = new_dm_context.strip() if new_dm_context else ""

        # 处理临时角色操作
        temp_ops = llm_result.get("temp_characters", [])
        if temp_ops and isinstance(temp_ops, list):
            from .utils import apply_temp_character_ops
            apply_temp_character_ops(
                temp_ops, location, sub_location, current_time
            )

        # 传递 private_targets 给调用方
        result["private_targets"] = llm_result.get("private_targets")
    except Exception as e:
        log("warning", f"DM 处理失败: {e}")

    return result


def _save_dm_context(dm_context: str):
    """保存 DM 便签到 state.json。"""
    state = load_state()
    if "dm_context" not in state:
        state["dm_context"] = {}
    state["dm_context"][get_player_character()] = dm_context
    save_state(state)


# ── 电话系统 ─────────────────────────────────────────────


def start_phone_call(target_name: str) -> tuple[Optional[Session], str]:
    """发起电话呼叫。

    返回 (session, 消息文本)。
    session 为 None 表示呼叫失败。
    """
    all_chars = _get_all_character_names()

    if target_name not in all_chars:
        return None, f"❌ 没有叫 {target_name} 的角色。"

    state = load_state()
    vtime = state.get("current_time", "")

    # 检查是否已经在和对方通话
    session_mgr = get_session_manager()
    for s in session_mgr.find_sessions_for(get_player_character()):
        if s.session_type == "phone" and target_name in s.participants:
            return None, f"📞 你已经在和 {target_name} 通话中了。"

    # 创建电话 session
    session = session_mgr.create(
        participants=[get_player_character(), target_name],
        location="电话",
        vtime=vtime,
        session_type="phone",
    )

    # 生成对方接电话的反应
    try:
        # 记录到 TurnLogger
        tl = start_turn_logger()
        tl.log_user_input(f"📞 呼叫 {target_name}", "电话", [target_name], vtime)

        session.add_message("system", f"{get_player_character()}给{target_name}打了电话", vtime[11:16] if len(vtime) >= 16 else vtime)
        session.save()

        reply = generate_reply(target_name, session)
        session.add_message(target_name, reply, vtime[11:16] if len(vtime) >= 16 else vtime)
        session.save()

        # 检测 NPC 是否拒接/挂断电话
        rejected = any(sig in reply for sig in _HANGUP_SIGNALS)
        if rejected:
            session_mgr.close(session.id)
            log("info", f"电话被拒: {target_name} 拒接了电话")

        # 延迟完成 Turn：等记忆管道结束后再开门
        def _finish_turn():
            finish_turn_logger()
            _turn_gate.set()

        threading.Thread(
            target=lambda: (_time.sleep(10), _finish_turn()),
            daemon=True,
        ).start()

        if rejected:
            return None, f"📞 呼叫 {target_name}..."
        return session, f"📞 正在呼叫 {target_name}...\n\n{reply}"
    except Exception as e:
        finish_turn_logger()
        log("warning", f"电话呼叫失败 [{target_name}]: {e}")
        return None, f"📞 呼叫 {target_name} 失败: {e}"


def handle_phone_message(user_text: str, session: Session) -> list[tuple[str, str]]:
    """处理电话 session 中的用户消息。"""
    vtime = load_state().get("current_time", "")
    time_short = vtime[11:16] if len(vtime) >= 16 else vtime

    # 创建 TurnLogger
    tl = start_turn_logger()
    tl.log_user_input(user_text, "电话", session.participants, vtime)

    session.add_message(get_player_character(), user_text, time_short)
    session.save()

    # 面前的人能听到用户说话（但听不到电话那头）
    location = get_user_location()
    session_mgr = get_session_manager()
    for s in session_mgr.active_sessions():
        if s.id == session.id:
            continue
        if get_player_character() in s.participants and s.location == location and len(s.participants) > 1:
            s.add_message(get_player_character(), f"（正在打电话）{user_text}", time_short)
            s.save()
            break

    other = [p for p in session.participants if p != get_player_character()]
    if not other:
        finish_turn_logger()
        _turn_gate.set()  # 电话断开，开门
        return [("system", "电话已断开。")], []

    target = other[0]
    results = []
    image_future = None

    try:
        # DM 对话前处理（行为裁定 + 环境旁白）— 基于用户物理位置 + 电话对方
        state = load_state()
        sub_loc = state.get("characters", {}).get(get_player_character(), {}).get("sub_location", "")
        physical_npcs = get_characters_at(location)
        pre_result = pre_conversation(user_text, location, sub_loc, physical_npcs, session, phone_target=target)
        adjudication = pre_result.get("adjudication", "")
        narration = pre_result.get("narration", "")

        if adjudication:
            session.add_message("system", f"[DM] {adjudication}", time_short)
            session.save()
            results.append(("system", f"🎲 {adjudication}"))
        if narration:
            session.add_message("system", f"[旁白] {narration}", time_short)
            session.save()
            results.append(("system", f"📖 {narration}"))

        # 保存 DM 便签
        _save_dm_context(pre_result.get("dm_context", ""))

        reply = generate_reply(target, session)
        session.add_message(target, reply, time_short)
        session.save()
        results.append((target, reply))

        # 检测 NPC 是否挂断电话
        hung_up = any(sig in reply for sig in _HANGUP_SIGNALS)
        if hung_up:
            session_mgr = get_session_manager()
            session_mgr.close(session.id)
            results.append(("system", f"📞 {target} 挂断了电话。"))

        # 构建当前轮次对话文本
        conv_parts = [f"用户: {user_text}"]
        if adjudication:
            conv_parts.append(f"（DM裁定）{adjudication}")
        if narration:
            conv_parts.append(f"（旁白）{narration}")
        conv_parts.append(f"{target}: {reply}")
        conv_text = "\n".join(conv_parts)

        # 无论是否挂断，都需要推进时间并更新 NPC 活动链
        # 挂断时 present_npcs=[] → NPC 不被排除，活动链会正常更新
        adv = auto_advance_time(user_text, reply, present_npcs=[] if hung_up else [target], conversation=conv_text)

        # 处理 NPC 离场（电话对方表示要去某地等）
        npc_departures = adv.get("npc_departures", [])
        if npc_departures:
            _process_npc_departures(npc_departures, location, session, results)

        # 大幅跳转旁白提示（电话中不应发生，但作为防御性代码）
        from .world import _get_large_jump_minutes
        if adv["minutes"] >= _get_large_jump_minutes() and adv.get("narration"):
            results.append(("system", f"⏳ {adv['narration']}"))

        # 时间推进完成后再启动记忆管道，确保 created 时间戳正确
        image_future = trigger_memory_pipeline(target, conv_text)

        # 玩家角色记忆+情绪管道
        player_conv = conv_text  # 玩家看到完整对话
        trigger_memory_pipeline(get_player_character(), player_conv, player_mode=True)
    except Exception as e:
        log("warning", f"电话回复失败 [{target}]: {e}")
        results.append((target, f"[系统错误: {e}]"))

    # 延迟完成 Turn：等记忆管道结束后再开门
    def _finish_turn():
        if image_future:
            try:
                image_future.result(timeout=60)
            except Exception:
                pass
        finish_turn_logger()
        _turn_gate.set()

    threading.Thread(target=_finish_turn, daemon=True).start()

    return results, [image_future] if image_future else []


def end_phone_call(target_name: str) -> str:
    """挂断电话。"""
    session_mgr = get_session_manager()
    for s in session_mgr.find_sessions_for(get_player_character()):
        if s.session_type == "phone" and target_name in s.participants:
            session_mgr.close(s.id)
            return f"📞 已挂断和 {target_name} 的电话。"
    return f"你没有在和 {target_name} 通话。"


# ── 主消息处理 ─────────────────────────────────────────────


def get_active_phone_session() -> Optional[Session]:
    """获取当前活跃的电话 session（如果有）。"""
    session_mgr = get_session_manager()
    for s in session_mgr.find_sessions_for(get_player_character()):
        if s.session_type == "phone":
            return s
    return None


def _process_npc_sessions():
    """先处理 NPC-only 的 session（不含用户的活跃互动）。

    主要工作：检查 NPC session 中是否有角色已离开地点，
    如果有，移除该参与者；如果只剩一人，关闭 session。
    """
    session_mgr = get_session_manager()
    state = load_state()
    chars = state.get("characters", {})

    for session in session_mgr.active_sessions():
        if get_player_character() in session.participants:
            continue  # 跳过用户参与的 session
        if session.session_type == "phone":
            continue  # 跳过电话 session

        # 检查参与者是否还在同一地点
        location = session.location
        to_remove = []
        for p in session.participants:
            cs = chars.get(p, {})
            if cs.get("location") != location:
                to_remove.append(p)

        for p in to_remove:
            session.remove_participant(p)
            log("info", f"NPC {p} 已离开 {location}，从 session {session.id} 移除")

        if len(session.participants) < 2:
            session_mgr.close(session.id)


async def handle_user_message(user_text: str) -> tuple[list[tuple[str, str]], list]:
    """处理用户消息，返回 [(角色名, 回复文本), ...] 列表。"""
    # 0a) Turn 门控：等上一轮后台任务完成
    gate_ok = await asyncio.to_thread(_turn_gate.wait, _TURN_GATE_TIMEOUT)
    if not gate_ok:
        log("warning", "Turn 门控超时，强制开始新 Turn")
    _turn_gate.clear()  # 关门

    try:
        return await _handle_user_message_inner(user_text)
    except Exception:
        # 确保异常时门控不会卡死
        log("warning", "handle_user_message 异常，强制开门")
        try:
            finish_turn_logger()
        except Exception:
            pass
        _turn_gate.set()
        raise


async def _handle_user_message_inner(user_text: str) -> tuple[list[tuple[str, str]], list]:
    """handle_user_message 的实际逻辑，被 try/except 包裹以保护门控。"""
    # 0b) NPC session 先处理
    _process_npc_sessions()

    # 1) 挂断电话检测（精确匹配 + 模糊匹配）
    _hangup_prefix = []  # 挂断消息前缀，会附加到最终结果
    phone = get_active_phone_session()
    if phone:
        is_exact_hangup = user_text.strip() in ("/hangup", "挂断", "挂了")
        is_fuzzy_hangup = any(sig in user_text for sig in _HANGUP_SIGNALS)

        if is_exact_hangup:
            # 纯挂断指令，直接挂断并返回
            other = [p for p in phone.participants if p != get_player_character()]
            _turn_gate.set()  # 快速操作，直接开门
            return [("system", end_phone_call(other[0] if other else ""))], []

        if is_fuzzy_hangup:
            # 用户消息中包含挂断信号（如「我挂断电话，开车去…」）
            # 先关闭电话 session，然后继续处理消息的其余内容（移动等）
            other = [p for p in phone.participants if p != get_player_character()]
            _hangup_prefix.append(("system", end_phone_call(other[0] if other else "")))
            log("info", f"模糊挂断检测: 用户消息含挂断信号，电话已关闭，继续处理后续内容")
            # 不 return，fall through 到正常消息处理流程

    # 2) 如果在打电话，消息发到电话 session
    phone_session = get_active_phone_session()
    if phone_session:
        return handle_phone_message(user_text, phone_session)

    # 3) 当前位置和在场角色
    location = get_user_location()
    state = load_state()
    vtime = state.get("current_time", "")
    time_short = vtime[11:16] if len(vtime) >= 16 else vtime

    npcs = get_characters_at(location)
    # 注：用户输入日志由 TurnLogger.log_user_input 统一记录到 engine.log

    results = []
    image_futures = []

    # 4) 无人在场
    if not npcs:
        # ★ 创建 TurnLogger 并记录用户输入
        tl = start_turn_logger()
        tl.log_user_input(user_text, location, [], vtime)
        tl.log_custom("无人在场", f"位置: {location}\n无 NPC 在场")

        # 维护 session 以保留 DM 对话上下文（临时角色互动历史）
        session_mgr = get_session_manager()
        session = session_mgr.find_for_user(get_player_character(), location)
        if session is None:
            session = session_mgr.create(
                participants=[get_player_character()],
                location=location,
                vtime=vtime,
            )
        session.add_message(get_player_character(), user_text, time_short)
        session.save()

        # DM 对话前处理（行为裁定 + 环境旁白）
        sub_loc = state.get("characters", {}).get(get_player_character(), {}).get("sub_location", "")
        pre_result = await asyncio.to_thread(
            pre_conversation, user_text, location, sub_loc, [], session
        )
        adjudication = pre_result.get("adjudication", "")
        narration = pre_result.get("narration", "")

        if adjudication:
            session.add_message("system", f"[DM] {adjudication}", time_short)
            session.save()
            results.append(("system", f"🎲 {adjudication}"))
        if narration:
            session.add_message("system", f"[旁白] {narration}", time_short)
            session.save()
            results.append(("system", f"📖 {narration}"))

        # 保存 DM 便签
        _save_dm_context(pre_result.get("dm_context", ""))

        # 同步调用时间推进 + 移动判断
        dm_output = (adjudication or "") + ("\n" + narration if narration else "")
        adv = auto_advance_time(user_text, dm_output.strip() or "（无人回应）")

        # 时间推进旁白：仅在 DM 没有输出裁定/旁白时显示，避免重复叙述
        dm_already_narrated = bool(adjudication or narration)
        from .world import _get_large_jump_minutes
        if adv["minutes"] >= _get_large_jump_minutes() and adv["narration"] and not dm_already_narrated:
            results.append(("system", f"⏳ {adv['narration']}"))

        if adv["destination"]:
            # 关闭旧地点的 face-to-face session
            _sm = get_session_manager()
            old_session = _sm.find_for_user(get_player_character(), location)
            if old_session:
                _sm.close(old_session.id)

            move_user(adv["destination"], adv.get("sub_destination", ""))
            new_npcs = get_characters_at(adv["destination"])
            sub_dest = adv.get("sub_destination", "")
            loc_display = f"{adv['destination']}/{sub_dest}" if sub_dest else adv['destination']
            present_str = f"这里有：{'、'.join(new_npcs)}" if new_npcs else "这里没有其他人"
            travel_note = _calc_travel_note(location, adv["destination"], adv.get("travel_mode", ""))
            results.append(("system", f"📍 你来到了{loc_display}。{travel_note}\n{present_str}"))
            tl = get_turn_logger()
            if tl:
                tl.log_custom("用户移动", f"移动到: {loc_display}\n在场: {', '.join(new_npcs) if new_npcs else '无'}")
        else:
            # 检查同地点内子地点移动
            sub_destination = adv.get("sub_destination", "")
            _cur_sub = state.get("characters", {}).get(get_player_character(), {}).get("sub_location", "")
            if sub_destination and sub_destination != _cur_sub:
                from .location import discover_sub_location as _disc_sub_loc
                move_user(location, sub_destination)
                loc_display = f"{location}/{sub_destination}"
                results.append(("system", f"📍 你来到了{loc_display}。"))
                tl = get_turn_logger()
                if tl:
                    tl.log_custom("子地点移动", f"移动到: {loc_display}")

            # 时间推进后重新检查在场角色（大跳转可能导致 NPC 移动到当前位置）
            new_npcs_here = get_characters_at(location)
            if new_npcs_here:
                present_str = f"这里有：{'、'.join(new_npcs_here)}"
                results.append(("system", present_str))
                tl = get_turn_logger()
                if tl:
                    tl.log_custom("时间推进后在场更新", f"位置: {location}\n新到达: {', '.join(new_npcs_here)}")

        # 记录最终发送给用户的消息
        tl = get_turn_logger()
        if tl:
            final_lines = [f"[{r[0]}] {r[1][:100]}" for r in results]
            tl.log_custom("发送给用户", "\n".join(final_lines))

        # 玩家角色记忆+情绪+图片生成管道（无人在场时也需要，否则拍照等行为无法触发图片生成）
        conv_parts = [f"用户: {user_text}"]
        if adjudication:
            conv_parts.append(f"（DM裁定）{adjudication}")
        if narration:
            conv_parts.append(f"（旁白）{narration}")
        player_conv = "\n".join(conv_parts)
        # 无人在场时玩家管道是唯一管道，需 allow_image=True 以允许图片生成
        player_fut = trigger_memory_pipeline(get_player_character(), player_conv, player_mode=True, allow_image=True)

        # Turn 完成：等记忆管道完成后再开门
        _pf = player_fut

        def _finish_turn_empty():
            if _pf:
                try:
                    _pf.result(timeout=60)
                except Exception:
                    pass
            finish_turn_logger()
            _turn_gate.set()

        threading.Thread(target=_finish_turn_empty, daemon=True).start()

        return _hangup_prefix + results, [player_fut]

    # 5) 解析 @角色名
    at_target = parse_at_mention(user_text)
    clean_text = strip_at_mention(user_text) if at_target else user_text

    if at_target:
        if at_target not in npcs:
            results.append(("system", f"[{time_short}] {at_target} 不在这里。"))
            _turn_gate.set()  # 提前返回，开门
            return _hangup_prefix + results, []
        target_npcs = [at_target]
    else:
        session_mgr = get_session_manager()
        existing = session_mgr.find_for_user(get_player_character(), location)
        dummy_session = existing or Session({
            "id": "tmp", "participants": [get_player_character()] + npcs,
            "location": location, "type": "face-to-face",
            "start_time": vtime, "end_time": None, "messages": [],
        })
        # 优化3: decide_responders 期间并行预加载所有在场 NPC 的角色数据
        from .character import preload_character_data
        preload_task = asyncio.to_thread(preload_character_data, npcs)
        responder_task = asyncio.to_thread(
            decide_responders, clean_text, npcs, dummy_session
        )
        target_npcs, _ = await asyncio.gather(responder_task, preload_task)

    # 6) Session 管理
    session_mgr = get_session_manager()
    session = session_mgr.find_for_user(get_player_character(), location)

    if session is None:
        participants = [get_player_character()] + npcs
        session = session_mgr.create(participants, location, vtime)

    for npc in npcs:
        session.add_participant(npc)

    # ★ 创建 TurnLogger 并记录用户输入
    tl = start_turn_logger()
    tl.log_user_input(clean_text, location, npcs, vtime)

    session.add_message(get_player_character(), clean_text, time_short)
    session.save()

    # 6.5) DM 对话前处理（行为裁定 + 环境旁白，写入 session 供 NPC 参考）
    sub_loc = state.get("characters", {}).get(get_player_character(), {}).get("sub_location", "")
    pre_result = await asyncio.to_thread(
        pre_conversation, clean_text, location, sub_loc, npcs, session
    )
    adjudication = pre_result.get("adjudication", "")
    narration = pre_result.get("narration", "")
    private_targets = pre_result.get("private_targets")  # None = 公开, [] = 隐秘, ["x"] = 特定

    # 私密/隐秘动作：回溯标记用户消息的可见性
    if private_targets is not None and isinstance(private_targets, list):
        import re
        player = get_player_character()
        # 生成脱敏版本：去掉所有括号内容
        redacted = re.sub(r'[（(][^）)]*[）)]', '', clean_text).strip()
        # 回溯找到刚存入的用户消息并标记
        for msg in reversed(session.messages):
            if msg["speaker"] == player:
                msg["visible_to"] = [player] + private_targets
                msg["redacted_text"] = redacted
                break
        session.save()

        tl = get_turn_logger()
        if tl:
            targets_str = '、'.join(private_targets) if private_targets else '无（隐秘行为）'
            tl.log_custom("动作可见性",
                f"private_targets: {targets_str}\n"
                f"原文: {clean_text[:100]}\n"
                f"脱敏: {redacted[:100] if redacted else '（完全隐藏）'}")

    if adjudication:
        session.add_message("system", f"[DM] {adjudication}", time_short)
        session.save()
        results.append(("system", f"🎲 {adjudication}"))
    if narration:
        session.add_message("system", f"[旁白] {narration}", time_short)
        session.save()
        results.append(("system", f"📖 {narration}"))

    # 保存 DM 便签
    _save_dm_context(pre_result.get("dm_context", ""))

    # 7) 轮流生成回复
    last_reply = ""
    for npc_name in target_npcs:
        try:
            reply_text = await asyncio.to_thread(generate_reply, npc_name, session)
            session.add_message(npc_name, reply_text, time_short)
            session.save()
            results.append((npc_name, reply_text))
            last_reply = reply_text
        except Exception as e:
            log("warning", f"生成回复失败 [{npc_name}]: {e}")
            results.append((npc_name, f"[系统错误: {e}]"))

    # 8) 先时间推进，再异步记忆管道
    # 重要：时间推进必须在记忆管道之前完成，否则记忆的 created 时间戳会用旧时间
    memory_futures = []  # concurrent.futures.Future 列表，用于 Turn 门控
    if last_reply:
        # 构建当前轮次的对话文本（只含本轮，不含历史，避免旧对话被重复处理）
        current_turn_parts = [f"用户: {clean_text}"]
        for r_speaker, r_text in results:
            if r_speaker == "system" and r_text.startswith("🎲 "):
                current_turn_parts.append(f"（DM裁定）{r_text[2:]}")
            elif r_speaker == "system" and r_text.startswith("📖 "):
                current_turn_parts.append(f"（旁白）{r_text[2:]}")
            elif r_speaker != "system":
                current_turn_parts.append(f"{r_speaker}: {r_text}")
        current_conv_text = "\n".join(current_turn_parts)

        # 先运行时间推进（同步等待完成），确保 state.json 中 current_time 已更新
        adv_future = asyncio.to_thread(
            auto_advance_time,
            clean_text, last_reply,
            present_npcs=npcs,
            conversation=current_conv_text,
        )

    # 9) 等待时间推进结果 + 移动判断（含群组移动）
    if last_reply:
        # 记录时间推进前的在场 NPC
        npcs_before = set(get_characters_at(location))

        # 等待 auto_advance_time 完成
        adv = await adv_future

        # 时间推进完成后，启动异步记忆管道（此时 state.json 中 current_time 已更新）
        user_line_full = f"用户: {clean_text}"
        if private_targets is not None and isinstance(private_targets, list):
            import re
            redacted_text = re.sub(r'[（(][^）)]*[）)]', '', clean_text).strip()
            user_line_redacted = f"用户: {redacted_text}" if redacted_text else ""
        else:
            user_line_redacted = None  # None = 不需要脱敏

        # 公共部分（DM 裁定、旁白、NPC 回复）
        common_parts = []
        for npc_name, reply_text in results:
            if npc_name == "system" and reply_text.startswith("🎲 "):
                common_parts.append(f"（DM裁定）{reply_text[2:]}")
            elif npc_name == "system" and reply_text.startswith("📖 "):
                common_parts.append(f"（旁白）{reply_text[2:]}")
            elif npc_name != "system":
                common_parts.append(f"{npc_name}: {reply_text}")
        common_text = "\n".join(common_parts)

        image_futures = []
        for npc_name, _ in results:
            if npc_name != "system":
                # 根据可见性选择对话文本
                if user_line_redacted is not None and npc_name not in (private_targets or []):
                    # 此 NPC 不在 private_targets 中 → 使用脱敏版本
                    npc_conv = (user_line_redacted + "\n" + common_text).strip() if user_line_redacted else common_text
                else:
                    npc_conv = user_line_full + "\n" + common_text
                fut = trigger_memory_pipeline(npc_name, npc_conv)
                image_futures.append(fut)
                memory_futures.append(fut)

        # 玩家角色记忆+情绪管道（使用完整对话文本）
        player_conv = user_line_full + "\n" + common_text
        player_fut = trigger_memory_pipeline(get_player_character(), player_conv, player_mode=True)
        memory_futures.append(player_fut)

        # 时间推进旁白：插入到第一个 NPC 回复之前（不是追加到末尾）
        # 这样大幅时间跳转时，先显示旁白（描述时间流逝），再显示 NPC 对话
        dm_already_narrated = bool(adjudication or narration)
        from .world import _get_large_jump_minutes
        if adv["minutes"] >= _get_large_jump_minutes() and adv["narration"] and not dm_already_narrated:
            # 找到第一个非 system 消息的位置
            insert_pos = len(results)
            for i, (speaker, _) in enumerate(results):
                if speaker != "system":
                    insert_pos = i
                    break
            results.insert(insert_pos, ("system", f"⏳ {adv['narration']}"))

        # 9a) 处理 NPC 自主离场
        npc_departures = adv.get("npc_departures", [])
        if npc_departures:
            _process_npc_departures(npc_departures, location, session, results)
            # 检查 session 是否被关闭
            if session and len(session.participants) < 2:
                session = None

        destination = adv["destination"]
        sub_destination = adv.get("sub_destination", "")
        companions = adv.get("companions", [])

        # 判断当前位置信息
        _cur_state = load_state()
        _player_cs = _cur_state.get("characters", {}).get(get_player_character(), {})
        _cur_loc = _player_cs.get("location", "")
        _cur_sub = _player_cs.get("sub_location", "")

        # 是否为同地点内子地点移动（destination 等于当前地点或为空，但 sub 不同）
        is_same_loc_sub_move = (
            not destination or destination == _cur_loc
        ) and sub_destination and sub_destination != _cur_sub

        if destination and destination != _cur_loc:
            # ── 跨地点移动 ──

            # 移动玩家
            move_user(destination, sub_destination)

            # 移动同行 NPC
            from .location import discover_location, get_default_sub_location, discover_sub_location
            moved_npcs = []
            if companions:
                # 同行 NPC 也去默认子地点
                comp_sub = sub_destination or get_default_sub_location(destination)
                state = load_state()
                for comp in companions:
                    if comp == get_player_character():
                        continue  # 玩家已经通过 move_user 移动了
                    comp_state = state.get("characters", {}).get(comp)
                    if comp_state is not None:
                        comp_state["location"] = destination
                        comp_state["sub_location"] = comp_sub
                        comp_state["activity"] = ""
                        moved_npcs.append(comp)
                        log("info", f"群组移动: {comp} → {destination}/{comp_sub}")
                if moved_npcs:
                    save_state(state)
                    for comp in moved_npcs:
                        discover_location(comp, destination)
                        if comp_sub:
                            discover_sub_location(comp, destination, comp_sub)

            new_npcs = get_characters_at(destination)
            loc_display = f"{destination}/{sub_destination}" if sub_destination else destination
            present_str = f"这里有：{'、'.join(new_npcs)}" if new_npcs else "这里没有其他人"
            travel_note = _calc_travel_note(_cur_loc, destination, adv.get("travel_mode", ""))

            if moved_npcs and session:
                # 有同行 NPC → 保留 session，更新 location，写入移动信息
                old_loc = f"{_cur_loc}/{_cur_sub}" if _cur_sub else _cur_loc
                session.data["location"] = destination
                session.add_message("system",
                    f"[场景转换] 你们一起从 {old_loc} 来到了 {loc_display}。{present_str}",
                    time_short)
                session.save()
                results.append(("system", f"📍 你和{'、'.join(moved_npcs)}一起来到了{loc_display}。{travel_note}\n{present_str}"))
            else:
                # 独自移动 → 关闭旧 session
                if session:
                    session_mgr.close(session.id)
                    session = None
                results.append(("system", f"📍 你来到了{loc_display}。{travel_note}\n{present_str}"))

            tl = get_turn_logger()
            if tl:
                extra = f"\n同行: {', '.join(moved_npcs)}" if moved_npcs else ""
                tl.log_custom("用户移动", f"移动到: {loc_display}\n在场: {', '.join(new_npcs) if new_npcs else '无'}{extra}")

        elif is_same_loc_sub_move:
            # ── 同地点内子地点移动 ──
            from .location import discover_sub_location as _disc_sub_loc
            actual_loc = _cur_loc
            move_user(actual_loc, sub_destination)

            # 正在对话中的 NPC 跟随移动子地点
            moved_npcs = []
            state = load_state()
            # 同行 companions 或当前在场对话 NPC 都跟随
            follow_npcs = set(companions) if companions else set(npcs) if npcs else set()
            for comp in follow_npcs:
                if comp == get_player_character():
                    continue
                comp_state = state.get("characters", {}).get(comp)
                if comp_state is not None and comp_state.get("location") == actual_loc:
                    comp_state["sub_location"] = sub_destination
                    moved_npcs.append(comp)
                    log("info", f"子地点移动: {comp} → {actual_loc}/{sub_destination}")
            if moved_npcs:
                save_state(state)
                for comp in moved_npcs:
                    _disc_sub_loc(comp, actual_loc, sub_destination)

            loc_display = f"{actual_loc}/{sub_destination}"
            new_npcs = get_characters_at(actual_loc)
            present_str = f"这里有：{'、'.join(new_npcs)}" if new_npcs else "这里没有其他人"

            if moved_npcs and session:
                # 有同行 NPC → 保留 session，写入子地点移动信息
                old_sub_display = f"{actual_loc}/{_cur_sub}" if _cur_sub else actual_loc
                session.add_message("system",
                    f"[场景转换] 你们一起从 {old_sub_display} 来到了 {loc_display}。",
                    time_short)
                session.save()
                results.append(("system", f"📍 你和{'、'.join(moved_npcs)}一起来到了{loc_display}。\n{present_str}"))
            else:
                results.append(("system", f"📍 你来到了{loc_display}。\n{present_str}"))

            tl = get_turn_logger()
            if tl:
                extra = f"\n同行: {', '.join(moved_npcs)}" if moved_npcs else ""
                tl.log_custom("子地点移动", f"移动到: {loc_display}\n在场: {', '.join(new_npcs) if new_npcs else '无'}{extra}")
        else:
            # 未移动，检测 NPC 到达/离开当前地点
            npcs_after = set(get_characters_at(location))
            arrived = npcs_after - npcs_before
            departed = npcs_before - npcs_after
            # 排除已被 npc_departures 处理的角色（避免重复通知）
            if npc_departures:
                already_departed = set()
                for dep in npc_departures:
                    already_departed.add(dep["name"])
                    already_departed.update(dep.get("companions", []))
                departed -= already_departed

            for npc_name in departed:
                results.append(("system", f"📤 {npc_name} 离开了{location}。"))
                if session:
                    session.add_message("system", f"{npc_name} 离开了", time_short)
                tl = get_turn_logger()
                if tl:
                    tl.log_custom("NPC 离开", f"{npc_name} 离开了 {location}")

            for npc_name in arrived:
                results.append(("system", f"📥 {npc_name} 来到了{location}。"))
                tl = get_turn_logger()
                if tl:
                    tl.log_custom("NPC 到达", f"{npc_name} 来到了 {location}")
                # 加入 session 并生成主动发言
                if session:
                    session.add_participant(npc_name)
                    session.add_message("system", f"{npc_name} 来到了这里", time_short)
                    session.save()
                    try:
                        greeting = await asyncio.to_thread(generate_reply, npc_name, session)
                        session.add_message(npc_name, greeting, time_short)
                        session.save()
                        results.append((npc_name, greeting))
                        log("info", f"NPC 到达发言 [{npc_name}]: {greeting[:50]}")
                    except Exception as e:
                        log("warning", f"NPC 到达发言失败 [{npc_name}]: {e}")

    # 10) 记录最终发送给用户的消息
    tl = get_turn_logger()
    if tl:
        final_lines = [f"[{r[0]}] {r[1][:120]}" for r in results]
        tl.log_custom("发送给用户", f"共 {len(results)} 条消息:\n" + "\n".join(final_lines))

    # 11) Turn 完成：等记忆管道全部完成后再开门
    _mem_futs = memory_futures  # 避免闭包捕获可变变量

    def _finish_turn_main():
        # 等待所有记忆管道后台线程完成
        for fut in _mem_futs:
            try:
                fut.result(timeout=60)
            except Exception:
                pass
        finish_turn_logger()
        _turn_gate.set()  # 开门

    threading.Thread(target=_finish_turn_main, daemon=True).start()

    return _hangup_prefix + results, image_futures if last_reply else []
