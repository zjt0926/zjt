## 1. 仓库与文档（T1）

- [x] 1.1 添加 `requirements.txt`、应用入口（如 `run.py` 或 gunicorn 模块路径）与 `app/` 包骨架 — verify: `pip install -r requirements.txt` 后 `python -c "import app"` 无报错

## 2. 数据模型与种子（T2）

- [x] 2.1 实现 `scripts/init_db.py`：创建班级、用户、讲义、作业、助手、技能、材料、知识库（或等价）表 — verify: 执行脚本后 `data/app.db` 存在且 `.schema` 含上述表名
- [x] 2.2 种子：班级 A/B、教师 A、学生 A1/B1；A/B 班各至少一条可区分标题的材料；用户密码写入 `password_hash` — verify: `sqlite3 data/app.db "SELECT username, role FROM users"` 含 teacher_a、student_a1、student_b1
- [x] 2.3 实现 `app/db.py`（连接、参数化查询、按 `class_id` 列表材料）— verify: 临时脚本对 class A 调用列表函数返回条数 ≥ 1 且不含 B 班标题

## 3. 登录、会话与密码（T3）

- [x] 3.1 实现 `/login` 页面与 POST 登录逻辑；session 写入 `role`、`class_id`；`SECRET_KEY` 仅来自环境变量 — verify: 教师 A、学生 A1 登录后会话字段正确；未设置 `SECRET_KEY` 时启动行为与 design 一致
- [x] 3.2 实现登出与 `login_required`：未登录访问 `/materials` 重定向登录页 — verify: 清 cookie 后 GET `/materials` 为 302 至 `/login` 且响应体无材料数据
- [x] 3.3 密码 verify 走哈希；错误密码登录失败 — verify: 错误口令无法建立 session；库中 password 字段非明文

## 4. 班级隔离（T4）

- [x] 4.1 材料列表 API/页面仅查询 `session.class_id` — verify: 学生 A1 登录后列表仅含 A 班标题，不出现 B 班样本标题
- [x] 4.2 按 material id 访问时校验 `class_id` — verify: 教师 A 请求 B 班材料 id 返回 403 或 404（与 README 一致）且响应无 B 班正文/标题
- [x] 4.3 确认路由不接受客户端传入的 `class_id` 覆盖会话 — verify: 篡改 query/body 中的 class 仍只能看到本班或越权被拒

## 5. 角色权限与上传入库（T5）

- [x] 5.1 学生 POST 上传返回 403；DB 与 uploads 无新增 — verify: 学生 A1 上传后 `materials`/`knowledge_entries` 行数与上传前相同
- [x] 5.2 教师上传：落盘 → 解析 txt/md → 同事务写入 materials + knowledge_entries，`class_id` 来自 session — verify: 教师 A 上传后两表各增 1 行且 class 为 A
- [x] 5.3 上传成功后本班列表可见；学生 A1 刷新列表可见新条但仍无法上传 — verify: 教师上传后 A1 列表含新标题；A1 POST 上传仍 403
- [x] 5.4 解析失败或非法扩展名返回 4xx 且无脏数据 — verify: 上传 `.exe` 或故意损坏文件后无孤立 DB 行

## 6. Docker Compose 与 GET /health（T6）

- [x] 6.1 添加 `Dockerfile`、`docker-compose.yml`、`.env.example`；挂载 `./data`、`./uploads` — verify: `docker compose up --build -d` 后容器 running
- [x] 6.2 实现 `GET /health` 无需登录；compose 配置 healthcheck — verify: `curl -sf http://localhost:8080/health` 返回 200 JSON
- [x] 6.3 容器 entrypoint/启动时自动 init_db（若库不存在）— verify: 删 `data/app.db` 后 compose up 仍可登录预置账号
- [x] 6.4 `docker compose down` 再 `up`（不删 volume）后预置与上传数据仍在 — verify: 上传一条后再 down/up，列表仍含该条
- [x] 6.5 README 补充 Compose 启动步骤、登录 URL、health URL、`.env` 说明 — verify: 按 README 从零可打开登录页并 curl health

## 7. 规约与收尾

- [x] 7.1 运行 `openspec validate add-auth-rbac-class-knowledge --strict` — verify: 命令 exit 0 且无 error（实际变更名为 `add-auth-rbac-class-materials`，`openspec validate add-auth-rbac-class-materials --strict` exit 0）
- [x] 7.2 手工对照 spec 关键 Scenario：跨班被拒、学生上传 403、上传后列表可查 — verify: 记录三条验收结果（通过/失败）于 PR 或实验报告

