"""工具函数 — 日志、文件读写、路径常量。"""
import json
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

# ── 路径常量 ──────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

# 当前世界目录（世界数据存这里）
WORLD_DIR = DATA_DIR / "current"
STATE_PATH = WORLD_DIR / "state.json"
TEMP_CHARACTERS_PATH = WORLD_DIR / "temp_characters.json"
EVENTS_DIR = WORLD_DIR / "events"
LOCATIONS_PATH = WORLD_DIR / "locations.json"
LORE_PATH = WORLD_DIR / "lore.json"
CHARACTERS_DIR = WORLD_DIR / "characters"
SESSIONS_DIR = WORLD_DIR / "sessions"
ACTIVE_SESSIONS_DIR = SESSIONS_DIR / "active"
ARCHIVE_SESSIONS_DIR = SESSIONS_DIR / "archive"

# 引擎 prompts 目录
ENGINE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = ENGINE_DIR / "prompts"
RULES_PATH = PROMPTS_DIR / "rules.md"

# 共享目录（位于项目根目录，与 data/ 平级）
MEDIA_DIR = PROJECT_DIR / "media"
LOG_DIR = PROJECT_DIR / "logs"

# 确保目录存在
for d in (LOG_DIR, WORLD_DIR, ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def get_media_dir(world_name: str = "") -> Path:
    """获取当前世界的 media 目录。"""
    if not world_name:
        try:
            state = load_state()
            world_name = state.get("world_name", "default")
        except Exception:
            world_name = "default"
    media_dir = MEDIA_DIR / world_name
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir

# ── 配置加载 ──────────────────────────────────────────────

_config_cache = None


def load_config() -> dict:
    """加载 config.json，带缓存。"""
    global _config_cache
    if _config_cache is None:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def reload_config() -> dict:
    """强制重新加载 config.json。"""
    global _config_cache
    _config_cache = None
    return load_config()


# ── 日志 ──────────────────────────────────────────────────
#
# engine.log — 完整事件流水账（以 Turn 为单位组织），记录“发生了什么”
# turns/turn_NNNN.html — 单轮交互调试视图，记录“怎么发生的”（完整 prompt / LLM 回复 / token 统计）
#

_logger = logging.getLogger("world-engine")
_logger.setLevel(logging.DEBUG)

# 文件 handler（按天命名：YYYY-MM-DD.log，保田30天）
_file_handler = logging.handlers.TimedRotatingFileHandler(
    str(LOG_DIR / "engine.log"),
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_file_handler.suffix = "%Y-%m-%d"
_file_handler.namer = lambda name: name.replace("engine.log.", "") + ".log" if "engine.log." in name else name
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
_logger.addHandler(_file_handler)

# stderr handler
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.INFO)
_stderr_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
_logger.addHandler(_stderr_handler)

# 当前活跃 turn 编号（用于在 engine.log 中标注来源 turn）
_current_turn_num: int = 0


def log(level: str, msg: str):
    """统一日志接口。engine.log 中自动附加 Turn 编号（如有）。"""
    # 把换行替换为 ↵，保持每条日志单行
    msg = msg.replace("\n", " ↵ ")
    if _current_turn_num:
        msg = f"[T#{_current_turn_num:04d}] {msg}"
    getattr(_logger, level, _logger.info)(msg)


# ── Turn 详细日志 ─────────────────────────────────────────

TURN_LOG_DIR = LOG_DIR / "turns"
TURN_LOG_DIR.mkdir(parents=True, exist_ok=True)

# 全局活跃的 turn logger（每次用户输入对应一个）
_active_turn_logger: "TurnLogger | None" = None
_turn_logger_lock = threading.Lock()


class TurnLogger:
    """一轮完整用户输入的日志记录器（HTML 格式）。

    从用户发送消息开始，到所有后续操作完成为止的完整记录：
    - 用户输入 + 场景信息
    - 多角色调度（完整 prompt + LLM 回复）
    - 角色回复生成（完整 system prompt + messages + reply）
    - 时间推进（完整 prompt + LLM 回复）
    - 世界模拟（活动链生成 + 离屏场景 + 事件）
    - 记忆管道（完整 prompt + LLM 回复 + 操作结果）
    - 移动判断

    写入路径：logs/turns/turn_{NNNN}.html
    """

    _CSS = """
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: #f8f9fa; color: #1a1a2e;
            padding: 24px; line-height: 1.6;
        }
        .header {
            background: linear-gradient(135deg, #ffffff, #f0f4ff);
            border-radius: 12px; padding: 20px 28px; margin-bottom: 24px;
            border-left: 5px solid #e94560;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        .header h1 { color: #c0392b; font-size: 22px; margin-bottom: 6px; }
        .header .meta { color: #6b7280; font-size: 14px; }
        .step {
            background: #ffffff; border-radius: 10px;
            margin-bottom: 16px; overflow: hidden;
            border-left: 4px solid #d1d5db;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }
        .step-header {
            padding: 14px 20px; font-size: 16px; font-weight: 600;
            display: flex; align-items: center; gap: 10px;
        }
        .step-header .num {
            background: #f3f4f6; border-radius: 6px;
            padding: 2px 10px; font-size: 13px; color: #6b7280;
        }
        .step-body { padding: 0 20px 16px 20px; }

        .step-user { border-left-color: #3b82f6; }
        .step-user .step-header { color: #2563eb; }
        .step-reply { border-left-color: #16a34a; }
        .step-reply .step-header { color: #15803d; }
        .step-time { border-left-color: #d97706; }
        .step-time .step-header { color: #b45309; }
        .step-memory { border-left-color: #9333ea; }
        .step-memory .step-header { color: #7e22ce; }
        .step-responder { border-left-color: #0d9488; }
        .step-responder .step-header { color: #0f766e; }
        .step-world { border-left-color: #0891b2; }
        .step-world .step-header { color: #0e7490; }
        .step-custom { border-left-color: #4f46e5; }
        .step-custom .step-header { color: #4338ca; }
        .step-image { border-left-color: #f43f5e; }
        .step-image .step-header { color: #e11d48; }
        .step-stats { border-left-color: #f59e0b; }
        .step-stats .step-header { color: #d97706; }
        .step-retrieval { border-left-color: #06b6d4; }
        .step-retrieval .step-header { color: #0891b2; }
        .step-session { border-left-color: #8b5cf6; }
        .step-session .step-header { color: #7c3aed; }

        details {
            background: #f9fafb; border-radius: 8px;
            margin: 10px 0; overflow: hidden;
            border: 1px solid #e5e7eb;
        }
        summary {
            padding: 10px 16px; cursor: pointer; font-size: 14px;
            color: #6b7280; font-weight: 500;
            user-select: none; list-style: none;
        }
        summary::-webkit-details-marker { display: none; }
        summary::before {
            content: '▶ '; font-size: 11px; color: #9ca3af;
        }
        details[open] summary::before { content: '▼ '; }
        details .content {
            padding: 12px 16px; border-top: 1px solid #e5e7eb;
            white-space: pre-wrap; word-break: break-word;
            font-size: 13px; color: #374151;
            max-height: 600px; overflow-y: auto;
        }

        .kv { margin: 6px 0; }
        .kv .label { color: #6b7280; font-size: 13px; margin-right: 8px; }
        .kv .value { color: #1f2937; font-size: 14px; }

        .highlight {
            background: #f0fdf4; border-radius: 8px;
            padding: 12px 16px; margin: 10px 0;
            border-left: 3px solid #16a34a;
            white-space: pre-wrap; word-break: break-word;
            font-size: 14px; line-height: 1.7;
        }
        .highlight-reply { border-left-color: #16a34a; background: #f0fdf4; color: #14532d; }
        .highlight-result { border-left-color: #d97706; background: #fffbeb; color: #78350f; }
        .highlight-emotion { border-left-color: #9333ea; background: #faf5ff; color: #581c87; }
        .highlight-ops { border-left-color: #e11d48; background: #fff1f2; color: #881337; }
        .highlight-world { border-left-color: #0891b2; background: #ecfeff; color: #164e63; }

        .json-block {
            background: #f8fafc; border-radius: 6px;
            padding: 12px 16px; margin: 8px 0;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 13px; color: #0369a1;
            white-space: pre-wrap; word-break: break-word;
            border: 1px solid #e2e8f0;
        }

        .footer {
            background: #ffffff; border-radius: 10px;
            padding: 16px 24px; margin-top: 20px;
            color: #6b7280; font-size: 14px; text-align: center;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }
        .msg-user { color: #2563eb; }
        .msg-assistant { color: #15803d; }
        .msg-system { color: #b45309; }
    </style>
    """

    def __init__(self, turn_num: int):
        import datetime as _dt
        self._turn_num = turn_num
        self.start_time = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._parts: list[str] = []
        self._step_count = 0
        self._llm_calls: list[dict] = []  # LLM 调用统计

    @staticmethod
    def _esc(text: str) -> str:
        """HTML 转义。"""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    def _step_open(self, title: str, css_class: str) -> int:
        self._step_count += 1
        self._parts.append(
            f'<div class="step {css_class}">'
            f'<div class="step-header">'
            f'<span class="num">Step {self._step_count}</span> {self._esc(title)}'
            f'</div><div class="step-body">'
        )
        return self._step_count

    def _step_close(self):
        self._parts.append('</div></div>')

    def _collapsible(self, summary: str, content: str, open_by_default: bool = False):
        open_attr = " open" if open_by_default else ""
        self._parts.append(
            f'<details{open_attr}><summary>{self._esc(summary)}</summary>'
            f'<div class="content">{self._esc(content)}</div></details>'
        )

    def _kv(self, label: str, value: str):
        self._parts.append(
            f'<div class="kv"><span class="label">{self._esc(label)}</span>'
            f'<span class="value">{self._esc(value)}</span></div>'
        )

    # ── 对话流程 ──

    def log_user_input(self, user_text: str, location: str, npcs: list[str],
                       vtime: str = ""):
        """记录用户输入和当前场景。"""
        self._step_open("用户输入", "step-user")
        self._kv("虚拟时间:", vtime)
        self._kv("位置:", location)
        self._kv("在场角色:", ', '.join(npcs) if npcs else '无')
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#3b82f6; background:#eff6ff; color:#1e3a5f;">'
            f'{self._esc(user_text)}</div>'
        )
        self._step_close()
        # engine.log 摘要
        npc_str = ', '.join(npcs) if npcs else '无'
        t = vtime[11:16] if len(vtime) > 15 else vtime
        log("info", f"用户输入 @ {location} [{t}]: '{user_text[:80]}' | 在场: {npc_str}")

    def log_responder_decision(self, prompt: str, result: dict, responders: list[str]):
        """记录多角色调度决策。"""
        self._step_open("多角色调度 (decide_responders)", "step-responder")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)
        self._parts.append(f'<div class="json-block">{self._esc(json_str)}</div>')
        self._parts.append(
            f'<div class="highlight highlight-result">'
            f'决定回复的角色: {self._esc(", ".join(responders))}</div>'
        )
        self._step_close()
        # engine.log 摘要
        log("info", f"多角色调度: 回复角色 -> {', '.join(responders)}")

    def log_reply_generation(self, character: str, system_prompt: str,
                             messages: list, reply: str):
        """记录角色回复生成（完整 prompt + messages + reply）。"""
        self._step_open(f"角色回复生成: {character}", "step-reply")
        self._collapsible(f"System Prompt ({len(system_prompt)}字)", system_prompt)

        if messages:
            msg_lines = []
            for i, m in enumerate(messages):
                role = m.get("role", "?")
                content = m.get("content", "")
                if role == "system" and i == 0:
                    msg_lines.append(f'<span class="msg-system">[{i}] system:</span> (见上方 System Prompt)')
                else:
                    css = {"user": "msg-user", "assistant": "msg-assistant", "system": "msg-system"}.get(role, "")
                    msg_lines.append(
                        f'<span class="{css}">[{i}] {self._esc(role)}:</span> '
                        f'{self._esc(content)}'
                    )
            msg_html = '<br><br>'.join(msg_lines)
            self._parts.append(
                f'<details><summary>Messages ({len(messages)}条)</summary>'
                f'<div class="content" style="white-space:pre-wrap">{msg_html}</div></details>'
            )

        self._parts.append(
            f'<div class="highlight highlight-reply">{self._esc(reply)}</div>'
        )
        self._step_close()
        # engine.log 摘要
        log("info", f"角色回复 [{character}]: {reply[:100]}")

    def log_time_advance(self, prompt: str, llm_result: dict,
                         minutes: int, reason: str, narration: str,
                         destination: str | None):
        """记录时间推进（完整 prompt + LLM 回复）。"""
        self._step_open("时间推进 (auto_advance_time)", "step-time")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(llm_result, ensure_ascii=False, indent=2) if isinstance(llm_result, dict) else str(llm_result)
        self._collapsible("LLM 完整回复", json_str)
        result_parts = [f"⏱ 推进 {minutes} 分钟  |  原因: {reason}"]
        if narration:
            result_parts.append(f"旁白: {narration}")
        if destination:
            sub_dest = llm_result.get("sub_destination", "") if isinstance(llm_result, dict) else ""
            loc_display = f"{destination}/{sub_dest}" if sub_dest else destination
            result_parts.append(f"📍 移动目标: {loc_display}")
        # 群组移动信息
        if isinstance(llm_result, dict):
            leader = llm_result.get("leader", "")
            companions = llm_result.get("companions", [])
            if leader:
                result_parts.append(f"👤 领头人: {leader}")
            if companions:
                result_parts.append(f"👥 同行者: {', '.join(companions)}")
        self._parts.append(
            f'<div class="highlight highlight-result">'
            f'{self._esc(chr(10).join(result_parts))}</div>'
        )
        self._step_close()

    # ── 世界模拟 ──

    def log_activity_chain(self, character: str, prompt: str, result: dict,
                           activities: list):
        """记录活动链生成（完整 prompt + LLM 回复）。"""
        self._step_open(f"活动链生成: {character}", "step-world")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (dict, list)) else str(result)
        self._collapsible("LLM 完整回复", json_str)
        if activities:
            act_lines = []
            for a in activities:
                s = a.get('start', '')[11:16] if len(a.get('start', '')) > 11 else a.get('start', '')
                e = a.get('end', '')[11:16] if len(a.get('end', '')) > 11 else a.get('end', '')
                act_lines.append(f"{s}~{e}  {a.get('location', '')}  {a.get('activity', '')}")
            self._parts.append(
                f'<div class="highlight highlight-world">'
                f'{len(activities)} 个活动:\n{self._esc(chr(10).join(act_lines))}</div>'
            )
        self._step_close()

    def log_offscreen_scene(self, location: str, time_range: str,
                            prompt: str, result: dict,
                            scene: str, summary: str,
                            memories: list, revised: dict):
        """记录离屏场景生成（完整 prompt + LLM 回复 + 结果）。"""
        self._step_open(f"离屏场景: {location} ({time_range})", "step-world")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)
        self._collapsible("LLM 完整回复", json_str)
        parts = [f"📖 {summary}"]
        if memories:
            parts.append(f"\n记忆写入 ({len(memories)}条):")
            for m in memories:
                parts.append(f"  [{m.get('character', '?')}] {m.get('memory', '')}")
        if revised:
            parts.append(f"\n活动修正: {', '.join(revised.keys())}")
        self._parts.append(
            f'<div class="highlight highlight-world">{self._esc(chr(10).join(parts))}</div>'
        )
        self._collapsible("场景全文", scene)
        self._step_close()

    def log_world_sim_summary(self, npc_count: int, overlap_count: int,
                              minutes: int, old_time: str, new_time: str):
        """记录世界模拟总结。"""
        self._step_open(f"世界模拟总结 ({old_time[11:16]}→{new_time[11:16]}, {minutes}分钟)", "step-world")
        self._parts.append(
            f'<div class="highlight highlight-world">'
            f'NPC 数量: {npc_count}\n'
            f'重叠场景数: {overlap_count}\n'
            f'时间跨度: {old_time} → {new_time} ({minutes}分钟)</div>'
        )
        self._step_close()

    # ── 记忆管道 ──

    def log_memory_pipeline(self, character: str, prompt: str,
                            llm_result: dict, operations: list,
                            emotion: str = ""):
        """记录记忆管道（完整 prompt + LLM 回复 + 操作）。"""
        self._step_open(f"记忆管道: {character}", "step-memory")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(llm_result, ensure_ascii=False, indent=2) if isinstance(llm_result, dict) else str(llm_result)
        self._collapsible("LLM 完整回复", json_str)

        if operations:
            ops_lines = []
            for op in operations:
                action = op.get('action', '?')
                content = op.get('content', '')
                vis = op.get('visibility', '')
                ttl = op.get('ttl', '')
                line = f"[{action}] {content}"
                if vis:
                    line += f"  (可见性: {vis})"
                if ttl:
                    line += f"  (TTL: {ttl})"
                ops_lines.append(line)
            self._parts.append(
                f'<div class="highlight highlight-ops">'
                f'记忆操作 ({len(operations)}个):\n{self._esc(chr(10).join(ops_lines))}</div>'
            )
        else:
            self._parts.append(
                '<div class="highlight" style="border-left-color:#9ca3af; background:#f9fafb; color:#6b7280;">'
                '记忆操作: 无</div>'
            )

        if emotion:
            self._parts.append(
                f'<div class="highlight highlight-emotion">'
                f'💭 情绪更新: {self._esc(emotion)}</div>'
            )
        self._step_close()
        # engine.log 摘要
        ops_summary = f"{len(operations)} 个操作" if operations else "无操作"
        emo_str = f" | 情绪: {emotion}" if emotion else ""
        log("info", f"记忆管道 [{character}]: {ops_summary}{emo_str}")

    # ── 图片生成 ──

    def log_image_generation(self, character: str, description: str,
                             image_characters: list[str],
                             prompt_input: str = "",
                             english_prompt: str = "",
                             aspect_ratio: str = "",
                             image_path: str = "",
                             error: str = ""):
        """记录图片生成全流程。"""
        status = "✅ 成功" if image_path else ("❌ 失败" if error else "⏳ 进行中")
        self._step_open(f"📷 图片生成: {character} — {status}", "step-image")

        # 触发信息
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#f43f5e; background:#fff1f2; color:#881337;">'
            f'场景描述: {self._esc(description)}\n'
            f'画面角色: {self._esc(", ".join(image_characters))}</div>'
        )

        # 富输入（可折叠）
        if prompt_input:
            self._collapsible(f"图片 Prompt LLM 输入 ({len(prompt_input)}字)", prompt_input)

        # 英文 prompt + 宽高比
        if english_prompt:
            self._parts.append(
                f'<div class="highlight" style="border-left-color:#16a34a; background:#f0fdf4; color:#14532d;">'
                f'英文 Prompt ({len(english_prompt.split())} 词):\n{self._esc(english_prompt)}\n\n'
                f'宽高比: {self._esc(aspect_ratio)}</div>'
            )

        # 结果
        if image_path:
            self._parts.append(
                f'<div class="highlight" style="border-left-color:#059669; background:#ecfdf5; color:#065f46;">'
                f'📁 图片路径: {self._esc(image_path)}</div>'
            )
        elif error:
            self._parts.append(
                f'<div class="highlight" style="border-left-color:#dc2626; background:#fef2f2; color:#991b1b;">'
                f'❌ 错误: {self._esc(error)}</div>'
            )

        self._step_close()

    # ── DM 对话前处理 ──

    def log_dm(self, prompt: str, llm_result: dict, adjudication: str,
               narration: str, dm_context: str):
        """记录 DM 对话前处理（完整 prompt + LLM 回复 + 结果）。"""
        self._step_open("DM 对话前处理", "step-custom")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        json_str = json.dumps(llm_result, ensure_ascii=False, indent=2) if isinstance(llm_result, dict) else str(llm_result)
        self._collapsible("LLM 完整回复", json_str)
        parts = []
        if adjudication:
            parts.append(f"裁定: {adjudication[:200]}")
        if narration:
            parts.append(f"旁白: {narration[:200]}")
        parts.append(f"便签: {dm_context[:100] if dm_context else '（空）'}")
        # 私密目标
        raw_pt = llm_result.get("private_targets") if isinstance(llm_result, dict) else None
        if raw_pt is not None:
            if raw_pt:
                parts.append(f"🔒 可见性: 仅 {'、'.join(raw_pt)}")
            else:
                parts.append(f"🔒 可见性: 隐秘行为（无人感知）")
        # 临时角色操作
        temp_ops = llm_result.get("temp_characters", []) if isinstance(llm_result, dict) else []
        if temp_ops and isinstance(temp_ops, list):
            tc_lines = []
            for op in temp_ops:
                action = op.get("action", "?")
                name = op.get("name", "?")
                icon = {"add": "➕", "update": "✏️", "remove": "🗑️"}.get(action, "❓")
                line = f"{icon} {action}: {name}"
                if action == "add":
                    desc = op.get("description", "")
                    state = op.get("state", "")
                    if desc:
                        line += f" — {desc[:60]}"
                    if state:
                        line += f" [{state[:40]}]"
                elif action == "update":
                    state = op.get("state", "")
                    if state:
                        line += f" → {state[:60]}"
                tc_lines.append(line)
            parts.append(f"👤 临时角色: {chr(10).join(tc_lines)}")
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#4f46e5; background:#eef2ff; color:#3730a3;">'
            f'{self._esc(chr(10).join(parts))}</div>'
        )
        self._step_close()

    # ── 通用 ──

    def log_custom(self, title: str, content: str):
        """记录自定义步骤。"""
        self._step_open(title, "step-custom")
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#4f46e5; background:#eef2ff; color:#3730a3;">'
            f'{self._esc(content)}</div>'
        )
        self._step_close()

    def log_llm_failure(self, label: str, prompt: str, raw_outputs: list[str],
                        error: str, retries: int):
        """记录 LLM 调用失败（展示完整 prompt 和每次重试的原始输出）。"""
        self._step_open(f"❌ LLM 调用失败: {label}", "step-custom")
        self._collapsible(f"Prompt ({len(prompt)}字)", prompt)
        for i, raw in enumerate(raw_outputs, 1):
            self._collapsible(f"第{i}次原始输出", raw)
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#dc2626; background:#fef2f2; color:#991b1b;">'
            f'错误: {self._esc(str(error))}\n重试次数: {retries}</div>'
        )
        self._step_close()

    # ── 预定事件 ──

    def log_scheduled_event(self, operations: list[dict], source: str = ""):
        """记录预定事件操作（add/update/delete）。"""
        if not operations:
            return

        # 获取当前虚拟时间作为创建时间
        try:
            _st = load_state()
            created_at = _st.get("current_time", "")
        except Exception:
            created_at = ""

        label = f"预定事件操作 ({len(operations)}个)"
        if source:
            label += f" — 来源: {source}"
        self._step_open(label, "step-custom")
        lines = []
        if created_at:
            lines.append(f"🕐 创建时间: {created_at}")
        for op in operations:
            action = op.get("action", "?")
            desc = op.get("description", "")
            time = op.get("time", "")
            participants = ", ".join(op.get("participants", []))
            location = op.get("location", "")
            match = op.get("match", "")
            eid = op.get("id", "")

            icon = {"add": "➕", "update": "✏️", "delete": "🗑️"}.get(action, "❓")
            line = f"{icon} [{action}]"
            if desc:
                line += f" {desc}"
            if time:
                line += f" @ {time}"
            if location:
                line += f" 📍{location}"
            if participants:
                line += f" 👥{participants}"
            if eid:
                line += f" (id: {eid})"
            if match:
                line += f" (match: {match})"
            lines.append(line)
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#059669; background:#ecfdf5; color:#065f46;">'
            f'{self._esc(chr(10).join(lines))}</div>'
        )
        self._step_close()

    # ── LLM 统计 ──

    def record_llm_call(self, label: str, model: str, elapsed: float,
                        prompt_tokens: int, completion_tokens: int):
        """累积 LLM 调用统计（不生成可见步骤，仅累加数据）。"""
        self._llm_calls.append({
            "label": label,
            "model": model,
            "elapsed": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        })

    def _render_llm_summary(self) -> str:
        """渲染 LLM 统计汇总 HTML（含费用）。"""
        if not self._llm_calls:
            return ""

        # 加载价格配置
        try:
            pricing = load_config().get("model_pricing", {})
        except Exception:
            pricing = {}

        usd_to_cny = 7

        def _calc_cost(model: str, prompt_t: int, completion_t: int) -> float:
            """返回费用（人民币）。"""
            p = pricing.get(model, {})
            # 按次计费（美元，需转人民币）
            if "per_call" in p:
                return p["per_call"] * usd_to_cny
            input_price = p.get("input", 0)  # $/1M tokens
            output_price = p.get("output", 0)
            usd = (prompt_t * input_price + completion_t * output_price) / 1_000_000
            return usd * usd_to_cny

        # 汇总
        total_calls = len(self._llm_calls)
        total_elapsed = sum(c["elapsed"] for c in self._llm_calls)
        total_prompt = sum(c["prompt_tokens"] for c in self._llm_calls)
        total_completion = sum(c["completion_tokens"] for c in self._llm_calls)
        total_tokens = total_prompt + total_completion
        total_cost = sum(_calc_cost(c["model"], c["prompt_tokens"], c["completion_tokens"]) for c in self._llm_calls)

        # 按操作类型分组
        from collections import defaultdict
        by_label = defaultdict(lambda: {"count": 0, "elapsed": 0.0, "prompt": 0, "completion": 0, "cost": 0.0})
        for c in self._llm_calls:
            g = by_label[c["label"]]
            g["count"] += 1
            g["elapsed"] += c["elapsed"]
            g["prompt"] += c["prompt_tokens"]
            g["completion"] += c["completion_tokens"]
            g["cost"] += _calc_cost(c["model"], c["prompt_tokens"], c["completion_tokens"])

        # 构建 HTML
        cost_str = f" | 💰 ¥{total_cost:.4f}" if pricing else ""
        parts = []
        parts.append(f'<div class="step step-stats">')
        parts.append(f'<div class="step-header">')
        parts.append(f'<span class="num">📊</span> '
                     f'LLM 统计：{total_calls} 次调用 | '
                     f'{total_tokens:,} tokens (P:{total_prompt:,} + C:{total_completion:,}) | '
                     f'{total_elapsed:.1f}s{cost_str}')
        parts.append(f'</div><div class="step-body">')

        # 明细表格（可展开）
        cost_th = '<th style="text-align:right; padding:6px 10px;">费用</th>' if pricing else ""
        detail_rows = []
        for label, g in sorted(by_label.items(), key=lambda x: -x[1]["cost"] if pricing else -x[1]["prompt"] - x[1]["completion"]):
            total = g["prompt"] + g["completion"]
            cost_td = f'<td style="text-align:right">¥{g["cost"]:.4f}</td>' if pricing else ""
            detail_rows.append(
                f'<tr><td>{self._esc(label)}</td>'
                f'<td style="text-align:right">{g["count"]}</td>'
                f'<td style="text-align:right">{g["prompt"]:,}</td>'
                f'<td style="text-align:right">{g["completion"]:,}</td>'
                f'<td style="text-align:right"><b>{total:,}</b></td>'
                f'<td style="text-align:right">{g["elapsed"]:.1f}s</td>{cost_td}</tr>'
            )

        table = (
            '<table style="width:100%; border-collapse:collapse; font-size:13px; margin:8px 0;">'
            '<tr style="background:#f3f4f6; border-bottom:1px solid #e5e7eb;">'
            '<th style="text-align:left; padding:6px 10px;">操作类型</th>'
            '<th style="text-align:right; padding:6px 10px;">次数</th>'
            '<th style="text-align:right; padding:6px 10px;">Prompt</th>'
            '<th style="text-align:right; padding:6px 10px;">Completion</th>'
            '<th style="text-align:right; padding:6px 10px;">总 Token</th>'
            '<th style="text-align:right; padding:6px 10px;">耗时</th>'
            f'{cost_th}'
            '</tr>' + ''.join(detail_rows) + '</table>'
        )
        parts.append(f'<details><summary>按操作类型展开</summary>'
                     f'<div class="content" style="padding:4px 8px">{table}</div></details>')

        # 每次调用明细（可展开）
        cost_th2 = '<th style="text-align:right; padding:4px 8px;">费用</th>' if pricing else ""
        call_rows = []
        for i, c in enumerate(self._llm_calls, 1):
            c_cost = _calc_cost(c["model"], c["prompt_tokens"], c["completion_tokens"])
            cost_td2 = f'<td style="text-align:right">¥{c_cost:.4f}</td>' if pricing else ""
            call_rows.append(
                f'<tr><td>{i}</td>'
                f'<td>{self._esc(c["label"])}</td>'
                f'<td>{self._esc(c["model"])}</td>'
                f'<td style="text-align:right">{c["prompt_tokens"]:,}</td>'
                f'<td style="text-align:right">{c["completion_tokens"]:,}</td>'
                f'<td style="text-align:right">{c["total_tokens"]:,}</td>'
                f'<td style="text-align:right">{c["elapsed"]}s</td>{cost_td2}</tr>'
            )
        call_table = (
            '<table style="width:100%; border-collapse:collapse; font-size:12px; margin:8px 0;">'
            '<tr style="background:#f3f4f6; border-bottom:1px solid #e5e7eb;">'
            '<th style="padding:4px 8px;">#</th>'
            '<th style="text-align:left; padding:4px 8px;">操作</th>'
            '<th style="text-align:left; padding:4px 8px;">模型</th>'
            '<th style="text-align:right; padding:4px 8px;">P</th>'
            '<th style="text-align:right; padding:4px 8px;">C</th>'
            '<th style="text-align:right; padding:4px 8px;">Total</th>'
            '<th style="text-align:right; padding:4px 8px;">耗时</th>'
            f'{cost_th2}'
            '</tr>' + ''.join(call_rows) + '</table>'
        )
        parts.append(f'<details><summary>全部调用明细 ({total_calls}次)</summary>'
                     f'<div class="content" style="padding:4px 8px">{call_table}</div></details>')

        parts.append('</div></div>')
        return ''.join(parts)

    # ── 记忆检索 ──

    def log_memory_retrieval(self, character: str, total: int, injected: int,
                             tier1: int, retrieved: int, mode: str):
        """记录记忆检索过程。"""
        self._step_open(f"记忆检索: {character}", "step-retrieval")
        self._parts.append(
            f'<div class="highlight" style="border-left-color:#06b6d4; background:#ecfeff; color:#164e63;">'
            f'模式: {self._esc(mode)}\n'
            f'记忆总数: {total}\n'
            f'注入数: {injected} (Tier1最近: {tier1}, 检索: {retrieved})</div>'
        )
        self._step_close()
        # engine.log 摘要
        log("debug", f"记忆检索 [{character}]: 模式={mode}, 总数={total}, 注入={injected} (T1={tier1}, 检索={retrieved})")

    # ── Session 事件 ──

    def log_session_event(self, event_type: str, session_id: str, detail: str = ""):
        """记录 Session 归档/压缩等事件。"""
        self._step_open(f"Session {event_type}: {session_id}", "step-session")
        if detail:
            self._parts.append(
                f'<div class="highlight" style="border-left-color:#8b5cf6; background:#f5f3ff; color:#5b21b6;">'
                f'{self._esc(detail)}</div>'
            )
        self._step_close()
        # engine.log 摘要
        detail_short = f": {detail[:80]}" if detail else ""
        log("info", f"Session {event_type} [{session_id[:8]}]{detail_short}")

    def save(self):
        """保存 HTML 日志文件，并清理过旧的 turn 文件。"""
        import datetime as _dt
        end_time = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # LLM 统计汇总
        llm_summary = self._render_llm_summary()

        # LLM 统计写入 engine.log
        if self._llm_calls:
            total_calls = len(self._llm_calls)
            total_prompt = sum(c["prompt_tokens"] for c in self._llm_calls)
            total_completion = sum(c["completion_tokens"] for c in self._llm_calls)
            total_elapsed = sum(c["elapsed"] for c in self._llm_calls)
            log("info", f"LLM 统计: {total_calls} 次调用, "
                f"{total_prompt + total_completion:,} tokens "
                f"(P:{total_prompt:,} + C:{total_completion:,}), "
                f"{total_elapsed:.1f}s")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Turn #{self._turn_num}</title>
{self._CSS}
</head><body>
<div class="header">
<h1>Turn #{self._turn_num}</h1>
<div class="meta">
开始: {self.start_time} &nbsp;|&nbsp; 结束: {end_time} &nbsp;|&nbsp;
共 {self._step_count} 个步骤</div>
</div>
{''.join(self._parts)}
{llm_summary}
<div class="footer">Turn #{self._turn_num} 结束 &nbsp;|&nbsp;
{self.start_time} → {end_time} &nbsp;|&nbsp;
共 {self._step_count} 个步骤</div>
</body></html>"""

        path = TURN_LOG_DIR / f"turn_{self._turn_num:04d}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log("debug", f"Turn 日志已保存: {path.name}")

        # 清理旧 turn 文件（保留最近 200 个）
        _cleanup_old_turns(200)


def _cleanup_old_turns(keep: int = 200):
    """清理旧 turn 文件，只保留最近 keep 个。"""
    try:
        files = sorted(TURN_LOG_DIR.glob("turn_*.html"))
        if len(files) > keep:
            for f in files[:-keep]:
                f.unlink()
            log("debug", f"Turn 清理: 删除 {len(files) - keep} 个旧文件，保留 {keep} 个")
    except Exception as e:
        log("warning", f"Turn 清理失败: {e}")


def start_turn_logger() -> TurnLogger:
    """创建新的 TurnLogger 并设为当前活跃。"""
    global _active_turn_logger, _current_turn_num
    with _turn_logger_lock:
        # 取最大编号 + 1，避免删文件后编号冲突
        import re
        max_num = 0
        for f in TURN_LOG_DIR.glob("turn_*.html"):
            m = re.search(r"turn_(\d+)", f.stem)
            if m:
                max_num = max(max_num, int(m.group(1)))
        turn_num = max_num + 1
        _active_turn_logger = TurnLogger(turn_num)
        _current_turn_num = turn_num
        log("info", f"{'=' * 40} Turn #{turn_num:04d} 开始 {'=' * 40}")
        return _active_turn_logger


def get_turn_logger() -> "TurnLogger | None":
    """获取当前活跃的 TurnLogger（任何模块都可以调用）。"""
    return _active_turn_logger


def finish_turn_logger():
    """保存并清理当前的 TurnLogger。"""
    global _active_turn_logger, _current_turn_num
    with _turn_logger_lock:
        tl = _active_turn_logger
        turn_num = _current_turn_num
        _active_turn_logger = None
        _current_turn_num = 0
    if tl:
        try:
            tl.save()
            log("info", f"{'=' * 40} Turn #{turn_num:04d} 结束 {'=' * 40}")
        except Exception as e:
            log("warning", f"Turn 日志保存失败: {e}")


# 兼容旧接口
def log_session_detail(session_id: str, character: str, *,
                       system_prompt: str = "",
                       messages: list = None,
                       reply: str = ""):
    """兼容旧接口，转发到当前 TurnLogger。"""
    tl = get_turn_logger()
    if tl:
        try:
            tl.log_reply_generation(character, system_prompt, messages or [], reply)
        except Exception:
            pass


# ── 文件读写 ──────────────────────────────────────────────


def read_file(path: str | Path) -> str:
    """读取文本文件，不存在返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_file(path: str | Path, content: str):
    """写入文本文件。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_json(path: str | Path) -> dict | list:
    """读取 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data):
    """写入 JSON 文件。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# 线程锁：保护 state.json 和角色文件的并发读写
# 使用 RLock 支持重入（state_transaction 内部调用 load/save 也需获取锁）
_state_lock = threading.RLock()
_char_lock = threading.Lock()

# ── 世界状态 ────────────────────────────────────────


def load_state() -> dict:
    """加载世界状态（线程安全）。文件不存在则返回空字典。"""
    with _state_lock:
        if not STATE_PATH.exists():
            return {}
        return read_json(STATE_PATH)


def save_state(state: dict):
    """保存世界状态（线程安全）。"""
    with _state_lock:
        write_json(STATE_PATH, state)


# ── 临时角色 ────────────────────────────────────────

_temp_char_lock = threading.Lock()


def load_temp_characters() -> dict:
    """加载临时角色数据（线程安全）。文件不存在则返回空字典。"""
    with _temp_char_lock:
        if not TEMP_CHARACTERS_PATH.exists():
            return {}
        return read_json(TEMP_CHARACTERS_PATH)


def save_temp_characters(data: dict):
    """保存临时角色数据（线程安全）。"""
    with _temp_char_lock:
        write_json(TEMP_CHARACTERS_PATH, data)



def apply_temp_character_ops(ops: list, location: str, sub_location: str = "",
                             current_time: str = ""):
    """应用 DM 返回的临时角色操作列表。

    ops 格式：
    - {"action": "add", "name": "...", "description": "...", "state": "..."}
    - {"action": "update", "name": "...", "state": "..."}  (description 可选)
    - {"action": "remove", "name": "..."}
    """
    if not ops:
        return

    data = load_temp_characters()
    for op in ops:
        action = op.get("action", "")
        name = op.get("name", "").strip()
        if not name:
            continue

        if action == "add":
            data[name] = {
                "location": location,
                "sub_location": sub_location,
                "description": op.get("description", ""),
                "state": op.get("state", ""),
                "updated": current_time,
            }
            log("info", f"临时角色添加: {name} @ {location}")

        elif action == "update" and name in data:
            if "description" in op:
                data[name]["description"] = op["description"]
            if "state" in op:
                data[name]["state"] = op["state"]
            data[name]["updated"] = current_time
            log("info", f"临时角色更新: {name}")

        elif action == "remove" and name in data:
            del data[name]
            log("info", f"临时角色移除: {name}")

    save_temp_characters(data)


# ── 世界设定（Lore）──────────────────────────────────────

_lore_cache: dict | None = None


def load_lore() -> dict:
    """加载世界设定 lore.json，带缓存。文件不存在则返回空字典。"""
    global _lore_cache
    if _lore_cache is not None:
        return _lore_cache
    if LORE_PATH.exists():
        _lore_cache = read_json(LORE_PATH)
    else:
        _lore_cache = {}
    return _lore_cache


def reload_lore() -> dict:
    """强制重新加载 lore.json（存档切换后调用）。"""
    global _lore_cache
    _lore_cache = None
    return load_lore()


def format_lore_for_prompt(include_secrets: bool = False) -> str:
    """将 lore.json 格式化为可注入 prompt 的文本。

    Args:
        include_secrets: True = DM 视角（含 secret），False = NPC 视角（仅 public）。
    """
    lore = load_lore()
    if not lore:
        return ""

    parts = []

    # 世界概述
    premise = lore.get("world_premise", "")
    if premise:
        parts.append(f"【世界概述】\n{premise}")

    # 时代与科技
    era = lore.get("era", "")
    if era:
        parts.append(f"【时代背景】\n{era}")

    # 叙事基调
    tone = lore.get("tone", "")
    if tone:
        parts.append(f"【叙事基调】\n{tone}")

    # 术语表
    glossary = lore.get("glossary", {})
    if glossary:
        glossary_lines = []
        for term, info in glossary.items():
            if isinstance(info, dict):
                pub = info.get("public", "")
                sec = info.get("secret", "")
                if pub:
                    line = f"  {term}：{pub}"
                    if include_secrets and sec:
                        line += f"\n    [隐藏真相] {sec}"
                    glossary_lines.append(line)
            elif isinstance(info, str) and info:
                glossary_lines.append(f"  {term}：{info}")
        if glossary_lines:
            parts.append("【术语表】\n" + "\n".join(glossary_lines))

    return "\n\n".join(parts)


def state_transaction():
    """读-改-写事务的上下文管理器（线程安全）。

    用法::

        with state_transaction() as state:
            state["characters"]["A"]["emotion"] = "开心"
            # 退出 with 块时自动保存

    整个 with 块持有 _state_lock，保证不会被其他线程打断。
    """
    from contextlib import contextmanager

    @contextmanager
    def _txn():
        with _state_lock:
            if not STATE_PATH.exists():
                st = {}
            else:
                st = read_json(STATE_PATH)
            yield st
            write_json(STATE_PATH, st)

    return _txn()


# ── 角色文件读写 ──────────────────────────────────────────

SECTION_KEYS = ("public_base", "private_base", "public_dynamic", "private_dynamic")


def char_file_path(name: str) -> Path:
    """获取角色 .json 文件路径。"""
    return CHARACTERS_DIR / f"{name}.json"


def load_character(name: str) -> dict:
    """加载角色完整 JSON 数据（线程安全）。返回 {section: [entries]}。
    
    兼容字符串格式：如果某个 section 是纯文本字符串，
    自动转为 [{content, ttl, created}] 数组格式。
    """
    with _char_lock:
        path = char_file_path(name)
        if path.exists():
            data = read_json(path)
            # 兼容字符串格式的 sections
            for key in SECTION_KEYS:
                val = data.get(key)
                if isinstance(val, str):
                    if val.strip():
                        data[key] = [
                            {"content": line.lstrip("- "), "ttl": "永久", "created": ""}
                            for line in val.split("\n")
                            if line.strip()
                        ]
                    else:
                        data[key] = []
            return data
        return {k: [] for k in SECTION_KEYS}


def save_character(name: str, data: dict):
    """保存角色完整 JSON 数据（线程安全）。"""
    with _char_lock:
        write_json(char_file_path(name), data)


def parse_character_file(source) -> dict[str, str]:
    """将角色数据解析为四个部分的纯文本字典。

    兼容两种输入：
      - dict（JSON 数据）: 从 entries 中提取 content 拼成文本
      - str（旧 .md 文本）: 按标题解析（向后兼容）

    返回 {"public_base": "- xxx\\n- yyy", ...}
    """
    if isinstance(source, dict):
        result = {}
        for key in SECTION_KEYS:
            entries = source.get(key, [])
            if isinstance(entries, str):
                # 纯文本格式（手动编辑的存档）
                result[key] = entries
            elif isinstance(entries, list):
                lines = [f"- {e.get('text', '') or e.get('content', '')}" for e in entries if isinstance(e, dict) and (e.get("text") or e.get("content"))]
                result[key] = "\n".join(lines)
            else:
                result[key] = ""
        return result

    # 旧格式兼容（str）
    _TITLE_TO_KEY = {
        "## 公开设定": "public_base",
        "## 私密设定": "private_base",
        "## 公开动态": "public_dynamic",
        "## 私密动态": "private_dynamic",
    }
    result = {k: "" for k in SECTION_KEYS}
    current_key = None
    current_lines: list[str] = []

    for line in source.split("\n"):
        stripped = line.strip()
        if stripped in _TITLE_TO_KEY:
            if current_key:
                result[current_key] = "\n".join(current_lines).strip()
            current_key = _TITLE_TO_KEY[stripped]
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)

    if current_key:
        result[current_key] = "\n".join(current_lines).strip()

    return result


