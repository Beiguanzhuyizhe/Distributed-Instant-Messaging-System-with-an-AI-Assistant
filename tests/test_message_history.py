import asyncio
import os
import tempfile

import pytest

from server.database import close_connection, init_db
from server.message_history import MessageHistory
from server.protocol import MessageType


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    close_connection()
    conn = init_db(path)
    conn.executemany(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        [
            (1, "alice", "hash", 1.0),
            (2, "bob", "hash", 1.0),
            (3, "carol", "hash", 1.0),
        ],
    )
    conn.commit()
    yield path
    close_connection()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
        except PermissionError:
            pass


def test_private_history_before_id_filters_both_directions(db_path):
    async def scenario():
        history = MessageHistory(db_path)
        await history.store_message(1, 2, None, MessageType.PRIVATE_MSG, "old a to b")
        await history.store_message(2, 1, None, MessageType.PRIVATE_MSG, "old b to a")
        await history.store_message(1, 2, None, MessageType.PRIVATE_MSG, "new a to b")
        latest = await history.get_private_history(1, 2, limit=10)
        before_id = latest[-1]["id"]

        older = await history.get_private_history(1, 2, limit=10, before_id=before_id)

        assert [m["content"] for m in older] == ["old a to b", "old b to a"]
        assert all(m["id"] < before_id for m in older)

    asyncio.run(scenario())


def test_private_history_never_includes_other_private_chat(db_path):
    async def scenario():
        history = MessageHistory(db_path)
        await history.store_message(1, 2, None, MessageType.PRIVATE_MSG, "alice-bob")
        await history.store_message(1, 3, None, MessageType.PRIVATE_MSG, "alice-carol")
        await history.store_message(3, 1, None, MessageType.PRIVATE_MSG, "carol-alice")

        bob_history = await history.get_private_history(1, 2, limit=10)

        assert [m["content"] for m in bob_history] == ["alice-bob"]

    asyncio.run(scenario())
