"""审计归档红测清单（甲路·物化包钉键路线，先于实现落笔）。

覆盖题面验收点：
1. 提交即刻写定入库，归档头与落点行入表
2. 列表台账串详情，读轨只认物化包
3. 校验戳记对不上即挡，不放行
4. 归档徽标对照归档键号，拧花可辨
5. 挡回刷：已归档行钉死，活审计可清
6. 徽标键号拧花即挡活审计改写
7. 两侧争写不加锁双成功 → 只许一胜
8. 落库写定失败须讲明白且不留半态
9. 钉点未移动拒绝重复钉定
10. 只留已选边，无乙路（旁路账册/写入令牌）骨架
"""

from __future__ import annotations

import threading

import pytest

from app.db import get_db
from app.services import archive, audit
from app.services.errors import ServiceError


def _events(db_path: str) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def _age_all_events(db_path: str, days: int = 60) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE audit_events SET created_at=datetime('now', ?)",
            (f"-{days} days",),
        )


def test_submit_persists_package_immediately(db_path):
    """提交即刻写定入库：归档头、落点行、钉点指针同时落表。"""
    before = _events(db_path)
    assert before, "种子应自带审计流水"
    arch = archive.create_archive(note="红测首钉", db_path=db_path)
    assert arch["archive_key"].startswith("AA-")
    assert arch["event_count"] == len(before)
    assert arch["first_event_id"] == before[0]["id"]
    assert arch["last_event_id"] == before[-1]["id"]
    assert len(arch["fingerprint"]) == 64
    with get_db(db_path) as conn:
        head = conn.execute(
            "SELECT * FROM audit_archives WHERE archive_key=?",
            (arch["archive_key"],),
        ).fetchone()
        assert head is not None, "归档头未落库"
        items = conn.execute(
            "SELECT * FROM audit_archive_items WHERE archive_id=? ORDER BY event_id",
            (head["id"],),
        ).fetchall()
        assert len(items) == len(before), "落点行未全部入表"
        ptr = conn.execute(
            "SELECT value FROM meta WHERE key='audit_archive_head'"
        ).fetchone()
        assert ptr and ptr["value"] == arch["archive_key"], "钉点指针未落库"


def test_list_queue_and_detail_reads_only_package(db_path):
    """列表台账串详情；读轨只认物化包，活审计被回刷不影响归档详情。"""
    arch = archive.create_archive(db_path=db_path)
    queue = archive.list_archives(db_path=db_path)
    assert queue, "归档队列形态面为空"
    assert queue[0]["archive_key"] == arch["archive_key"]
    detail = archive.get_archive(arch["id"], db_path=db_path)
    assert detail["archive_key"] == arch["archive_key"]
    assert len(detail["items"]) == arch["event_count"]
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE audit_events SET detail='被回刷' "
            "WHERE id=(SELECT MIN(id) FROM audit_events)"
        )
    detail2 = archive.get_archive(arch["id"], db_path=db_path)
    assert all(it["detail"] != "被回刷" for it in detail2["items"]), (
        "读轨未只认物化包，回刷渗进了归档详情"
    )


def test_tampered_package_stamp_mismatch_blocked(db_path):
    """校验戳记对不上却仍放行就挡掉：包内行被篡改 → 详情拒放、徽标拧花。"""
    arch = archive.create_archive(db_path=db_path)
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE audit_archive_items SET detail='篡改' "
            "WHERE archive_id=? AND event_id=("
            "  SELECT MIN(event_id) FROM audit_archive_items WHERE archive_id=?"
            ")",
            (arch["id"], arch["id"]),
        )
    with pytest.raises(ServiceError):
        archive.get_archive(arch["id"], db_path=db_path)
    assert archive.badge(db_path=db_path)["status"] == "拧花"


