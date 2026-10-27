## 1. 依赖与配置

- [x] 1.1 在 `requirements.txt` 新增 `PyJWT`（含版本），并执行 `pip install -r requirements.txt` 验证安装成功
- [x] 1.2 在 `.env.example` 增加 `TOKEN_EXPIRES_HOURS`（默认 24）说明，并在 `app/__init__.py` 读取该环境变量写入 `app.config["TOKEN_EXPIRES_HOURS"]`；验证缺失时回退默认 24

## 2. auth.py 核心 token 逻辑

- [x] 2.1 实现 `issue_token(user)` 函数：使用 `PyJWT` 以 `HS256` 签名，载荷含 `user_id`、`role`、`class_id`、`exp`（基于 `TOKEN_EXPIRES_HOURS`），密钥取 `current_app.config["SECRET_KEY"]`；验证签发的 token 可被正确解码且载荷字段齐全
- [x] 2.2 实现 `decode_token(token)` 函数：捕获 `ExpiredSignatureError`、`InvalidTokenError` 等异常返回 `None`；验证过期 token 与篡改 token 均返回 `None`
- [x] 2.3 实现 `get_current_identity()`：按顺序从 `Authorization: Bearer <token>` 头与 `request.cookies.get("token")` 提取 token，解码后返回身份字典或 `None`；验证无 token、无效 token 均返回 `None`

## 3. auth.py 请求上下文与装饰器

- [x] 3.1 在 `auth.py` 注册 `before_request`：调用 `get_current_identity()` 并写入 `g.current_user`；注册 `context_processor` 将 `current_user` 注入模板上下文；验证受保护路由内 `g.current_user` 可用
- [x] 3.2 改造 `login_required`：从 `g.current_user` 判断登录态，缺失时页面路由重定向到 `/login`、API 路由返回 401 JSON；验证未登录访问 `/materials` 重定向、`/api/materials` 返回 401
- [x] 3.3 改造 `role_required`：校验 `g.current_user["role"]` 等于目标角色，否则返回 403；验证学生访问 `/materials/upload` 返回 403、教师可通过

## 4. auth.py 登录与登出路由

- [x] 4.1 改造 `login()` POST 分支：校验密码成功后调用 `issue_token`，通过 `set_cookie("token", ..., httponly=True, samesite="Lax", max_age=...)` 下发，不再写入 `session`；验证响应 `Set-Cookie` 含 `token` 且 `HttpOnly`
- [x] 4.2 改造 `logout()`：清除 `token` cookie（`set_cookie("token", "", expires=0)`）；验证登出后 cookie 被清除且访问受保护路由回到登录页

## 5. 业务路由迁移（materials.py / search.py）

- [x] 5.1 将 `materials.py` 中所有 `session["user_id"]`、`session["role"]`、`session["class_id"]`、`session.get(...)` 替换为 `g.current_user["..."]`；验证列表、详情、下载、上传、删除路由功能正常
- [x] 5.2 将 `search.py` 中所有 `session["class_id"]` 替换为 `g.current_user["class_id"]`；验证 `/search` 页面与 `/api/knowledge/search` 检索正常

## 6. 模板迁移

- [x] 6.1 修改 `base.html`：将 `session.get('user_id')`、`session.get('role')` 改为 `current_user`（context_processor 注入）；验证登录后导航栏显示角色与登出链接、未登录显示登录链接
- [x] 6.2 检查 `materials.html`、`material_detail.html`、`search.html`、`login.html` 是否引用 `session`，如有则改为 `current_user` 或路由传入变量；验证各页面渲染无 `UndefinedError`

## 7. 应用配置与收尾

- [x] 7.1 `app/__init__.py` 保留 `SECRET_KEY` 强制校验；保留 Flask session 配置（用于 flash 消息）或精简无关项；验证应用在缺少 `SECRET_KEY` 时启动失败、正常时可启动
- [x] 7.2 全量搜索代码中 `session[` 与 `session.get` 的残留引用，确认仅 flash 消息等非身份用途保留；验证无身份相关 session 读取残留

## 8. 集成验证

- [x] 8.1 浏览器端：登录教师 A → 材料列表可见本班材料 → 上传文件成功 → 登出 → 再访问列表被重定向到登录页
- [x] 8.2 浏览器端：登录学生 A1 → 尝试上传返回 403 → 仅可见本班材料 → 跨班材料 ID 访问返回 404
- [x] 8.3 API 端：用 `curl` 携带 `Authorization: Bearer <token>` 访问 `/api/materials` 返回本班列表；不带 token 返回 401；过期 token 返回 401
- [x] 8.4 安全性：响应中 `token` cookie 含 `HttpOnly` 与 `SameSite=Lax`；登录失败不签发 cookie；篡改 token 后访问受保护路由被拒
