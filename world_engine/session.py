"""Session 管理 — 对话历史的创建、消息追加、归档。"""
import json
import time
from pathlib import Path
from typing import Optional

from .utils import (
    log, load_state, save_state,
    ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR,
    read_json, write_json,
)


class Session:
    """一个 Session = 谁和谁在互动的对话框。"""

    def __init__(self, data: dict):
        self.data = data

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def participants(self) -> list[str]:
        return self.data["participants"]

    @property
    def location(self) -> str:
        return self.data["location"]

    @property
    def session_type(self) -> str:
        return self.data.get("type", "face-to-face")

    @property
    def messages(self) -> list[dict]:
        return self.data["messages"]

    @property
    def start_time(self) -> str:
        return self.data["start_time"]

    def add_message(self, speaker: str, text: str, vtime: str, msg_type: str = "dialogue",
                    visible_to: list[str] | None = None, redacted_text: str | None = None):
        """追加一条消息。

        Args:
            visible_to: 可选，能看到完整内容的角色名列表。
                        None = 所有人可见（默认）。非空 = 仅列出的角色能看到原文。
            redacted_text: 可选，不在 visible_to 中的角色看到的脱敏版本。
                           为空字符串时，不可见角色完全跳过此消息。
        """
        msg = {
            "time": vtime,
            "speaker": speaker,
            "text": text,
            "type": msg_type,
        }
        if visible_to is not None:
            msg["visible_to"] = visible_to
        if redacted_text is not None:
            msg["redacted_text"] = redacted_text
        self.data["messages"].append(msg)

    def add_participant(self, name: str):
        """加入参与者。"""
        if name not in self.data["participants"]:
            self.data["participants"].append(name)

    def remove_participant(self, name: str):
        """移除参与者。"""
        if name in self.data["participants"]:
            self.data["participants"].remove(name)

    def get_history_for(self, character_name: str) -> list[dict]:
        """获取适合传给 LLM 的对话历史。

        将 session messages 转换为 OpenAI 格式：
        - speaker == character_name → role: assistant
        - speaker == "system" → role: system
        - 其他 speaker → role: user, 前缀角色名

        支持消息级可见性过滤：
        - 有 visible_to 且角色不在列表中 → 使用 redacted_text 替代
        - redacted_text 为空 → 完全跳过该消息

        如果存在压缩摘要，则用摘要替代旧消息以节省 token。
        """
        history = []

        # 如果有压缩摘要，先加入摘要，只返回摘要之后的消息
        summary = self.data.get("summary")
        summary_up_to = self.data.get("summary_up_to", 0)

        if summary and summary_up_to > 0:
            history.append({"role": "system", "content": f"[之前的对话摘要] {summary}"})
            messages = self.messages[summary_up_to:]
        else:
            messages = self.messages

        for msg in messages:
            speaker = msg["speaker"]
            text = msg["text"]
            msg_type = msg.get("type", "dialogue")

            # 可见性过滤
            visible_to = msg.get("visible_to")
            if visible_to and character_name not in visible_to:
                # 此角色无法看到完整内容
                redacted = msg.get("redacted_text", "")
                if not redacted:
                    continue  # 完全私密的动作，跳过
                text = redacted

            if msg_type == "system":
                history.append({"role": "system", "content": text})
            elif speaker == character_name:
                history.append({"role": "assistant", "content": text})
            else:
                # 其他角色 or 玩家 → user 消息，始终前缀角色名
                history.append({"role": "user", "content": f"[{speaker}] {text}"})
        return history

    def compress_if_needed(self):
        """如果消息超过 30 条，生成摘要供 AI 使用（保留原始消息）。

        摘要存储在 data["summary"] 和 data["summary_up_to"] 中，
        原始 messages 数组不会被修改，确保 dashboard 和文件中可以完整查看。
        """
        # 距离上次摘要之后的新消息不足 30 条，跳过
        summary_up_to = self.data.get("summary_up_to", 0)
        new_msg_count = len(self.messages) - summary_up_to
        if new_msg_count < 30:
            return

        from .llm import chat_json

        # 压缩到只保留最近 10 条之前的所有消息
        cutoff = len(self.messages) - 10
        old_msgs = self.messages[:cutoff]

        dialogue = "\n".join(
            f"[{m['time']}] {m['speaker']}: {m['text'][:100]}" for m in old_msgs
        )

        from .utils import read_file, PROMPTS_DIR
        template = read_file(PROMPTS_DIR / "session_compress.md")
        prompt = template.format(
            participants="、".join(self.participants),
            dialogue=dialogue[:3000],
        )

        try:
            result = chat_json([{"role": "user", "content": prompt}], label="Session压缩")
            summary = result.get("summary", "（旧对话已压缩）")
            log("info", f"Session {self.id} 压缩: {cutoff} 条消息 → 摘要（原始消息已保留）")

            self.data["summary"] = summary
            self.data["summary_up_to"] = cutoff

            from .utils import get_turn_logger
            tl = get_turn_logger()
            if tl:
                tl.log_session_event("压缩", self.id, f"{cutoff} 条消息 → 摘要")
        except Exception as e:
            log("warning", f"Session 压缩失败: {e}")

    def save(self):
        """保存到 active 目录。"""
        self.compress_if_needed()
        path = ACTIVE_SESSIONS_DIR / f"{self.id}.json"
        write_json(path, self.data)

    def archive(self):
        """归档（从 active 移到 archive）。"""
        active_path = ACTIVE_SESSIONS_DIR / f"{self.id}.json"
        archive_path = ARCHIVE_SESSIONS_DIR / f"{self.id}.json"

        if not self.data.get("end_time"):
            try:
                state = load_state()
                self.data["end_time"] = state.get("current_time", "")
            except Exception:
                self.data["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(archive_path, self.data)

        if active_path.exists():
            active_path.unlink()

        log("info", f"Session {self.id} 已归档")

        from .utils import get_turn_logger
        tl = get_turn_logger()
        if tl:
            participants = ', '.join(self.participants)
            tl.log_session_event("归档", self.id, f"参与者: {participants}\n类型: {self.session_type}")


class SessionManager:
    """Session 管理器。"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._load_active()

    def _load_active(self):
        """加载所有活跃 session。"""
        for path in ACTIVE_SESSIONS_DIR.glob("*.json"):
            try:
                data = read_json(path)
                session = Session(data)
                self._sessions[session.id] = session
            except Exception as e:
                log("warning", f"加载 session 失败 {path}: {e}")

        log("info", f"加载了 {len(self._sessions)} 个活跃 session")

    def _next_id(self) -> str:
        """生成下一个 session ID，基于现有文件中的最大序号 +1。"""
        max_id = 0
        for d in (ACTIVE_SESSIONS_DIR, ARCHIVE_SESSIONS_DIR):
            if not d.exists():
                continue
            for path in d.glob("s_*.json"):
                try:
                    num = int(path.stem.split("_", 1)[1])
                    if num > max_id:
                        max_id = num
                except (ValueError, IndexError):
                    pass
        return f"s_{max_id + 1}"

    def create(
        self,
        participants: list[str],
        location: str,
        vtime: str,
        session_type: str = "face-to-face",
    ) -> Session:
        """创建新 session。"""
        sid = self._next_id()
        data = {
            "id": sid,
            "participants": participants,
            "location": location,
            "type": session_type,
            "start_time": vtime,
            "end_time": None,
            "messages": [],
        }
        session = Session(data)
        session.save()
        self._sessions[sid] = session
        log("info", f"创建 Session {sid}: {participants} @ {location} ({session_type})")
        return session

    def get(self, session_id: str) -> Optional[Session]:
        """获取 session。"""
        return self._sessions.get(session_id)

    def find_for_user(self, user_name: str, location: str) -> Optional[Session]:
        """找到用户在指定地点的 face-to-face session。"""
        for session in self._sessions.values():
            if (
                user_name in session.participants
                and session.location == location
                and session.session_type == "face-to-face"
            ):
                return session
        return None

    def find_sessions_for(self, character_name: str) -> list[Session]:
        """找到角色参与的所有活跃 session。"""
        return [s for s in self._sessions.values() if character_name in s.participants]

    def active_sessions(self) -> list[Session]:
        """获取所有活跃 session。"""
        return list(self._sessions.values())

    def close(self, session_id: str):
        """关闭并归档 session。"""
        session = self._sessions.pop(session_id, None)
        if session:
            session.archive()


# 全局单例
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局 SessionManager 实例。"""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


def reload_session_manager():
    """重置 SessionManager 单例，重新从磁盘加载活跃 sessions。"""
    global _manager
    _manager = SessionManager()
    log("info", f"SessionManager 已重新加载（{len(_manager._sessions)} 个活跃 session）")
