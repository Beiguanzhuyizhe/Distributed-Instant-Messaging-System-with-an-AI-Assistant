"""
客户端本地消息存储模块 (JSON 文件)
保存会话列表、消息缓存、联系人列表
"""
import os
import json
import threading
import time
import uuid
from typing import List


class MessageStore:
    """本地消息存储，使用 JSON 文件"""

    def __init__(self, storage_dir: str = None):
        self.storage_dir = storage_dir or os.path.join(
            os.path.dirname(__file__), "data"
        )
        os.makedirs(self.storage_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _user_file(self, username: str) -> str:
        return os.path.join(self.storage_dir, f"{username}.json")

    def _empty_user_data(self, username: str) -> dict:
        return {
            "username": username,
            "contacts": [],
            "sessions": [],
            "messages": []
        }

    def _load_user_data_unlocked(self, username: str) -> dict:
        filepath = self._user_file(username)
        if not os.path.exists(filepath):
            return self._empty_user_data(username)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            # JSON 文件可能因为异常退出留下半截内容。备份坏文件后重置，
            # 避免整个客户端因一条损坏历史记录无法启动。
            backup = f"{filepath}.corrupt.{int(time.time())}.{uuid.uuid4().hex[:8]}"
            self._backup_bad_json_unlocked(filepath, backup)
            data = self._empty_user_data(username)
            self._save_user_data_unlocked(username, data)
            return data
        data.setdefault("username", username)
        data.setdefault("contacts", [])
        data.setdefault("sessions", [])
        data.setdefault("messages", [])
        return data

    def _save_user_data_unlocked(self, username: str, data: dict):
        filepath = self._user_file(username)
        tmp_path = f"{filepath}.tmp.{uuid.uuid4().hex}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        self._replace_file_unlocked(tmp_path, filepath)

    @staticmethod
    def _replace_file_unlocked(tmp_path: str, filepath: str):
        try:
            os.replace(tmp_path, filepath)
            return
        except PermissionError:
            # 某些 Windows 同步目录/安全软件会拒绝 os.replace，但允许普通写入。
            # 此时降级为复制临时文件内容到目标文件，优先保证聊天记录不丢。
            with open(tmp_path, "rb") as src, open(filepath, "wb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _backup_bad_json_unlocked(filepath: str, backup: str):
        try:
            os.replace(filepath, backup)
            return
        except PermissionError:
            with open(filepath, "rb") as src, open(backup, "wb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            try:
                os.remove(filepath)
            except (FileNotFoundError, PermissionError):
                pass

    def load_user_data(self, username: str) -> dict:
        """加载用户数据，返回包含 sessions, contacts, messages 的 dict"""
        with self._lock:
            return self._load_user_data_unlocked(username)

    def save_user_data(self, username: str, data: dict):
        """保存用户数据"""
        with self._lock:
            self._save_user_data_unlocked(username, data)

    def add_message(self, username: str, message: dict):
        """添加一条消息到本地存储"""
        with self._lock:
            data = self._load_user_data_unlocked(username)
            data.setdefault("messages", []).append(message)
            # 最多保留最近 1000 条
            if len(data["messages"]) > 1000:
                data["messages"] = data["messages"][-1000:]
            self._save_user_data_unlocked(username, data)

    def get_messages(self, username: str,
                     session_id: str = None,
                     limit: int = 50) -> List[dict]:
        """获取消息历史，可按会话过滤"""
        data = self.load_user_data(username)
        messages = data.get("messages", [])
        if session_id:
            messages = [
                m for m in messages
                if self._message_matches_session(m, session_id)
            ]
        return messages[-limit:]

    def add_contact(self, username: str, contact: dict):
        """添加联系人"""
        with self._lock:
            data = self._load_user_data_unlocked(username)
            contacts = data.setdefault("contacts", [])
            # 去重
            for i, c in enumerate(contacts):
                if c.get("username") == contact.get("username"):
                    contacts[i] = contact
                    self._save_user_data_unlocked(username, data)
                    return
            contacts.append(contact)
            self._save_user_data_unlocked(username, data)

    def get_contacts(self, username: str) -> List[dict]:
        """获取联系人列表"""
        data = self.load_user_data(username)
        return data.get("contacts", [])

    def update_session(self, username: str, session: dict):
        """更新会话列表"""
        with self._lock:
            data = self._load_user_data_unlocked(username)
            sessions = data.setdefault("sessions", [])
            for i, s in enumerate(sessions):
                if s.get("session_id") == session.get("session_id"):
                    sessions[i] = session
                    self._save_user_data_unlocked(username, data)
                    return
            sessions.append(session)
            self._save_user_data_unlocked(username, data)

    # ── 按会话分文件存储 ──────────────────────────────────────────

    def _session_file(self, username: str, target_id: str) -> str:
        """历史消息文件名：history_<username>_<target_id>.json"""
        safe_target = target_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return os.path.join(self.storage_dir, f"history_{username}_{safe_target}.json")

    def save_message(self, msg: dict):
        """保存单条消息（按会话分文件存储）"""
        username = msg.get("_username", "")
        target_id = msg.get("chat_key") or msg.get("related_target") or msg.get("target_id", "")
        if not username or not target_id:
            return
        filepath = self._session_file(username, target_id)
        with self._lock:
            history = []
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except json.JSONDecodeError:
                    backup = f"{filepath}.corrupt.{int(time.time())}.{uuid.uuid4().hex[:8]}"
                    self._backup_bad_json_unlocked(filepath, backup)
                    history = []
            history.append(msg)
            if len(history) > 1000:
                history = history[-1000:]
            tmp_path = f"{filepath}.tmp.{uuid.uuid4().hex}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            self._replace_file_unlocked(tmp_path, filepath)

    def update_message_id(self, username: str, local_msg_id: str,
                          server_msg_id: str, timestamp=None,
                          status: str = "") -> bool:
        """
        将本地临时消息 ID 更新为服务端确认的 UUID。

        客户端发送消息时只能先生成本地临时 ID；服务端存库后会在 ACK 中返回
        真正可用于撤回的 msg_id。本方法负责把这两个 ID 对齐。
        """
        if not str(local_msg_id) or not str(server_msg_id):
            return False
        with self._lock:
            data = self._load_user_data_unlocked(username)
            changed = False
            for msg in data.get("messages", []):
                if self._match_msg_id(msg, local_msg_id):
                    msg["server_msg_id"] = str(server_msg_id)
                    msg["msg_id"] = str(server_msg_id)
                    if timestamp is not None:
                        msg["timestamp"] = timestamp
                    if status:
                        msg["status"] = status
                    changed = True
                    break
            if changed:
                self._save_user_data_unlocked(username, data)
            return changed

    def mark_recalled(self, username: str, msg_id: str) -> bool:
        """按服务端 msg_id 将本地消息标记为已撤回。"""
        if not str(msg_id):
            return False
        with self._lock:
            data = self._load_user_data_unlocked(username)
            changed = False
            for msg in data.get("messages", []):
                if self._match_msg_id(msg, msg_id):
                    msg["is_recalled"] = True
                    msg["content"] = "[已撤回]"
                    changed = True
            if changed:
                self._save_user_data_unlocked(username, data)
            return changed

    @staticmethod
    def chat_key(target_type: str, target_id) -> str:
        return f"{target_type}:{target_id}"

    @classmethod
    def _message_matches_session(cls, msg: dict, session_id: str) -> bool:
        target = str(session_id)
        if not target:
            return False

        if str(msg.get("chat_key", "")) == target:
            return True

        if target.startswith("private:"):
            peer = target.split(":", 1)[1]
            return cls._message_matches_private_peer(msg, peer)

        if target.startswith("group:"):
            group_id = target.split(":", 1)[1]
            return cls._message_matches_group(msg, group_id)

        return (
            str(msg.get("sender", "")) == target
            or str(msg.get("receiver", "")) == target
            or str(msg.get("target_id", "")) == target
            or str(msg.get("related_target", "")) == target
        )

    @staticmethod
    def _message_matches_private_peer(msg: dict, peer: str) -> bool:
        if msg.get("type") not in (None, "private"):
            return False
        if msg.get("related_type") and msg.get("related_type") != "private":
            return False

        # 新格式已经有稳定会话键；如果键不匹配，不再继续用 from_id/receiver_id
        # 兜底，否则入站消息容易被误归到“自己和自己”的私聊。
        if msg.get("chat_key"):
            return False
        if msg.get("related_target") not in (None, ""):
            return str(msg.get("related_target")) == peer
        if msg.get("target_id") not in (None, ""):
            return str(msg.get("target_id")) == peer

        from_id = msg.get("from_id")
        receiver_id = msg.get("receiver_id", msg.get("to_id"))
        if from_id not in (None, "") and receiver_id not in (None, ""):
            if str(from_id) == str(receiver_id):
                return str(from_id) == peer
            if msg.get("sender") and not msg.get("receiver"):
                return str(from_id) == peer
            return str(from_id) == peer or (not msg.get("sender") and str(receiver_id) == peer)

        id_fields = ("from_id", "receiver_id", "to_id")
        if any(str(msg.get(field, "")) == peer for field in id_fields):
            return True
        name_fields = ("sender", "receiver")
        return any(str(msg.get(field, "")) == peer for field in name_fields)

    @staticmethod
    def _message_matches_group(msg: dict, group_id: str) -> bool:
        if msg.get("type") not in (None, "group"):
            return False
        if msg.get("related_type") and msg.get("related_type") != "group":
            return False
        return any(
            str(msg.get(field, "")) == group_id
            for field in ("related_target", "target_id", "group_id")
        )

    @staticmethod
    def _match_msg_id(msg: dict, msg_id: str) -> bool:
        """同时兼容临时 ID、服务端 ID 和旧字段。"""
        target = str(msg_id)
        if not target:
            return False
        return any(
            str(msg.get(key, "")) == target
            for key in ("msg_id", "local_msg_id", "server_msg_id")
        )

    def get_history(self, target_type: str, target_id: str,
                    username: str = "", limit: int = 50) -> list:
        """获取指定会话的历史消息"""
        if not username:
            return []
        filepath = self._session_file(username, target_id)
        if not os.path.exists(filepath):
            return []
        with self._lock:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except json.JSONDecodeError:
                backup = f"{filepath}.corrupt.{int(time.time())}.{uuid.uuid4().hex[:8]}"
                self._backup_bad_json_unlocked(filepath, backup)
                return []
        return history[-limit:]
