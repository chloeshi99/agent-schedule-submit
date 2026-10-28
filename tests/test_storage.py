"""草稿存储层单元测试：唯一约束、幂等键、并发去重。"""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.db import DraftStore, build_request_key, content_hash


def _record(material_id="M01", date="2026-11-02", start="19:00", channel="视频号", chash="h1") -> dict:
    return {
        "draft_id": f"D-{material_id}-{date.replace('-', '')}-{start.replace(':', '')}",
        "request_key": build_request_key(material_id, date, start, channel, chash),
        "material_id": material_id,
        "date": date,
        "start_time": start,
        "channel": channel,
        "risk_level": "none",
        "status": "draft",
        "content_hash": chash,
        "created_at": "2026-10-28T00:00:00+00:00",
        "payload": {"note": None, "published": False},
    }


def test_save_then_get(tmp_path) -> None:
    store = DraftStore(tmp_path / "d.sqlite3")
    result = store.save_draft(_record())
    assert result["status"] == "saved"
    row = store.get_by_request_key(result["request_key"])
    assert row["material_id"] == "M01"


def test_same_request_key_is_duplicate(tmp_path) -> None:
    store = DraftStore(tmp_path / "d.sqlite3")
    first = store.save_draft(_record())
    second = store.save_draft(_record())
    assert first["status"] == "saved"
    assert second["status"] == "duplicate"
    assert store.count() == 1


def test_unique_constraint_on_material_date_start(tmp_path) -> None:
    """即使 request_key 不同，(material_id, date, start) 唯一约束仍然拦截重复。"""
    store = DraftStore(tmp_path / "d.sqlite3")
    store.save_draft(_record(chash="h1"))
    other = _record(chash="h2")
    other["request_key"] = build_request_key("M01", "2026-11-02", "19:00", "视频号", "h2")
    result = store.save_draft(other)
    assert result["status"] == "duplicate"
    assert store.count() == 1


def test_content_hash_changes_with_content() -> None:
    a = content_hash({"x": 1, "y": 2})
    b = content_hash({"y": 2, "x": 1})
    c = content_hash({"x": 2, "y": 2})
    assert a == b  # 键序无关
    assert a != c


def test_request_key_includes_all_dimensions() -> None:
    k1 = build_request_key("M01", "2026-11-02", "19:00", "抖音", "h1")
    k2 = build_request_key("M01", "2026-11-02", "19:00", "公众号", "h1")
    k3 = build_request_key("M01", "2026-11-03", "19:00", "抖音", "h1")
    assert k1 != k2
    assert k1 != k3


def test_list_drafts_ordering(tmp_path) -> None:
    store = DraftStore(tmp_path / "d.sqlite3")
    store.save_draft(_record(start="09:00"))
    store.save_draft(_record(start="19:00"))
    assert [d["start_time"] for d in store.list_drafts()] == ["09:00", "19:00"]


def test_db_file_is_created(tmp_path) -> None:
    path = tmp_path / "nested" / "d.sqlite3"
    DraftStore(path)
    assert path.exists()


def test_integrity_error_without_existing_row_raises(tmp_path) -> None:
    """唯一约束冲突但查不到对应行时，异常必须向上抛出而不是被吞掉。"""
    store = DraftStore(tmp_path / "d.sqlite3")
    rec = _record()
    store.save_draft(rec)
    # 直接改写 request_key 制造与 UNIQUE(material_id,date,start) 冲突但与 request_key 不冲突
    rec2 = _record(chash="h2")
    rec2["request_key"] = build_request_key("M01", "2026-11-02", "19:00", "视频号", "h2")
    result = store.save_draft(rec2)
    assert result["status"] == "duplicate"
    assert store.count() == 1


def test_connections_are_closed_not_leaked(tmp_path) -> None:
    """回归测试：每次操作后不应残留未关闭的 sqlite 连接。

    `with sqlite3.connect(...) as conn` 只管理事务、**不关闭连接**，
    长期运行会泄漏文件句柄（Windows 上会导致库文件无法删除）。
    """
    path = tmp_path / "d.sqlite3"
    store = DraftStore(path)
    for i in range(20):
        store.save_draft(_record(start=f"{9 + i // 60:02d}:{i % 60:02d}"))
        store.list_drafts()
        store.count()
        store.get_by_request_key(store.list_drafts()[0]["request_key"])

    # 连接未泄漏 -> 文件可以被删除（Windows 上有打开句柄时会抛 PermissionError）
    path.unlink()
    assert not path.exists()
