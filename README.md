# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 候审超时催领

候审（`status='pending'`）太久可按规则催领。顶栏「催领」落地页包含**超时分钟阈值**开关与**催领流水**：

- 仅**记录员**可设置分钟数并打开/关闭催领开关；**值班员**只读清单与流水，改阈值返回 403。
- 开关开启后，接口进程每秒按**数据库时钟**对账一次：候审时间超过阈值且仍未认领的读数写入**催领清单**，同时追加一条**催领流水**；两者在**同一事务**提交，不会只写一半。
- 是否超时一律由服务端对账，**前端不做倒计时**。
- 关闭开关立即停止新催领；已有催领清单行与流水保留。
- 后台工人认领读数时也在同一认领事务内兜底对账，避免认领与超时同时发生时漏记。

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/reminder/config` | 登录用户 | 当前开关与阈值 |
| PUT | `/api/reminder/config` | 记录员 | `{"enabled": bool, "timeout_minutes": 1..43200}` |
| GET | `/api/reminder/entries` | 登录用户 | 催领清单 |
| GET | `/api/reminder/logs` | 登录用户 | 催领流水（最近 200 条） |

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
