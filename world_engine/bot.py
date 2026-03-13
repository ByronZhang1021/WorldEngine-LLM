"""Telegram Bot — 收发消息、命令处理、消息分段发送 + 打字延迟。

复用旧系统的 blockStreaming 分段逻辑和 naturalDelay 延迟。
"""
import asyncio
import random
import re
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest

from .utils import log, load_config, reload_config, read_file, write_file
from .scene import handle_user_message, start_phone_call, end_phone_call
from .world import (
    get_time_display,
    get_all_character_locations,
    get_all_activity_chains,
    advance_time,
    set_time,
    move_user,
)


# ── 消息分段 ──────────────────────────────────────────────


def split_message(text: str) -> list[str]:
    """按 AI 自己的空行分段，每段一条消息。只在超 4096 时强制切割。"""
    # 按空行分段（AI 自己的段落结构）
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    if not paragraphs:
        return [text]

    # 4096 硬限制保护
    result = []
    for para in paragraphs:
        if len(para) <= 4096:
            result.append(para)
        else:
            remaining = para
            while len(remaining) > 4096:
                cut = remaining[:4096].rfind("\n")
                if cut < 1000:
                    cut = remaining[:4096].rfind("。")
                if cut < 1000:
                    cut = 4090
                result.append(remaining[:cut + 1])
                remaining = remaining[cut + 1:]
            if remaining:
                result.append(remaining)

    return result


# ── 打字延迟 ──────────────────────────────────────────────


def calculate_typing_delay(text: str) -> float:
    """计算自然打字延迟（秒）。模拟真人打字速度。"""
    length = len(text)
    # 基础：每个字 0.05-0.08 秒
    base = length * random.uniform(0.05, 0.08)
    # 加随机波动
    jitter = random.uniform(0.3, 1.2)
    # 限制在 0.5 - 5 秒
    delay = min(max(base + jitter, 0.5), 3.0)
    return delay


# ── 命令处理 ──────────────────────────────────────────────


async def cmd_start_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """聊天 Bot 的 /start 命令。"""
    log("info", f"[ChatBot] /start [user={update.effective_user.id}]")
    await update.message.reply_text(
        "直接发消息开始对话。\n"
        "用 @角色名 指定和某人说话。\n"
        "/call 角色名 — 打电话\n"
        "/help — 查看详细帮助"
    )


