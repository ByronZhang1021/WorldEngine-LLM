"""角色引擎 — prompt 构建、回复生成。"""
from typing import Optional

from .utils import (
    log, log_session_detail, read_file, load_state,
    CHARACTERS_DIR, RULES_PATH, LOCATIONS_PATH, PROMPTS_DIR,
    parse_character_file, load_character,
)
from .llm import chat_stream
from .location import get_location_manager
from .session import Session
from datetime import datetime


def _load_tail_reminder(character: str) -> str:
    """从文件加载 tail reminder，注入角色名。"""
    path = PROMPTS_DIR / "tail_reminder.md"
    content = read_file(path)
    if not content.strip():
        return ""
    return content.format(character=character)


def _get_day_of_week(time_str: str) -> str:
    """从时间字符串自动计算星期。"""
    try:
        dt = datetime.fromisoformat(time_str)
        return ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][dt.weekday()]
    except Exception:
        return ''


def _read_char_sections(character: str) -> dict[str, str]:
    """读取并解析角色文件的四个部分。"""
    return parse_character_file(load_character(character))


def preload_character_data(characters: list[str]):
    """预加载多个角色的文件数据（用于 decide_responders 期间并行加载）。

    将角色文件读入缓存，这样后续 build_system_prompt 中的
    _read_char_sections 可以直接使用缓存数据，减少文件 I/O 等待。
    """
    for name in characters:
        try:
            _read_char_sections(name)
        except Exception:
            pass  # 预加载失败不影响主流程


def build_system_prompt(character: str, session: Session) -> str:
    """为角色构建完整的 system prompt。

    结构：
    [System]
    ├─ 全局规则（rules.md）
    ├─ 角色文件的四个部分
    ├─ 在场角色的公开信息
    ├─ 场景信息
    └─ 当前地点描述
    """
    # 全局规则
    rules = read_file(RULES_PATH)

    # 自己的 base 设定（始终全量注入）
    sections = _read_char_sections(character)
    public_base = sections["public_base"]
    private_base = sections["private_base"]

    # 世界状态
    state = load_state()
    current_time = state.get("current_time", "未知")
    day_of_week = _get_day_of_week(current_time)

    # 角色当前状态
    char_state = state.get("characters", {}).get(character, {})
    char_location = char_state.get("location", "未知")
    char_activity = char_state.get("activity", "")

    # 在场的人
    all_chars = state.get("characters", {})
    present = [
        name for name, cs in all_chars.items()
        if cs.get("location") == char_location and name != character
    ]

    # 智能检索记忆（public_dynamic + private_dynamic 统一检索）
    from .memory_retrieval import retrieve_memories
    from .location import get_location_manager as _get_loc_mgr

    # 构建对话上下文：从 session 最近几条消息取
    recent_msgs = session.messages[-6:] if session.messages else []
    conversation_context = "\n".join(
        f"{m.get('speaker', '')}: {m.get('text', '')}" for m in recent_msgs
    )

    all_char_names = list(all_chars.keys())
    _loc_mgr = _get_loc_mgr()
    all_locations = list(_loc_mgr.all_locations().keys())

    memories = retrieve_memories(
        character=character,
        conversation_context=conversation_context,
        present_chars=present,
        current_location=char_location,
        all_char_names=all_char_names,
        all_locations=all_locations,
    )
    public_dynamic = memories["public"]
    private_dynamic = memories["private"]

    # 地点描述
    loc_mgr = get_location_manager()
    loc = loc_mgr.get(char_location)
    loc_desc = loc.description if loc else ""

    # 组装
    parts = []

    # 世界设定（NPC 视角，仅 public）
    from .utils import format_lore_for_prompt
    world_lore = format_lore_for_prompt(include_secrets=False)
    if world_lore:
        parts.append("=== 世界设定 ===")
        parts.append(world_lore)

    parts.append("\n=== 世界规则 ===")
    parts.append(rules)

    parts.append("\n=== 你的公开设定 ===")
    parts.append(public_base)

    if private_base.strip():
        parts.append("\n=== 你的私密设定（只有你自己知道） ===")
        parts.append(private_base)

    if public_dynamic.strip():
        parts.append("\n=== 你的当前外在状态（别人能看到） ===")
        parts.append(public_dynamic)

    if private_dynamic.strip():
        parts.append("\n=== 你的内心（只有你自己知道） ===")
        parts.append(private_dynamic)

    # 在场角色的公开信息
    if present:
        parts.append("\n=== 在场的人 ===")
        for name in present:
            other_sec = _read_char_sections(name)
            parts.append(f"--- {name} ---")
            if other_sec["public_base"].strip():
                parts.append(other_sec["public_base"].strip())
            if other_sec["public_dynamic"].strip():
                parts.append(f"当前状态：{other_sec['public_dynamic'].strip()}")
    else:
        parts.append("\n在场的人：没有其他人")

    parts.append("\n=== 当前场景 ===")
    parts.append(f"虚拟时间：{current_time} {day_of_week}")
    parts.append(f"你的位置：{char_location}")
    if char_activity:
        until = char_state.get("until", "")
        if until:
            until_short = until.replace("T", " ")[5:16]  # "01-01 09:00"
            # 检查活动是否已超时
            try:
                until_dt = datetime.fromisoformat(until)
                now_dt = datetime.fromisoformat(current_time)
                if now_dt > until_dt:
                    overdue_min = int((now_dt - until_dt).total_seconds() / 60)
                    parts.append(f"你原定在做：{char_activity}（原计划到 {until_short}，已超时 {overdue_min} 分钟）")
                else:
                    remaining_min = int((until_dt - now_dt).total_seconds() / 60)
                    parts.append(f"你正在：{char_activity}（预计到 {until_short}，还剩 {remaining_min} 分钟）")
            except Exception:
                parts.append(f"你正在：{char_activity}（预计到 {until_short}）")
        else:
            parts.append(f"你正在：{char_activity}")
    char_emotion = char_state.get("emotion", "")
    if char_emotion:
        parts.append(f"你现在的心情：{char_emotion}")
    if loc_desc:
        parts.append(f"地点描述：{loc_desc}")

    # 明确告知 NPC 玩家角色身份
    from .world import get_player_character
    player = get_player_character()
    if player != character:
        parts.append(f"\n对话中 [{player}] 标记的消息来自{player}。")

    # Session 类型提示
    if session.session_type == "phone":
        parts.append("\n[电话通话中 — 你们不在同一地点，通过电话交流]")

    return "\n".join(parts)


def generate_reply(
    character: str,
    session: Session,
    model: Optional[str] = None,
) -> str:
    """为角色生成回复。

    1. 构建 system prompt
    2. 获取 session 对话历史
    3. 调用 LLM
    4. 返回纯文本
    """
    log("info", f"生成回复: {character} @ Session {session.id}")

    # 构建 system prompt
    system_prompt = build_system_prompt(character, session)

    # 获取对话历史（转换为 LLM 格式）
    history = session.get_history_for(character)

    # 组装 messages
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)

    # Tail Reminder（利用 U-shaped attention 强化规则遵守）
    if len(history) >= 2:
        reminder = _load_tail_reminder(character)
        if reminder:
            messages.append({"role": "system", "content": reminder})

    # 调用 LLM（流式，获取完整文本）
    reply = chat_stream(messages, model=model, label="回复生成", config_key="primary_story")

    # 基本清理
    reply = reply.strip()

    # 记录 session 详细日志
    log_session_detail(
        session.id, character,
        system_prompt=system_prompt,
        messages=messages,
        reply=reply,
    )

    log("info", f"回复生成完毕 [{character}] ({len(reply)}字)")
    return reply
