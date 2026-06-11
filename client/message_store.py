"""
客户端本地消息存储模块 (JSON 文件)
保存会话列表、消息缓存、联系人列表
"""
import os
import json
import threading
from typing import Optional, List, Dict, Any


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

    def load_user_data(self, username: str) -> dict:
        """加载用户数据，返回包含 sessions, contacts, messages 的 dict"""
        filepath = self._user_file(username)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "username": username,
            "contacts": [],
            "sessions": [],
            "messages": []
        }

    def save_user_data(self, username: str, data: dict):
        """保存用户数据"""
        with self._lock:
            filepath = self._user_file(username)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def add_message(self, username: str, message: dict):
        """添加一条消息到本地存储"""
        data = self.load_user_data(username)
        data.setdefault("messages", []).append(message)
        # 最多保留最近 1000 条
        if len(data["messages"]) > 1000:
            data["messages"] = data["messages"][-1000:]
        self.save_user_data(username, data)

    def get_messages(self, username: str,
                     session_id: str = None,
                     limit: int = 50) -> List[dict]:
        """获取消息历史，可按会话过滤"""
        data = self.load_user_data(username)
        messages = data.get("messages", [])
        if session_id:
            messages = [
                m for m in messages
                if m.get("sender") == session_id or m.get("receiver") == session_id
            ]
        return messages[-limit:]

    def add_contact(self, username: str, contact: dict):
        """添加联系人"""
        data = self.load_user_data(username)
        contacts = data.setdefault("contacts", [])
        # 去重
        for i, c in enumerate(contacts):
            if c.get("username") == contact.get("username"):
                contacts[i] = contact
                self.save_user_data(username, data)
                return
        contacts.append(contact)
        self.save_user_data(username, data)

    def get_contacts(self, username: str) -> List[dict]:
        """获取联系人列表"""
        data = self.load_user_data(username)
        return data.get("contacts", [])

    def update_session(self, username: str, session: dict):
        """更新会话列表"""
        data = self.load_user_data(username)
        sessions = data.setdefault("sessions", [])
        for i, s in enumerate(sessions):
            if s.get("session_id") == session.get("session_id"):
                sessions[i] = session
                self.save_user_data(username, data)
                return
        sessions.append(session)
        self.save_user_data(username, data)

    # ── 按会话分文件存储 ──────────────────────────────────────────

    def _session_file(self, username: str, target_id: str) -> str:
        """历史消息文件名：history_<username>_<target_id>.json"""
        safe_target = target_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return os.path.join(self.storage_dir, f"history_{username}_{safe_target}.json")

    def save_message(self, msg: dict):
        """保存单条消息（按会话分文件存储）"""
        username = msg.get("_username", "")
        target_id = msg.get("target_id", "")
        if not username or not target_id:
            return
        filepath = self._session_file(username, target_id)
        with self._lock:
            history = []
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    history = json.load(f)
            history.append(msg)
            if len(history) > 1000:
                history = history[-1000:]
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

    def update_message_id(self, username: str, local_msg_id: str,
                          server_msg_id: str, timestamp=None,
                          status: str = "") -> bool:
        """
        将本地临时消息 ID 更新为服务端确认的 UUID。

        客户端发送消息时只能先生成本地临时 ID；服务端存库后会在 ACK 中返回
        真正可用于撤回的 msg_id。本方法负责把这两个 ID 对齐。
        """
        data = self.load_user_data(username)
        changed = False
        if not str(local_msg_id) or not str(server_msg_id):
            return False
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
            self.save_user_data(username, data)
        return changed

    def mark_recalled(self, username: str, msg_id: str) -> bool:
        """按服务端 msg_id 将本地消息标记为已撤回。"""
        data = self.load_user_data(username)
        changed = False
        if not str(msg_id):
            return False
        for msg in data.get("messages", []):
            if self._match_msg_id(msg, msg_id):
                msg["is_recalled"] = True
                msg["content"] = "[已撤回]"
                changed = True
        if changed:
            self.save_user_data(username, data)
        return changed

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
            with open(filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
        return history[-limit:]
