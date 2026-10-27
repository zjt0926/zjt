## Purpose

让教师与学生以各自角色登录 CampusClaw，以班级作为数据边界隔离教学材料；教师上传的内容解析后写入知识库，本班成员通过列表查看；越权上传、跨班访问与未登录访问在服务端被拒绝。

## ADDED Requirements

### Requirement: 用户登录

系统 MUST 提供教师（teacher）、学生（student）两类角色的账号密码登录；登录成功后 MUST 建立服务端会话；未登录用户访问受保护页面 MUST 被引导到登录页，且 MUST NOT 泄露任何业务数据。

#### Scenario: 教师与学生登录成功

- **WHEN** 用户使用有效账号与密码发起登录（预置账号含教师 A、学生 A1、学生 B1）
- **THEN** 登录 MUST 成功并建立服务端会话
- **AND** 会话中 MUST 记录用户标识、角色（teacher 或 student）与所属班级（如班级 A 或 B）

#### Scenario: 错误密码登录失败

- **WHEN** 用户提供存在用户名但错误密码
- **THEN** 登录 MUST 失败
- **AND** 系统 MUST NOT 建立有效会话
- **AND** 响应 MUST NOT 暗示密码哪一位错误以外的敏感信息（如不返回密码哈希）

#### Scenario: 未登录访问受保护页面

- **WHEN** 未携带有效会话的用户请求受保护页面（如材料列表）或受保护 API
- **THEN** 对页面请求 MUST 重定向到登录页
- **AND** 对 API 请求 MUST 返回未授权（如 HTTP 401）
- **AND** 响应 MUST NOT 包含任何本班或他班材料标题、正文或文件路径

### Requirement: 角色权限

系统 MUST 按会话中的角色授权：教师可上传与管理本班材料；学生对本班材料只读；学生调用上传或管理接口 MUST 被拒绝。

#### Scenario: 学生上传被拒绝（403）

- **WHEN** 以 student 角色（如学生 A1）的有效会话向材料上传接口提交文件
- **THEN** 系统 MUST 拒绝请求并返回 HTTP 403（或等价语义的错误码）
- **AND** `materials` 与知识库相关表 MUST 无新增或变更记录
- **AND** 上传目录 MUST 无因该请求产生的新文件（或事务回滚后不可见）

#### Scenario: 教师上传被允许

- **WHEN** 以 teacher 角色（如教师 A）的有效会话向材料上传接口提交支持的文件
- **THEN** 系统 MUST 接受请求并执行入库流程（见「材料上传与知识库入库」）
- **AND** 返回 MUST 表示成功（如 HTTP 201）并含新材料标识

#### Scenario: 学生仅只读本班列表

- **WHEN** 学生 A1 登录后打开本班材料列表
- **THEN** 系统 MUST 展示本班可读材料
- **AND** 页面或 API MUST NOT 提供学生可用的上传或删除本班材料的写操作入口（服务端仍 MUST 拒绝写 API，见上一 Scenario）

### Requirement: 班级隔离

班级是数据边界。所有材料、知识库及相关业务查询 MUST 在服务端按会话中的班级标识过滤；A 班用户 MUST NOT 读取、修改或删除 B 班材料；前端隐藏按钮 MUST NOT 作为满足本要求的唯一手段。

#### Scenario: 跨班按 ID 访问材料被拒绝

- **WHEN** 班级 A 的用户（学生 A1 或教师 A）通过 URL、API 路径参数或请求体指定 B 班某条材料的 ID 发起访问
- **THEN** 系统 MUST 拒绝访问（HTTP 403 或 404，实现择一并在文档中固定）
- **AND** 响应 MUST NOT 返回 B 班材料的标题、正文片段、文件路径或存储键

#### Scenario: 跨班枚举列表不得泄露 B 班

- **WHEN** 学生 A1 登录后请求材料列表（含分页、搜索参数为空或任意合法值）
- **THEN** 返回集合中每一条记录的班级归属 MUST 均为班级 A
- **AND** MUST NOT 出现 B 班预置材料的可区分标题（如标题中含「B 班」的样本）

#### Scenario: 材料列表仅含本班记录

- **WHEN** 教师 A 登录后打开本班材料列表
- **THEN** 列表中每一条记录 MUST 归属于班级 A
- **AND** 列表数据 MUST 来自数据库查询且查询条件含班级 A 的过滤

### Requirement: 材料上传与知识库入库

教师上传教学材料后，系统 MUST 将文件落盘、解析文本（或等价内容）并写入知识库记录；材料与知识库条目 MUST 关联上传者所属班级；本班材料列表 MUST 从数据库查询展示；上传成功后刷新列表 MUST 可见新记录。

#### Scenario: 上传后知识库与列表可查

