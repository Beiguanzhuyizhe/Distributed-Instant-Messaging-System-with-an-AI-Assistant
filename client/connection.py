"""
TCP 连接管理：建立连接、心跳、自动重连、消息收发
使用阻塞 socket + 后台接收线程，适合 CLI/GUI 使用
"""

import socket
import threading
import time
from protocol import (
    encode_message, MessageProtocol, MessageType
)


class ChatConnection:
    """TCP 连接管理器，使用阻塞 socket + 后台线程接收消息"""

    def __init__(self):
        self.sock = None
        self.protocol = MessageProtocol()
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._callbacks = {}
        self._reconnect_attempts = 0
        self._max_reconnect = 5
        self._host = None
        self._port = None
        self._on_connected_cb = None
        self._on_disconnected_cb = None
        self._heartbeat_interval = 3  # seconds，演示断线重连时需要较快暴露状态
        self._last_heartbeat = 0
        self.connected = False
        self._connect_lock = threading.Lock()
        self._disconnect_notified = False

    # --- Callback 注册 ---

    def on_connected(self, callback):
        """连接成功时的回调"""
        self._on_connected_cb = callback

    def on_disconnected(self, callback):
        """断开连接时的回调"""
        self._on_disconnected_cb = callback

    def register_callback(self, msg_type, callback):
        """注册消息回调"""
        self._callbacks[msg_type] = callback

    def unregister_callback(self, msg_type):
        """取消注册消息回调"""
        self._callbacks.pop(msg_type, None)

    # --- 连接管理 ---

    def connect(self, host, port):
        """建立 TCP 连接并启动接收线程"""
        self._host = host
        self._port = port
        self._reconnect_attempts = 0
        ok = self._do_connect()
        if ok and not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._thread.start()
        return ok

    def _do_connect(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((self._host, self._port))
            # 接收循环需要定期醒来发送心跳；短超时不会断开连接，只让 recv 可轮询。
            sock.settimeout(1.0)
            self.sock = sock
            self.protocol.reset()
            self._reconnect_attempts = 0
            self.connected = True
            self._disconnect_notified = False
            self._last_heartbeat = time.time()
            return True
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self.connected = False
            return False

    # --- 消息发送 ---

    def send_message(self, msg_type, payload, seq=None):
        """发送编码后的消息，线程安全"""
        from protocol import encode_message
        data = encode_message(msg_type, payload, seq)
        failed = False
        with self._lock:
            if self.sock:
                try:
                    self.sock.sendall(data)
                    return True
                except Exception:
                    failed = True
        if failed:
            self._mark_disconnected()
        return False

    def send_heartbeat(self):
        """发送心跳包"""
        return self.send_message(MessageType.HEARTBEAT, {})

    def _mark_disconnected(self):
        """统一处理断线状态和通知，避免发送失败时 UI 仍显示在线。"""
        old_sock = None
        with self._lock:
            old_sock = self.sock
            self.sock = None
        self.connected = False
        if old_sock:
            try:
                old_sock.close()
            except OSError:
                pass
        if not self._disconnect_notified:
            self._disconnect_notified = True
            cb = self._on_disconnected_cb
            if cb:
                try:
                    cb()
                except Exception:
                    pass

    # --- 接收循环（运行在后台线程） ---

    def _receive_loop(self):
        while self._running:
            try:
                if not self.sock:
                    time.sleep(0.1)
                    continue

                # 定时心跳
                now = time.time()
                if now - self._last_heartbeat >= self._heartbeat_interval:
                    if not self.send_heartbeat():
                        raise ConnectionError("heartbeat send failed")
                    self._last_heartbeat = now

                data = self.sock.recv(4096)
                if not data:
                    raise ConnectionError("服务器关闭了连接")

                self.protocol.feed(data)
                for msg in self.protocol.next_messages():
                    self._dispatch(*msg)

            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                self._mark_disconnected()
                if self._running:
                    if self._reconnect():
                        continue
                break

    def _dispatch(self, msg_type, seq, payload):
        cb = self._callbacks.get(msg_type)
        if cb:
            try:
                cb(msg_type, seq, payload)
            except Exception:
                pass

    # --- 自动重连（指数退避） ---

    def _reconnect(self):
        delay = 1
        while self._running:
            time.sleep(delay)
            self._reconnect_attempts += 1
            if self._do_connect():
                cb = self._on_connected_cb
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
                return True
            delay = min(delay * 2, 30)
        return False

    # --- 关闭 ---

    def close(self):
        self._running = False
        with self._lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
        self.connected = False

    @property
    def is_connected(self):
        return self.connected
