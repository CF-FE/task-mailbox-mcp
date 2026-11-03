# Mac 协调桥（Mac Coordination Bridge）

本目录实现 [事件与钩子方案](../docs/hooks.md) 的本地收件、会话感知和结果补传。VPS 九项工具契约不变；本桥不调用模型、Herdr、任意 shell 或任务携带的回调 URL。

本地组件统一称为 **Mac 协调桥（Mac Coordination Bridge）**，远端信箱及 MCP 插件称为 **Task Mailbox**。运行目录使用 `~/Library/Application Support/MacCoordinationBridge`，launchd 标识为 `io.maccoordinationbridge.coordinator`，钩子标签为 `Mac Coordination Bridge local coordination`。旧 `TaskMailbox` 路径及标识只作为历史迁移记录保留，不作为第二个收件入口。

既有 OAuth 注册的客户端名称仍可能显示历史名称 `Task Mailbox Mac Bridge`；当前授权沿原专用缓存使用，新的注册使用 `Mac Coordination Bridge`。不为修改显示名重建注册或重新授权。

## 可用能力

- 全局事件分页入本机 SQLite，整页和游标同事务提交；稳定事件去重，冲突或满库不推进水位。
- 按原 job 核对任务摘要、精确结果版本及 hash；取消保持请求，UNKNOWN 保持待对账。
- 保存项目目标／路由摘要并明确标为调用方数据；不把目标文字当执行许可。
- Codex 生命周期只记录配置范围内的 session、turn、工具引用与最近观察时间，不读取 transcript，不保存 prompt／末条回复。
- 显式绑定 session/job；结构化结果先暂存，绑定会话的 Stop 才将它放入持久补传队列，重复 Stop 不新增报告。
- `status` 汇总待办、HELD、取消、UNKNOWN、待补传、最近会话信号和连接健康；旧信号不证明进程仍在运行或已停止。

维护来源固定为 `CF-FE/task-mailbox-mcp`。安装时记录该仓库的固定提交；本机运行副本、OAuth 和 SQLite 仍独立保存在私密运行目录，不能从部署副本推导 Issue 落点。

## 独立安装

已验证的 Mac 依赖环境为 Python 3.13.1（x86_64）、FastMCP 4.0.11、MCP 2.3.0、cryptography 48.0.1；[Mac 独立锁定文件](requirements.lock.macos-py313.txt)与 VPS Linux 清单分开。只使用适合本机 Python／架构的二进制依赖，不为安装桥改动系统 Rust。仅使用本地盘。以下路径均为占位示例，运行时凭据、真实端点和配置不进入仓库：

```bash
python3.13 -m venv "$HOME/Library/Application Support/MacCoordinationBridge/.venv"
"$HOME/Library/Application Support/MacCoordinationBridge/.venv/bin/python" -m pip install -r mac/requirements.lock.macos-py313.txt
python3 mac/install.py \
  --runtime-dir "$HOME/Library/Application Support/MacCoordinationBridge" \
  --state-dir "$HOME/Library/Application Support/MacCoordinationBridge/state" \
  --endpoint https://mcp.example.com/mailbox/mcp \
  --allowed-root /absolute/path/to/herdr-project \
  --allowed-root /absolute/path/to/mailbox-repository \
  --source-commit FIXED_SOURCE_COMMIT
```

安装器独立复制源码，记录摘要和版本，生成权限 `0600` 的私密配置、Codex hooks.json 及 launchd 单元；保留其它 hook，修改已有 hook 前保存私密备份。它**不授予 hook trust、不启动 launchd、不登录 OAuth**。同一运行目录不允许静默更换源身份或监测范围。

在 Codex 设置的“钩子”或原生 `/hooks` 中审阅新定义并信任精确当前版本。钩子只使用本机 SQLite，超时 3 秒；SessionStart／UserPromptSubmit 的附加上下文只含固定提示、关联 ID／hash 和计数。[官方钩子规则](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)

