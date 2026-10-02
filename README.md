# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 候审催领

顶栏「催领」为催领落地页：

- 记录员设置**超时分钟阈值**并打开开关；服务端工人每 `REMINDER_POLL_SECONDS`（默认 1 秒）按数据库时间扫描候审（`pending`/`processing`）超过阈值的读数，把对账出的新增集合在**同一事务**内写入催领清单与催领流水。
- 关闭开关立即停止新催领，已有催领行与流水保留；重新打开后补催关闭期间积压的超时单。
- 一条候审单只入清单一次；清单始终与真实候审超时集合对账，**前端不做倒计时**，是否超时只由服务端 `now()` 判定。
- 值班员只读催领清单与流水，不可改阈值与开关。

| 接口 | 权限 | 说明 |
|------|------|------|
| `GET /api/reminders` | 登录用户 | 开关/阈值、催领清单、催领流水（只读） |
| `PUT /api/reminders/settings` | 仅记录员 | `{enabled, timeout_minutes}`，设置与开关流水同事务落库 |


## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
