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


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可提交读数"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


REMINDER_LOG_LIMIT = 200


def _iso(value):
    return value.isoformat() if value else None


async def list_reminders(request: web.Request) -> web.Response:
    """催领落地页数据：当前开关/阈值、催领清单、催领流水。登录即可只读。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    settings = await pool.fetchrow(
        """
        SELECT enabled, timeout_minutes, updated_by, updated_at
        FROM reminder_settings WHERE id = 1
        """
    )
    items = await pool.fetch(
        """
        SELECT cr.id, cr.reading_id, cr.probe_id,
               cr.waited_minutes, cr.reminded_by, cr.reminded_at,
               pr.status AS reading_status
        FROM claim_reminders cr
        JOIN probe_readings pr ON pr.id = cr.reading_id
        ORDER BY cr.id DESC
        """
    )
    logs = await pool.fetch(
        """
        SELECT id, action, reading_id, detail, operator, created_at
        FROM claim_reminder_logs
        ORDER BY id DESC
        LIMIT $1
        """,
        REMINDER_LOG_LIMIT,
    )
    return web.json_response(
        {
            "settings": {
                "enabled": settings["enabled"],
                "timeout_minutes": settings["timeout_minutes"],
                "updated_by": settings["updated_by"],
                "updated_at": _iso(settings["updated_at"]),
            },
            "items": [
                {
                    "id": r["id"],
                    "reading_id": r["reading_id"],
                    "probe_id": r["probe_id"],
                    "waited_minutes": r["waited_minutes"],
                    "reminded_by": r["reminded_by"],
                    "reminded_at": _iso(r["reminded_at"]),
                    "reading_status": r["reading_status"],
                }
                for r in items
            ],
            "logs": [
                {
                    "id": r["id"],
                    "action": r["action"],
                    "reading_id": r["reading_id"],
                    "detail": r["detail"],
                    "operator": r["operator"],
                    "created_at": _iso(r["created_at"]),
                }
                for r in logs
            ],
        }
    )


async def update_reminder_settings(request: web.Request) -> web.Response:
    """记录员设置超时分钟阈值与开关。值班员只读，返回 403。

    设置更新与开关流水在同一事务内提交。
    """
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可修改催领阈值与开关"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "开关必须是布尔值"}, ensure_ascii=False),
            content_type="application/json",
        )
    timeout_minutes = body.get("timeout_minutes")
    if (
        isinstance(timeout_minutes, bool)
        or not isinstance(timeout_minutes, int)
        or timeout_minutes < 1
    ):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "超时分钟数必须是不小于 1 的整数"}, ensure_ascii=False),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow(
                "SELECT enabled, timeout_minutes FROM reminder_settings WHERE id = 1 FOR UPDATE"
            )
            await conn.execute(
                """
                INSERT INTO reminder_settings (id, enabled, timeout_minutes, updated_by, updated_at)
                VALUES (1, $1, $2, $3, now())
                ON CONFLICT (id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    timeout_minutes = EXCLUDED.timeout_minutes,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """,
                enabled,
                timeout_minutes,
                user["username"],
            )
            changes = []
            if not old or bool(old["enabled"]) != enabled:
                changes.append(
                    (
                        "switch_on" if enabled else "switch_off",
                        None,
                        f"催领开关已{'打开' if enabled else '关闭'}"
                        + ("，关闭后停止新催领，已有催领行保留" if not enabled else ""),
                    )
                )
            if not old or int(old["timeout_minutes"]) != timeout_minutes:
                changes.append(
                    (
                        "threshold_changed",
                        None,
                        f"超时分钟阈值改为 {timeout_minutes} 分钟"
                        + (f"（原 {int(old['timeout_minutes'])} 分钟）" if old else ""),
                    )
                )
            for action, reading_id, detail in changes:
                await conn.execute(
                    """
                    INSERT INTO claim_reminder_logs (action, reading_id, detail, operator, created_at)
                    VALUES ($1, $2, $3, $4, now())
                    """,
                    action,
                    reading_id,
                    detail,
                    user["username"],
                )

    row = await pool.fetchrow(
        "SELECT enabled, timeout_minutes, updated_by, updated_at FROM reminder_settings WHERE id = 1"
    )
    return web.json_response(
        {
            "enabled": row["enabled"],
            "timeout_minutes": row["timeout_minutes"],
            "updated_by": row["updated_by"],
            "updated_at": _iso(row["updated_at"]),
        }
    )


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


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/reminders", list_reminders)
    app.router.add_put("/api/reminders/settings", update_reminder_settings)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