async def cmd_start_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理 Bot 的 /start 命令。"""
    log("info", f"[AdminBot] /start [user={update.effective_user.id}]")
    await update.message.reply_text(
        "🛠️ 管理控制台\n\n"
        "/time — 查看/调整虚拟时间\n"
        "/where — 查看所有角色位置\n"
        "/activities — 查看所有角色活动链\n"
        "/locations — 查看地点和距离\n"
        "/move — 强制移动角色\n"
        "/play 角色名 — 切换扮演的角色\n"
        "/call 角色名 — 打电话\n"
        "/memo — 查看角色记忆\n"
        "/schedule — 查看预定事件\n"
        "/events — 查看事件列表\n"
        "/event <ID> — 查看事件详情\n"
        "/sessions — 查看对话记录\n"
        "/session <ID> — 查看对话详情\n"
        "/saves — 查看存档列表\n"
        "/save 名称 — 保存当前存档\n"
        "/load 名称 — 加载存档\n"
        "/help — 查看详细帮助"
    )


async def cmd_help_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """聊天 Bot 的 /help 命令 — 详细使用帮助。"""
    log("info", "[ChatBot] /help")
    await update.message.reply_text(
        "📖 聊天帮助\n\n"
        "💬 对话\n"
        "  直接发消息即可和当前场景中的角色对话。\n"
        "  如果场景中有多个角色，系统会自动判断谁来回复。\n\n"
        "🎯 指定对话\n"
        "  用 @角色名 开头来指定和某个角色说话。\n"
        "  例如: @小美 你今天怎么样？\n\n"
        "📞 打电话\n"
        "  /call 角色名 — 给不在身边的角色打电话\n"
        "  通话中直接发消息即可继续对话\n"
        "  对方可能会不接（根据当前状态判断）\n\n"
        "📅 日程\n"
        "  /myplan — 查看你的待办预定事件\n\n"
        "🗺️ 地点\n"
        "  /locations — 查看已知地点和步行距离\n"
        "  移动到其他地点会由 AI 根据对话自动判断\n\n"
        "💡 小提示\n"
        "  • 说 \"去XX\" 可以触发移动\n"
        "  • 时间会根据对话内容自动推进\n"
        "  • 每条消息后会显示当前时间和位置"
    )


async def cmd_help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理 Bot 的 /help 命令 — 详细命令帮助。"""
    log("info", "[AdminBot] /help")
    await update.message.reply_text(
        "📖 管理命令详细帮助\n\n"
        "⏰ 时间控制\n"
        "  /time — 查看当前虚拟时间\n"
        "  /time +30m — 推进 30 分钟\n"
        "  /time +2h — 推进 2 小时\n"
        "  /time 14:30 — 跳到指定时间\n"
        "  /time next-day — 跳到第二天\n\n"
        "📍 位置\n"
        "  /where — 查看所有角色当前位置\n"
        "  /activities — 查看所有角色的活动链\n"
        "  /locations — 查看地点列表和距离\n"
        "  /move 角色名 地点名 — 强制移动角色到指定地点\n"
        "  /move 角色名 地点名 子地点 — 可指定子地点\n\n"
        "🎭 角色\n"
        "  /play — 查看可用角色列表\n"
        "  /play 角色名 — 切换你扮演的角色\n"
        "  /memo — 查看所有角色记忆概览\n"
        "  /memo 角色名 — 查看某角色全部记忆\n"
        "  /memo 角色名 5 — 只看最近 5 条动态记忆\n"
        "  /memo cleanup — 清理所有过期记忆\n\n"
        "📅 事件\n"
        "  /schedule — 查看所有预定事件\n"
        "  /schedule 角色名 — 查看某角色的预定\n"
        "  /events — 查看最近 10 条事件记录\n"
        "  /events 20 — 查看最近 20 条\n"
        "  /event <ID> — 查看事件详情\n\n"
        "💬 对话记录\n"
        "  /sessions — 查看活跃和归档对话\n"
        "  /session <ID> — 查看对话详情\n\n"
        "💾 存档\n"
        "  /saves — 查看存档列表\n"
        "  /save 名称 — 保存当前存档\n"
        "  /load 名称 — 加载存档"
    )


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /move 命令 — 强制移动角色到指定地点。"""
    from .utils import load_state, save_state, CHARACTERS_DIR
    from .location import discover_location, get_default_sub_location, discover_sub_location

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "用法: /move 角色名 地点名 [子地点]\n\n"
            "例如:\n"
            "  /move 小美 商业街\n"
            "  /move 小美 商业街 咖啡店"
        )
        return

    char_name = context.args[0]
    location = context.args[1]
    sub_location = context.args[2] if len(context.args) >= 3 else ""

    log("info", f"[Bot] /move {char_name} → {location}/{sub_location}")

    # 检查角色是否存在
    char_file = CHARACTERS_DIR / f"{char_name}.json"
    if not char_file.exists():
        await update.message.reply_text(f"❌ 找不到角色: {char_name}")
        return

    state = load_state()
    chars = state.setdefault("characters", {})
    char_state = chars.setdefault(char_name, {})

    old_loc = char_state.get("location", "未知")
    old_sub = char_state.get("sub_location", "")
    old_display = f"{old_loc}/{old_sub}" if old_sub else old_loc

    # 更新位置
    char_state["location"] = location
    if not sub_location:
        sub_location = get_default_sub_location(location)
    char_state["sub_location"] = sub_location
    char_state["activity"] = ""  # 清空当前活动
    save_state(state)

    # 自动发现地点
    discover_location(char_name, location)
    if sub_location:
        discover_sub_location(char_name, location, sub_location)

    new_display = f"{location}/{sub_location}" if sub_location else location
    await update.message.reply_text(
        f"📍 已移动 {char_name}\n"
        f"  {old_display} → {new_display}"
    )


async def cmd_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /time 命令。"""
    args = context.args
    log("info", f"[Bot] /time {' '.join(args) if args else '(无参数)'}")

    if not args:
        # 查看时间
        await update.message.reply_text(get_time_display())
        return

    arg = " ".join(args)

    # /time +30m, /time +2h
    m = re.match(r"^\+(\d+)(m|h)$", arg)
    if m:
        val, unit = int(m.group(1)), m.group(2)
        minutes = val if unit == "m" else val * 60
        advance_time(minutes)
        await update.message.reply_text(f"⏩ 时间推进了 {arg}\n\n{get_time_display()}")
        return

    # /time next-day
    if arg == "next-day":
        advance_time(24 * 60)  # 简化：直接推进 24 小时
        await update.message.reply_text(f"⏩ 跳到第二天\n\n{get_time_display()}")
        return

    # /time 14:30
    m = re.match(r"^(\d{1,2}):(\d{2})$", arg)
    if m:
        from datetime import datetime, timedelta
        from .world import get_current_time
        current = get_current_time()
        dt = datetime.fromisoformat(current)
        target = dt.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        if target <= dt:
            target += timedelta(days=1)
        diff = int((target - dt).total_seconds() / 60)
        advance_time(diff)
        await update.message.reply_text(f"⏩ 跳到 {arg}\n\n{get_time_display()}")
        return

    await update.message.reply_text(f"用法: /time, /time +30m, /time +2h, /time 14:30, /time next-day")


async def cmd_where(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /where 命令。"""
    log("info", "[Bot] /where")
    await update.message.reply_text(get_all_character_locations())


async def cmd_activities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /activities 命令 — 查看所有角色的活动链。"""
    log("info", "[Bot] /activities")
    text = get_all_activity_chains()
    # 活动链可能较长，分段发送
    if len(text) <= 4096:
        await update.message.reply_text(text)
    else:
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk)


