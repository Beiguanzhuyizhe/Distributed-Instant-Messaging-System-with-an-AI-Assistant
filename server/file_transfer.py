"""
文件传输模块（服务端中继模式）
文件块以临时文件存储，支持断点续传。
"""
import time
import asyncio
import os
import uuid
from server.database import get_db


class FileTransfer:
    """服务端中继文件传输管理器"""

    def __init__(self, config):
        self._db_path = config.db_path
        self._storage_dir = config.file_storage_dir
        self._chunk_size = config.file_chunk_size
        self._max_file_size = config.max_file_size
        os.makedirs(self._storage_dir, exist_ok=True)

    def _gen_file_id(self) -> str:
        return str(uuid.uuid4())

    async def init_transfer(
        self, from_id: int, to_id: int, filename: str, filesize: int,
        group_id: int = None, client_file_id=None
    ) -> dict:
        """初始化文件传输，分配 file_id 并创建空文件占位"""
        if filesize > self._max_file_size:
            return {"success": False, "error": "文件大小超过限制"}

        file_id = str(client_file_id) if client_file_id is not None else self._gen_file_id()
        filepath = os.path.join(self._storage_dir, file_id)
        chunks_total = (filesize + self._chunk_size - 1) // self._chunk_size
        now = time.time()

        def _run():
            with get_db(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO file_transfers
                       (file_id, sender_id, receiver_id, group_id, filename, filesize,
                        filepath, transfer_type, status, chunk_size,
                        chunks_total, chunks_received, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (file_id, from_id, to_id, group_id, filename, filesize, filepath,
                     "relay", "pending", self._chunk_size, chunks_total, 0, now),
                )
                conn.commit()
            # 预分配文件空间
            with open(filepath, "wb") as f:
                f.truncate(filesize)
            return {
                "success": True,
                "file_id": file_id,
                "chunk_size": self._chunk_size,
                "chunks_total": chunks_total,
            }
        return await asyncio.to_thread(_run)

    async def store_chunk(self, file_id: str, chunk_index: int, data: bytes) -> dict:
        """存储一个文件块到临时文件"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM file_transfers WHERE file_id = ?", (file_id,)
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "文件传输不存在"}
                transfer = dict(row)

            filepath = transfer["filepath"]
            offset = chunk_index * transfer["chunk_size"]
            with open(filepath, "r+b") as f:
                f.seek(offset)
                f.write(data)

            with get_db(self._db_path) as conn:
                conn.execute(
                    "UPDATE file_transfers SET status = 'transferring' WHERE file_id = ? AND status = 'pending'",
                    (file_id,),
                )
                conn.execute(
                    "UPDATE file_transfers SET chunks_received = chunks_received + 1 WHERE file_id = ?",
                    (file_id,),
                )
                cur = conn.execute(
                    "SELECT chunks_received, chunks_total FROM file_transfers WHERE file_id = ?",
                    (file_id,),
                )
                row = cur.fetchone()
                if row and row["chunks_received"] >= row["chunks_total"]:
                    conn.execute(
                        "UPDATE file_transfers SET status = 'completed', completed_at = ? WHERE file_id = ?",
                        (time.time(), file_id),
                    )
                    conn.commit()
                    return {"success": True, "chunk_index": chunk_index, "completed": True}
                conn.commit()
            return {"success": True, "chunk_index": chunk_index, "completed": False}
        return await asyncio.to_thread(_run)

    async def get_chunk(self, file_id: str, offset: int) -> dict:
        """从临时文件读取一个文件块"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM file_transfers WHERE file_id = ?", (file_id,)
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "文件传输不存在"}
                transfer = dict(row)

            filepath = transfer["filepath"]
            with open(filepath, "rb") as f:
                f.seek(offset)
                data = f.read(transfer["chunk_size"])
            return {"success": True, "data": data, "offset": offset, "size": len(data)}
        return await asyncio.to_thread(_run)

    async def get_transfer_progress(self, file_id: str) -> dict:
        """获取文件传输进度信息"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM file_transfers WHERE file_id = ?", (file_id,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        return await asyncio.to_thread(_run)
