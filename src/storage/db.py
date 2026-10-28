"""SQLite 草稿存储。

要点：
1. 唯一约束 `uniq_draft_key` 保证同一 (material_id, date, start) 只能存在一份草稿，
   实现「重复提交只产生一份草稿」的硬保证（即使模型重复调用保存工具）。
2. 幂等键 `request_key` 由 (material_id, date, start, channel, content_hash) 组成。
3. 保存失败必须真实抛出，调用方据此返回 failed，不允许乐观上报。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id      TEXT UNIQUE NOT NULL,
    request_key   TEXT UNIQUE NOT NULL,
    material_id   TEXT NOT NULL,
    date          TEXT NOT NULL,
    start_time    TEXT NOT NULL,
    channel       TEXT,
    risk_level    TEXT,
    status        TEXT NOT NULL,
    payload       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (material_id, date, start_time)
);
"""


def content_hash(material: dict) -> str:
    stable = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def build_request_key(material_id: str, date: str, start: str, channel: str | None, chash: str) -> str:
    return f"{material_id}|{date}|{start}|{channel or '-'}|{chash}"


class DraftStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _cursor(self):
        """打开连接并在退出时**关闭**。

        注意：`with sqlite3.connect(...) as conn` 只管理事务，不关闭连接，
        长期运行会泄漏文件句柄（Windows 上会导致无法删除库文件）。
        """
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._cursor() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def save_draft(self, record: dict[str, Any]) -> dict[str, Any]:
        """保存草稿。

        返回 {"status": "saved" | "duplicate", "draft_id": ..., "request_key": ...}
        写入失败时抛出异常（由调用方转换为明确的失败状态）。
        """
        request_key = record["request_key"]
        existing = self.get_by_request_key(request_key)
        if existing is not None:
            return {
                "status": "duplicate",
                "draft_id": existing["draft_id"],
                "request_key": request_key,
                "detail": "相同请求已存在草稿，未重复创建",
            }

        with self._cursor() as conn:
            try:
                conn.execute(
                    """INSERT INTO drafts
                       (draft_id, request_key, material_id, date, start_time, channel,
                        risk_level, status, payload, content_hash, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["draft_id"],
                        request_key,
                        record["material_id"],
                        record["date"],
                        record["start_time"],
                        record.get("channel"),
                        record.get("risk_level"),
                        record.get("status", "draft"),
                        json.dumps(record.get("payload"), ensure_ascii=False),
                        record["content_hash"],
                        record["created_at"],
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                # 唯一约束冲突：视为重复而非失败。
                # 注意：冲突可能来自 UNIQUE(material_id, date, start_time) 而非 request_key
                # （例如素材内容变更后 content_hash 变化 -> request_key 不同但排期相同），
                # 因此两处都要查，查不到才视为真实失败。
                conn.rollback()
                row = self.get_by_request_key(request_key) or self.get_by_slot(
                    record["material_id"], record["date"], record["start_time"]
                )
                if row is not None:
                    return {
                        "status": "duplicate",
                        "draft_id": row["draft_id"],
                        "request_key": row["request_key"],
                        "detail": f"唯一约束拦截重复写入: {exc}",
                    }
                raise
        return {
            "status": "saved",
            "draft_id": record["draft_id"],
            "request_key": request_key,
            "detail": "草稿已写入本地草稿库",
        }

    def get_by_request_key(self, request_key: str) -> dict | None:
        with self._cursor() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE request_key = ?", (request_key,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_slot(self, material_id: str, date: str, start_time: str) -> dict | None:
        """按 (material_id, date, start_time) 查询草稿（对应唯一约束）。"""
        with self._cursor() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE material_id = ? AND date = ? AND start_time = ?",
                (material_id, date, start_time),
            ).fetchone()
        return dict(row) if row else None

    def list_drafts(self) -> list[dict]:
        with self._cursor() as conn:
            rows = conn.execute("SELECT * FROM drafts ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._cursor() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])
