# 开发约定

- 称呼用户为“公子”或“您”。先读 README、SPEC；部署与状态分别以 `docs/deployment.md`、`docs/verification.md` 为准。
- 本服务是确定性持久信箱。保持任务收发与执行分离，不隐式加入 shell、Herdr 控制、Agent 调用、后台拉取或自动唤醒；新增执行能力须有明确任务范围。
- 同一任务使用原 `job_id` 对账，稳定事件 ID 用于重传去重；冲突拒绝、不覆盖，`UNKNOWN` 不自动重跑。取消请求、实际停止、结果报告、读取确认与业务验收分别记录。
- SQLite 仅放本机持久磁盘；任务变更与事件在同一事务内提交。结果、小任务和校验边界由 Store 维护；认证与外部输入边界由 MCP 服务维护。
- 默认单份任务／结果 1 MiB、主数据库 256 MiB、10000 个 job。任务数满额拒新 job，数据库满额无法持久化的写入明确失败，保留原数据与对账能力；不自动删除、清理、vacuum 或扩大预算。`mailbox_usage` 只返回使用量元数据，主数据库配额不包含 journal、OAuth、日志和依赖。
- 保留 systemd 的内存／CPU／进程／文件描述符保护上限；上限不是实测保证。独立记录 OAuth 目录、journald 与长期容量，未经测量不宣称小 VPS 已有充足余量。
- OAuth 配置缺失时拒绝启动。新服务使用独立 OAuth 应用／回调、稳定签名密钥和持久 OAuth 状态，不复制既有 Gitee 凭据或改动其服务／URL。
- OAuth 持久值须加密，保留稳定派生密钥与单进程唯一写者；专用目录默认 8 MiB／512 文件是保守拒写预算，不是宿主硬配额。日志默认 1 MiB × 3，bootstrap 错误仍属 journald；应用不启用 HTTP access log。
- 缺少新 OAuth App 配置时，指引公子在宿主 TTY 用 `configure-private.py` 隐藏输入新 secret；不在聊天索取凭据，不以绕过认证取得连通结果。该配置前置不重复请求已存在的部署许可。
- PUBLIC 仓库不提交真实域名／地址／账号、令牌、私钥、认证状态、数据库、原始终端输出或私密配置；示例只用占位值。依赖与运行目录独立，不建立跨仓运行导入或软链接。
- 改动前读取目标文件，保留无关修改；不因文档或本地测试通过宣称 VPS、云端、客户端或业务已验收。
- Store 改动执行 `python3 -m unittest discover -s tests -v`；MCP 与 OAuth 存储分别用 `tests/smoke_mcp.py`、`tests/smoke_oauth_storage.py` 在独立依赖环境验证，标准库测试不导入框架。部署、认证、执行侧及指定客户端回读分别在验证文档中记证据。
