# Mac 本机监控 1.1.0

2026-11-14。公子要求 Web 在 Mac 本机免登录运行，并关闭 VPS Web。数据库仍为原 Win PostgreSQL 18.7，不迁移数据、不创建数据库备份。

## 实际部署

- 本机源码：`2ce2892be8f7420a14e84d1de7eabf37a86458ba`，项目版本 1.1.0。
- 地址：`http://127.0.0.1:8767/mailbox/ui/`。独立用户 LaunchAgent 托管 Web 与 Mac→Win 回环转发，沿局域网访问专用 PostgreSQL。原 Win、Herdr 与 VPS 连接保持。
- 独立数据库账号只有 `alembic_version`、`platform_cursors`、`observation_snapshots`、`observation_events` 四表 SELECT，事务默认只读；没有原信箱、Web 会话或写权限。实际 TLS、schema、角色属性及表权限核对通过。只新增一条精确 HBA 规则，原 ACL 保持，Win 服务未重启。
- 本机不签发 Cookie，不读取 GitHub 身份，返回 `mode=local` 与显式允许项目。页面显示“本机只读”。Host、Origin 和真实回环来源检查均已验证；跨源请求 403，写入 405，旧登录入口 404。
- Mac 复用已有平台环境，未升级依赖。Python 3.14.3，FastAPI 0.162.6、SQLAlchemy 2.1.8、psycopg 3.3.9、uvicorn 0.54.0；Pydantic 为已有 2.14.3，不冒称与 Linux 锁文件逐项相同。

## VPS Web 已关闭

VPS 使用 `a7fda7dbec6a7907171661504a81531975ce4c6a` 的关闭开关，`TASK_PLATFORM_WEB_ENABLED=false`。页面、静态资源、session/login、查询、SSE 和 `web.` 回调在回环与公网均实际返回 404。原 MCP POST、未认证的来源上报／回执仍返回 401，OAuth 元数据返回 200。实际服务就绪，切换成功回执的重启计数为 0。没有停掉整个信箱服务。

原整包上传观察超时后，先核对远端文件与无在途部署，再复用已上传版本；114 个源文件逐一匹配固定提交。一次就绪检查误用了 MCP GET=401，实际 GET 为405，导致回退。回读确认原配置／服务恢复后改用 MCP POST，完成关闭。保留失败回执，没有把超时当成功或重传业务。VPS Web 查询负载已停止，本轮没有足够的同负载前后样本量化内存／CPU 节省。

## 验证结果

- 原 203 项标准库／平台合同测试全部通过；本机入口 4 项、VPS 关闭路由 3 项通过。Web 类型检查和生产构建通过，旧 GitHub session 与新 local session 的前端解析均验证。
- 原生浏览器无登录直接加载凡人项目。项目总览、角色活动、R4 任务详情、R4 工作流图和阻塞中心均读取真实 PostgreSQL 数据；实时流已连接，未启用 fixture。
- 五个视图检查后控制台无 error/warn。本机 HTTP 的 session、项目、角色、任务、工作流和实时流均取得实际 200；读取来自现场 PostgreSQL。
- 总览读取的三次独立本机 HTTP 样本为 **108.61、115.56、156.88 ms**，水位 20。浏览器请求也已实际完成；这些是短样本，不是长期 P95 或业务来源更新延迟。
- 显示保留原证据差异：R4 已由负责人限定验收、协调待办仍为 rev38 待处理，S2 与 C001 阻塞仍在。页面运行不代表这些业务阻塞已解除。
- 关闭 VPS Web 后，原生 MCP `whoami` 和 `mailbox_usage` 均成功，信箱仍为 8／8／24、观测仍为 20／3。该回归中的第一次 usage 发生发送错误，之后同入口一次只读回读成功，不能据此宣称公网抖动消失。
- 原桥最后回读为 `authentication=READY`、`collector=OK / ROUND_COMPLETE`、协调端口 `READY`，成功时点为 2026-11-14 09:57:27 北京时间；8 个本机任务、0 个待回报、0 个 UNKNOWN 保持。此前切版期间的认证与网络错误保留在历史回执，详见[登录恢复](login-recovery-1.0.1.md)。

## 接续与运行

代码入口为 `python -m platform_service.local --config <PRIVATE_CONFIG_JSON>`。配置字段为 `database_url`、`policy_path`、`ui_root`、非空 `projects` 和可选 `stale_seconds`。DSN 使用只读账号与 verify-full TLS，私有配置不提交仓库。来源 policy 仅保留项目、实体及证据权限元数据，不复制原来源令牌或 GitHub 账号。

服务标识为 `io.taskmailbox.monitor-web` 与 `io.taskmailbox.monitor-pg`。本机进程、安装摘要、源版本、HBA 回执与检查点保存在用户 Application Support 的 TaskMailboxMonitor 私有目录；原桥的历史登录和 VPS 发布回执仍留原私有验收目录。不要通过重启原 Win／Herdr 连接恢复这个独立看板。