- **WHEN** 教师 A 通过上传接口提交一份合法文件（如 `.md` 或 `.txt`）且解析成功
- **THEN** 知识库（或 `knowledge_entries` 等价表）中 MUST 出现与该文件对应的新记录
- **AND** 该记录 MUST 关联班级 A 与对应材料主键
- **AND** 材料表 MUST 新增一条标题可辨的记录
- **AND** 教师 A 刷新本班材料列表时 MUST 看到该条新记录

#### Scenario: 上传后学生本班可见只读

- **WHEN** 教师 A 成功上传新材料后，学生 A1 刷新本班材料列表
- **THEN** 学生 A1 MUST 能在列表中看到该条新材料
- **AND** 学生 A1 对该材料 MUST 仍为只读（不得通过 API 修改或再次上传覆盖）

#### Scenario: 上传失败时不产生脏数据

- **WHEN** 上传过程因不支持格式、空文件或解析失败而终止
- **THEN** 系统 MUST 返回明确错误（如 HTTP 400）
- **AND** MUST NOT 在材料表或知识库中留下不完整记录
- **AND** MUST NOT 保留孤立的未关联文件（或 MUST 在失败时清理已写文件）

### Requirement: 预置核心数据

系统 MUST 建立班级、用户、讲义、作业、助手、技能六类核心数据结构（表或等价实体），并预置可验收的样本数据，以支持登录、班级隔离与上传验收。

#### Scenario: 种子数据满足双班与用户

- **WHEN** 首次执行数据库初始化或种子脚本（含 Compose 首次启动时的 entrypoint）
- **THEN** 库中 MUST 存在班级 A、班级 B
- **AND** MUST 存在教师 A（归属班级 A）、学生 A1（归属 A）、学生 B1（归属 B）
- **AND** 各用户 MUST 具备可登录的用户名与已哈希的密码字段

#### Scenario: 两班材料标题可区分

- **WHEN** 种子脚本执行完成
- **THEN** MUST 存在至少一条明确归属 A 班的材料标题（如含「A 班」）
- **AND** MUST 存在至少一条明确归属 B 班的材料标题（如含「B 班」）
- **AND** 六类核心结构中讲义、作业、助手、技能表 MUST 已创建（可各含 0~1 条占位行，不要求本 change 验收业务功能）

### Requirement: 密码哈希与会话密钥

系统 MUST 使用单向密码哈希算法存储用户密码，禁止明文；会话签名密钥（如 `SECRET_KEY`）MUST 仅通过服务端环境变量或 Compose 注入，MUST NOT 硬编码在源码或提交到版本库。

#### Scenario: 库中无明文密码

- **WHEN** 直接查询用户表中密码相关字段（如 `password_hash`）
- **THEN** 字段值 MUST NOT 等于任何已知预置账号的明文口令
- **AND** MUST 可识别为哈希格式（如 bcrypt `$2` 前缀或 argon2 标识）

#### Scenario: 登录校验使用哈希比较

- **WHEN** 用户使用正确明文密码登录
- **THEN** 系统 MUST 通过哈希验证通过并建立会话
- **WHEN** 同一用户使用错误密码登录
- **THEN** 验证 MUST 失败且不建立会话

#### Scenario: 缺少会话密钥时服务不得静默使用默认值

- **WHEN** 生产/Compose 配置未提供会话签名必需的环境变量
- **THEN** 应用启动 MUST 失败或使用文档明确禁止的不安全默认值；`.env.example` MUST 列出所需变量名

### Requirement: Docker Compose 部署与健康检查

系统 MUST 以 Docker Compose 作为标准启动方式；应用 MUST 提供 `GET /health`；数据库文件与上传目录 MUST 通过 volume 持久化，以便容器重建后数据仍在。

#### Scenario: Compose 启动后可访问

- **WHEN** 操作者按 README 复制 `.env.example` 并执行 `docker compose up --build`（或文档等价命令）直至 healthcheck 通过
- **THEN** 浏览器 MUST 可访问登录页 URL
- **AND** `GET /health` MUST 返回 HTTP 200 且 body 表明服务可用（如 JSON `status: ok`）

#### Scenario: health 不依赖登录态

- **WHEN** 未携带会话 cookie 的请求访问 `GET /health`
- **THEN** MUST 仍返回成功响应
- **AND** MUST NOT 重定向到登录页

#### Scenario: 重建容器后数据仍在

- **WHEN** 已存在预置用户、材料或教师上传产生的记录后，执行 `docker compose down` 再 `docker compose up` 且未删除数据 volume
- **THEN** 预置班级与用户 MUST 仍可登录
- **AND** 已上传材料与知识库记录 MUST 仍可通过本班列表或 SQL 查询到

