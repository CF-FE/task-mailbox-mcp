# Task Mailbox 协作监控

Web 位于 `/mailbox/ui/`，使用同源 `/mailbox/api/v1/`。页面提供项目总览、角色活动、任务详情、工作流运行图和阻塞中心。任务与运行链接在 URL fragment 中保留所选项目和 ID，可刷新、分享和使用浏览器返回。

## 构建与接入

在维护仓的 `web/` 中使用 Node.js 22.12+ 或 20.19+ 与 npm 执行：

```sh
npm ci
npm run typecheck
npm run build
```

构建输出在 `web/dist/`。将该目录内容以静态文件提供在 `/mailbox/ui/`，保留该路径末尾的斜杠。对 `/mailbox/ui/assets/` 按文件路径读取，找不到时返回 404。静态入口建议每次核对版本，带内容哈希的 assets 可长缓存。浏览器路由使用 fragment，静态入口无需接收任务或运行子路径。

同一 HTTPS 入口将 `/mailbox/api/v1/*` 和 `/mailbox/auth/callback` 的完整路径转发到 T2 的 `server.py`，由其 PublicPrefix 处理路径前缀。转发时保留 Cookie、状态码和 SSE 响应流。SSE 需要即时转发和允许长连接。既有 OAuth 回调通过 `/mailbox/auth/callback` 接入，登录成功由服务端重定向至 `/mailbox/ui/`。Web 从会话 401 读取 `login_url`，用浏览器导航启动登录；Cookie 和项目授权由后端管理。应用使用只读 GET。

生产构建不包含开发 fixture。缺少 API、格式不符、401、403 或连接中断时显示对应的状态。已经读取的快照在连接中断时保留并标为可能陈旧；授权失效时隐藏受保护内容。

## 本地验证

```sh
npm run dev:fixture -- --port 45173
```

打开 `http://127.0.0.1:45173/mailbox/ui/`。fixture 使用合成测试数据，页面显示开发标识。使用真实 API 的本地联调可设置仅供 Vite 代理使用的 `MAILBOX_API_UPSTREAM`，其目标入口需接收 `/mailbox/api/v1/` 和 `/mailbox/auth/` 的原路径。实际 OAuth 回跳由目标入口的注册配置控制。

接口字段以 T2 的 OpenAPI 为准。页面按服务返回的游标翻页，筛选范围是已经加载的记录。来源与快照时间使用北京时间。耗时优先展示 API 已计算值，工作流节点和总览执行区间仅在来源有一致起止点时计算。范围总数未知时展示已加载工作项和服务端计数。
