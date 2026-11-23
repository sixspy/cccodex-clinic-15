"""审计归档服务（甲路·物化包钉键路线，已拍板选定边）。

形态：提交即刻把当前未钉审计流水物化成只读包——
归档头（audit_archives）记下当场键号与键指纹，
落点行（audit_archive_items）整段拷入物化包本体。
读轨只认物化包；钉点之前（含钉点）的活审计行拒绝回刷。
写侧 BEGIN IMMEDIATE 抢锁，两侧争写只许一胜，败者拿到明白话。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from app.db import _meta_get, _meta_set, get_db
from app.services.errors import ServiceError

_META_SEQ = "audit_archive_seq"
_META_HEAD = "audit_archive_head"


def _fingerprint(items: list[dict]) -> str:
    canon = [
        {
            "event_id": it["event_id"],
            "actor": it["actor"],
            "action": it["action"],
            "entity_type": it["entity_type"],
            "entity_id": it["entity_id"],
            "detail": it["detail"],
            "created_at": it["created_at"],
        }
        for it in items
    ]
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _latest(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM audit_archives ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def _items(conn: sqlite3.Connection, archive_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT event_id, actor, action, entity_type, entity_id, detail, created_at
        FROM audit_archive_items
        WHERE archive_id=?
        ORDER BY event_id
        """,
        (archive_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _verify(latest: dict, items: list[dict]) -> bool:
    if len(items) != latest["event_count"]:
        return False
    return _fingerprint(items) == latest["fingerprint"]


def _consistency(conn: sqlite3.Connection) -> tuple[str, dict | None]:
    """归档徽标对照归档键号：一致 / 拧花 / 未归档。"""
    latest = _latest(conn)
    head_ptr = _meta_get(conn, _META_HEAD, "")
    if latest is None:
        return ("拧花" if head_ptr else "未归档", None)
    if not head_ptr or head_ptr != latest["archive_key"]:
        return ("拧花", latest)
    items = _items(conn, latest["id"])
    if not _verify(latest, items):
        return ("拧花", latest)
    return ("一致", latest)


def pinned_last_id(conn: sqlite3.Connection) -> int:
    latest = _latest(conn)
    return int(latest["last_event_id"]) if latest else 0


def guard_live_rewrite(conn: sqlite3.Connection) -> None:
    """活审计改写闸门：徽标键号拧花即挡。"""
    status, _ = _consistency(conn)
    if status == "拧花":
        raise ServiceError("归档徽标与归档键号拧花，已挡活审计改写", 409)


def create_archive(
    note: str = "",
    actor: str = "admin",
    db_path: str | None = None,
) -> dict:
    """提交归档：即刻写定入库。失败讲明白，不留半态。"""
    try:
        with get_db(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            pinned = pinned_last_id(conn)
            rows = conn.execute(
                """
                SELECT id, actor, action, entity_type, entity_id, detail, created_at
                FROM audit_events
                WHERE id > ?
                ORDER BY id
                """,
                (pinned,),
            ).fetchall()
            if not rows:
                raise ServiceError(
                    "审计归档钉点未移动：无新流水可钉，拒绝重复钉定", 409
                )
            items = [
                {
                    "event_id": r["id"],
                    "actor": r["actor"],
                    "action": r["action"],
                    "entity_type": r["entity_type"],
                    "entity_id": r["entity_id"],
                    "detail": r["detail"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            seq = int(_meta_get(conn, _META_SEQ, "0") or "0") + 1
            archive_key = f"AA-{seq:04d}"
            fingerprint = _fingerprint(items)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO audit_archives(
                      archive_key, fingerprint, event_count,
                      first_event_id, last_event_id, note, actor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        archive_key,
                        fingerprint,
                        len(items),
                        items[0]["event_id"],
                        items[-1]["event_id"],
                        (note or "").strip(),
                        actor or "admin",
                    ),
                )
                archive_id = cur.lastrowid
                conn.executemany(
                    """
                    INSERT INTO audit_archive_items(
                      archive_id, event_id, actor, action,
                      entity_type, entity_id, detail, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            archive_id,
                            it["event_id"],
                            it["actor"],
                            it["action"],
                            it["entity_type"],
                            it["entity_id"],
                            it["detail"],
                            it["created_at"],
                        )
                        for it in items
                    ],
                )
                _meta_set(conn, _META_SEQ, str(seq))
                _meta_set(conn, _META_HEAD, archive_key)
            except sqlite3.Error as exc:
                raise ServiceError(f"审计归档落库写定失败：{exc}", 500) from exc
            row = conn.execute(
                "SELECT * FROM audit_archives WHERE id=?", (archive_id,)
            ).fetchone()
            return dict(row)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise ServiceError(
                "审计归档争用：他端正在钉定，请稍后重试", 409
            ) from exc
        raise


def list_archives(db_path: str | None = None) -> list[dict]:
    """归档队列形态面：新钉在前。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_archives ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_archive(archive_id: int, db_path: str | None = None) -> dict:
    """台账串详情：读轨只认物化包，校验戳记对不上即挡。"""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM audit_archives WHERE id=?", (int(archive_id),)
        ).fetchone()
        if not row:
            raise ServiceError("审计归档不存在", 404)
        head = dict(row)
        items = _items(conn, head["id"])
        if not _verify(head, items):
            raise ServiceError("审计归档校验戳记对不上，已挡放行", 409)
        head["items"] = items
        return head


def badge(db_path: str | None = None) -> dict:
    """归档徽标（台参数页亮起）：键号、指纹短码、对照状态。"""
    with get_db(db_path) as conn:
        status, latest = _consistency(conn)
        if latest is None:
            return {
                "status": status,
                "archive_key": None,
                "fingerprint_short": None,
                "event_count": 0,
                "last_event_id": 0,
                "created_at": None,
            }
        return {
            "status": status,
            "archive_key": latest["archive_key"],
            "fingerprint_short": latest["fingerprint"][:12],
            "event_count": latest["event_count"],
            "last_event_id": latest["last_event_id"],
            "created_at": latest["created_at"],
        }


def head(db_path: str | None = None) -> dict:
    """归档键号（邻页亮起）：当前钉点。"""
    return badge(db_path)
