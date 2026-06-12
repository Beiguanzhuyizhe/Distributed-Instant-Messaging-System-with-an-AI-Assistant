import asyncio
import time

import pytest

from server.config import ServerConfig
from server.database import close_connection, get_db, init_db
from server.file_transfer import FileTransfer


@pytest.fixture
def file_transfer(tmp_path):
    close_connection()
    db_path = str(tmp_path / "chat.db")
    storage_dir = str(tmp_path / "files")
    init_db(db_path)
    now = time.time()
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (1, "alice", "hash", now),
        )
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (2, "bob", "hash", now),
        )
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (3, "mallory", "hash", now),
        )
        conn.execute(
            "INSERT INTO groups (id, name, owner_id, created_at) VALUES (?, ?, ?, ?)",
            (1, "demo", 1, now),
        )
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
            (1, 1, "owner", now),
        )
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
            (1, 2, "member", now),
        )
        conn.commit()

    config = ServerConfig(
        db_path=db_path,
        file_storage_dir=storage_dir,
        file_chunk_size=4,
        max_file_size=32,
    )
    yield FileTransfer(config)
    close_connection()


def test_rejects_unsafe_file_id(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(
            from_id=1,
            to_id=2,
            filename="safe.txt",
            filesize=4,
            client_file_id="../evil",
        )
    )

    assert result == {"success": False, "error": "invalid_file_id"}


def test_sanitizes_filename(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(
            from_id=1,
            to_id=2,
            filename="../nested/secret.txt",
            filesize=4,
            client_file_id="file_a",
        )
    )

    assert result["success"] is True
    assert result["filename"] == "secret.txt"
    progress = asyncio.run(file_transfer.get_transfer_progress("file_a"))
    assert progress["filename"] == "secret.txt"


def test_store_chunk_requires_original_sender(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(1, 2, "a.txt", 4, client_file_id="file_b")
    )
    assert result["success"] is True

    denied = asyncio.run(
        file_transfer.store_chunk("file_b", 0, b"abcd", sender_id=2, total_chunks=1)
    )

    assert denied == {"success": False, "error": "permission_denied"}
    progress = asyncio.run(file_transfer.get_transfer_progress("file_b"))
    assert progress["chunks_received"] == 0


def test_duplicate_chunk_does_not_increment_progress(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(1, 2, "a.txt", 8, client_file_id="file_c")
    )
    assert result["chunks_total"] == 2

    first = asyncio.run(
        file_transfer.store_chunk("file_c", 0, b"abcd", sender_id=1, total_chunks=2)
    )
    duplicate = asyncio.run(
        file_transfer.store_chunk("file_c", 0, b"abcd", sender_id=1, total_chunks=2)
    )
    progress = asyncio.run(file_transfer.get_transfer_progress("file_c"))

    assert first["success"] is True
    assert duplicate["duplicate"] is True
    assert progress["chunks_received"] == 1
    assert progress["status"] == "transferring"

    completed = asyncio.run(
        file_transfer.store_chunk("file_c", 1, b"efgh", sender_id=1, total_chunks=2)
    )
    assert completed["completed"] is True
    progress = asyncio.run(file_transfer.get_transfer_progress("file_c"))
    assert progress["chunks_received"] == 2
    assert progress["status"] == "completed"


def test_download_requires_receiver(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(1, 2, "a.txt", 4, client_file_id="file_d")
    )
    assert result["success"] is True
    asyncio.run(file_transfer.store_chunk("file_d", 0, b"abcd", sender_id=1, total_chunks=1))

    denied = asyncio.run(file_transfer.get_chunk("file_d", 0, requester_id=3))
    allowed = asyncio.run(file_transfer.get_chunk("file_d", 0, requester_id=2))

    assert denied == {"success": False, "error": "permission_denied"}
    assert allowed["success"] is True
    assert allowed["data"] == b"abcd"


def test_group_download_allows_members_only(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(
            from_id=1,
            to_id=None,
            filename="group.txt",
            filesize=4,
            group_id=1,
            client_file_id="file_e",
        )
    )
    assert result["success"] is True
    asyncio.run(file_transfer.store_chunk("file_e", 0, b"abcd", sender_id=1, total_chunks=1))

    member = asyncio.run(file_transfer.get_chunk("file_e", 0, requester_id=2))
    outsider = asyncio.run(file_transfer.get_chunk("file_e", 0, requester_id=3))

    assert member["success"] is True
    assert member["data"] == b"abcd"
    assert outsider == {"success": False, "error": "permission_denied"}


def test_rejects_bad_chunk_bounds(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(1, 2, "a.txt", 5, client_file_id="file_f")
    )
    assert result["chunks_total"] == 2

    out_of_range = asyncio.run(
        file_transfer.store_chunk("file_f", 2, b"x", sender_id=1, total_chunks=2)
    )
    too_large = asyncio.run(
        file_transfer.store_chunk("file_f", 1, b"toolong", sender_id=1, total_chunks=2)
    )

    assert out_of_range == {"success": False, "error": "chunk_index_out_of_range"}
    assert too_large == {"success": False, "error": "invalid_chunk_size"}


def test_rejects_invalid_total_chunks_without_raising(file_transfer):
    result = asyncio.run(
        file_transfer.init_transfer(1, 2, "a.txt", 4, client_file_id="file_g")
    )
    assert result["success"] is True

    bad_total = asyncio.run(
        file_transfer.store_chunk("file_g", 0, b"abcd", sender_id=1, total_chunks="bad")
    )

    assert bad_total == {"success": False, "error": "invalid_total_chunks"}
