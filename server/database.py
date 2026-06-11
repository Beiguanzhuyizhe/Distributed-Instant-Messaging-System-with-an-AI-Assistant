"""
服务端 SQLite 数据库层
定义所有表结构并提供 CRUD 基础操作
"""
import sqlite3
import os
import json
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict, Any


_local = threading.local()


def get_connection(db_path: str) -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程局部）"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def close_connection():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


@contextmanager
def get_db(db_path: str):
    conn = get_connection(db_path)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pass  # 连接由线程生命周期管理


def init_db(db_path: str):
    """初始化数据库，创建所有表"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 用户表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            public_key TEXT DEFAULT '',
            created_at REAL NOT NULL,
            last_login REAL DEFAULT 0,
            is_online INTEGER DEFAULT 0
        )
    """)

    # 群组表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """)

    # 群组成员表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT DEFAULT 'member',
            joined_at REAL NOT NULL,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # 消息表 (存储所有经过服务端的消息)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT UNIQUE NOT NULL,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER DEFAULT NULL,
            group_id INTEGER DEFAULT NULL,
            msg_type INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_encrypted INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            recalled INTEGER DEFAULT 0,
            FOREIGN KEY (sender_id) REFERENCES users(id),
            FOREIGN KEY (receiver_id) REFERENCES users(id),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)

    # 离线消息表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offline_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_user_id INTEGER NOT NULL,
            msg_id TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_encrypted INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            delivered INTEGER DEFAULT 0,
            FOREIGN KEY (target_user_id) REFERENCES users(id),
            FOREIGN KEY (sender_id) REFERENCES users(id)
        )
    """)

    # 文件传输记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT UNIQUE NOT NULL,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER DEFAULT NULL,
            group_id INTEGER DEFAULT NULL,
            filename TEXT NOT NULL,
            filesize INTEGER NOT NULL,
            filepath TEXT NOT NULL,
            transfer_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            chunk_size INTEGER DEFAULT 65536,
            chunks_total INTEGER DEFAULT 0,
            chunks_received INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            completed_at REAL DEFAULT NULL,
            FOREIGN KEY (sender_id) REFERENCES users(id),
            FOREIGN KEY (receiver_id) REFERENCES users(id)
        )
    """)

    # 索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_offline_target ON offline_messages(target_user_id, delivered)")

    conn.commit()
    return conn
