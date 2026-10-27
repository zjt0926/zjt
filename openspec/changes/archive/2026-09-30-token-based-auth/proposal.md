## Why

当前 CampusClaw 使用 Flask 签名 cookie 会话（`session`）承载用户身份（`user_id`、`role`、`class_id`）。会话状态依赖服务端签名密钥与 Flask 会话机制，且身份信息散落在 `session` 字典中，与业务逻辑强耦合。改用 token（JWT）方式可实现无状态认证：身份由自包含的签名 token 携带，便于横向扩展与 API 客户端接入，同时对浏览器导航保持友好（token 通过 httpOnly cookie 自动携带）。

## What Changes

- 登录成功后由服务端签发 JWT token（载荷含 `user_id`、`role`、`class_id`、`exp`），替代写入 Flask `session`。
- Token 通过 httpOnly、SameSite=Lax 的 cookie（`token`）下发，供浏览器导航自动携带；同时支持 `Authorization: Bearer <token>` 请求头，供 API/脚本客户端使用。
- `login_required` / `role_required` 装饰器改为校验 token（优先 Authorization 头，回退 cookie），从解码后的载荷中读取用户身份，替代直接读取 `session`。
- 登出清除 token cookie。
- 业务路由（materials、search）与模板中对 `session["user_id"]` / `session["role"]` / `session["class_id"]` 的读取，统一改为从 token 载荷获取。
- 新增 `PyJWT` 依赖；复用现有 `SECRET_KEY` 作为 JWT 签名密钥。
- **BREAKING**：客户端不再依赖 Flask session cookie；原有基于 `session` 的身份读取逻辑全部迁移至 token 载荷。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `auth-upload`：将「用户登录」中"建立服务端会话"的要求改为"签发签名 token"；受保护路由与 API 的身份校验从读取 `session` 改为校验 token 载荷；角色与班级隔离的语义不变（仍从载荷中取 `role`、`class_id`）。

## Impact

- **代码**：`app/auth.py`（登录/登出/装饰器）、`app/materials.py`、`app/search.py`、`app/templates/base.html`、`app/__init__.py`（会话配置调整）。
- **依赖**：`requirements.txt` 新增 `PyJWT`。
- **配置**：复用 `SECRET_KEY`；token 有效期通过环境变量 `TOKEN_EXPIRES_HOURS`（默认 24）配置。
- **部署**：Nginx 无需改动（cookie 与 Authorization 头均可正常透传）。
- **兼容性**：现有 API 客户端需改用 token 认证（Bearer 头或 cookie）；浏览器端行为对用户透明（仍是表单登录后自动携带 cookie）。