async def cmd_locations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /locations 命令 — 查看已知地点和距离。"""
    from .utils import load_state
    from .world import get_player_character, _get_distances
    log("info", "[Bot] /locations")

    state = load_state()
    player = get_player_character()
    player_cs = state.get("characters", {}).get(player, {})
    current_loc = player_cs.get("location", "未知")
    current_sub = player_cs.get("sub_location", "")
    loc_display = f"{current_loc}/{current_sub}" if current_sub else current_loc
    distances = _get_distances(current_loc, player)

    await update.message.reply_text(
        f"🗺️ 已知地点\n📍 当前位置: {loc_display}\n\n{distances}"
    )




async def cmd_locations_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理 Bot 的 /locations 命令 — 查看全部地点和距离（不按角色过滤）。"""
    from .utils import load_state
    from .world import get_player_character, _get_distances
    log("info", "[AdminBot] /locations (all)")

    state = load_state()
    player = get_player_character()
    player_cs = state.get("characters", {}).get(player, {})
    current_loc = player_cs.get("location", "未知")
    current_sub = player_cs.get("sub_location", "")
    loc_display = f"{current_loc}/{current_sub}" if current_sub else current_loc
    distances = _get_distances(current_loc)  # 不传 character，显示全部地点

    await update.message.reply_text(
        f"🗺️ 全部地点\n📍 当前位置（{player}）: {loc_display}\n\n{distances}"
    )


async def cmd_myplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /myplan 命令 — 查看玩家角色的待执行预定事件。"""
    from .events import get_upcoming_events
    from .world import get_player_character
    log("info", "[ChatBot] /myplan")

    player = get_player_character()
    events = get_upcoming_events(player)

    if not events:
        await update.message.reply_text("📅 暂无待办预定")
        return

    lines = [f"📅 你的预定事件（{len(events)} 条）\n"]
    for evt in events:
        t = (evt.get('time', '') or '')[:16].replace('T', ' ')
        desc = evt.get('description', '')
        loc = evt.get('location', '')
        sub = evt.get('sub_location', '')
        loc_display = f"{loc}/{sub}" if sub else loc
        others = [p for p in evt.get('participants', []) if p != player]
        window = evt.get('flexible_window', 30)
        created_by = evt.get('created_by', '')

        lines.append(f"  ⏰ {t}")
        lines.append(f"    {desc}")
        if loc_display:
            lines.append(f"    📍 {loc_display}")
        if others:
            lines.append(f"    👥 {'、'.join(others)}")
        lines.append(f"    等{window}分钟 · 创建: {created_by}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /call 命令 — 打电话。"""
    if not context.args:
        await update.message.reply_text("用法: /call 角色名")
        return

    target = " ".join(context.args)
    log("info", f"[Bot] /call {target}")
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    session, msg = start_phone_call(target)
    await update.message.reply_text(msg)