本机实测 `hooks stable true`，未自定义 `CODEX_HOME`，有效定义为 `~/.codex/hooks.json`；同一 Codex host 的 App／CLI 共享这套配置。首次安装后，公子完成对应定义的审阅信任，已观察 SessionStart／PreToolUse／PostToolUse／UserPromptSubmit 四类 App 事件。目录迁移后，公子已再次确认新定义信任，主执行侧直接回读迁移前快照之后新增的 PreToolUse／PostToolUse，确认来自当前会话与新运行路径。没有运行 CLI 模型 turn，不能由配置共享宣称 CLI 的原生触发已验收；其余事件也须各自取得回执。

## 授权、查询和运行

进入独立复制的 app 目录后：

```bash
../.venv/bin/python bridge.py login
../.venv/bin/python bridge.py status
../.venv/bin/python bridge.py held --cursor 0 --limit 25
../.venv/bin/python bridge.py once
../.venv/bin/python bridge.py run
```

`login` 才允许用户浏览器完成本桥自己的 OAuth；原授权 URL 先校验同源 HTTPS 与精确 `/authorize` 路径，再直接交给系统浏览器，不另发库的授权页预检 GET。客户端 SDK 日志只保留级别和异常类型，不渲染认证异常正文、参数或堆栈。不读取 Codex／Kimi 的认证缓存。专用缓存加密保存，目录 `0700`、文件 `0600`；正常运行保留同一 client 的静默 refresh。缺缓存或认证明确拒绝停止该分支，不自动重新登录、DCR、换端点或认证方式。OAuth 路径发现也持久保存，重启不把带前缀 token endpoint 简化为站点根路径。

本桥已完成专用 OAuth 和 whoami 实测，原 nonce 已由 Mac 真实取回、持久入 outbox 并上传；当前桌面会话通过云端 App 读回、核对摘要及 nonce 并确认读取。此前若干尝试的 RemoteProtocolError 不反推为用户拒绝；当前成功也不能推导长期稳定。保留原 job／event，不重新投递；指定 Dot 已报告读取，服务精确 ACK 已直接回读；反方向与持续收件状态见验证记录。

认证发现的两类同源 well-known GET 若在完整响应头前遇到 RemoteProtocolError，每个 client 生命周期最多额外尝试一次，两次共用 10 秒预算。实现固定使用 httpx2 2.16.0 的发送钩子，不替换底层 transport，因此保留环境代理与 TLS；保持原默认不跟随重定向。令牌／注册／refresh／MCP POST、其它 URL、HTTP 错误、超时和响应体错误不走这项重试。既有 outbox 按稳定 event_id 补传的契约不变。

`run` 使用单进程文件锁，默认每 30 秒一轮、每页 25、最多 4 页／10 RPC／20 秒；单 RPC 10 秒。网络错误退避 60／120／300 秒，SIGTERM 可结束等待。同步、钩子与本地暂存互不等待远端网络。程序收到取消只记录／提示，不宣称下游已停止。

