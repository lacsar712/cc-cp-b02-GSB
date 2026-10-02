"""后台工人：用 SKIP LOCKED 认领 pending 读数并写入合格/超温结论。

认领瞬间若读数已超过催领阈值且催领开关开启，则在同一事务内补写催领清单与
流水（与接口进程的周期对账互为兜底；NOT EXISTS 与主键保证不重复）。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id, probe_id, temp_c
            FROM probe_readings
            WHERE status = 'pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        # 认领行锁内对账：超时候审且尚未入清单者，清单与流水同事务写入。
        conn.execute(
            """
            WITH setting AS (
                SELECT enabled, timeout_minutes
                FROM reminder_settings WHERE id = 1
            ),
            eligible AS (
                SELECT pr.id AS reading_id,
                       pr.probe_id,
                       pr.created_at + make_interval(mins => s.timeout_minutes) AS timed_out_at,
                       s.timeout_minutes
                FROM probe_readings pr
                CROSS JOIN setting s
                WHERE pr.id = %s
                  AND s.enabled
                  AND pr.created_at <= now() - make_interval(mins => s.timeout_minutes)
                  AND NOT EXISTS (
                      SELECT 1 FROM reminder_entries e WHERE e.reading_id = pr.id
                  )
            ),
            inserted AS (
                INSERT INTO reminder_entries (reading_id, timed_out_at)
                SELECT reading_id, timed_out_at FROM eligible
                RETURNING reading_id, timed_out_at, added_at
            )
            INSERT INTO reminder_log
                (reading_id, probe_id, timed_out_at, added_at, timeout_minutes)
            SELECT i.reading_id, e.probe_id, i.timed_out_at, i.added_at, e.timeout_minutes
            FROM inserted i
            JOIN eligible e ON e.reading_id = i.reading_id
            """,
            (row["id"],),
        )
        conn.execute(
            "UPDATE probe_readings SET status = 'processing' WHERE id = %s",
            (row["id"],),
        )
        return row


def finish(conn, reading_id: int, temp_c: float) -> None:
    verdict, reason = judge_temp(temp_c)
    conn.execute(
        """
        UPDATE probe_readings
        SET status = 'done', verdict = %s, reason = %s, processed_at = now()
        WHERE id = %s
        """,
        (verdict, reason, reading_id),
    )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(conn, row["id"], float(row["temp_c"]))
    except Exception:
        conn.execute(
            "UPDATE probe_readings SET status = 'pending' WHERE id = %s",
            (row["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
