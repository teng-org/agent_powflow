# MinIO 服务身份与 TLS v1

## 目标与边界

文档对象链路不再由所有组件共享 MinIO root 凭据，并为 MinIO API、治理初始化和 Prometheus 抓取建立可验证的 TLS 信任链。开发环境可显式选择 TLS；生产启动必须同时启用可观测性和 TLS。

## 身份拆分

一次性服务 `minio-service-identity-init` 使用 root 凭据创建并幂等更新两个运行身份：

| 身份 | 允许能力 | 明确不允许 |
| --- | --- | --- |
| Application | 指定文档 bucket 的 list/location/list versions/multipart、对象 get/put/delete/delete version；只读 versioning/lifecycle 供健康检查 | 管理员 API、治理写入、其他 bucket |
| Governance | 指定文档 bucket 的 versioning/lifecycle 读写及必要的 bucket 元数据读取 | 对象内容读写、管理员 API、其他 bucket |
| Metrics | MinIO 生成的 Prometheus Bearer Token | 对象与 bucket 业务操作 |

root 凭据仅保留在 MinIO 服务、身份初始化和监控 token 初始化边界。Execution API/Worker 只接收 Application 凭据；文档 bucket 初始化器只接收 Governance 凭据。初始化器会验证应用身份能够访问目标 bucket 且无法调用 `mc admin info`，任何失败都会阻断依赖服务启动。

Application 的版本列举与版本删除权限只作用于指定文档 bucket，用于满足显式文档删除的物理清除语义；普通覆盖仍保留版本并由 lifecycle 管理。

## TLS 实现

- `infra/compose/docker-compose.minio-tls.yml` 为 MinIO、两个 `mc` 初始化器、治理初始化器、Execution API/Worker 和 Prometheus叠加证书挂载与 HTTPS 配置。
- `runtime.object_storage.ObjectStorage` 在 `MINIO_SECURE=true` 时使用 `MINIO_CA_CERT_PATH` 构造强制 `CERT_REQUIRED` 的连接池；CA 文件缺失会 fail closed。
- Prometheus 使用 `https://minio:9000`、Bearer Token、CA 文件和 `server_name=minio` 抓取官方指标。
- `new-minio-dev-certificates.ps1` 生成本地开发 CA 与 RSA 3072 服务证书，SAN 固定包含 `minio`、`localhost` 和 `127.0.0.1`；输出位于已忽略的 `.local/minio-certs`，不会覆盖已有证书，除非显式传入 `-Force`。

开发证书生成器不能替代生产 PKI。生产证书必须由批准的证书流程提供到 `.local/minio-certs/server.crt`、`server.key`、`ca.crt`；生产启动不会自动生成开发 CA。

## 启动与停止

开发 TLS 全栈：

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack -Observability -MinioTls
.\infra\scripts\stop-dev-all.ps1 -Observability -MinioTls
```

生产启动必须显式传入：

```powershell
.\infra\scripts\start-stack.ps1 -Production -Observability -MinioTls -ProductionEnvFile .env.production -Build
```

生产门禁要求 `MINIO_SECURE=true`、显式 CA 路径、三组不同 access key、三组不同 secret，并拒绝遗留共享键 `MINIO_ACCESS_KEY/MINIO_SECRET_KEY`。

## 验收证据（2026-08-03）

- MinIO API/WebUI 实际以 HTTPS 启动，开发证书链和 SAN 校验通过；
- 三个一次性初始化器均退出 0，文档 bucket versioning/lifecycle 复读一致；
- Execution 应用身份治理健康为 `OK` 且 `secure_transport=true`；
- Prometheus target 的 scheme/scrape URL 均为 HTTPS，状态 `up` 且无抓取错误；
- `minio-least-privilege-smoke.ps1` 证明应用对象 put/get/delete 成功、治理身份对象读取被拒绝、唯一测试键全部版本清零；
- `minio-bounded-pressure-smoke.ps1` 证明 16 MiB 单对象、16 路并发和同键 10 版本可在 TLS Application 身份下完成，27 个物理版本复读后全部清零；
- Execution 全量 `126/126` 在真实 PostgreSQL、MinIO TLS 与显式 CA 下通过，无跳过；
- 生产策略 smoke 证明明文传输和身份复用均被拒绝；生成器输出通过 `required=24 distinct_secrets=14` 校验。

## 尚未闭环

- 生产 PKI 的自动签发、续期、吊销与双证书平滑轮换；
- MinIO 多节点/纠删码环境中的 mTLS、网络策略和独立主机验证；
- root 凭据迁入外部 Secret Manager/Vault，并建立年龄、轮换逾期和旧凭据使用告警；
- 有界大对象、并发和版本膨胀基线已通过；磁盘满、持续饱和、多节点和节点离线仍需专用环境演练。