新安装默认只准备单元。明确采用后台收件时，给安装命令增加 `--start-at-login`，生成 `RunAtLoad=true`、`KeepAlive=false`；升级会保留同一单元已开启的登录启动选项。安装器不自行加载或启动，随后执行用户级加载：

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.maccoordinationbridge.coordinator.plist"
launchctl print "gui/$(id -u)/io.maccoordinationbridge.coordinator"
```

`RunAtLoad=true` 使单元加载及以后登录时启动；进程中的 collector 保持轮询，网络错误按原预算退避。`KeepAlive=false` 让认证或协议错误停止后保持停止，修好后用 `launchctl kickstart "gui/$(id -u)/io.maccoordinationbridge.coordinator"` 显式恢复；本轮不增加崩溃自恢复策略。停止并卸载用 `launchctl bootout "gui/$(id -u)/io.maccoordinationbridge.coordinator"`。不要在 launchd 已收件时同时运行 `once`／`login`，先停止原网络写者；独立本地钩子照常记录。

本机已启用登录自启并回读后台 PID 与更新的成功收件时点，具体版本及覆盖见验证记录。单元没有打开 Mac 公网端口。Mac 睡眠时不能保证持续感知，恢复后沿原游标补读。

## 收件／补传公平性与安全诊断

收件和已持久 outbox 共用一个总 RPC 计数及单一轮次 deadline，不因切换方向重置预算。RPC 预算至少 3 时，首方向为后方向保留 1 次调用；1–2 RPC 不强行拆分，避免需要 `get_job` 加 `get_result` 的两步事件永久无法完成。worker 每轮轮换首方向，时间不固定减半；繁忙方向耗尽本轮时间时，另一方向在后续轮优先。预算必须能覆盖单事件必要调用，不承诺每轮两向都完成。一次 `collect_once` 是有界单轮，跨轮公平由持续 worker 实现。

仅已知传输失败允许在剩余总预算内尝试另一方向，本轮不重试失败请求；未知／本地状态错误结束当前轮，原退避规则保留。明确认证、配置和整体协议失败停止分支；单事件已隔离为 HELD 的校验失败仍保留原原因与原 ID，不创建新执行。任何方向出现网络失败都不会因另一方向成功而记整体 OK，也不更新 `collector_last_success`。

失败的 `health.detail` 采用 `CAUSE stage:STAGE type:TYPE`，仅记录固定白名单码／阶段／类型；未知项明确为 `UNKNOWN_CAUSE`／`UNKNOWN`，时间沿 `updated_at`。认证链中的安全原因优先于外层协议包装；原异常正文、URL、token、payload 不进入日志或状态。按组件与时间读取健康信息，旧 `network` 记录不代替新一轮 `collector` 结果。2026-11-02 的旧 AUTH_FAILED/CLIENT_UNAVAILABLE 根因仍 UNKNOWN，新测试不反推现场原因，也不增加自动登录、注册或新的重试分支。

## 定位 HELD 事件

`held --cursor 0 --limit 25` 只读本地台账，按原 inbox cursor 升序返回 HELD 的原 event/job、类型、安全原因和可解析的创建时间；不包含任务正文、data 或凭据，不需要 OAuth 或网络。使用返回的 `next_cursor` 继续分页，`has_more=false` 表示当前尾页；空页保留输入游标。limit 范围 1–100；诊断游标不写回收件 cursor，不改变 HELD、配额或补传队列。不存在的台账不会被创建，身份不符则拒绝。

`status` 给出 `held_query` 入口。并发时较早 PENDING 可能稍后才变 HELD，需要从 0 重新检查；分页不是完整历史通知。接口、时间脱敏及无写入边界见[本地诊断契约](../SPEC.md#mac-本地-held-诊断接口)。

## 从旧安装目录迁移

已有台账的 `TaskMailbox` 安装须显式迁移，不能直接在新目录运行全新安装或只重命名虚拟环境。迁移先核目标目录、原启动项、七类自有 hook 和依赖锁定版本，准备私密配置／启动项／源码回退副本；不要复制另一套 OAuth 身份或创建新的信箱 consumer。

1. 在最终新路径重建同版本 Python 环境及锁定依赖；先核依赖闭包与真实模块导入，旧桥继续沿原入口工作。[Python venv 的迁移限制](https://docs.python.org/3.13/library/venv.html#how-venvs-work)
2. 卸载旧 launchd 单元，短暂移除本桥 owned hooks 以停止新本地写入，等在途 hook 退出并取得独占锁。保留其他 hook，不改 Codex 原生信任记录。
3. 整体移动运行目录及原 `state/`，将新建环境放回最终新路径；逐一比较 SQLite 完整性／业务表和游标，以及 OAuth 密钥和加密值文件摘要；OAuth FileTree 的集合 `*-info.json` 元数据须只将 `directory` 从旧缓存路径映射到新路径，其余字段保持不变。历史证据中记录的旧绝对路径保持原文。
4. 更新 `config.state_dir`、默认配置路径、hook 命令、launchd argv／WorkingDirectory／日志路径和安装凭据；端点、source_id、consumer_id、允许的项目根不变。安装器识别旧／新 hook 标签以去重，不为旧安装自动搬台账或加载第二个单元。
5. 回读新配置和启动项，显式加载新单元；用原专用缓存确认实际 collector 成功、游标连续与待补传状态。认证失败保留原状态，不为迁移重新登录或换凭据。
6. 更新后的 hook 定义须由公子在 Codex 设置“钩子”或 CLI `/hooks` 审阅并信任。路径改动改变定义 hash，旧信任不自动转移；在原生信任前标记 `REVIEW_REQUIRED`，不把人工调用脚本当作原生 hook 触发。

旧启动项从 LaunchAgents 移到私密迁移备份中，旧环境及配置只作回退材料；新路径只有一份活动台账及 OAuth 缓存，不保留旧路径软链接或并行收件器。失败时先停止新单元，再恢复目录、旧环境、旧配置／hooks／plist，最后按原启动状态恢复原单元。实际迁移和原生信任状态统一见[验证记录](../docs/verification.md)。

## 绑定与报告

新项目任务先按[Projects 关联契约](../docs/projects.md)核读原正式条目、任务正文和已有 Iteration，再将固定 `tracking` 随 payload 提交。正常项目 `bind`／`stage-report` 必须有该关联，结果自动保留为 `task_tracking`；本机结构验证不代表 GitHub 已实际核读。旧数据任务仍可收件查看及直接数据回报。

只有已经校验并缓存摘要的 job 可以绑定；cwd 须在配置的项目范围内：

```bash
../.venv/bin/python bridge.py bind --session-id ORIGINAL_SESSION --job-id ORIGINAL_JOB --cwd /absolute/project/path
../.venv/bin/python bridge.py stage-report --session-id ORIGINAL_SESSION --turn-id ORIGINAL_TURN \
  --event-id STABLE_REPORT_EVENT --status UNKNOWN --result-file /private/path/result.json
