# 精确任务结果的 MCP Events

本功能由 `CF-FE/task-mailbox-mcp` 维护，是**默认关闭的可选传输能力**。原云端 Dot 在自己的对话订阅一个项目和一个 job；结果发布后，服务向该订阅的回调发送就绪元数据，Dot 再用原工具读取结果并确认。业务联系、原生主体核实和验收仍由原项目协调入口负责。[开发任务 #2](https://github.com/CF-FE/task-mailbox-mcp/issues/2)。

依据：[OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)、[Standard Webhooks v1](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md)。两份资料在 2026-11-03 核读。框架保持 FastMCP 4.0.11／MCP 2.3.0，不增加依赖、服务、端口或 OAuth 客户端。

## 合同与边界

启用后，同一个经过 OAuth 保护的 `/mailbox/mcp` 端点在旧版 `initialize` 和 MCP 2.0 `server/discover` 的顶层 capabilities 声明 `events:{}`，并支持：

| 方法 | 行为 |
| --- | --- |
| `events/list` | 只对有私有授权条目的认证主体列出 `mailbox.result_ready` |
| `events/subscribe` | 固定 `arguments={project_id,job_id}`；验证真实平台提供的 HTTPS 回调及 `whsec_` 密钥后持久化订阅 |
| `events/unsubscribe` | 按认证主体、原事件名、过滤条件及回调关闭订阅，重复调用安全 |

原九工具与账号限制保留；事件方法另外检查相同允许账号和非空认证 subject，正文中的 sender 不参与鉴权。订阅 ID 从主体、回调、事件名和规范化过滤参数确定。回调 URL 和密钥由真正订阅的 ChatGPT 对话提供，不能由运维人员代造。

仅提供结果就绪事件，不提供任意 Dot 发信、查询待办唤醒、业务 submit 代办或读 ACK 代办。`auth/callback` 继续只用于 OAuth。订阅证明一个回调通道的控制权；共享账号不能独自证明“指定云端 Dot”的角色、任命或某次提交来源，这些证据仍沿原生端核验。

事件只带 `project_id/job_id/payload_hash/version/result_hash/status`，不带任务正文、负责人答复、私密路径或行为指令。完整 HTTP body 不超过 256 KiB；现有任务和结果各 1 MiB 上限保持。结果从原 `RESULT_REPORTED` 日志派生，不另发模型请求或重做业务。webhook ID 为 `mbe_` 加原事件 ID 的 SHA-256，重试沿同 ID、同字节，签名时刻每次更新。

## 私有启用策略

未设置 `TASK_MAILBOX_EVENTS_POLICY_FILE` 时，不声明事件能力、不建立订阅表、不启动投递器，也不发出回调。只有核准实际公网回调出站范围后，才在原服务环境显式设置这一个变量：

```text
TASK_MAILBOX_EVENTS_POLICY_FILE=/var/lib/task-mailbox/events-policy.json
```

文件与直接父目录分别为服务账户拥有的 0600／0700 真实文件与目录。文件不得为符号链接。模板见 [events-policy.example.json](../deployment/events-policy.example.json)，只含占位值。策略精确绑定已有认证 subject、CF-FE 项目仓库、预先核准的 job ID 和 payload hash；没有通配符。允许 job 在核准后尚未提交，但其首次实际原件必须与摘要、target.project_id 和 tracking.repository 一致。默认模板的业务 jobs 为空。

`allowed_callback_hosts` 是逐个核准的精确 ASCII DNS 主机名，仅允许标准 443。不能依据订阅请求自动加入主机。每次真实连接重新检查全部 DNS 结果，拒绝任何非公网地址；连接固定到已核 IP，TLS SNI 与证书校验仍用原域名，不读代理、不跟重定向。校验挑战与事件发送共用该边界。总等待预算为 10 秒；系统 DNS 无法取消，最多保留两个尚未返回的 daemon 查询，HTTP 慢流由截止 watchdog 中止。

平台提供的签名密钥要求 base64 解码为 24–64 字节。挑战为单次随机值；只有 2xx 与常量时间匹配的回声才激活。成功校验按主体、URL、密钥摘要缓存十分钟。订阅变更串行处理，验证后再次核政策，取消不会被先前未完成的验证重新激活。

回调 URL 和签名密钥由已有稳定签名材料派生的独立用途 Fernet 密钥加密，保存在原 SQLite；不复用 OAuth token、不生成聊天中转凭据。轮换时重新验证，旧密钥最多保留五分钟并同时签名。未要求无限有效：默认一天，最短一分钟、最长七天，`ttlMs:null` 也只授予有限七天并返回真实 `refreshBefore`。原 Dot 负责按官方协议刷新；到期停止投递。

策略每次操作、准备事件和实际出站前重读。撤回 job/subject/主机即拒绝新增或暂停后续投递。停止订阅保留历史和未决证据；已开始的单次网络请求可能仍到达，不能声称取消撤销了已发生的送达。账号断开、平台退订及真实过期刷新仍须在目标端单独验收。

## 持久化、恢复与容量

新增两张表由原 Store 的事务、FULL 同步、rollback journal 和 256 MiB 主库上限管理，不另放网络数据库。原结果及日志写入成功后，扫描器在同一事务里保存待发记录和扫描水位；进程重启从原记录继续。初次订阅可按该精确 job 从游标 0 取回已有结果，覆盖先发布后订阅的竞态。返回游标不越过未发送记录。

出站前先持久记录尝试和 30 秒恢复租约。网络回执丢失可能重传，接收者按稳定事件 ID 去重；不承诺 exactly-once。2xx 只记 webhook `SENT`；Dot 的读取确认仍须原 `get_result(version)`、摘要核对和 `ack_result_read`。事件投递不会自动登记业务验收或协调核心的 `CLOUD_DOT_READ`。

重试最多八次，退避为 5、30、120、300、900 秒并在末值封顶；410 停该订阅，413 和非临时 4xx 停该通知。达到尝试上限记 `EXHAUSTED`。默认最多保存 64 份订阅和 1024 份投递记录（包含历史），满额拒新增、不自动删记录或扩容；已有待发结果继续补传。需要清理或提高预算时由原维护方明确处理。取消过的历史通知不会因重订阅自动复活。

## 固定部署与撤回

本轮交付源码和隔离验证，**未部署或启用 VPS 回调，未让真实 Dot 注册订阅**。原 Mac collector、台账、探针配置和凭据保持原维护入口。本功能不依赖 Mac 桥安装升级。

核准启用前，部署者需准备：实际固定提交；原服务文件与状态的受限备份；原 Dot 的允许主体与原 job/hash；平台将采用的准确 callback 主机；出站和订阅保留期限。源文件集合为原 `server.py/mailbox_store.py/oauth_storage.py` 加 `event_protocol.py/event_service.py/event_store.py/callback_http.py`，依赖沿原锁定环境。先在隔离目录验证固定文件，服务切换与实际端点重扫沿原部署授权执行。

先安装代码且不设置变量时，仍只有原工具。需要事件时另准备受限策略并显式启用、重启唯一原服务，再在插件页重新扫描。由原 Dot 订阅精确 job，检查订阅到达、挑战、回调接收、原 Dot 工具调用与同版本 ACK。禁止用桌面代订阅或本地模拟冒称原 Dot。

撤回时先由原订阅端停止监控，核待发及在途；移除启用变量并沿原唯一服务重启，保留新增表和原结果，旧版本不读这些表。不要重建数据库、OAuth 或清理原事件。新事件发送器是本服务内部任务，不新增 systemd 单元。

## 验证证据

标准库回归 **181 项通过**（含网络边界 17、持久事件 17）。独立加密生命周期 **14 项通过**；覆盖来源/过滤拒绝、挑战失败、精确结果与无正文事件、轮换、撤回、稳定重试、满队列原件补传，以及验证期间取消/撤权。

真实 SDK 回环验证通过两种协议的能力和三方法、错误账号／缺 subject 拒绝、原工具鉴权和未认证 401。另一个隔离 HTTP 样本使用实际服务 lifespan 自动发送结果事件，接收者按原工具读结果、写精确 ACK、退订；所有账号、数据和接收器均为合成对象。原 MCP 与 OAuth 存储 smoke 均通过。

```bash
python3 -B -m unittest discover -s tests -v
"$PYTHON" -B tests/smoke_events_protocol.py
"$PYTHON" -B tests/smoke_event_service.py
"$PYTHON" -B tests/smoke_events_end_to_end.py
"$PYTHON" -B tests/smoke_mcp.py
"$PYTHON" -B tests/smoke_oauth_storage.py
```

`$PYTHON` 使用既有锁定依赖环境。检查只使用临时 SQLite、回环 HTTP、模拟 DNS／TLS 边界和合成接收器，没有真实公网回调或实际 Dot 执行；真实 TLS 接收服务、VPS 宿主、平台订阅生命周期、原 Dot 醒来及工具卡读回、长期稳定性均为 `NOT_RUN`。既有凡人请求的直接联络与最终验收沿 Herdr 原任务单独维护。