def test_badge_mirrors_head_key_and_detects_mismatch(db_path):
    """归档徽标对照归档键号：一致时同键，指针被改口即拧花。"""
    assert archive.badge(db_path=db_path)["status"] == "未归档"
    arch = archive.create_archive(db_path=db_path)
    badge = archive.badge(db_path=db_path)
    assert badge["status"] == "一致"
    assert badge["archive_key"] == arch["archive_key"]
    assert badge["fingerprint_short"] == arch["fingerprint"][:12]
    head = archive.head(db_path=db_path)
    assert head["archive_key"] == arch["archive_key"]
    assert head["last_event_id"] == arch["last_event_id"]
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE meta SET value='AA-9999' WHERE key='audit_archive_head'"
        )
    assert archive.badge(db_path=db_path)["status"] == "拧花"


def test_purge_freezes_archived_rows_but_cleans_live(db_path):
    """挡回刷：已归档行钉死不删，钉后活审计仍可清。"""
    arch = archive.create_archive(db_path=db_path)
    audit.write_event("新活审计", "setting", 0, "钉后新写", db_path=db_path)
    _age_all_events(db_path, 60)
    res = audit.purge_old(keep_days=30, db_path=db_path)
    assert res["blocked_archived"] == arch["event_count"]
    remaining = _events(db_path)
    assert len(remaining) == arch["event_count"] + 1, (
        "归档行须全留，仅余清理动作自写的一条活审计"
    )
    assert all(
        r["id"] <= arch["last_event_id"] or r["action"] == "清理审计"
        for r in remaining
    )


def test_mismatch_blocks_live_rewrite(db_path):
    """归档徽标对照归档键号拧花即挡活审计改写。"""
    archive.create_archive(db_path=db_path)
    _age_all_events(db_path, 60)
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE meta SET value='AA-0000' WHERE key='audit_archive_head'"
        )
    with pytest.raises(ServiceError):
        audit.purge_old(keep_days=30, db_path=db_path)


def test_concurrent_archive_single_winner(db_path):
    """争用再核：两侧仓管同时钉定，只许一胜，钉点不得互踩。"""
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[ServiceError] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(archive.create_archive(db_path=db_path))
        except ServiceError as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(20)
    t2.join(20)
    assert not t1.is_alive() and not t2.is_alive(), "归档线程卡死"
    assert len(results) == 1 and len(errors) == 1, (
        f"双成功或双失败即踩钉点: ok={len(results)} err={len(errors)}"
    )
    queue = archive.list_archives(db_path=db_path)
    assert len(queue) == 1, "落表归档须唯一"
    assert archive.badge(db_path=db_path)["archive_key"] == results[0]["archive_key"]


def test_write_failure_is_clear_and_leaves_no_partial_state(db_path):
    """落库写定失败须讲明白：报文明说，且不留半态、钉点不动。"""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_archives("
            "  archive_key, fingerprint, event_count, first_event_id, last_event_id"
            ") VALUES ('AA-0001', '占位', 0, 0, 0)"
        )
    with pytest.raises(ServiceError) as excinfo:
        archive.create_archive(db_path=db_path)
    assert "落库写定失败" in excinfo.value.message
    with get_db(db_path) as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM audit_archives").fetchone()["c"]
        assert cnt == 1, "失败归档不得留下第二条半态归档头"
        ptr = conn.execute(
            "SELECT value FROM meta WHERE key='audit_archive_head'"
        ).fetchone()
        assert ptr is None, "失败归档不得挪动钉点指针"


def test_no_new_events_refuses_repin(db_path):
    """钉点未移动：无新流水时拒绝重复钉定。"""
    archive.create_archive(db_path=db_path)
    with pytest.raises(ServiceError):
        archive.create_archive(db_path=db_path)


def test_purge_without_archive_still_works(db_path):
    """未归档时活审计清理不受挡（回归旧行为）。"""
    _age_all_events(db_path, 60)
    res = audit.purge_old(keep_days=30, db_path=db_path)
    assert res["removed"] >= 1
    assert res["blocked_archived"] == 0


def test_only_route_a_skeleton_remains(db_path):
    """只留已选边：物化包两表在，旁路账册/写入令牌等乙路骨架无存。"""
    with get_db(db_path) as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"audit_archives", "audit_archive_items"} <= tables
    forbidden = [t for t in tables if any(k in t for k in ("shadow", "sidecar", "token", "ledger_b"))]
    assert not forbidden, f"乙路分支骨架尚存: {forbidden}"
