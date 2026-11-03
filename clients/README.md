# 客户端接入

这些文件是占位模板，不是已登记插件或运行配置。先完成宿主 OAuth／HTTPS 验证，再替换 `mcp.example.com` 为实际服务域名。保持各客户端同一 `/mailbox/mcp` 端点，不携带服务器端 GitHub client secret。

## Codex

在实际使用的客户端执行，或将 [TOML 条目](codex.config.example.toml) 合并至其现有配置：

```bash
codex mcp add task-mailbox --url https://mcp.example.com/mailbox/mcp
codex mcp login task-mailbox
```

本轮 Mac CLI 0.166.0-alpha.5.1 已原生登记该端点并回读配置；首次登录收到了本地回调，但令牌交换返回 `Request failed`。没有自动重登、更换认证或读取令牌缓存。桌面、CLI 与其他机器的有效配置、工具发现及身份须分别回读，不能互相推定。

原生 OAuth 的 `127.0.0.1:<临时端口>/callback` 是运行登录流程的 Mac 客户端接收地址；它不同于 GitHub OAuth App 中服务器的 `/mailbox/auth/callback`，也不同于云端插件自己的回调。不要因看到 loopback 回调就改写服务器配置。参见 [OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp#oauth-client-registration-and-callbacks)。

Codex App 的“钩子”可用于会话查信、结果入本地待发队列等生命周期适配；它不替代持续收件桥，当前没有安装或启用，见[设计与限制](../docs/hooks.md#codex-app-原生钩子适配)。

## Kimi Code

将 [JSON 条目](kimi.mcp.example.json) 合并至实际版本使用的 MCP 配置，保留既有条目。当前官方 kimi-code 文档使用 `~/.kimi-code/mcp.json` 或项目内 `.kimi-code/mcp.json`，交互命令为 `/mcp-config`、`/mcp-config login task-mailbox`，通过 `/mcp` 回读。旧 kimi-cli 的 `kimi mcp add` 不应套用到不提供该子命令的新版 CLI。

配置与登录行为依据 [Kimi Code 官方 MCP 文档](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/customization/mcp.md)。当前机器 Kimi Code 2.4.1 不提供 `mcp add` 子命令；委托执行侧已将端点原子写入其用户级 MCP 配置并核对保留既有条目；主执行侧另行回读端点匹配与文件权限 `0600`。尚未启动 Kimi 新会话、登录或回读工具。

## 同一 Task Mailbox：桌面 App 与云端

初次通过 Plugin Creator 上传的 portable MCP 包被原生界面标为“仅限桌面端”，公子已卸载它。上传、保存 OAuth 字段与云端连接是不同步骤，不能再次把模板上传当作云端注册成功。

当前唯一入口为公子通过 ChatGPT「插件 → ＋ → 添加自定义 MCP 服务器」注册的 Task Mailbox App 连接；其原生 App 绑定已回读确认，实际地址和标识只留私密配置。新入口可由桌面与云端各自选用和授权；当前桌面会话的九项工具发现、whoami／mailbox_usage 已直接通过，指定 Dot 的读取仍单独取证。[官方原生连接流程](https://developers.openai.com/plugins/build/plugins#create-and-test-a-plugin-locally-with-an-mcp-server)

[cloud-plugin](cloud-plugin/)中的原 portable `.example` 文件保留为初始来源和桌面 MCP 模板，不再用它们新建另一个“仅限桌面端”的云端替身。若后续确需包装已注册 App，必须从原生文件读取真实 App ID，以 `.app.json` 及 `extensions.com.openai.apps` 绑定，保留该 App 的身份、认证与源；不得把插件 URL 的 ID 改前缀猜成 App ID。

Mac 主动收件、项目感知及显式结果回传由独立 [协调桥](../mac/README.md)承担。它不靠插件本身在空闲时自动运行，不把本地 command hook 打进云端 App 使它再次依赖本地执行。

## 双向验收

复用原 Dot 检查计划的单件自然首发及自动读确认，见[连通性任务合同](../SPEC.md#原计划的单件连通性任务)。保存计划配置与运行时提交分开；不新建本地定时任务替代原云端计划。

Dot 提交一个新的固定 job ID 与随机片段，本地客户端按游标读取并发布变换结果；Dot 读取完整结果，核版本／摘要并确认。随后交换方向再做一次。记录各侧真实调用证据，不能用回环模拟客户端替代真实 Dot 或 Mac 客户端。信箱不负责自动唤醒、后台轮询或执行 Herdr。
