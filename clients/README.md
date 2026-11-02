# 客户端接入

这些文件是占位模板，不是已登记插件或运行配置。先完成宿主 OAuth／HTTPS 验证，再替换 `mcp.example.com` 为实际服务域名。保持各客户端同一 `/mailbox/mcp` 端点，不携带服务器端 GitHub client secret。

## Codex

在实际使用的客户端执行，或将 [TOML 条目](codex.config.example.toml) 合并至其现有配置：

```bash
codex mcp add task-mailbox --url https://mcp.example.com/mailbox/mcp
codex mcp login task-mailbox
```

这两个命令需在端点准备完成后执行；本轮只读取了已安装 CLI 的 `mcp add --help`，没有更改用户配置或登录。桌面、CLI 与其他机器是否继承同一配置须分别回读，不能互相推定。

## Kimi Code

将 [JSON 条目](kimi.mcp.example.json) 合并至实际版本使用的 MCP 配置，保留既有条目。当前官方 kimi-code 文档使用 `~/.kimi-code/mcp.json` 或项目内 `.kimi-code/mcp.json`，交互命令为 `/mcp-config`、`/mcp-config login task-mailbox`，通过 `/mcp` 回读。旧 kimi-cli 的 `kimi mcp add` 不应套用到不提供该子命令的新版 CLI。

配置与登录行为依据 [Kimi Code 官方 MCP 文档](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/customization/mcp.md)。当前机器已读取的 CLI help 不提供 `mcp add` 子命令；尚未修改或验证其实际服务配置。

## ChatGPT 云端／Dot

[cloud-plugin](cloud-plugin/) 仅保存 portable plugin 的源模板。实际端点验证后，将 `.example` 文件复制为 `plugin.json` 和 `mcp.json`，填入真实 HTTPS 端点，校验并打包到临时产物目录，再通过获准的插件连接流程登记、安装并完成 OAuth。不要上传带占位端点的包来试探登记；不要把认证密钥打进包中。

插件元数据有严格长度／格式要求，修改后需重新验证。此处尚未保存至账号；上传也不代表 Dot 实际调用或读取结果。云端认证与注册依据 [OpenAI MCP 接入说明](https://developers.openai.com/api/docs/mcp#handle-authentication)。

## 双向验收

Dot 提交一个新的固定 job ID 与随机片段，本地客户端按游标读取并发布变换结果；Dot 读取完整结果，核版本／摘要并确认。随后交换方向再做一次。记录各侧真实调用证据，不能用回环模拟客户端替代真实 Dot 或 Mac 客户端。信箱不负责自动唤醒、后台轮询或执行 Herdr。
