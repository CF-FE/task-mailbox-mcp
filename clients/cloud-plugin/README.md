# 云端连接与历史模板

本目录的 portable plugin/MCP 模板是初次独立包来源，**不作为当前云端安装入口**。原生界面曾将该包标记为“仅限桌面端”，公子已卸载。OAuth 字段正确不等于已注册可供云端使用的 App。

当前 Task Mailbox 使用已在 ChatGPT 原生注册的私有 MCP App 连接；桌面与云端选择同一 App，各自完成原生授权。真实 App/plugin ID 与端点留在私密配置，不提交 PUBLIC 仓库。

若后续包装该连接，按[官方流程](https://developers.openai.com/plugins/build/plugins#create-and-test-a-plugin-locally-with-an-mcp-server)先回读原生 `.app.json` 获取实际 App ID，再以 `extensions.com.openai.apps` 引用绑定。不要再创建同名纯 MCP 包或猜测 ID；保留原 App 的认证和身份。完整步骤见[客户端指引](../README.md)，当前取证见[验证记录](../../docs/verification.md)。

<a id="cloud-onboarding"></a>

## 云端接入与核验

### 云端 Dot

在 ChatGPT 网页设置打开已登记的 **Task Mailbox** App 详情。确认仍是原连接，使用该页的刷新工具功能拉取服务清单。保留既有连接和各工具的原授权，核新增五项查询、`publish_observation_batch` 与 `get_observation_receipt` 是否可见及启用。管理端刷新和目标会话实际加载分别记录。[官方 App 工具管理](https://developers.openai.com/api/docs/guides/custom-mcp-server#how-to-use)

沿原委派核对 Dot 的实际接收会话和回程。原计划的历史输出会话与单独建立的规范读取会话各有自己的范围。使用指定的 Dot 会话回读实际工具、`whoami` 项目及来源授权，再沿原项目和任务查上游、下游、工作流及阻塞。用同一批次回执与任务时间线确认一条授权自报。已安装 App、目录可见和账号自报分别留证。

### Codex Cloud

复用已发布的 Herdr 环境和唯一 Router 派发入口。确认原任务、跟踪引用、输入提交、交付、授权和稳定事件标识。实际 Cloud 任务记录平台任务 ID、首次检出、原生工具及技能加载证据。用该任务的 MCP 连接核 `whoami`，再查询原任务；桌面工具与网络许可仅证明各自入口。Skill 来源按实际检出和加载清单核对。[官方 Cloud 环境](https://learn.chatgpt.com/docs/environments/cloud-environments#connect-to-services)

获准的独立 HTTP 来源令牌可用于观测上报和按批次查回执。由宿主维护者通过允许域和 Cloud 管理的网络凭据配置，绑定项目、任务、工作流和执行实例。它只证明该来源的上报范围，查询仍需该任务实际获准的 MCP 工具。VPS Web 与 REST 查询关闭，Mac 监控页面只在本机可用。[现行部署](../../docs/verification/local-monitor.md)

两端共用[云端执行侧清单](../../.agents/skills/task-collaboration-monitor/references/cloud.md)。实际生命周期和委派由原宿主或 Router 回报。业务决定及正式结项沿原任务的指定验收者和正式记录办理。
