# 生产 Secret 管理与轮换手册 v1

## 1. 安全边界

开发环境仍允许 Compose 使用显式开发默认值，便于本地启动；生产环境必须使用独立、未跟踪的 `.env.production`，并通过启动前门禁。生产启动不读取 `.env.example` 或 `runtime-config.default.json` 中的开发凭据作为合格 Secret。

门禁覆盖 24 个必填字段与 14 类独立 Secret：环境标识、PostgreSQL、MongoDB、RabbitMQ、MinIO root/Application/Governance 三身份、Qdrant、管理员凭据、Control/Execution 两套 JWT、内部令牌、Alertmanager webhook 令牌和 Workflow Fernet 主密钥。校验内容包括：

除 Secret 外，门禁还要求正数租户配额、合法文档 bucket、1–3650 天非当前版本到期、1–100 个保留版本，以及 `MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=true`。生成器会直接生成这些非敏感生产治理项，避免生成后立即被校验器拒绝。

- 必填与最小长度；
- 开发默认值/占位符拒绝；
- DSN/URI 使用值的 URL-safe 字符约束；
- Workflow 主密钥必须解码为 32 bytes；
- 14 类 Secret 禁止跨用途复用，尤其禁止 MinIO 三身份、Control 与 Execution JWT 主密钥复用；
- JWT `kid`、previous key 数量、独立性和最长 7 天截止时间校验；
- 错误输出只含键名与规则，不输出 Secret 值。

## 2. 初次生产部署

生成本地生产 env：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\new-production-env.ps1
```

脚本默认创建 `.env.production`，文件已加入 `.gitignore`。生成后应立即将值迁移到批准的 Secret Manager；本地文件只用于当前 Compose 部署，不应通过聊天、工单或日志传递。

仅生成并验证 Compose 配置，不启动服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\start-stack.ps1 -Production -Observability -MinioTls -ProductionEnvFile .env.production -GenerateOnly
```

