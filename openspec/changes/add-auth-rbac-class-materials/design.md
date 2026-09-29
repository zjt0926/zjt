## Context

仓库根已有 README 三行说明与 OpenSpec change 四件套；尚无业务应用代码。动机见 `proposal.md`；可验收行为见 `specs/auth-upload/spec.md`。Apply 阶段在本仓库实现 CampusClaw 最小可运行栈。

## Goals / Non-Goals

**Goals:**

- 登录可用，会话携带 `role` 与 `class_id`。
- 权限在服务端可判定（学生上传 403）。
- 班级隔离不可通过改前端绕过（查询与按 ID 访问均校验班级）。
- 教师上传链路：落盘 → 解析 → 双表入库 → 列表查库可见。
- `docker compose up` 一键启动；`GET /health` 供探活；密钥与 DB 路径可配置。

**Non-Goals:**

- 见 `proposal.md` Non-goals（检索问答、对话、作业、SSO、生产 HA 等）。

---

## 会话与密码

### 会话模型

- 采用 **Flask 服务端会话**（signed cookie）：登录成功后设置 HttpOnly、`SameSite=Lax` 的 session cookie。
- Session payload 至少含：`user_id`、`role`（`teacher` | `student`）、`class_id`。
- 签名密钥 **`SECRET_KEY` 仅从环境变量读取**（Compose `env_file: .env`）；禁止在源码中写死生产密钥。
- 受保护路由使用统一前置逻辑（装饰器或 `before_request`）：无有效 session → 页面 `302` 至 `/login`；API 返回 `401` JSON。

### 密码存储

- 用户表字段 `password_hash`（禁止 `password` 明文列）。
- 种子脚本与（若有）改密逻辑使用 **bcrypt**（或 argon2）；登录时用库提供的 verify（如 `werkzeug.security.check_password_hash` 或 `bcrypt.checkpw`）。
- 比较 SHOULD 使用恒定时间语义，避免时序侧信道（依赖库默认行为即可）。

### 登出

- `POST /logout` 或 `GET /logout` 清除 session，重定向登录页（实现细节，spec 未强制路径名）。

---

## 技术栈：Flask + SQLite

| 层次 | 选择 | 说明 |
| --- | --- | --- |
| Web | Flask 3.x | 路由、模板 SSR 材料列表/登录页；上传与 JSON API 同进程 |
| DB | SQLite 3 | 单文件 `data/app.db`，标准库 `sqlite3` 或薄封装 |
| 密码 | bcrypt / werkzeug | `requirements.txt` 显式列出 |
| 运行 | gunicorn（Compose 内）或 `flask run`（本地 dev） | Compose 生产式入口推荐 gunicorn 绑定 `0.0.0.0:8080` |

**备选栈：** Node + Express + better-sqlite3 —— 行为等价即可，本 design 按 Python 栈写 tasks；若换栈须同步改 tasks 命令但 spec 不变。

### 模块划分（建议）

```
app/
  __init__.py      # create_app(), SECRET_KEY 校验
  auth.py          # login/logout, login_required
  materials.py     # 列表、按 id、upload
  knowledge.py     # parse_file + insert knowledge_entries
  db.py            # get_conn, 带 class_id 的查询 helper
  templates/       # login.html, materials.html
scripts/init_db.py
```

---

## 服务端班级隔离

隔离 **MUST 实现在服务端**，不得仅隐藏前端按钮。

### 列表与集合查询

- 所有 `SELECT` 材料/知识库条目时 **强制** `WHERE class_id = :session_class_id`。
- 封装为 `list_materials(class_id)` 等函数，业务路由禁止传入客户端可控的 `class_id` 覆盖会话（管理员场景本 change 无）。

### 按 ID 访问单条资源

1. `SELECT`  by `id`（可不带 class 条件以检测越权）。
2. 若行不存在 → `404`。
3. 若 `row.class_id != session['class_id']` → **`403` 或 `404`（择一，在 README 固定）**，响应体不得含他班标题/正文/路径。

