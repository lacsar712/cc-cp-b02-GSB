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

-- 催领设置：单行表（id 恒为 1），记录员维护超时分钟阈值与开关
CREATE TABLE IF NOT EXISTS reminder_settings (
    id smallint PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    timeout_minutes integer NOT NULL DEFAULT 5,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reminder_settings_singleton CHECK (id = 1)
);

-- 催领清单：一条超时候审读数只入清单一次
CREATE TABLE IF NOT EXISTS claim_reminders (
    id serial PRIMARY KEY,
    reading_id integer NOT NULL UNIQUE REFERENCES probe_readings(id) ON DELETE CASCADE,
    probe_id text NOT NULL,
    waited_minutes integer NOT NULL,
    reminded_by text NOT NULL,
    reminded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claim_reminders_reading ON claim_reminders (reading_id);

-- 催领流水：与清单逐笔对应，只在清单入账的同一事务内写入
CREATE TABLE IF NOT EXISTS claim_reminder_logs (
    id serial PRIMARY KEY,
    action text NOT NULL,
    reading_id integer REFERENCES probe_readings(id) ON DELETE CASCADE,
    detail text NOT NULL,
    operator text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claim_reminder_logs_created ON claim_reminder_logs (created_at, id);

INSERT INTO reminder_settings (id, enabled, timeout_minutes)
VALUES (1, false, 5)
ON CONFLICT (id) DO NOTHING;
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
