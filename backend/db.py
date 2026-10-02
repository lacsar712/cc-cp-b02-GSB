import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 催领设置：单行（id = 1）。enabled 为开关，timeout_minutes 为超时阈值（分钟）。
CREATE TABLE IF NOT EXISTS reminder_settings (
    id int PRIMARY KEY DEFAULT 1,
    enabled boolean NOT NULL DEFAULT false,
    timeout_minutes integer NOT NULL DEFAULT 30,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reminder_settings_singleton CHECK (id = 1)
);
INSERT INTO reminder_settings (id, enabled, timeout_minutes)
VALUES (1, false, 30)
ON CONFLICT (id) DO NOTHING;

-- 催领清单：每个读数至多一行。是否超时一律由服务端按数据库时间判定。
CREATE TABLE IF NOT EXISTS reminder_entries (
    reading_id integer PRIMARY KEY REFERENCES probe_readings(id),
    timed_out_at timestamptz NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now()
);

-- 催领流水：每次把超时候审读数写入清单时追加一条；清单纯新增才追加。
CREATE TABLE IF NOT EXISTS reminder_log (
    id bigserial PRIMARY KEY,
    reading_id integer NOT NULL REFERENCES probe_readings(id),
    probe_id text NOT NULL,
    timed_out_at timestamptz NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now(),
    timeout_minutes integer NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminder_log_added ON reminder_log (added_at DESC);
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason),
        )
    conn.commit()
