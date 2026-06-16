"""
WebView 桥接层 — Python 与 JavaScript 双向通信中枢

消息流向：
  JS → pywebview.api.method() → WebBridge.method() → handler.send_*() → server
  server → handler callback → WebBridge._on_*() → evaluate_js() → JS 渲染

所有暴露给 JS 的方法在类上定义，pywebview 通过 js_api 自动注册。
"""

import json
import os
import threading
import time
import uuid

import webview

from protocol import MessageType


def _now() -> int:
    return int(time.time())


class WebBridge:
    """Python 侧桥接：接收 JS 命令、维护状态、推送服务器事件到 JS"""

    AI_USERNAME = "AI Assistant"
    AI_USER_ID = -1

    def __init__(self, conn, handler, store, p2p, download_dir):
        self.conn = conn
        self.handler = handler
        self.store = store
        self.p2p = p2p
        self._download_dir = download_dir
        os.makedirs(self._download_dir, exist_ok=True)

        # --- 会话状态 (与旧 gui.py 一致) ---
        self._user_id = None
        self._username = None
        self._password_hash = None
        self._logged_in = False
        self._online_users = {}  # name -> id
        self._groups = {}  # gid -> name
        self._available_groups = {}  # gid -> {id, name, member_count, joined}
        self._messages = []
        self._current_target = None
        self._current_target_id = None
        self._chat_type = "private"
        self._pending_acks = {}
        self._last_sent_msg_id = None
        self._dl_state = {}
        self._pending_ai_context = {}
        self._pending_recall_context = None
        self._pending_group_leave = {}
        self._pending_file_uploads = {}

        self._register_callbacks()

    # =============================================================
    # 回调注册
    # =============================================================

    def _register_callbacks(self):
        reg = self.handler.register
        reg(MessageType.LOGIN_RESP, self._on_login_resp)
        reg(MessageType.REGISTER_RESP, self._on_register_resp)
        reg(MessageType.PRIVATE_MSG, self._on_private_msg)
        reg(MessageType.GROUP_MSG, self._on_group_msg)
        reg(MessageType.STATUS_UPDATE, self._on_status_update)
        reg(MessageType.AI_RESP, self._on_ai_resp)
        reg(MessageType.CONTENT_WARN, self._on_content_warn)
        reg(MessageType.ERROR, self._on_error)
        reg(MessageType.MSG_RECALL, self._on_recall)
        reg(MessageType.HISTORY_RESP, self._on_history)
        reg(MessageType.ONLINE_USERS, self._on_online_users)
        reg(MessageType.GROUP_CREATE, self._on_group_create_resp)
        reg(MessageType.GROUP_JOIN, self._on_group_join_resp)
        reg(MessageType.GROUP_LEAVE, self._on_group_leave_resp)
        reg(MessageType.FILE_INIT, self._on_file_init)
        reg(MessageType.FILE_ACK, self._on_file_ack)

        self.conn.on_disconnected(self._on_disconnected)
        self.conn.on_connected(self._on_reconnected)

        self.p2p.register_message_handler(self.handler)

    # =============================================================
    # 向 JS 推送事件
    # =============================================================

    def _push(self, event_type: str, data: dict):
        """向 JS 发送事件（线程安全 — pywebview evaluate_js 可在任意线程调用）"""
        try:
            payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
            # 寻找活动窗口
            for w in webview.windows:
                if w:
                    w.evaluate_js(f"window.__pyEvent({payload})")
                    return
        except Exception:
            pass  # WebView 尚未就绪时静默忽略

    def _push_msg(self, msg: dict):
        """推送一条消息对象到 JS（统一格式）"""
        if not any(msg.get(key) for key in ("msg_id", "local_msg_id", "server_msg_id", "event_id")):
            msg["event_id"] = f"evt-{uuid.uuid4()}"
        self._push("new_message", msg)

    def _sync_group_state(self, payload: dict):
        """同步服务端返回的群组状态：groups 是已加入群，available_groups 是可加入群。"""
        if not isinstance(payload, dict):
            return
        groups = payload.get("groups")
        if isinstance(groups, dict):
            self._groups = {str(k): v for k, v in groups.items()}
        available = payload.get("available_groups")
        if isinstance(available, dict):
            self._available_groups = {str(k): v for k, v in available.items()}

    def _group_payload(self) -> dict:
        return {
            "groups": {k: v for k, v in getattr(self, "_groups", {}).items()},
            "available_groups": {k: v for k, v in getattr(self, "_available_groups", {}).items()},
        }

    def _connected_or_warn(self) -> bool:
        conn = getattr(self, "conn", None)
        if conn is None or conn.is_connected:
            return True
        msg = {
            "type": "system",
            "content": "Cannot send while disconnected. Waiting for reconnect...",
            "timestamp": _now(),
        }
        msg.update(self._current_chat_context())
        self._push_msg(msg)
        self._push("connection_status", {"status": "disconnected"})
        return False

    # =============================================================
    # 状态管理（与旧 gui.py 逻辑一致）
    # =============================================================

    def _append_and_store(self, msg: dict):
        self._messages.append(msg)
        if self._username:
            self.store.add_message(self._username, msg)

    def _remember_pending(self, send_result: dict, msg: dict):
        seq = send_result.get("seq") if send_result else None
        if seq is not None:
            self._pending_acks[seq] = msg

    def _context_from_message(self, msg: dict) -> dict:
        context = {}
        for key in ("related_type", "related_target", "chat_key", "group_id"):
            value = msg.get(key)
            if value not in (None, ""):
                context[key] = value
        return context or self._current_chat_context()

    def _apply_message_ack(self, seq: int, payload: dict):
        msg = self._pending_acks.pop(seq, None)
        if not msg:
            return
        local_msg_id = str(msg.get("local_msg_id", msg.get("msg_id", "")))
        msg["status"] = payload.get("status", "ack")
        if payload.get("timestamp"):
            msg["timestamp"] = payload["timestamp"]
        server_msg_id = str(payload.get("msg_id", "") or "")
        if not server_msg_id:
            if self._username:
                self.store.update_message_status(
                    self._username, local_msg_id, msg["status"],
                )
            ack_event = {
                "local_msg_id": local_msg_id,
                "msg_id": local_msg_id,
                "timestamp": msg.get("timestamp", _now()),
                "status": msg["status"],
            }
            if payload.get("error"):
                ack_event["error"] = payload["error"]
                system_msg = {
                    "type": "system",
                    "content": f"Message rejected: {payload['error']}",
                    "timestamp": _now(),
                }
                system_msg.update(self._context_from_message(msg))
                self._push_msg(system_msg)
            self._push("message_acked", ack_event)
            return
        old_msg_id = str(msg.get("msg_id", ""))
        msg["server_msg_id"] = server_msg_id
        msg["msg_id"] = server_msg_id
        self._last_sent_msg_id = server_msg_id
        if self._username:
            self.store.update_message_id(
                self._username, local_msg_id, server_msg_id,
                timestamp=msg.get("timestamp"), status=msg.get("status", ""),
            )
        # 通知 JS 消息已确认（更新 msg_id 和 status）
        self._push("message_acked", {
            "local_msg_id": old_msg_id or local_msg_id,
            "msg_id": server_msg_id,
            "timestamp": msg.get("timestamp", _now()),
        })

    def _mark_recalled(self, msg_id: str):
        target = str(msg_id)
        if not target:
            return
        for msg in self._messages:
            ids = {
                str(msg.get("msg_id", "")),
                str(msg.get("local_msg_id", "")),
                str(msg.get("server_msg_id", "")),
            }
            if target in ids:
                msg["is_recalled"] = True
                msg["content"] = "[已撤回]"
        if self._username:
            self.store.mark_recalled(self._username, target)

    def _history_sender(self, msg: dict) -> str:
        sender_id = msg.get("sender_id", msg.get("from_id"))
        if sender_id == self._user_id:
            return self._username or "You"
        for name, uid in self._online_users.items():
            if uid == sender_id:
                return name
        return f"User#{sender_id}" if sender_id else msg.get("sender", "unknown")

    @staticmethod
    def _chat_key(chat_type: str, target_id) -> str:
        return f"{chat_type}:{target_id}"

    @classmethod
    def _with_chat_context(cls, msg: dict, chat_type: str, target_id) -> dict:
        """给消息补上稳定会话键，避免前端再用显示名/发送者做模糊匹配。"""
        target = str(target_id)
        msg["related_type"] = chat_type
        msg["related_target"] = target
        msg["chat_key"] = cls._chat_key(chat_type, target)
        return msg

    def _current_chat_context(self) -> dict:
        if self._chat_type == "private":
            target = self._current_target_id
        elif self._chat_type == "ai":
            target = self.AI_USERNAME
        else:
            target = self._current_target
        if target is None or target == "":
            return {}
        return {
            "related_type": self._chat_type,
            "related_target": str(target),
            "chat_key": self._chat_key(self._chat_type, target),
        }

    # =============================================================
    # 消息回调（在后台线程执行）
    # =============================================================

    def _on_login_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._user_id = payload.get("user_id")
            self._username = payload.get("username", self._username)
            self._logged_in = True
            self._sync_group_state(payload)
            # 立即将自己加入在线列表（服务器可能还未返回 ONLINE_USERS）
            if self._username and self._user_id:
                self._online_users[self._username] = self._user_id
            # 将当前已有状态一起推送给 JS
            self._push("login_success", {
                "user_id": self._user_id,
                "username": self._username,
                "online_users": {k: v for k, v in self._online_users.items()},
                "connected": getattr(getattr(self, "conn", None), "is_connected", True),
                **self._group_payload(),
            })
            # 主动请求在线用户列表（响应会通过 _on_online_users 补充其他用户）
            self.handler.request_online_users()
        else:
            msg = payload.get("error") or payload.get("message", "Login failed")
            self._push("login_error", {"error": msg})

    def _on_register_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._push("register_success", {"message": "Registration successful! Please login."})
        else:
            msg = payload.get("error") or payload.get("message", "Registration failed")
            self._push("register_error", {"error": msg})

    def _on_private_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        peer_id = payload.get("from_id", 0)
        msg = {
            "type": "private", "sender": sender,
            "receiver_id": payload.get("to_id", self._user_id),
            "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": peer_id,
            "target_id": peer_id,
        }
        self._with_chat_context(msg, "private", peer_id)
        self._append_and_store(msg)
        self._push_msg(msg)

    def _on_group_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        gid = str(payload.get("group_id", ""))
        msg = {
            "type": "group", "group_id": gid,
            "group_name": self._groups.get(gid, f"Group#{gid}"),
            "sender": sender, "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": payload.get("from_id", 0),
            "target_id": gid,
        }
        self._with_chat_context(msg, "group", gid)
        self._append_and_store(msg)
        self._push_msg(msg)

    def _on_status_update(self, msg_type, seq, payload):
        username = payload.get("username", "")
        uid = payload.get("user_id", 0)
        is_online = payload.get("is_online", False)
        if is_online:
            self._online_users[username] = uid
        else:
            self._online_users.pop(username, None)
        self._push("status_update", {
            "username": username,
            "user_id": uid,
            "is_online": is_online,
            "online_users": {k: v for k, v in self._online_users.items()},
            **self._group_payload(),
        })

    def _on_ai_resp(self, msg_type, seq, payload):
        content = payload.get("content", "")
        if content:
            # 创建 ai 类型消息，显示为 AI Assistant 发送的聊天消息
            msg = {
                "type": "ai",
                "sender": self.AI_USERNAME,
                "from_id": self.AI_USER_ID,
                "content": content,
                "timestamp": _now(),
            }
            group_id = payload.get("group_id")
            if group_id not in (None, "", 0, "0"):
                ctx = {
                    "related_type": "group",
                    "related_target": str(group_id),
                    "chat_key": self._chat_key("group", group_id),
                    "group_id": str(group_id),
                    "target_id": str(group_id),
                }
            else:
                pending = getattr(self, '_pending_ai_context', {})
                ctx = pending.pop(seq, {}) if isinstance(pending, dict) else {}
            if ctx:
                msg.update(ctx)
            self._append_and_store(msg)
            self._push_msg(msg)

    def _on_content_warn(self, msg_type, seq, payload):
        msg = {"type": "system", "content": f"[WARN] {payload.get('message', 'Content warning')}", "timestamp": _now()}
        msg.update(self._context_from_message(payload))
        self._push_msg(msg)

    def _on_error(self, msg_type, seq, payload):
        msg = {"type": "system", "content": f"[Error {payload.get('code', -1)}] {payload.get('message', '')}", "timestamp": _now()}
        msg.update(self._context_from_message(payload))
        self._push_msg(msg)

    def _on_recall(self, msg_type, seq, payload):
        ctx = getattr(self, '_pending_recall_context', None) or {}
        if payload.get("success") is False:
            err = payload.get("error") or payload.get("message", "recall failed")
            msg = {"type": "system", "content": f"Recall failed: {err}"}
            msg.update(ctx)
            self._push_msg(msg)
            return
        msg_id = str(payload.get("msg_id", ""))
        self._mark_recalled(msg_id)
        mid = msg_id[:8]
        self._push("message_recalled", {"msg_id": msg_id})
        msg = {"type": "system", "content": f"Message {mid}... was recalled"}
        msg.update(ctx)
        self._push_msg(msg)

    def _on_history(self, msg_type, seq, payload):
        history = payload.get("messages", [])
        formatted = []
        for m in history:
            kind = "group" if payload.get("type") == "group" or m.get("group_id") else "private"
            if kind == "group":
                target_id = str(m.get("group_id") or payload.get("target_id") or "")
            else:
                sender_id = m.get("sender_id", m.get("from_id", 0))
                receiver_id = m.get("receiver_id", m.get("to_id", 0))
                target_id = payload.get("target_id") or (
                    receiver_id if str(sender_id) == str(self._user_id) else sender_id
                )
            item = {
                "type": kind,
                "sender": self._history_sender(m),
                "content": "[已撤回]" if m.get("recalled") or m.get("is_recalled") else m.get("content", ""),
                "group_id": str(m.get("group_id") or (target_id if kind == "group" else "")),
                "timestamp": int(m.get("timestamp") or m.get("created_at") or 0),
                "msg_id": str(m.get("msg_id", "")),
                "from_id": m.get("sender_id", m.get("from_id", 0)),
                "receiver_id": m.get("receiver_id", m.get("to_id", 0)),
                "target_id": str(target_id),
            }
            self._with_chat_context(item, kind, target_id)
            formatted.append(item)
        self._messages.extend(formatted)
        self._push("history", {
            "type": payload.get("type", "private"),
            "target_id": payload.get("target_id"),
            "chat_key": self._chat_key(payload.get("type", "private"), payload.get("target_id")),
            "messages": formatted,
            "count": len(formatted),
        })

    def _on_online_users(self, msg_type, seq, payload):
        self._sync_group_state(payload)
        users = payload.get("users", [])
        self._online_users = {}
        for u in users:
            uid = u.get("id", 0)
            name = u.get("username", f"User#{uid}")
            self._online_users[name] = uid
        self._push("online_users", {
            "online_users": {k: v for k, v in self._online_users.items()},
            **self._group_payload(),
        })

    def _on_group_create_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", "")
            self._groups[gid] = name
            self._push("group_created", {
                "group_id": gid, "name": name,
                **self._group_payload(),
            })
            self._push_msg({"type": "system", "content": f"Created group #{gid} \"{name}\""})
            self.handler.request_online_users()
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Create group failed: {err}"})

    def _on_group_join_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", gid)
            self._groups[gid] = name
            self._push("group_joined", {
                "group_id": gid, "name": name,
                **self._group_payload(),
            })
            self._push_msg({"type": "system", "content": f"Joined group #{gid} \"{name}\""})
            self.handler.request_online_users()
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Join failed: {err}"})

    def _on_group_leave_resp(self, msg_type, seq, payload):
        gid = str(payload.get("group_id") or self._pending_group_leave.pop(seq, "") or "")
        if payload.get("success"):
            self._groups.pop(gid, None)
            self._push("group_left", {
                "group_id": gid,
                **self._group_payload(),
            })
            self._push_msg({"type": "system", "content": f"Left group {gid}"})
            self.handler.request_online_users()
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Leave failed: {err}"})

    def _on_file_init(self, msg_type, seq, payload):
        pending_uploads = getattr(self, "_pending_file_uploads", {})
        pending_upload = pending_uploads.pop(seq, None)
        if pending_upload is not None:
            filepath, file_id, filesize, filename, context = pending_upload
            if payload.get("success"):
                safe_filename = payload.get("filename") or filename
                threading.Thread(
                    target=self._send_file_worker,
                    args=(filepath, file_id, filesize, safe_filename, context),
                    daemon=True,
                ).start()
            else:
                event = {
                    "type": "system",
                    "content": "File send failed: " + str(payload.get("error") or "unknown error"),
                    "timestamp": _now(),
                }
                event.update(context)
                self._push_msg(event)
            return

        status = payload.get("status", "")
        if status != "completed":
            return
        file_id = payload.get("file_id", "")
        from_id = payload.get("from_id", 0)
        filename = payload.get("filename", "unknown")
        filesize = payload.get("filesize", 0)
        group_id = payload.get("group_id")
        sender = f"User#{from_id}"
        for name, uid in self._online_users.items():
            if uid == from_id:
                sender = name
                break
        if group_id not in (None, "", 0, "0"):
            context = {
                "related_type": "group",
                "related_target": str(group_id),
                "chat_key": payload.get("chat_key") or self._chat_key("group", group_id),
                "group_id": str(group_id),
            }
        else:
            context = {
                "related_type": "private",
                "related_target": str(from_id),
                "chat_key": payload.get("chat_key") or self._chat_key("private", from_id),
            }
        self._push("file_incoming", {
            "file_id": file_id,
            "filename": filename,
            "filesize": filesize,
            "sender": sender,
            "from_id": from_id,
            **context,
        })
        threading.Thread(target=self._gui_download_file,
                         args=(file_id, filename, filesize, context), daemon=True).start()

    def _on_file_ack(self, msg_type, seq, payload):
        file_id = payload.get("file_id", "")
        offset = payload.get("offset", 0)
        data_b64 = payload.get("data", "")
        if payload.get("success") is False or not data_b64:
            state = self._dl_state.get(file_id)
            if state:
                state["error"] = payload.get("error") or "Download failed"
                state["event"].set()
            return
        state = self._dl_state.get(file_id)
        if not state:
            return
        import base64
        data = base64.b64decode(data_b64)
        state["data"][offset] = data
        state["remaining"] -= len(data)
        if state["remaining"] <= 0:
            state["event"].set()

    def _gui_download_file(self, file_id, filename, filesize, context=None):
        CHUNK_SIZE = 64 * 1024
        filename = os.path.basename(str(filename or "unknown").replace("\\", "/")) or "unknown"
        dest = os.path.join(self._download_dir, filename)
        context = context or {
            "related_type": "private",
            "related_target": "",
            "chat_key": self._chat_key("private", ""),
        }
        if filesize == 0:
            try:
                os.makedirs(self._download_dir, exist_ok=True)
                with open(dest, "wb"):
                    pass
                event = {
                    "filename": filename, "filesize": filesize,
                    "success": True, "path": dest,
                }
                event.update(context)
                self._push("file_download_result", event)
            except Exception as e:
                event = {
                    "filename": filename, "success": False, "error": str(e),
                }
                event.update(context)
                self._push("file_download_result", event)
            return
        state = {"data": {}, "remaining": filesize,
                 "event": threading.Event(), "chunk_size": CHUNK_SIZE}
        self._dl_state[file_id] = state
        for offset in range(0, filesize, CHUNK_SIZE):
            self.handler.request_file_chunk(file_id, offset)
        if not state["event"].wait(timeout=30):
            event = {
                "filename": filename, "success": False,
                "error": "Download timed out",
            }
            event.update(context)
            self._push("file_download_result", event)
            self._dl_state.pop(file_id, None)
            return
        if state.get("error"):
            event = {
                "filename": filename, "success": False,
                "error": state["error"],
            }
            event.update(context)
            self._push("file_download_result", event)
            self._dl_state.pop(file_id, None)
            return
        try:
            os.makedirs(self._download_dir, exist_ok=True)
            with open(dest, "wb") as f:
                for offset in sorted(state["data"].keys()):
                    f.write(state["data"][offset])
            event = {
                "filename": filename, "filesize": filesize,
                "success": True, "path": dest,
            }
            event.update(context)
            self._push("file_download_result", event)
        except Exception as e:
            event = {
                "filename": filename, "success": False, "error": str(e),
            }
            event.update(context)
            self._push("file_download_result", event)
        self._dl_state.pop(file_id, None)

    def _on_disconnected(self):
        self._push("connection_status", {"status": "disconnected"})
        msg = {"type": "system", "content": "Disconnected from server. Reconnecting...", "timestamp": _now()}
        msg.update(self._current_chat_context())
        self._push_msg(msg)

    def _on_reconnected(self):
        self._push("connection_status", {"status": "reconnected"})
        msg = {"type": "system", "content": "Reconnected to server. Restoring session...", "timestamp": _now()}
        msg.update(self._current_chat_context())
        self._push_msg(msg)
        if self._username and self._password_hash:
            self.handler.send_login(self._username, self._password_hash)

    # =============================================================
    # JS → Python API (通过 pywebview js_api 暴露)
    # =============================================================

    def login(self, username: str, password: str) -> dict:
        """JS 调用：登录"""
        self._username = username
        self._password_hash = password
        self.handler.send_login(username, password)
        return {"ok": True}

    def register(self, username: str, password: str) -> dict:
        """JS 调用：注册"""
        self._password_hash = password
        self.handler.send_register(username, password)
        return {"ok": True}

    def send_private_msg(self, target_id: int, content: str) -> dict:
        """JS 调用：发送私聊消息"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        result = self.handler.send_private_msg(self._user_id, target_id, content)
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        msg = {
            "type": "private", "sender": self._username or "You",
            "receiver": None, "receiver_id": target_id, "target_id": target_id,
            "content": content, "local_msg_id": local_msg_id,
            "msg_id": local_msg_id, "timestamp": _now(),
            "status": "pending", "from_id": self._user_id,
        }
        self._with_chat_context(msg, "private", target_id)
        self._append_and_store(msg)
        self._remember_pending(result, msg)
        self._push_msg(msg)
        return {"ok": True, "msg": msg}

    def send_group_msg(self, group_id: int, content: str) -> dict:
        """JS 调用：发送群聊消息"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        result = self.handler.send_group_msg(self._user_id or 0, group_id, content)
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        gid = str(group_id)
        msg = {
            "type": "group", "group_id": gid,
            "group_name": self._groups.get(gid, f"Group#{gid}"),
            "sender": self._username or "You", "content": content,
            "local_msg_id": local_msg_id, "msg_id": local_msg_id,
            "timestamp": _now(), "status": "pending",
            "from_id": self._user_id, "target_id": gid,
        }
        self._with_chat_context(msg, "group", gid)
        self._append_and_store(msg)
        self._remember_pending(result, msg)
        self._push_msg(msg)
        return {"ok": True, "msg": msg}

    def send_ai_query(self, query: str, group_id: int = 0, context_msgs=None) -> dict:
        """JS 调用：发送 AI 查询"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        # 保存上下文，以便 AI 回复能关联到正确的聊天
        # 注意：AI Assistant 独立对话（chat_type=ai 时）不加 related_target，保持纯 AI 对话独立
        if self._chat_type == 'ai' or self._current_target == self.AI_USERNAME:
            ai_context = {
                "related_type": "ai",
                "related_target": self.AI_USERNAME,
                "chat_key": self._chat_key("ai", self.AI_USERNAME),
            }
        else:
            ai_context = {
                "related_type": self._chat_type,
                "related_target": str(self._current_target_id) if self._chat_type == 'private' else str(self._current_target),
                "chat_key": self._chat_key(
                    self._chat_type,
                    self._current_target_id if self._chat_type == 'private' else self._current_target,
                ),
            }
        # 携带会话上下文（最近对话历史）
        ctx_list = []
        if context_msgs and isinstance(context_msgs, list):
            for cm in context_msgs[-10:]:  # 最多携带最近10条
                role = "user" if cm.get("sender") != self.AI_USERNAME else "assistant"
                ctx_list.append({"role": role, "content": cm.get("content", "")})
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        result = self.handler.send_ai_query(self._user_id, group_id, query, context=ctx_list)
        seq = result.get("seq") if result else None
        if seq is not None:
            self._pending_ai_context[seq] = ai_context
        return {"ok": True}

    def request_history(self, target_type: str, target_id: int) -> dict:
        """JS 调用：请求历史消息"""
        self.handler.request_history(target_type, target_id)
        return {"ok": True}

    def request_online_users(self) -> dict:
        """JS 调用：请求在线用户列表"""
        self.handler.request_online_users()
        return {"ok": True}

    def group_create(self, name: str) -> dict:
        """JS 调用：创建群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        self.handler.group_create(name, self._user_id)
        return {"ok": True}

    def group_join(self, group_id: int) -> dict:
        """JS 调用：加入群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        self.handler.group_join(group_id, self._user_id)
        return {"ok": True}

    def group_leave(self, group_id: int) -> dict:
        """JS 调用：退出群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        if not self._connected_or_warn():
            return {"ok": False, "error": "Disconnected"}
        result = self.handler.group_leave(group_id, self._user_id)
        seq = result.get("seq") if result else None
        if seq is not None:
            self._pending_group_leave[seq] = str(group_id)
        return {"ok": True}

    def send_recall(self, msg_id: str) -> dict:
        """JS 调用：撤回消息"""
        self._pending_recall_context = {
            "related_type": self._chat_type,
            "related_target": str(self._current_target_id) if self._chat_type == 'private' else str(self._current_target),
            "chat_key": self._chat_key(
                self._chat_type,
                self._current_target_id if self._chat_type == 'private' else self._current_target,
            ),
        }
        self.handler.send_recall(msg_id)
        return {"ok": True}

    def _tk_file_dialog(self, title="Select a file"):
        """使用 tkinter filedialog 打开 Windows 原生文件选择对话框（同最原始代码）"""
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        try:
            filepath = filedialog.askopenfilename(title=title)
            return filepath if filepath else None
        finally:
            root.destroy()

    def select_and_send_file(self) -> dict:
        """JS 调用：打开文件选择对话框并发送文件（使用 tkinter 原生对话框）"""
        try:
            if not self._connected_or_warn():
                return {"ok": False, "error": "Disconnected"}
            filepath = self._tk_file_dialog()
            if not filepath:
                return {"ok": False, "error": "No file selected"}
            filesize = os.path.getsize(filepath)
            filename = os.path.basename(filepath)
            file_id = str(uuid.uuid4())
            if not self._user_id:
                return {"ok": False, "error": "Not logged in"}
            is_group = self._chat_type == "group"
            target_id = None if is_group else self._current_target_id
            group_id = int(self._current_target) if is_group and self._current_target else None
            if (not is_group and not target_id) or (is_group and not group_id):
                return {"ok": False, "error": "No target selected"}
            result = self.handler.send_file_init(
                self._user_id, target_id, filename, filesize, file_id, group_id=group_id
            )
            # 捕获文件上下文（在后台线程完成前聊天可能已切换）
            context_target = group_id if is_group else target_id
            file_context = {
                "related_type": self._chat_type,
                "related_target": str(context_target),
                "chat_key": self._chat_key(self._chat_type, context_target),
            }
            if is_group:
                file_context["group_id"] = str(group_id)
            seq = result.get("seq") if result else None
            if not result or not result.get("ok"):
                return {"ok": False, "error": "Failed to initialize file transfer"}
            if seq is None:
                return {"ok": False, "error": "File transfer init missing sequence"}
            self._pending_file_uploads[seq] = (
                filepath, file_id, filesize, filename, file_context,
            )
            return {"ok": True, "filename": filename, "filesize": filesize}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _send_file_worker(self, filepath, file_id, filesize, filename, context=None):
        chunk_size = 64 * 1024
        with open(filepath, "rb") as f:
            idx = 0
            total = (filesize + chunk_size - 1) // chunk_size
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.handler.send_file_data(file_id, chunk, idx, total)
                idx += 1
                time.sleep(0.01)
        event = {"filename": filename, "filesize": filesize}
        if context:
            event.update(context)
        self._push("file_sent", event)

    def set_current_target(self, target_name: str, target_id, chat_type: str):
        """JS 调用：切换当前聊天目标"""
        self._current_target = target_name
        self._current_target_id = target_id
        self._chat_type = chat_type
        return {"ok": True}

    def get_initial_state(self) -> dict:
        """JS 调用：获取初始状态"""
        return {
            "online_users": {k: v for k, v in self._online_users.items()},
            **self._group_payload(),
            "username": self._username,
            "user_id": self._user_id,
            "connected": self.conn.is_connected,
        }

    def get_connection_status(self) -> dict:
        """JS 调用：获取连接状态"""
        return {
            "connected": self.conn.is_connected,
            "host": self.conn.host if hasattr(self.conn, 'host') else "unknown",
            "port": self.conn.port if hasattr(self.conn, 'port') else 0,
        }

    def get_online_users_snapshot(self) -> dict:
        """JS 调用：获取当前在线用户快照"""
        return {
            "online_users": {k: v for k, v in self._online_users.items()},
            **self._group_payload(),
        }