async def cmd_memo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /memo 命令 — 查看/管理角色记忆。"""
    from .utils import CHARACTERS_DIR, load_character, SECTION_KEYS
    from .memory import cleanup_all_characters, cleanup_expired

    args = context.args

    if not args:
        # 无参数：列出所有角色及各 section 条目数
        if not CHARACTERS_DIR.exists():
            await update.message.reply_text("📝 暂无角色数据")
            return
        lines = ["📝 记忆概览\n"]
        for f in sorted(CHARACTERS_DIR.iterdir()):
            if f.is_file() and f.suffix == ".json" and not f.name.startswith("."):
                name = f.stem
                data = load_character(name)
                counts = []
                for key in SECTION_KEYS:
                    entries = data.get(key, [])
                    n = len(entries) if isinstance(entries, list) else 0
                    counts.append(str(n))
                lines.append(f"  👤 {name}")
                lines.append(f"    公开基础:{counts[0]} | 私密基础:{counts[1]} | 公开动态:{counts[2]} | 私密动态:{counts[3]}")
        lines.append("\n命令：")
        lines.append("/memo 角色名 — 查看全部记忆")
        lines.append("/memo 角色名 数字 — 查看最近N条")
        lines.append("/memo cleanup — 清理所有过期记忆")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0] == "cleanup":
        log("info", "[Bot] /memo cleanup")
        # 先统计清理前数量
        results = []
        if CHARACTERS_DIR.exists():
            for f in sorted(CHARACTERS_DIR.iterdir()):
                if f.is_file() and f.suffix == ".json" and not f.name.startswith("."):
                    removed = cleanup_expired(f.stem)
                    if removed > 0:
                        results.append(f"  {f.stem}: 清理 {removed} 条")
        if results:
            await update.message.reply_text("✅ 记忆清理完成\n\n" + "\n".join(results))
        else:
            await update.message.reply_text("✅ 没有需要清理的过期记忆")
        return

    # 解析角色名和可选的数量参数
    # /memo 角色名 或 /memo 角色名 数字
    char_name = args[0]
    limit = None
    if len(args) >= 2:
        try:
            limit = int(args[-1])
            char_name = " ".join(args[:-1])
        except ValueError:
            char_name = " ".join(args)

    log("info", f"[Bot] /memo {char_name} (limit={limit})")

    char_path = CHARACTERS_DIR / f"{char_name}.json"
    if not char_path.exists():
        await update.message.reply_text(f"❌ 找不到角色: {char_name}")
        return

    data = load_character(char_name)

    section_names = {
        "public_base": "📖 公开基础设定",
        "private_base": "🔒 私密基础设定",
        "public_dynamic": "📢 公开动态记忆",
        "private_dynamic": "🧠 私密动态记忆",
    }

    all_text_parts = [f"📝 {char_name} 的记忆\n"]

    for key in SECTION_KEYS:
        entries = data.get(key, [])
        if isinstance(entries, str):
            # 纯文本格式
            if entries.strip():
                all_text_parts.append(f"\n{section_names.get(key, key)} ({1}条)")
                all_text_parts.append(entries[:500])
            continue
        if not isinstance(entries, list):
            continue

        display_entries = entries
        suffix = ""
        if limit and key in ("private_dynamic", "public_dynamic"):
            if len(entries) > limit:
                display_entries = entries[-limit:]
                suffix = f"（显示最近 {limit}/{len(entries)} 条）"

        all_text_parts.append(f"\n{section_names.get(key, key)} ({len(entries)}条){suffix}")

        if not display_entries:
            all_text_parts.append("  （空）")
            continue

        for e in display_entries:
            if not isinstance(e, dict):
                continue
            content = e.get("text", "") or e.get("content", "")
            ttl = e.get("ttl", "")
            created = e.get("created", "")
            created_short = created[5:16].replace("T", " ") if created else ""

            ttl_display = ttl
            if ttl == "永久":
                ttl_display = "♾️"
            elif ttl.endswith("h"):
                hours = int(ttl[:-1])
                if hours >= 24:
                    ttl_display = f"{hours//24}天"
                else:
                    ttl_display = f"{hours}小时"

            meta = f"[{ttl_display}]"
            if created_short:
                meta += f" {created_short}"
            all_text_parts.append(f"  • {content}")
            all_text_parts.append(f"    {meta}")

    full_text = "\n".join(all_text_parts)

    # 分段发送
    if len(full_text) <= 4096:
        await update.message.reply_text(full_text)
    else:
        chunks = []
        current = ""
        for line in all_text_parts:
            if len(current) + len(line) + 1 > 4000:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk)


# ── 消息处理（核心） ───────────────────────────────────────


async def _safe_send(bot, chat_id, text, max_retries=2):
    """发送 Telegram 消息 + 自动重试（网络抖动保护）。"""
    from telegram.error import TimedOut as TgTimedOut, NetworkError as TgNetworkError, RetryAfter
    for attempt in range(max_retries + 1):
        try:
            return await bot.send_message(chat_id=chat_id, text=text)
        except RetryAfter as e:
            wait = e.retry_after + 1
            log("warning", f"[Bot] Telegram 限流，等待 {wait}s")
            await asyncio.sleep(wait)
            continue
        except (TgTimedOut, TgNetworkError) as e:
            if attempt < max_retries:
                wait = 2 * (attempt + 1)
                log("warning", f"[Bot] Telegram 发送失败 (第{attempt+1}次), {wait}s 后重试: {e}")
                await asyncio.sleep(wait)
                continue
            log("warning", f"[Bot] Telegram 发送失败 (已重试{max_retries}次，放弃): {e}")
            raise


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户发送的普通消息。"""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # 白名单检查（每次读最新配置）
    owner_id = reload_config().get("telegram", {}).get("owner_id")
    if owner_id and user_id != owner_id:
        log("info", f"忽略非授权用户 [user={user_id}, chat={chat_id}]")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    log("info", f"[Bot] 收到消息 [user={user_id}]: {user_text[:100]}")

    # 持续发送 "正在输入..." 状态的后台任务
    async def keep_typing():
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(keep_typing())

    try:
        # 处理消息，获取回复列表 + 图片 Future
        replies, image_futures = await handle_user_message(user_text)

        for character_name, reply_text in replies:
            if not reply_text:
                continue

            # 只在超 4096 时切割
            chunks = split_message(reply_text)

            for i, chunk in enumerate(chunks):
                # 打字延迟（除了第一段，因为 LLM 生成已经有延迟了）
                if i > 0:
                    # 先刷新 typing 状态：send_message 会清除 TYPING，
                    # 必须在延迟前重新发送，否则等待期间用户看不到 "正在输入"
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    delay = calculate_typing_delay(chunk)
                    await asyncio.sleep(delay)

                # 多角色时加前缀（排除 system 消息，避免移动通知误触发）
                npc_reply_count = len(set(r[0] for r in replies if r[0] != "system"))
                if npc_reply_count > 1 and character_name != "system":
                    prefix = f"[{character_name}]\n" if i == 0 else ""
                else:
                    prefix = ""

                await _safe_send(
                    context.bot, chat_id,
                    f"{prefix}{chunk}",
                )

            log("info", f"[Bot] 回复 [{character_name}]: {reply_text[:80]}")

        # 等待图片生成并发送（在文字之后、时间地点之前）
        for fut in image_futures:
            try:
                aio_fut = asyncio.wrap_future(fut)
                image_path = await asyncio.wait_for(aio_fut, timeout=90)
                if image_path:
                    try:
                        with open(image_path, 'rb') as img_file:
                            await context.bot.send_photo(chat_id=chat_id, photo=img_file)
                        log("info", f"[Bot] 图片已发送: {image_path}")
                    except Exception as e:
                        log("warning", f"[Bot] 发送图片失败: {e}")
            except asyncio.TimeoutError:
                log("warning", "[Bot] 图片生成超时，跳过")
            except Exception as e:
                log("warning", f"[Bot] 图片处理异常: {e}")

        # 发送当前时间和位置
        try:
            from .utils import load_state
            from .world import get_player_character
            st = load_state()
            t = st.get("current_time", "")
            dow = st.get("day_of_week", "")
            player_cs = st.get("characters", {}).get(get_player_character(), {})
            loc = player_cs.get("location", "未知")
            sub = player_cs.get("sub_location", "")
            loc_display = f"{loc}/{sub}" if sub else loc
            time_short = t.replace("T", " ")[5:16] if t else ""
            await _safe_send(
                context.bot, chat_id,
                f"🕐 {time_short} {dow} · 📍 {loc_display}",
            )
        except Exception:
            pass

    except Exception as e:
        log("warning", f"处理消息失败: {type(e).__name__}: {e}")
        # 识别网络错误，给友好提示而非暴露原始异常
        import requests as _req
        if isinstance(e, _req.exceptions.ConnectionError):
            err_msg = "⚠️ 网络连接暂时不稳定，请稍后再试"
        elif isinstance(e, _req.exceptions.Timeout):
            err_msg = "⚠️ AI 服务响应超时，请稍后再试"
        elif isinstance(e, _req.exceptions.HTTPError):
            err_msg = "⚠️ AI 服务暂时不可用，请稍后再试"
        else:
            err_msg = f"⚠️ 系统错误: {e}"
        try:
            await _safe_send(context.bot, chat_id, err_msg)
        except Exception:
            log("warning", "连错误提示消息都发送失败了")
    finally:
        typing_task.cancel()



