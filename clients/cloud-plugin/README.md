# 云端连接与历史模板

本目录的 portable plugin/MCP 模板是初次独立包来源，**不作为当前云端安装入口**。原生界面曾将该包标记为“仅限桌面端”，公子已卸载。OAuth 字段正确不等于已注册可供云端使用的 App。

当前 Task Mailbox 使用已在 ChatGPT 原生注册的私有 MCP App 连接；桌面与云端选择同一 App，各自完成原生授权。真实 App/plugin ID 与端点留在私密配置，不提交 PUBLIC 仓库。

若后续包装该连接，按[官方流程](https://developers.openai.com/plugins/build/plugins#create-and-test-a-plugin-locally-with-an-mcp-server)先回读原生 `.app.json` 获取实际 App ID，再以 `extensions.com.openai.apps` 引用绑定。不要再创建同名纯 MCP 包或猜测 ID；保留原 App 的认证和身份。完整步骤见[客户端指引](../README.md)，当前取证见[验证记录](../../docs/verification.md)。
