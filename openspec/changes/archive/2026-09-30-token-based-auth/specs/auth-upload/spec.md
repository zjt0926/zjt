## MODIFIED Requirements

### Requirement: 用户登录

系统 MUST 提供教师（teacher）、学生（student）两类角色的账号密码登录；登录成功后 MUST 签发签名 token；未登录用户访问受保护页面 MUST 被引导到登录页，且 MUST NOT 泄露任何业务数据。

#### Scenario: 教师与学生登录成功

- **WHEN** 用户使用有效账号与密码发起登录（预置账号含教师 A、学生 A1、学生 B1）
- **THEN** 登录 MUST 成功并签发签名 token
- **AND** token 载荷中 MUST 包含用户标识、角色（teacher 或 student）与所属班级（如班级 A 或 B）
- **AND** token MUST 通过 httpOnly cookie 下发，使浏览器后续导航自动携带

#### Scenario: 错误密码登录失败

- **WHEN** 用户提供存在用户名但错误密码
- **THEN** 登录 MUST 失败
- **AND** 系统 MUST NOT 签发有效 token
- **AND** 响应 MUST NOT 暗示密码哪一位错误以外的敏感信息（如不返回密码哈希）

#### Scenario: 未登录访问受保护页面

- **WHEN** 未携带有效 token 的用户请求受保护页面（如材料列表）或受保护 API
- **THEN** 对页面请求 MUST 重定向到登录页
- **AND** 对 API 请求 MUST 返回未授权（如 HTTP 401）
- **AND** 响应 MUST NOT 包含任何本班或他班材料标题、正文或文件路径

#### Scenario: API 客户端通过 Bearer 头携带 token

- **WHEN** API 客户端在 `Authorization` 请求头中以 `Bearer <token>` 形式携带有效 token 访问受保护 API
- **THEN** 系统 MUST 校验 token 并允许访问
- **AND** 当 token 缺失、格式错误或签名无效时 MUST 返回 HTTP 401

### Requirement: 角色权限

系统 MUST 按 token 载荷中的角色授权：教师可上传与管理本班材料；学生对本班材料只读；学生调用上传或管理接口 MUST 被拒绝。

#### Scenario: 学生上传被拒绝（403）

- **WHEN** 以 student 角色（如学生 A1）的有效 token 向材料上传接口提交文件
- **THEN** 系统 MUST 拒绝请求并返回 HTTP 403（或等价语义的错误码）
- **AND** `materials` 与知识库相关表 MUST 无新增或变更记录
- **AND** 上传目录 MUST 无因该请求产生的新文件（或事务回滚后不可见）

#### Scenario: 教师上传被允许

- **WHEN** 以 teacher 角色（如教师 A）的有效 token 向材料上传接口提交支持的文件
- **THEN** 系统 MUST 接受请求并执行入库流程（见「材料上传与知识库入库」）
- **AND** 返回 MUST 表示成功（如 HTTP 201）并含新材料标识

#### Scenario: 学生仅只读本班列表

- **WHEN** 学生 A1 登录后打开本班材料列表
- **THEN** 系统 MUST 展示本班可读材料
- **AND** 页面或 API MUST NOT 提供学生可用的上传或删除本班材料的写操作入口（服务端仍 MUST 拒绝写 API，见上一 Scenario）

### Requirement: 班级隔离

班级是数据边界。所有材料、知识库及相关业务查询 MUST 在服务端按 token 载荷中的班级标识过滤；A 班用户 MUST NOT 读取、修改或删除 B 班材料；前端隐藏按钮 MUST NOT 作为满足本要求的唯一手段。

#### Scenario: 跨班按 ID 访问材料被拒绝

- **WHEN** 班级 A 的用户（学生 A1 或教师 A）通过 URL、API 路径参数或请求体指定 B 班某条材料的 ID 发起访问
- **THEN** 系统 MUST 拒绝访问（HTTP 403 或 404，实现择一并在文档中固定）
- **AND** 响应 MUST NOT 返回 B 班材料的标题、正文片段、文件路径或存储键

#### Scenario: 跨班枚举列表不得泄露 B 班

- **WHEN** 学生 A1 携带有效 token 请求材料列表（含分页、搜索参数为空或任意合法值）
- **THEN** 返回集合中每一条记录的班级归属 MUST 均为班级 A
- **AND** MUST NOT 出现 B 班预置材料的可区分标题（如标题中含「B 班」的样本）

#### Scenario: 材料列表仅含本班记录

- **WHEN** 教师 A 携带有效 token 打开本班材料列表
- **THEN** 列表中每一条记录 MUST 归属于班级 A
- **AND** 列表数据 MUST 来自数据库查询且查询条件含班级 A 的过滤

### Requirement: 密码哈希与会话密钥

系统 MUST 使用单向密码哈希算法存储用户密码，禁止明文；token 签名密钥（如 `SECRET_KEY`）MUST 仅通过服务端环境变量或 Compose 注入，MUST NOT 硬编码在源码或提交到版本库。

#### Scenario: 库中无明文密码

- **WHEN** 直接查询用户表中密码相关字段（如 `password_hash`）
- **THEN** 字段值 MUST NOT 等于任何已知预置账号的明文口令
- **AND** MUST 可识别为哈希格式（如 bcrypt `$2` 前缀或 argon2 标识）

#### Scenario: 登录校验使用哈希比较

- **WHEN** 用户使用正确明文密码登录
- **THEN** 系统 MUST 通过哈希验证通过并签发 token
- **WHEN** 同一用户使用错误密码登录
- **THEN** 验证 MUST 失败且不签发 token

#### Scenario: 缺少签名密钥时服务不得静默使用默认值

- **WHEN** 生产/Compose 配置未提供 token 签名必需的环境变量
- **THEN** 应用启动 MUST 失败或使用文档明确禁止的不安全默认值；`.env.example` MUST 列出所需变量名

#### Scenario: token 过期或被篡改后不可用

- **WHEN** 请求携带的 token 已超过过期时间，或签名校验失败
- **THEN** 系统 MUST 拒绝该请求（页面请求重定向到登录页，API 请求返回 HTTP 401）
- **AND** MUST NOT 泄露任何业务数据