# ── Bot 创建 ──────────────────────────────────────────────

async def cmd_play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /play 命令 — 切换扮演的角色。"""
    from .world import get_player_character, set_player_character
    from .utils import CHARACTERS_DIR

    if not context.args:
        current = get_player_character()
        # 列出所有可用角色
        chars = [f.stem for f in CHARACTERS_DIR.iterdir() if f.suffix == '.json' and not f.name.startswith('.')]
        lines = [f"🎭 当前扮演: {current}\n\n可用角色："]
        for c in chars:
            marker = ' ← 当前' if c == current else ''
            lines.append(f"  • {c}{marker}")
        lines.append("\n用法: /play 角色名")
        await update.message.reply_text("\n".join(lines))
        return

    target = " ".join(context.args)
    log("info", f"[Bot] /play {target}")

    # 检查角色是否存在
    char_file = CHARACTERS_DIR / f"{target}.json"
    if not char_file.exists():
        await update.message.reply_text(f"❌ 找不到角色: {target}")
        return

    old = get_player_character()
    if target == old:
        await update.message.reply_text(f"你已经在扮演 {target}")
        return

    set_player_character(target)
    await update.message.reply_text(
        f"🎭 角色切换: {old} → {target}\n"
        f"现在你扮演的是 {target}，{old} 变为 NPC"
    )


# ── 存档管理 ──────────────────────────────────────────────


async def cmd_saves(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /saves 命令 — 列出所有存档。"""
    from .dashboard import _list_saves
    log("info", "[Bot] /saves")

    saves = _list_saves()
    if not saves:
        await update.message.reply_text("📦 没有存档。\n用 /save 名称 来创建存档。")
        return

    lines = ["📦 存档列表\n"]
    for s in saves:
        lines.append(f"  • {s['name']}")
    lines.append(f"\n共 {len(saves)} 个存档")
    lines.append("用 /load 名称 加载存档")
    await update.message.reply_text("\n".join(lines))


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /save 命令 — 保存当前状态为存档。"""
    from .dashboard import _list_saves, _copy_data_to, SAVES_DIR

    if not context.args:
        await update.message.reply_text("用法: /save 存档名称")
        return

    name = " ".join(context.args)
    log("info", f"[Bot] /save {name}")

    # 检查重名，自动加数字后缀
    existing_names = {s["name"] for s in _list_saves()}
    final_name = name
    if final_name in existing_names:
        i = 2
        while f"{name}_{i}" in existing_names:
            i += 1
        final_name = f"{name}_{i}"
        log("info", f"[Bot] 存档名重复，改为: {final_name}")

    folder = final_name
    dest = SAVES_DIR / folder
    dest.mkdir(parents=True, exist_ok=True)
    _copy_data_to(dest)

    msg = f"💾 存档已保存: {final_name}"
    if final_name != name:
        msg += f"\n（名称 '{name}' 已存在，自动改为 '{final_name}'）"
    await update.message.reply_text(msg)


async def cmd_load(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /load 命令 — 加载存档到当前。"""
    from .dashboard import _list_saves, _copy_data_from, SAVES_DIR

    if not context.args:
        await update.message.reply_text("用法: /load 存档名称\n用 /saves 查看可用存档")
        return

    name = " ".join(context.args)
    log("info", f"[Bot] /load {name}")

    # 按名称找存档
    saves = _list_saves()
    target = None
    for s in saves:
        if s["name"] == name:
            target = s
            break

    if not target:
        await update.message.reply_text(
            f"❌ 找不到存档: {name}\n用 /saves 查看可用存档"
        )
        return

    dest = SAVES_DIR / target["id"]
    if not dest.exists():
        await update.message.reply_text(f"❌ 存档目录不存在: {target['id']}")
        return

    _copy_data_from(dest)
    # 刷新内存中的单例缓存（与 Dashboard 的 api_archive_apply 保持一致）
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
    await update.message.reply_text(
        f"📂 已加载存档: {name}\n\n{get_time_display()}"
    )