### 上传与写入

- 新记录的 `class_id` **只取自 session**，忽略表单中的 class 字段（若有则丢弃或拒绝）。
- 教师仅能在本会话班级写入；不存在「代传 B 班」API。

### 测试关注点

- 学生 A1 列表不见 B 班标题；教师 A 用 B 班 material id 访问被拒；与 spec 三个班级 Scenario 一一对应。

## 上传与入库数据流

```
Client (teacher, multipart/form-data)
  POST /api/materials/upload  或  POST /materials/upload
    │
    ├─ login_required + role == teacher  else 403
    ├─ 校验文件扩展名/大小（MVP: .txt, .md）
    ├─ 保存 uploads/{class_id}/{uuid}_{safe_filename}
    ├─ parse → plain text（失败则删文件，返回 400，无 DB 行）
    ├─ BEGIN TRANSACTION
    │     INSERT materials (title, class_id, file_path, uploaded_by, ...)
    │     INSERT knowledge_entries (material_id, class_id, body_text, ...)
    ├─ COMMIT
    └─ 201 { "material_id": ... }

Client GET /materials  (或 /api/materials)
    │
    ├─ login_required
    ├─ rows = list_materials(session.class_id)   # 仅本班
    └─ 200 HTML 或 JSON
```

- **标题**：默认取自文件名或表单字段；须在本班列表可展示。
- **知识库**：MVP 将解析全文或摘要写入 `body_text`；后续 RAG 不在本 change。
- **学生**：同一 GET 列表 API，无 POST 上传路由权限。

## Docker Compose 与 GET /health

### 文件约定

- `Dockerfile`：多阶段或单阶段安装依赖、复制 `app/`、`scripts/`；`CMD` 启动 gunicorn 或等价。
- `docker-compose.yml`：
  - service `app`，`build: .`，`ports: ["8080:8080"]`（或与 README 一致）。
  - `volumes`: `./data:/app/data`，`./uploads:/app/uploads`。
  - `env_file: .env`；`environment` 可覆盖 `SECRET_KEY`。
  - `healthcheck`: 例如 `curl -f http://127.0.0.1:8080/health` 或 `wget -qO- ...`。
- `.env.example`：列出 `SECRET_KEY=` 占位与说明；**不提交真实密钥**。

### 启动与初始化

- 容器 entrypoint 或首次启动：若 `data/app.db` 不存在则运行 `python scripts/init_db.py`（建表 + 种子 A/B 班、用户、样本材料）。
- README 步骤：`cp .env.example .env` → 填 `SECRET_KEY` → `docker compose up --build` → 打开 `http://localhost:8080/login`。

### GET /health

- 路由：`GET /health`，**无需登录**。
- 响应：`200`，`Content-Type: application/json`，body 如 `{"status":"ok"}`。
- 可选：检测 SQLite 文件可读；失败时返回 `503`（本 change 可简化为进程存活即 ok）。
- Compose `healthcheck` 依赖此端点，避免「容器 up 但应用未就绪」。

---

## Decisions（摘要）

| # | 决策 | 备选 |
| --- | --- | --- |
| 1 | Flask + SQLite | Node + Express |
| 2 | Signed cookie session | JWT |
| 3 | bcrypt 密码哈希 | PBKDF2-only |
| 4 | 单库 + class_id 过滤 | 每班独立库 |
| 5 | MVP 解析 txt/md | PDF 管道后置 |

## Risks / Trade-offs

- [PDF 解析复杂] → 首版仅 txt/md；失败场景 spec 已要求无脏数据。
- [403 vs 404 跨班] → 在 README 与 tasks 验收中固定一种。
- [Flask dev server] → Compose 内用 gunicorn。

## Migration Plan

从零起步：由 `init_db.py` 建表与种子，无旧数据需要迁移。实现顺序见 `tasks.md`（数据 → 登录 → 隔离 → 上传 → Compose → validate）。

## Open Questions

（无。）