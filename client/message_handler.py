"""
消息处理中枢：接收服务端推送的所有消息并分发到应用层回调
"""

from protocol import MessageType, SequenceGenerator


class MessageHandler:
    """
    消息分发器
    - 自动将所有网络消息通过 _dispatch 路由到 connection 的回调系统
    - 应用层通过 register() 注册回调
    """

    def __init__(self, connection):
        self.connection = connection
        self._callbacks = {}
        self._setup_routing()

    def _setup_routing(self):
        """将 connection 收到的所有消息类型路由到 _dispatch"""
        if not self.connection:
            return
        for msg_type in MessageType:
            self.connection.register_callback(msg_type, self._dispatch)

    def register(self, msg_type, callback):
        """注册应用层回调"""
        self._callbacks[msg_type] = callback

    def unregister(self, msg_type):
        """取消注册"""
        self._callbacks.pop(msg_type, None)

    def _dispatch(self, msg_type, seq, payload):
        """从 connection 收到消息 -> 查找应用层回调"""
        cb = self._callbacks.get(msg_type)
        if cb:
            try:
                cb(msg_type, seq, payload)
            except Exception:
                pass

    @staticmethod
    def next_msg_id():
        return SequenceGenerator.next()

    def _send_tracked(self, msg_type, payload):
        """
        发送一条需要客户端追踪 ACK 的消息。

        服务端回复 ACK 时会复用这里显式传入的 seq，因此 CLI/GUI 可以用
        seq 找回本地的 pending 消息，再把服务端返回的 UUID msg_id 写回去。
        """
        seq = self.next_msg_id()
        ok = self.connection.send_message(msg_type, payload, seq=seq)
        return {
            "ok": bool(ok),
            "seq": seq,
            "msg_type": msg_type,
            "payload": payload,
            "client_msg_id": payload.get("msg_id"),
            "client_file_id": payload.get("file_id"),
        }

    # --- 快捷发送方法 ---

    def send_login(self, username, password_hash):
        from protocol import make_login_payload
        return self.connection.send_message(
            MessageType.LOGIN_REQ,
            make_login_payload(username, password_hash)
        )

    def send_register(self, username, password_hash, public_key=""):
        from protocol import make_register_payload
        return self.connection.send_message(
            MessageType.REGISTER_REQ,
            make_register_payload(username, password_hash, public_key)
        )

    def send_private_msg(self, from_id, to_id, content, msg_id=None):
        from protocol import make_private_msg_payload
        client_msg_id = msg_id if msg_id is not None else self.next_msg_id()
        payload = make_private_msg_payload(
            from_id, to_id, content, msg_id=client_msg_id
        )
        return self._send_tracked(
            MessageType.PRIVATE_MSG,
            payload
        )

    def send_group_msg(self, from_id, group_id, content, msg_id=None):
        from protocol import make_group_msg_payload
        client_msg_id = msg_id if msg_id is not None else self.next_msg_id()
        payload = make_group_msg_payload(
            from_id, group_id, content, msg_id=client_msg_id
        )
        return self._send_tracked(
            MessageType.GROUP_MSG,
            payload
        )

    def send_ai_query(self, from_id, group_id, query, context=None):
        from protocol import make_ai_query_payload
        payload = make_ai_query_payload(from_id, group_id, query)
        if context:
            payload["context"] = context
        return self._send_tracked(
            MessageType.AI_QUERY, payload
        )

    def send_recall(self, msg_id):
        from protocol import make_recall_payload
        return self._send_tracked(MessageType.MSG_RECALL, make_recall_payload(str(msg_id)))

    def request_history(self, target_type, target_id, limit=50):
        return self._send_tracked(
            MessageType.HISTORY_REQ,
            {
                "type": target_type,
                "target_type": target_type,
                "target_id": target_id,
                "limit": limit,
            }
        )

    def request_online_users(self):
        return self._send_tracked(MessageType.ONLINE_USERS, {})

    def group_create(self, name, owner_id):
        return self._send_tracked(
            MessageType.GROUP_CREATE,
            {"name": name, "owner_id": owner_id}
        )

    def group_join(self, group_id, user_id):
        return self._send_tracked(
            MessageType.GROUP_JOIN,
            {"group_id": group_id, "user_id": user_id}
        )

    def group_leave(self, group_id, user_id):
        return self._send_tracked(
            MessageType.GROUP_LEAVE,
            {"group_id": group_id, "user_id": user_id}
        )

    def send_file_init(self, from_id, to_id, filename, filesize, file_id):
        return self._send_tracked(
            MessageType.FILE_INIT,
            {"from_id": from_id, "to_id": to_id,
             "filename": filename, "filesize": filesize, "file_id": str(file_id)}
        )

    def send_file_data(self, file_id, chunk_data, chunk_index, total_chunks):
        import base64
        return self._send_tracked(
            MessageType.FILE_DATA,
            {"file_id": str(file_id),
             "data": base64.b64encode(chunk_data).decode(),
             "chunk_index": chunk_index, "total_chunks": total_chunks}
        )

    def request_file_chunk(self, file_id, offset):
        """请求服务端返回指定偏移的文件块（接收方下载用）"""
        return self._send_tracked(
            MessageType.FILE_ACK,
            {"file_id": str(file_id), "offset": offset}
        )