# ── 查看事件和对话 ─────────────────────────────────────


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /schedule 命令 — 查看预定事件。支持 /schedule 角色名。"""
    from .events import get_all_events, get_upcoming_events
    log("info", "[Bot] /schedule")

    # 如果指定了角色名，只看该角色的
    if context.args:
        char_name = " ".join(context.args)
        events = get_upcoming_events(char_name)
        if not events:
            await update.message.reply_text(f"📅 {char_name} 暂无预定事件")
            return
        lines = [f"📅 {char_name} 的预定事件（{len(events)} 条）\n"]
        for evt in events:
            t = (evt.get('time', '') or '')[:16].replace('T', ' ')
            desc = evt.get('description', '')
            loc = evt.get('location', '')
            sub = evt.get('sub_location', '')
            loc_display = f"{loc}/{sub}" if sub else loc
            window = evt.get('flexible_window', 30)
            lines.append(f"  ⏰ {t}  📍 {loc_display}")
            lines.append(f"    {desc} [±{window}分钟]")
            lines.append("")
        await update.message.reply_text("\n".join(lines))
        return

    # 无参数：显示所有
    all_events = get_all_events()
    if not all_events:
        await update.message.reply_text("📅 暂无预定事件")
        return

    pending = [e for e in all_events if e.get('status', 'pending') == 'pending']
    completed = [e for e in all_events if e.get('status') == 'completed']
    missed = [e for e in all_events if e.get('status') == 'missed']

    lines = [f"📅 预定事件（共 {len(all_events)} 条）\n"]

    if pending:
        lines.append(f"⏳ 待执行 ({len(pending)})")
        for evt in sorted(pending, key=lambda e: e.get('time', '')):
            t = (evt.get('time', '') or '')[:16].replace('T', ' ')
            desc = evt.get('description', '')
            loc = evt.get('location', '')
            chars = ', '.join(evt.get('participants', []))
            window = evt.get('flexible_window', 30)
            lines.append(f"  [{evt.get('id', '?')}] ⏰ {t}")
            lines.append(f"    {desc}")
            if loc:
                lines.append(f"    📍 {loc}")
            lines.append(f"    👥 {chars} [±{window}分钟]")
            lines.append("")

    if completed:
        lines.append(f"\n✅ 已完成 ({len(completed)})")
        for evt in completed[-5:]:
            t = (evt.get('time', '') or '')[:16].replace('T', ' ')
            desc = evt.get('description', '')
            lines.append(f"  ⏰ {t} — {desc}")

    if missed:
        lines.append(f"\n❌ 已错过 ({len(missed)})")
        for evt in missed[-5:]:
            t = (evt.get('time', '') or '')[:16].replace('T', ' ')
            desc = evt.get('description', '')
            lines.append(f"  ⏰ {t} — {desc}")

    lines.append("\n用 /schedule 角色名 查看某角色的预定")
    await update.message.reply_text("\n".join(lines))


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /events 命令 — 查看事件列表。支持 /events <数量>。"""
    from .utils import EVENTS_DIR, read_json
    log("info", "[Bot] /events")

    if not EVENTS_DIR.exists():
        await update.message.reply_text("📝 暂无事件记录")
        return

    events = []
    for f in sorted(EVENTS_DIR.glob("*.json")):
        try:
            events.append(read_json(f))
        except Exception:
            pass

    if not events:
        await update.message.reply_text("📝 暂无事件记录")
        return

    # 可选参数：显示数量
    count = 10
    if context.args:
        try:
            count = int(context.args[0])
        except ValueError:
            pass

    recent = events[-count:]
    lines = [f"📝 事件列表（最近 {len(recent)}/{len(events)} 条）\n"]
    for ev in reversed(recent):
        eid = ev.get("id", "?")
        t = ev.get("time", "")[:16].replace("T", " ")
        loc = ev.get("location", "")
        chars = ", ".join(ev.get("characters", []))
        summary = ev.get("summary", "")[:60]
        lines.append(f"[#{eid}] ⏰ {t}  📍 {loc}")
        lines.append(f"  👥 {chars}")
        lines.append(f"  {summary}")
        lines.append("")

    lines.append("用 /event <ID> 查看事件详情")
    await update.message.reply_text("\n".join(lines))


