# Windows PostgreSQL 部署

从 [EDB 官方 PostgreSQL 二进制归档](https://productsdl.enterprisedb.com/download-postgresql-binaries) 获取 Windows x64 发行。先核归档 SHA-256、程序版本、目标路径、可用端口和既有数据库监听。将归档内的 `pgsql/bin`、`pgsql/lib`、`pgsql/share` 和相关许可证解压到独立实例根目录。不要修改全局 PATH。

使用受限目录中的 `provision/` 暂存以下材料。实例根目录从创建起仅由 SYSTEM 和管理员管理。

| 文件 | 内容 |
| --- | --- |
| `roles.json` | `admin`、`application`、`test` 各使用独立的随机 64 位十六进制口令 |
| `server.crt`、`server.key` | 端到端验证使用的服务器证书及私钥。证书 SAN 必须包含连接所用环回地址 |
| `root.crt` | 单独的 CA 证书。先检查完整证书链和地址验证 |

在已提升权限的 PowerShell 中执行 `Initialize-TaskMailboxPostgres.ps1 -Root <new-instance-root> -Port <unused-port>`。脚本仅初始化新集群。若初始化中止，先检查专用 data 目录、服务是否已注册和角色创建进度，再接续相应阶段。部署后移除 Windows 的暂存凭据、私钥副本和归档，保留实例证书及私钥，并在私有交付目录保存管理员凭据和证书签发材料。

## VPS 接线

Windows PostgreSQL 仅监听回环。VPS 通过独立的 SSH 前台转发进程连接既有私网 SSH，复用预先获准、来源受限且已固定主机身份的 SSH 配置。现有 Herdr 连接、EasyTier 规则及 Windows 防火墙不需要改动。填充 [systemd 示例](task-mailbox-pg-forward.service.example) 中的私有 SSH 配置、目标别名及端口。若需要等待既有私网服务，可在 `After=` 增加原服务名。确认独占 VPS 回环监听后启用该单元。

把公开的 CA 证书安装到应用可读路径。连接参数使用 VPS 回环地址、转发端口、目标库及对应独立角色。客户端使用 `sslmode=verify-full` 和 `sslrootcert`，支持时启用 SCRAM channel binding。建议使用库的结构化参数或环境注入，避免把密码拼进进程参数。

启动时核对两个端点都只监听回环。从 VPS 执行证书链及地址校验，随后用业务和测试角色分别执行 SELECT 并核对 TLS、角色权限和明文拒绝。只重启本实例和专用转发，检查新进程以及连接恢复。业务表及迁移由应用负责。服务自动启动与独立服务重启可在不重启宿主机时验证。
