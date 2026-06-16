"""
数据库层单元测试
"""
import os
import pytest
import tempfile
from server.database import init_db, get_connection, close_connection


@pytest.fixture(autouse=True)
def reset_connection():
    """每个测试前重置数据库连接缓存，避免 threading.local 跨测试污染"""
    from server.database import close_connection
    close_connection()
    yield
    close_connection()


@pytest.fixture
def db_path():
    tmp = tempfile.mktemp(suffix=".db")
    yield tmp
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except PermissionError:
            pass  # Windows 文件锁延迟释放


class TestDatabaseInit:
    def test_init_creates_tables(self, db_path):
        conn = init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cursor.fetchall()}
        assert "users" in tables
        assert "groups" in tables
        assert "group_members" in tables
        assert "messages" in tables
        assert "offline_messages" in tables
        assert "file_transfers" in tables

    def test_init_idempotent(self, db_path):
        init_db(db_path)
        init_db(db_path)  # 第二次调用不应报错

    def test_foreign_keys_enabled(self, db_path):
        conn = init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1

    def test_user_insert_and_query(self, db_path):
        conn = init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("alice", "hash123", 1000.0)
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE username=?", ("alice",))
        row = cursor.fetchone()
        assert row["username"] == "alice"
        assert row["password_hash"] == "hash123"

    def test_username_unique(self, db_path):
        conn = init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("alice", "hash1", 1000.0)
        )
        conn.commit()
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                ("alice", "hash2", 2000.0)
            )