async def cmd_event_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /event <id> 命令 — 查看事件详情。"""
    from .utils import EVENTS_DIR, read_json

    if not context.args:
        await update.message.reply_text("用法: /event <事件ID>\n用 /events 查看事件列表")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 事件 ID 必须是数字")
        return

    log("info", f"[Bot] /event {target_id}")

    event_path = EVENTS_DIR / f"{target_id}.json"
    if not event_path.exists():
        await update.message.reply_text(f"❌ 找不到事件 #{target_id}")
        return

    ev = read_json(event_path)

    if not ev:
        await update.message.reply_text(f"❌ 找不到事件 #{target_id}")
        return

    t_start = ev.get("time", "")[:16].replace("T", " ")
    t_end = ev.get("end_time", "")[:16].replace("T", " ")
    loc = ev.get("location", "")
    chars = ", ".join(ev.get("characters", []))
    summary = ev.get("summary", "")
    scene = ev.get("scene", "")

    lines = [
        f"📝 事件 #{target_id}",
        f"⏰ {t_start} ~ {t_end}",
        f"📍 {loc}",
        f"👥 {chars}",
        f"\n📋 摘要：{summary}",
    ]

    if scene:
        # 场景文本可能很长，截断保护
        if len(scene) > 3500:
            scene = scene[:3500] + "\n...(截断)"
        lines.append(f"\n🎬 场景：\n{scene}")

    await update.message.reply_text("\n".join(lines))


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /sessions 命令 — 查看对话记录。"""
    from .session import get_session_manager
    from .utils import ARCHIVE_SESSIONS_DIR, read_json
    log("info", "[Bot] /sessions")

    sm = get_session_manager()
    lines = []

    # 活跃对话
    active = sm.active_sessions()
    if active:
        lines.append("🟢 活跃对话\n")
        for s in active:
            participants = ", ".join(s.participants)
            msg_count = len(s.messages)
            t = s.start_time[:16].replace("T", " ") if s.start_time else ""
            lines.append(f"  • {s.id} | {participants} @ {s.location}")
            lines.append(f"    {t} | {msg_count} 条消息 | {s.session_type}")
        lines.append("")

    # 归档对话（最近 5 条）
    archived = []
    if ARCHIVE_SESSIONS_DIR.exists():
        files = sorted(ARCHIVE_SESSIONS_DIR.glob("*.json"), reverse=True)
        for f in files[:5]:
            try:
                data = read_json(f)
                archived.append(data)
            except Exception:
                pass

    if archived:
        lines.append("📁 最近归档对话\n")
        for d in archived:
            participants = ", ".join(d.get("participants", []))
            msg_count = len(d.get("messages", []))
            t = (d.get("start_time", "") or "")[:16].replace("T", " ")
            loc = d.get("location", "")
            lines.append(f"  • {d.get('id', '?')} | {participants} @ {loc}")
            lines.append(f"    {t} | {msg_count} 条消息 | {d.get('type', '')}")
        lines.append("")

    if not lines:
        await update.message.reply_text("💬 暂无对话记录")
        return

    lines.append("用 /session <ID> 查看对话详情")
    await update.message.reply_text("\n".join(lines))


