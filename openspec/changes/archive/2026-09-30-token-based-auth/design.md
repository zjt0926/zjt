## Context

见 proposal.md。当前实现通过 Flask 签名 cookie 会话承载身份（`session["user_id"]`、`session["role"]`、`session["class_id"]`），身份读取散落在 `auth.py`、`materials.py`、`search.py` 及 `base.html` 模板中。应用为纯服务端渲染（Jinja2 模板 + HTML 表单），无前端 JS 框架，Nginx 反向代理透传所有请求头与 cookie。

## Goals / Non-Goals

**Goals:**
- 用自包含签名 token（JWT）替代 Flask `session` 承载用户身份。
- 对浏览器导航保持透明：token 通过 httpOnly cookie 自动携带，无需 JS。
- 同时支持 `Authorization: Bearer <token>` 供 API/脚本客户端。
- 角色与班级隔离语义不变，仅身份载体从 session 改为 token 载荷。
- 复用现有 `SECRET_KEY` 作为 JWT 签名密钥，不引入新的密钥管理。

**Non-Goals:**
- 不实现 refresh token / token 黑名单 / 服务端会话存储（保持无状态）。
- 不改变登录表单 UI、不引入 OAuth/OIDC。
- 不改动材料上传、知识库检索、Nginx 配置等业务逻辑。
- 不引入前端框架改造页面。

## Decisions

### 1. Token 格式：JWT (HS256)

使用 JWT 而非不透明随机 token，原因：
- 载荷自包含 `user_id`、`role`、`class_id`、`exp`，无需服务端查库即可鉴权。
- 可复用现有 `SECRET_KEY`（HMAC-SHA256），无需额外密钥。
- 库选型 `PyJWT`（`requirements.txt` 新增），其 `jwt.encode`/`jwt.decode` 足够。

备选：不透明随机 token + 服务端存储（Redis/DB）→ 放弃，因引入状态存储与新依赖，违背"无状态"目标。

### 2. Token 传递：httpOnly cookie 优先，Authorization 头兜底

- 登录成功后 `set_cookie("token", jwt, httponly=True, samesite="Lax", max_age=...)`。
- 鉴权时按顺序提取：
  1. `Authorization: Bearer <token>` 头（API 客户端）
  2. `request.cookies.get("token")`（浏览器）
- 两者皆无或校验失败 → 未授权。

备选：仅用 Authorization 头 → 放弃，因纯服务端渲染页面的浏览器导航（`<a>`、表单 POST）无法自动附带 Bearer 头，会导致登录后页面跳转全部 401。

### 3. 身份读取统一封装

在 `auth.py` 中新增 `get_current_identity()` 辅助函数：提取并校验 token，返回 `{"user_id", "role", "class_id"}` 字典或 `None`。装饰器 `login_required`、`role_required` 与业务路由均调用此函数，不再直接读取 `session`。

`flask.g` 可缓存本次请求的身份结果，避免同一请求内重复解码。

### 4. 模板中的身份信息

`base.html` 当前用 `session.get('user_id')`、`session.get('role')` 判断登录态与显示角色。改为通过 `g.current_user`（在 `before_request` 中由 `get_current_identity()` 填充）注入模板上下文，或由各路由显式传入。优先采用 `before_request` + `g` + `context_processor`，避免每个路由重复传参。

### 5. 过期时间

默认 24 小时，通过 `TOKEN_EXPIRES_HOURS` 环境变量覆盖。过期后 `jwt.decode` 抛出 `ExpiredSignatureError` → 视为未授权。

### 6. 登出

清除 `token` cookie（`set_cookie("token", "", expires=0)`），不做服务端吊销（无状态设计）。

## Risks / Trade-offs

- **JWT 无法主动吊销** → 无状态的代价；登出仅清除客户端 cookie。若需强制下线需引入黑名单，本次不做。缓解：过期时间较短（24h）。
- **token 被盗用风险** → httpOnly + SameSite=Lax 降低 XSS/CSRF 风险；Nginx 已透传 `X-Forwarded-Proto`，生产环境应配合 HTTPS（cookie 的 `secure` 标志可在 `create_app` 中根据 `X-Forwarded-Proto` 或环境变量启用）。
- **SECRET_KEY 泄漏影响扩大** → 原本仅影响 session 伪造，现同时影响 JWT 伪造。缓解：`SECRET_KEY` 已要求强制环境变量注入、不得硬编码；保持该约束不变。
- **现有 `auth-upload` 规约未归档** → delta spec 直接修改其需求文本，归档时需确保与未来归档的 `auth-upload` 主规约合并正确。

## Migration Plan

1. 新增 `PyJWT` 依赖，更新 `requirements.txt` 与 Dockerfile 构建。
2. 改造 `auth.py`：实现 `get_current_identity()`、JWT 签发/校验、装饰器改造、登录/登出 cookie 处理。
3. 改造 `materials.py`、`search.py`：`session["..."]` → `g.current_user["..."]`。
4. 改造 `base.html` 与相关模板：`session.get(...)` → `current_user` 上下文变量。
5. 调整 `__init__.py`：保留 `SECRET_KEY` 校验，session cookie 配置可保留（不再承载身份，但 Flask session 仍可用于 flash 消息）或精简。
6. 更新 `.env.example` 增加 `TOKEN_EXPIRES_HOURS` 说明。
7. 本地验证：浏览器登录后导航正常、API 用 Bearer 头可访问、过期/篡改 token 被拒。

## Open Questions

无。上述决策均已在设计中确定，不影响 spec 与任务拆分。
