# PostgreSQL T1 部署验证

日期 2026-11-14，Asia/Shanghai。范围为 Windows 独立数据库和 VPS 私网接线。真实连接、主机输出和证书签发材料见指定私有回执。

| 检查 | 实测结果 |
| --- | --- |
| 软件来源 | EDB 官方 Windows x64 二进制归档 18.7-1。完整归档在 VPS 下载并通过 SHA-256 校验。Windows 接收从已验归档提取的服务器组件，复核传输归档 SHA-256，`postgres.exe --version` 返回 18.7。 |
| Windows 实例 | 原生 `TaskMailboxPostgreSQL18` 服务为 Running、Automatic，使用专用虚拟服务账户、独立数据目录和回环监听。原 PostgreSQL 18.1 保留，原监听在受控重启前后为同一 PID。 |
| 库与角色 | 业务库及独立测试库各有专属所有者角色。通过认证查询验证两角色均无超级用户、创建库、创建角色及复制权限。 |
| VPS 接线 | `task-mailbox-pg-forward.service` 已启用并运行，仅绑定 VPS 回环。独立 SSH 进程经现有 EasyTier 私网连接 Win sshd，再转到 Windows 回环。Windows DB 无私网或 LAN 监听，防火墙和 EasyTier 策略沿用既有配置。 |
| TLS 与 SQL | VPS 本机以 `openssl s_client -starttls postgres` 校验证书链和 IP SAN，协商 TLS 1.3。从 Mac 经 VPS 转发用两套角色执行 `SELECT 1`，均使用 `verify-full`、TLS 1.3 及强制 SCRAM channel binding。两库明文连接均被 HBA 拒绝。 |
| VPS 原生认证 SQL | 公子明确许可应用凭据部署及测试后，在既有 VPS 上以业务和测试两个身份分别完成认证 `SELECT 1`。两次均返回 `1`、`server_version_num=180007`、TLS 已启用，各约 3.8 秒。 |
| 应用准备 | T1 交付时新版源码已预置，私有 `runtime.env` 和访问策略已配置。后续主控已完成应用及原信箱的统一 PostgreSQL 切换，见[首期验证](platform-v1.md)。 |
| 持久恢复 | 分别重启新 Windows PG 服务和 VPS 专用转发，新进程及自动启动配置读回通过。重跑 VPS TLS 和两库经 VPS SQL 检查通过。重启转发期间原 VPS 信箱及 EasyTier 进程 PID 保留。 |
| 仓库材料 | Windows PowerShell 原生解析器检查通过。实机分阶段执行了归档安装、实例初始化、服务注册和建库。交付脚本合并这些步骤，未在另一台 Windows 从零重装。 |

首次 `initdb` 在 Windows 受限令牌下无法从仅授予管理员组访问权的安装根目录加载 DLL。初始化阶段临时按调用账户 SID 授权，之后撤回。首次证书验证失败来自本机默认 OpenSSL 配置生成的重复 CA 扩展，使用明确配置重新签发并通过证书链及地址校验。首次经 VPS 的 SQL 检查超时，重试及服务重启后的复测均通过。

此前自动审批拒绝将含密码的测试连接文件上传并落盘到 VPS，当时仅取得 VPS 本机 TLS 验证和 Mac 经 VPS 的认证查询。后续按公子明确许可完成了上表的 VPS 本机认证验证。

Windows／VPS 宿主机整机重启未验证。上述 T1 结果覆盖凭据和私网数据库连通性。生产 schema、原 SQLite 导入、应用切换和后续回读的实际结果由[首期验证](platform-v1.md)统一维护，步骤见[部署说明](../deployment.md#协作平台切换到-postgresql)。本轮未创建备份或 dump。
