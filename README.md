# CampusClaw

面向中小学的教研智能体最小可运行栈：教师与学生以角色登录，以班级为数据边界隔离教学材料；教师上传材料后解析入库，本班成员列表可见。

## 场景

- 教师登录后上传 `.txt` / `.md` 材料，材料与知识库条目关联本班并入库。
- 学生登录后仅可查看本班材料（只读），上传接口返回 403。
- A 班用户无法读取 B 班材料（按 ID 跨班访问返回 404，响应不泄露他班标题/正文）。

## 不做

不做 RAG/智能问答、聊天助手、作业流程、SSO/OAuth、多租户与生产级高可用（详见 change `add-auth-rbac-class-materials` 的 proposal）。

## 预置账号

| 用户名 | 密码 | 角色 | 班级 |
| --- | --- | --- | --- |
| `teacher_a` | `teacherpass` | 教师 | 班级 A |
| `student_a1` | `studentpass` | 学生 | 班级 A |
| `student_b1` | `studentpass` | 学生 | 班级 B |

## Docker Compose 启动

```bash
cp .env.example .env
# 编辑 .env，填入 SECRET_KEY（必须）
docker compose up --build -d
```

- 登录页：<http://localhost:8080/login>
- 材料列表：<http://localhost:8080/materials>
- 健康检查：<http://localhost:8080/health>（`GET`，无需登录，返回 `{"status":"ok"}`）

`./data`（SQLite）与 `./uploads`（上传文件）通过 volume 挂载，`docker compose down` 后数据仍在。容器首次启动时若 `data/app.db` 不存在会自动执行 `scripts/init_db.py` 建表并写入种子数据。

## 本地开发

```bash
pip install -r requirements.txt
export SECRET_KEY=your-secret   # Windows: set SECRET_KEY=your-secret
python scripts/init_db.py        # 可选：首次初始化
python run.py                    # http://localhost:8080
```

## 安全说明

- 密码使用 bcrypt 哈希存储（`password_hash` 列，`$2b$` 前缀），禁止明文。
- 会话签名密钥 `SECRET_KEY` 仅通过环境变量注入，源码不含默认值；缺失时应用启动失败。
- 所有材料/知识库查询在服务端按 `session.class_id` 过滤，路由忽略客户端传入的 `class_id`。
- 跨班按 ID 访问统一返回 **404**（不区分"不存在"与"无权"，避免泄露他班记录是否存在）。