async def cmd_session_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /session <id> 命令 — 查看对话详情。"""
    from .session import get_session_manager
    from .utils import ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR, read_json

    if not context.args:
        await update.message.reply_text("用法: /session <会话ID>\n用 /sessions 查看会话列表")
        return

    target_id = context.args[0].strip()
    log("info", f"[Bot] /session {target_id}")

    # 先从活跃会话找
    data = None
    sm = get_session_manager()
    for s in sm.active_sessions():
        if s.id == target_id:
            data = {
                "id": s.id,
                "participants": s.participants,
                "location": s.location,
                "type": s.session_type,
                "start_time": s.start_time,
                "messages": [m.__dict__ if hasattr(m, '__dict__') else m for m in s.messages],
            }
            break

    # 再从归档找
    if not data:
        for d in [ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR]:
            f = d / f"{target_id}.json"
            if f.exists():
                try:
                    data = read_json(f)
                except Exception:
                    pass
                break

    if not data:
        await update.message.reply_text(f"❌ 找不到会话: {target_id}")
        return

    # 构建头部信息
    participants = ", ".join(data.get("participants", []))
    loc = data.get("location", "")
    stype = data.get("type", "")
    t = (data.get("start_time", "") or "")[:16].replace("T", " ")
    msgs = data.get("messages", [])

    header = (
        f"💬 会话 {data.get('id', target_id)}\n"
        f"⏰ {t}  📍 {loc}\n"
        f"👥 {participants}  | {stype}\n"
        f"📨 {len(msgs)} 条消息\n"
    )

    # 构建消息列表
    lines = [header, "─" * 30]
    for m in msgs:
        speaker = m.get("speaker", m.get("role", "?"))
        text = m.get("text", m.get("content", ""))
        time = m.get("time", "")
        lines.append(f"\n[{time}] {speaker}")
        lines.append(text)

    full_text = "\n".join(lines)

    # Telegram 消息限制 4096 字符，分段发送
    if len(full_text) <= 4096:
        await update.message.reply_text(full_text)
    else:
        # 先发头部
        await update.message.reply_text(header + f"\n（消息较长，分段发送）")
        # 分段发消息内容
        chunk = ""
        for m in msgs:
            speaker = m.get("speaker", m.get("role", "?"))
            text = m.get("text", m.get("content", ""))
            time = m.get("time", "")
            entry = f"\n[{time}] {speaker}\n{text}\n"
            if len(chunk) + len(entry) > 4000:
                if chunk:
                    await update.message.reply_text(chunk)
                chunk = entry
            else:
                chunk += entry
        if chunk:
            await update.message.reply_text(chunk)


async def _chat_post_init(application: Application):
    """聊天 Bot 启动后设置命令菜单。"""
    from telegram import BotCommand
    try:
        commands = [
            BotCommand("call", "打电话给某个角色"),
            BotCommand("myplan", "查看你的预定事件"),
            BotCommand("locations", "查看地点和距离"),
            BotCommand("help", "查看详细帮助"),
        ]
        await application.bot.set_my_commands(commands)
        log("info", f"聊天 Bot 命令菜单已注册 ({len(commands)} 个命令)")
    except Exception as e:
        log("warning", f"聊天 Bot 命令菜单注册失败: {e}")


async def _admin_post_init(application: Application):
    """管理 Bot 启动后设置命令菜单。"""
    from telegram import BotCommand
    try:
        commands = [
            BotCommand("time", "查看/调整虚拟时间"),
            BotCommand("where", "查看所有角色位置"),
            BotCommand("activities", "查看所有角色活动链"),
            BotCommand("locations", "查看全部地点和距离"),
            BotCommand("move", "强制移动角色到指定地点"),
            BotCommand("play", "切换扮演的角色"),
            BotCommand("memo", "查看角色记忆"),
            BotCommand("schedule", "查看预定事件"),
            BotCommand("events", "查看事件列表"),
            BotCommand("event", "查看事件详情"),
            BotCommand("sessions", "查看对话记录"),
            BotCommand("session", "查看对话详情"),
            BotCommand("saves", "查看存档列表"),
            BotCommand("save", "保存当前存档"),
            BotCommand("load", "加载存档"),
            BotCommand("help", "查看详细帮助"),
        ]
        await application.bot.set_my_commands(commands)
        log("info", f"管理 Bot 命令菜单已注册 ({len(commands)} 个命令)")
    except Exception as e:
        log("warning", f"管理 Bot 命令菜单注册失败: {e}")


def _build_robust_request() -> HTTPXRequest:
    """创建健壮的 HTTP 请求配置，防止代理断连后卡死。"""
    return HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=40.0,
        write_timeout=20.0,
        pool_timeout=10.0,
        connection_pool_size=8,
    )


def create_chat_bot() -> Application:
    """创建聊天 Bot（沉浸式对话）。"""
    config = load_config()
    token = config["telegram"]["bot_token"]

    request = _build_robust_request()
    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(_build_robust_request())
        .post_init(_chat_post_init)
        .build()
    )

    # 仅保留对话中必需的命令
    app.add_handler(CommandHandler("start", cmd_start_chat))
    app.add_handler(CommandHandler("help", cmd_help_chat))
    app.add_handler(CommandHandler("call", cmd_call))
    app.add_handler(CommandHandler("myplan", cmd_myplan))
    app.add_handler(CommandHandler("locations", cmd_locations))

    # 普通消息 → 角色对话
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log("info", "聊天 Bot 已配置（已启用健壮网络配置）")
    return app


def create_admin_bot() -> Application:
    """创建管理 Bot（命令控制台）。"""
    config = load_config()
    token = config["telegram"].get("admin_bot_token", "")
    if not token:
        log("warning", "未配置 admin_bot_token，管理 Bot 不启动")
        return None

    request = _build_robust_request()
    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(_build_robust_request())
        .post_init(_admin_post_init)
        .build()
    )

    # 所有管理命令
    app.add_handler(CommandHandler("start", cmd_start_admin))
    app.add_handler(CommandHandler("help", cmd_help_admin))
    app.add_handler(CommandHandler("time", cmd_time))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("activities", cmd_activities))
    app.add_handler(CommandHandler("move", cmd_move))
    app.add_handler(CommandHandler("play", cmd_play))
    app.add_handler(CommandHandler("memo", cmd_memo))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("event", cmd_event_detail))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("session", cmd_session_detail))
    app.add_handler(CommandHandler("saves", cmd_saves))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("locations", cmd_locations_all))
    app.add_handler(CommandHandler("load", cmd_load))

    log("info", "管理 Bot 已配置（已启用健壮网络配置）")
    return app

