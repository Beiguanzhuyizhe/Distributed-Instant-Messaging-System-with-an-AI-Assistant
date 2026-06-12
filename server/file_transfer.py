"""
Server-side relay file transfer manager.

The client protocol stays unchanged: clients still send file_id, chunk_index,
total_chunks and base64 data. This module adds server-side validation and
authorization so relay transfer is safe enough for the final demo.
"""

import asyncio
import os
import re
import threading
import time
import uuid

from server.database import get_db


_SAFE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class FileTransfer:
    """Manage relay-mode file transfers on the server."""

    def __init__(self, config):
        self._db_path = config.db_path
        self._storage_dir = os.path.abspath(config.file_storage_dir)
        self._chunk_size = int(config.file_chunk_size)
        self._max_file_size = int(config.max_file_size)
        self._received_chunks: dict[str, set[int]] = {}
        self._chunk_lock = threading.Lock()
        os.makedirs(self._storage_dir, exist_ok=True)

    def _gen_file_id(self) -> str:
        return str(uuid.uuid4())

    def _normalize_file_id(self, value) -> str | None:
        if value is None or value == "":
            return self._gen_file_id()
        file_id = str(value)
        if not _SAFE_FILE_ID.fullmatch(file_id):
            return None
        return file_id

    def _safe_storage_path(self, file_id: str) -> str | None:
        if not _SAFE_FILE_ID.fullmatch(str(file_id)):
            return None
        path = os.path.abspath(os.path.join(self._storage_dir, str(file_id)))
        if os.path.commonpath([self._storage_dir, path]) != self._storage_dir:
            return None
        return path

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        basename = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        return (basename or "unnamed_file")[:255]

    @staticmethod
    def _to_int_or_none(value):
        if value in (None, "", 0, "0"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def init_transfer(
        self,
        from_id: int,
        to_id: int,
        filename: str,
        filesize: int,
        group_id: int = None,
        client_file_id=None,
    ) -> dict:
        """Initialize a relay transfer and create the placeholder file."""
        try:
            filesize = int(filesize)
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid_filesize"}

        if filesize < 0:
            return {"success": False, "error": "invalid_filesize"}
        if filesize > self._max_file_size:
            return {"success": False, "error": "file_too_large"}

        file_id = self._normalize_file_id(client_file_id)
        if not file_id:
            return {"success": False, "error": "invalid_file_id"}

        filepath = self._safe_storage_path(file_id)
        if not filepath:
            return {"success": False, "error": "invalid_file_id"}

        receiver_id = self._to_int_or_none(to_id)
        group_id = self._to_int_or_none(group_id)
        if receiver_id is None and group_id is None:
            return {"success": False, "error": "missing_receiver"}

        safe_filename = self._sanitize_filename(filename)
        chunks_total = (filesize + self._chunk_size - 1) // self._chunk_size if filesize else 0
        now = time.time()
        status = "completed" if chunks_total == 0 else "pending"
        completed_at = now if chunks_total == 0 else None

        def _run():
            try:
                with get_db(self._db_path) as conn:
                    if group_id is not None:
                        cur = conn.execute(
                            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                            (group_id, from_id),
                        )
                        if not cur.fetchone():
                            return {"success": False, "error": "not_group_member"}

                    conn.execute(
                        """INSERT INTO file_transfers
                           (file_id, sender_id, receiver_id, group_id, filename, filesize,
                            filepath, transfer_type, status, chunk_size,
                            chunks_total, chunks_received, created_at, completed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            file_id,
                            from_id,
                            receiver_id,
                            group_id,
                            safe_filename,
                            filesize,
                            filepath,
                            "relay",
                            status,
                            self._chunk_size,
                            chunks_total,
                            chunks_total if chunks_total == 0 else 0,
                            now,
                            completed_at,
                        ),
                    )
                    conn.commit()

                with open(filepath, "wb") as f:
                    f.truncate(filesize)

                with self._chunk_lock:
                    self._received_chunks[file_id] = set()

                return {
                    "success": True,
                    "file_id": file_id,
                    "chunk_size": self._chunk_size,
                    "chunks_total": chunks_total,
                    "filename": safe_filename,
                }
            except Exception as exc:
                return {"success": False, "error": f"init_failed:{type(exc).__name__}"}

        return await asyncio.to_thread(_run)

    async def store_chunk(
        self,
        file_id: str,
        chunk_index: int,
        data: bytes,
        sender_id: int = None,
        total_chunks: int = None,
    ) -> dict:
        """Store one chunk and update progress without double-counting duplicates."""
        if not file_id or not _SAFE_FILE_ID.fullmatch(str(file_id)):
            return {"success": False, "error": "invalid_file_id"}
        try:
            chunk_index = int(chunk_index)
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid_chunk_index"}
        if not isinstance(data, (bytes, bytearray)):
            return {"success": False, "error": "invalid_chunk_data"}
        chunk_data = bytes(data)
        expected_total_chunks = None
        if total_chunks is not None:
            try:
                expected_total_chunks = int(total_chunks)
            except (TypeError, ValueError):
                return {"success": False, "error": "invalid_total_chunks"}
        sender_id_int = None
        if sender_id is not None:
            try:
                sender_id_int = int(sender_id)
            except (TypeError, ValueError):
                return {"success": False, "error": "invalid_sender"}

        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute("SELECT * FROM file_transfers WHERE file_id = ?", (str(file_id),))
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "file_not_found"}
                transfer = dict(row)

            if sender_id_int is not None and int(transfer["sender_id"]) != sender_id_int:
                return {"success": False, "error": "permission_denied"}

            chunks_total = int(transfer["chunks_total"])
            if chunks_total <= 0:
                return {"success": False, "error": "no_chunks_expected"}
            if chunk_index < 0 or chunk_index >= chunks_total:
                return {"success": False, "error": "chunk_index_out_of_range"}
            if expected_total_chunks is not None and expected_total_chunks != chunks_total:
                return {"success": False, "error": "total_chunks_mismatch"}
            if transfer["status"] == "completed":
                return {
                    "success": True,
                    "chunk_index": chunk_index,
                    "completed": True,
                    "duplicate": True,
                }

            offset = chunk_index * int(transfer["chunk_size"])
            remaining = int(transfer["filesize"]) - offset
            max_len = min(int(transfer["chunk_size"]), remaining)
            if offset < 0 or offset >= int(transfer["filesize"]):
                return {"success": False, "error": "offset_out_of_range"}
            if len(chunk_data) <= 0 or len(chunk_data) > max_len:
                return {"success": False, "error": "invalid_chunk_size"}

            with self._chunk_lock:
                received = self._received_chunks.setdefault(str(file_id), set())
                if chunk_index in received:
                    return {
                        "success": True,
                        "chunk_index": chunk_index,
                        "completed": transfer["status"] == "completed",
                        "duplicate": True,
                    }
                received.add(chunk_index)
                received_count = len(received)

            filepath = os.path.abspath(transfer["filepath"])
            if os.path.commonpath([self._storage_dir, filepath]) != self._storage_dir:
                return {"success": False, "error": "invalid_storage_path"}

            with open(filepath, "r+b") as f:
                f.seek(offset)
                f.write(chunk_data)

            completed = received_count >= chunks_total
            with get_db(self._db_path) as conn:
                conn.execute(
                    """UPDATE file_transfers
                       SET status = ?, chunks_received = ?, completed_at = COALESCE(completed_at, ?)
                       WHERE file_id = ?""",
                    (
                        "completed" if completed else "transferring",
                        received_count,
                        time.time() if completed else None,
                        str(file_id),
                    ),
                )
                conn.commit()

            return {"success": True, "chunk_index": chunk_index, "completed": completed}

        return await asyncio.to_thread(_run)

    async def get_chunk(self, file_id: str, offset: int, requester_id: int = None) -> dict:
        """Read one chunk for an authorized receiver or group member."""
        if not file_id or not _SAFE_FILE_ID.fullmatch(str(file_id)):
            return {"success": False, "error": "invalid_file_id"}
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid_offset"}
        requester_id_int = None
        if requester_id is not None:
            try:
                requester_id_int = int(requester_id)
            except (TypeError, ValueError):
                return {"success": False, "error": "invalid_requester"}

        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute("SELECT * FROM file_transfers WHERE file_id = ?", (str(file_id),))
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "file_not_found"}
                transfer = dict(row)

                if requester_id_int is not None:
                    requester = requester_id_int
                    receiver_id = transfer["receiver_id"]
                    group_id = transfer["group_id"]
                    allowed = receiver_id is not None and int(receiver_id) == requester
                    if group_id is not None:
                        cur = conn.execute(
                            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                            (group_id, requester),
                        )
                        allowed = cur.fetchone() is not None
                    if not allowed:
                        return {"success": False, "error": "permission_denied"}

            if transfer["status"] != "completed":
                return {"success": False, "error": "file_not_completed"}

            filesize = int(transfer["filesize"])
            chunk_size = int(transfer["chunk_size"])
            if filesize == 0:
                if offset != 0:
                    return {"success": False, "error": "offset_out_of_range"}
                return {"success": True, "data": b"", "offset": 0, "size": 0}
            if offset < 0 or offset >= filesize or offset % chunk_size != 0:
                return {"success": False, "error": "offset_out_of_range"}

            filepath = os.path.abspath(transfer["filepath"])
            if os.path.commonpath([self._storage_dir, filepath]) != self._storage_dir:
                return {"success": False, "error": "invalid_storage_path"}

            with open(filepath, "rb") as f:
                f.seek(offset)
                data = f.read(chunk_size)
            return {"success": True, "data": data, "offset": offset, "size": len(data)}

        return await asyncio.to_thread(_run)

    async def get_transfer_progress(self, file_id: str) -> dict:
        """Return persisted transfer progress."""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM file_transfers WHERE file_id = ?", (str(file_id),)
                )
                row = cur.fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)