正式启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\scripts\start-stack.ps1 -Production -Observability -MinioTls -ProductionEnvFile .env.production -Build
```

`-Production` 会先执行 `validate-production-secrets.ps1`。失败时不会执行 `docker compose up`。
生产模式强制同时传入 `-Observability -MinioTls`，叠加 production、observability 与 MinIO TLS 配置；明文 MinIO、缺少 CA 路径、三身份 access key 复用或遗留共享 MinIO 凭据都会被拒绝。

## 3. 已有 volume 的重要限制

PostgreSQL、MongoDB、RabbitMQ、MinIO 的初始化用户名/密码环境变量通常只在空数据目录首次初始化时生效。对已有 named volume 直接替换 `.env.production` 并重建容器，不等同于完成数据库内部凭据轮换，可能导致客户端全部失联。

已有环境应按以下顺序处理：

1. 生成新 Secret，但暂不删除旧 Secret；
2. 在对应数据库/中间件内部创建或修改凭据；
3. 更新 Secret Manager 和部署 env；
4. 协调重启依赖服务并运行健康检查/Stack Smoke；
5. 确认无旧凭据连接后再撤销旧值；
6. 记录操作者、时间、受影响服务和验证证据。

## 4. 各类 Secret 轮换影响

| Secret | 轮换要求 |
| --- | --- |
| Provider API Key | 使用 API Pool 的轮换/禁用流程；验证新 Key 计费、限流和模型权限后撤销旧 Key |
| `CONTROL_PLANE_JWT_SECRET` | 只签发 Control/Web/Chat 用户令牌；Chat 使用同一 Control 验证 key ring，但不持有 Execution 主密钥 |
| `EXECUTION_JWT_SECRET` | 只签发 Execution 管理令牌；不得与 Control 主密钥复用 |
| `CONTROL_PLANE_INTERNAL_TOKEN` | Control Plane 与 Execution API/Worker 协调更新并重启，随后验证内部 dispatch/event 回写 |
| 数据库/消息/对象存储密码 | 必须先修改服务端凭据，再更新客户端；不能只改 Compose env |
| `QDRANT_API_KEY` | Qdrant 与 Execution API/Worker 同步更新；恢复演练脚本支持 `-ApiKey` 或 `QDRANT_API_KEY` 环境变量 |
| `WORKFLOW_SECRET_MASTER_KEY` | 使用主/旧密钥双读窗口和事务重加密工具；新写入始终只使用主密钥，全部密文验证后才能移除旧密钥 |

## 5. Workflow Fernet 主密钥轮换

轮换前必须先执行 PostgreSQL 备份恢复演练，并把旧密钥保存在可回滚的 Secret Manager 版本中。

1. 生成一个新的 Fernet key；不要在命令行或日志中打印它。
2. 部署 Execution API/Worker：`WORKFLOW_SECRET_MASTER_KEY=<新密钥>`，`WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS=<旧密钥>`。
3. 确认服务健康。此时新 Secret 使用新密钥加密，旧 Secret 可通过 `previous:1` key slot 解密。
4. 在 Execution API 容器中先 dry-run：

```powershell
docker exec agentpowflow-execution-api python -m runtime.workflow_secret_rotation
```

5. 核对 `total_rows`、`already_primary`、`needs_rotation` 后执行：

```powershell
docker exec agentpowflow-execution-api python -m runtime.workflow_secret_rotation --apply --confirm ROTATE_WORKFLOW_SECRET_MASTER_KEY
```

6. 再次 dry-run，必须得到 `needs_rotation=0`，并执行引用 Secret 的 Workflow Smoke。
7. 清空 `WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS`，协调重启 API/Worker，再次验证 Secret 解析。

工具覆盖 ACTIVE 与 DISABLED 行；重加密不会递增业务 `version`，但会写入 `updated_by=system:master-key-rotation` 和更新时间。工具取得事务 advisory lock 和表写锁，任何密文无法解密、指纹不一致或并发修改都会使整笔事务回滚。输出只含计数，不含名称、明文、密文或 key。

若必须回滚到旧密钥：在旧密钥仍安全的前提下，将旧密钥重新设为主密钥、新密钥放入 previous 列表，重复 dry-run/apply，使全部密文反向重加密后再移除新密钥。不得只回退容器环境而不重加密已经迁移的行。

## 6. JWT 无停机轮换

新令牌使用标准三段 HS256 JWT，并强制携带 `typ=JWT`、`kid`、`iss`、`aud`、`iat`、`exp`。Control 与 Execution 分别使用 `agentpowflow-control/control-plane` 和 `agentpowflow-execution/execution-plane`，因此即使迁移期间暂时共享旧验证密钥，标准令牌仍不能跨平面使用。Chat 只验证 Control key ring。

每个平面分别配置：

- 主密钥：`*_JWT_SECRET` 与 `*_JWT_KEY_ID`；
- 临时旧密钥：`*_JWT_PREVIOUS_KEYS=kid:secret[,kid:secret]`，最多 3 个；
- 旧密钥截止：`*_JWT_PREVIOUS_KEYS_EXPIRES_AT`；
- 旧两段令牌截止：`*_JWT_LEGACY_ACCEPT_UNTIL`。

两个截止时间必须是显式 UTC ISO-8601、处于未来且不超过 7 天。未设置 `LEGACY_ACCEPT_UNTIL` 时，旧两段令牌默认拒绝；到期后即使签名来自当前主密钥也会拒绝。新签发始终只使用主密钥，未知 `kid` 不回退遍历其他密钥。

从历史共享密钥迁移的推荐顺序：

1. 分别生成 Control、Execution 新主密钥和新 `kid`，不得复用；
2. 将旧共享密钥以临时 previous entry 配入两套 key ring；如仍有旧两段令牌，再设置独立 `LEGACY_ACCEPT_UNTIL`；
3. 为 previous 与 legacy 使用尽可能短、最长 7 天的截止时间，重建 Control、Execution API/Worker 与 Chat；
4. 运行 `tools/smoke/jwt-rotation-smoke.ps1`，确认两类新令牌均为三段、Control→Chat 正常、跨平面调用均为 401；
5. 观察旧 key slot 使用情况并等待最长令牌 TTL；窗口内旧两段令牌没有 audience，存在受限的跨平面混淆风险，因此不可把兼容窗口当作长期配置；
6. 截止后清空 previous keys、previous expiry 和 legacy deadline，重启服务并复跑 Smoke。

Control refresh token 采用单次轮换：刷新事务会对持久化记录加写锁、撤销旧 token、签发带随机 `jti` 的新 access/refresh token，并将新 refresh token 返回客户端。旧 refresh token 的并发或后续重放均返回 401；客户端必须原子替换本地 refresh token。

验收命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\smoke\jwt-rotation-smoke.ps1 -EnvFile config\stack.generated.env
```

## 7. 仓库扫描门禁

CI 执行：

```powershell
python -m unittest tools.audit.test_secret_hygiene -v
python tools/audit/verify_secret_hygiene.py
```

本地审计可额外包含未跟踪文件：

```powershell
python tools/audit/verify_secret_hygiene.py --include-untracked
```

扫描器检测私钥块、常见云/平台 Token 格式、敏感字段中的高熵字面量、带高熵凭据的 URI，以及被强制加入 Git 的运行时 `.env*` 文件。报告不会输出命中的值。

模式扫描不能证明 Secret 从未泄露。本轮本地审计曾在未跟踪的运行态 API Pool 文件和原始调试日志中发现 Token 格式字面量；这些路径已加入 `.gitignore`，但相关 Provider 凭据仍应在供应商侧主动撤销并轮换。

## 8. 仍待实现

- 与 Vault/KMS/云 Secret Manager 的运行时集成；
- Secret 年龄、即将过期、轮换失败与旧 Key 仍被使用的统一指标和告警。
