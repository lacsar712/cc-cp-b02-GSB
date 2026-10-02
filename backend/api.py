import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
REMINDER_POLL_SECONDS = float(os.environ.get("REMINDER_POLL_SECONDS", "1"))
MIN_TIMEOUT_MINUTES = 1
MAX_TIMEOUT_MINUTES = 60 * 24 * 30
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request, detail: str = "仅记录员可提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": detail}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, now())
        RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        """,
        probe_id,
        temp_c,
        user["username"],
    )
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


# ---------------- 候审超时催领 ----------------
#
# 规则：
# 1. 是否超时一律由服务端按数据库时钟对账（status='pending' 且 created_at 超过阈值），
#    前端不得自行倒计时或判定。
# 2. 开关关闭时对账循环立即跳过，不产生任何新催领；已有清单行永久保留。
# 3. 新增清单行与追加催领流水在同一事务内完成，不会只写一半。


def _config_payload(row: asyncpg.Record) -> dict:
    return {
        "enabled": row["enabled"],
        "timeout_minutes": row["timeout_minutes"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def get_reminder_config(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        SELECT enabled, timeout_minutes, updated_by, updated_at
        FROM reminder_settings WHERE id = 1
        """
    )
    return web.json_response(_config_payload(row))


async def update_reminder_config(request: web.Request) -> web.Response:
    user = require_writer(request, "仅记录员可修改催领阈值与开关")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "请求体必须是 JSON 对象"}, ensure_ascii=False),
            content_type="application/json",
        )

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "开关必须是布尔值"}, ensure_ascii=False),
            content_type="application/json",
        )
    # bool 是 int 的子类，先挡掉 True/False 混入分钟数
    if isinstance(body.get("timeout_minutes"), bool) or not isinstance(
        body.get("timeout_minutes"), int
    ):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "超时分钟数必须是整数"}, ensure_ascii=False),
            content_type="application/json",
        )
    timeout_minutes = body["timeout_minutes"]
    if not (MIN_TIMEOUT_MINUTES <= timeout_minutes <= MAX_TIMEOUT_MINUTES):
        raise web.HTTPBadRequest(
            text=json.dumps(
                {"detail": f"超时分钟数须在 {MIN_TIMEOUT_MINUTES} 到 {MAX_TIMEOUT_MINUTES} 之间"},
                ensure_ascii=False,
            ),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        UPDATE reminder_settings
        SET enabled = $1,
            timeout_minutes = $2,
            updated_by = $3,
            updated_at = now()
        WHERE id = 1
        RETURNING enabled, timeout_minutes, updated_by, updated_at
        """,
        enabled,
        timeout_minutes,
        user["username"],
    )
    return web.json_response(_config_payload(row))


async def list_reminder_entries(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT e.reading_id,
               r.probe_id,
               r.temp_c,
               r.status,
               r.created_by,
               r.created_at,
               e.timed_out_at,
               e.added_at
        FROM reminder_entries e
        JOIN probe_readings r ON r.id = e.reading_id
        ORDER BY e.added_at DESC, e.reading_id DESC
        """
    )
    return web.json_response(
        [
            {
                "reading_id": r["reading_id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "timed_out_at": r["timed_out_at"].isoformat() if r["timed_out_at"] else None,
                "added_at": r["added_at"].isoformat() if r["added_at"] else None,
            }
            for r in rows
        ]
    )


async def list_reminder_logs(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, reading_id, probe_id, timed_out_at, added_at, timeout_minutes
        FROM reminder_log
        ORDER BY id DESC
        LIMIT 200
        """
    )
    return web.json_response(
        [
            {
                "id": r["id"],
                "reading_id": r["reading_id"],
                "probe_id": r["probe_id"],
                "timed_out_at": r["timed_out_at"].isoformat() if r["timed_out_at"] else None,
                "added_at": r["added_at"].isoformat() if r["added_at"] else None,
                "timeout_minutes": r["timeout_minutes"],
            }
            for r in rows
        ]
    )


async def sweep_reminders_once(pool: asyncpg.Pool) -> int:
    """把“超时仍候审”且尚未入清单的读数，在单事务内写入清单并追加流水。

    返回本次新增的催领条数。开关关闭时直接返回 0。
    """
    async with pool.acquire() as conn:
        setting = await conn.fetchrow(
            "SELECT enabled, timeout_minutes FROM reminder_settings WHERE id = 1"
        )
        if not setting or not setting["enabled"]:
            return 0
        timeout_minutes = setting["timeout_minutes"]
        async with conn.transaction():
            # 同一 SQL 语句（同一事务）：先对账出超时集合，再写清单，最后写流水。
            affected = await conn.execute(
                """
                WITH overdue AS (
                    SELECT r.id, r.probe_id, r.created_at
                    FROM probe_readings r
                    WHERE r.status = 'pending'
                      AND r.created_at <= now() - make_interval(mins => $1::int)
                      AND NOT EXISTS (
                          SELECT 1 FROM reminder_entries e
                          WHERE e.reading_id = r.id
                      )
                ),
                inserted AS (
                    INSERT INTO reminder_entries (reading_id, timed_out_at)
                    SELECT id, created_at + make_interval(mins => $1::int)
                    FROM overdue
                    RETURNING reading_id, timed_out_at, added_at
                )
                INSERT INTO reminder_log
                    (reading_id, probe_id, timed_out_at, added_at, timeout_minutes)
                SELECT i.reading_id, o.probe_id, i.timed_out_at, i.added_at, $1
                FROM inserted i
                JOIN overdue o ON o.id = i.reading_id
                """,
                timeout_minutes,
            )
        # asyncpg execute 返回如 "INSERT 0 3"
        try:
            return int(affected.split()[-1])
        except (AttributeError, ValueError):
            return 0


async def reminder_sweeper(app: web.Application) -> None:
    pool: asyncpg.Pool = app["pool"]
    while True:
        try:
            n = await sweep_reminders_once(pool)
            if n:
                print(f"reminder sweep: {n} 条超时候审已入催领清单", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 对账失败不拖垮接口进程
            print(f"reminder sweep error: {exc}", flush=True)
        await asyncio.sleep(REMINDER_POLL_SECONDS)


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)
    app["reminder_task"] = asyncio.create_task(reminder_sweeper(app))


async def on_cleanup(app: web.Application) -> None:
    task = app.get("reminder_task")
    if task:
        task.cancel()
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/reminder/config", get_reminder_config)
    app.router.add_put("/api/reminder/config", update_reminder_config)
    app.router.add_get("/api/reminder/entries", list_reminder_entries)
    app.router.add_get("/api/reminder/logs", list_reminder_logs)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
