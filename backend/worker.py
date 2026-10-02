"""后台工人。

1. 用 SKIP LOCKED 认领 pending 读数并写入合格/超温结论；
2. 按记录员设置的分钟阈值扫描“超时仍候审”的读数，把对账出的新增集合
   在同一个数据库事务内写入催领清单与催领流水。

是否超时一律以数据库 now() 为准，前端不得自行倒计时。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))
REMINDER_POLL_SECONDS = float(os.environ.get("REMINDER_POLL_SECONDS", "1.0"))


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


# 与“真实候审超时集合”对账：候审(pending/processing)且 created_at 早于
# now()-阈值、且尚未进入催领清单的读数。全部在单事务内完成读取与写入。
OVERDUE_SQL = """
    SELECT pr.id, pr.probe_id,
           GREATEST(1, EXTRACT(EPOCH FROM (now() - pr.created_at))::integer / 60)
               AS waited_minutes
    FROM probe_readings pr
    WHERE pr.status IN ('pending', 'processing')
      AND pr.created_at <= now() - (%s::integer * interval '1 minute')
      AND NOT EXISTS (
          SELECT 1 FROM claim_reminders cr WHERE cr.reading_id = pr.id
      )
    ORDER BY pr.id
    FOR UPDATE OF pr
"""


def scan_reminders(conn) -> int:
    """扫描一轮催领，返回新写入清单的条数。

    清单行与流水行在同一事务内提交：要么同时落库，要么一起回滚，
    不会出现只写一半。开关关闭时不产生任何写入。
    """
    added = 0
    with conn.transaction():
        settings = conn.execute(
            """
            SELECT enabled, timeout_minutes
            FROM reminder_settings
            WHERE id = 1
            FOR UPDATE
            """
        ).fetchone()
        if not settings or not settings["enabled"]:
            return 0

        timeout_minutes = int(settings["timeout_minutes"])
        overdue = conn.execute(OVERDUE_SQL, (timeout_minutes,)).fetchall()
        for r in overdue:
            waited_minutes = int(r["waited_minutes"])

            conn.execute(
                """
                INSERT INTO claim_reminders
                    (reading_id, probe_id, waited_minutes, reminded_by, reminded_at)
                VALUES (%s, %s, %s, 'system', now())
                """,
                (r["id"], r["probe_id"], waited_minutes),
            )
            conn.execute(
                """
                INSERT INTO claim_reminder_logs
                    (action, reading_id, detail, operator, created_at)
                VALUES ('reminded', %s, %s, 'system', now())
                """,
                (
                    r["id"],
                    f"探头{r['probe_id']} 候审已 {waited_minutes} 分钟，"
                    f"超过 {timeout_minutes} 分钟阈值，写入催领清单",
                ),
            )
            added += 1
    conn.commit()
    return added


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    next_reminder_scan = 0.0
    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False

        if time.monotonic() >= next_reminder_scan:
            try:
                with connect_sync() as conn:
                    scan_reminders(conn)
            except Exception as exc:
                print(f"reminder scan error: {exc}", flush=True)
            next_reminder_scan = time.monotonic() + REMINDER_POLL_SECONDS

        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