```

报告文件必须是 JSON 对象，规范化后最多 1 MiB。报告与 session/turn/job 匹配后，Stop 入 outbox，成功后把暂存原件移入本机归档，允许下一份报告继续暂存；入队失败保留原件。同事件 ID 上传 ACK 丢失时只补传原报告，不重跑业务。不得悄悄用另一个结果覆盖尚未入队的暂存；变更需沿原事件对账。

## 验证与边界

在服务根运行 `python3 -m unittest discover -s tests -v`（135 项）。另用桥的独立解释器运行 `tests/smoke_client_transport.py`，9 项固定依赖测试不发外部网络，验证元数据窄重试、代理路由／超时／重定向保留及 OAuth 接合；不能把它加入标准库发现路径。台账／collector fake transport／hook 和安装测试只用标准库、临时目录和本地子进程，不调用外部模型或业务执行器。完整安装的依赖环境与真实 OAuth／工具调用单独验证，当前证据见 [verification.md](../docs/verification.md)。

本机数据库默认 32 MiB、未处理记录／待发报告各 500；VPS 配额保持原值。日志／journal／OAuth 状态／结果暂存另占空间，不自动删历史腾空间。当前无多 Mac claim/fencing、无服务端 stream identity；只允许同一未替换源数据库与单个资源写者。云端插件绑定和 Dot 自动唤醒不由本桥安装推导。

云端 Dot 的 whoami／mailbox_usage 成功已有公子转述的实际调用回报；它不替代本桥授权、nonce 或云端 hook 验收。[官方企业边界](https://learn.chatgpt.com/docs/enterprise/agent-security#hooks-in-local-computer-access-with-work-cloud)仅在 enterprise managed policy 与 remote hooks 启用等条件下支持 Global `requirements.toml` 的管理员远端 `mcp_tool` hook；personal accounts 不适用，本地 command／插件 hook 不支持云编排。本桥不承诺自动唤醒 Dot。
