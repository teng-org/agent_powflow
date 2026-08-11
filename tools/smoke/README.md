# Smoke 测试说明

本目录保存依赖真实 Docker Compose 服务的端到端冒烟脚本。脚本默认不调用外部 LLM/Embedding Provider；凡可能产生外部费用的路径，必须由调用者显式授权对应开关。

真实商业 Provider 契约不属于默认 smoke。请使用仓库根目录的 `tools/provider_contract.py`；该工具要求付费调用开关、环境变量密钥、供应商单价和单次费用上限，并只输出脱敏报告。完整门禁见 `docs/design/real-provider-contract-gate-v1.zh-CN.md`。

## 前置条件

基础全栈：

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack
```

MinIO TLS、物理容量和告警相关测试需要：

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack -Observability -MinioTls
```

所有脚本都应使用随机唯一测试标识，并在 `finally` 中只清理自己创建的资源。测试失败后必须阅读其清理错误；不得用通配符扩大删除范围。

## 脚本目录

### `stack-smoke.ps1`

验证 Control、Execution、Chat 三平面健康和认证边界，以及 Workflow 生命周期、DAG/条件/并行、审批、节点事件、Retrieval、HTTP 默认拒绝、租户隔离、文档/KB/Chat 临时数据和严格清理。

```powershell
.\tools\smoke\stack-smoke.ps1
```

脚本会先读取 Execution 的 `embedding_mode`。默认只允许不会调用外部 Provider 的模式；只有明确批准真实供应商调用和费用时才能使用脚本提供的显式外部 Provider 开关。

### `jwt-rotation-smoke.ps1`

验证 Control/Execution 标准 JWT、`kid/iss/aud`、Control→Chat 信任、跨平面拒绝、refresh token 单次轮换和旧 token 重放拒绝。脚本不输出 token 或 Secret。

```powershell
.\tools\smoke\jwt-rotation-smoke.ps1 -EnvFile config\stack.generated.env
```

### `outbox-reliability-smoke.ps1`

短暂停止并自动恢复 Execution API，验证 Outbox 重试耗尽、数据库死信、管理员重放、RabbitMQ/Celery 消费和 Run 最终恢复。

```powershell
.\tools\smoke\outbox-reliability-smoke.ps1
```

### `dlq-audit-smoke.ps1`

验证执行死信的 RabbitMQ 消费、Control Plane 权威持久化、Console 管理查询、重放和完整审计轨迹。

```powershell
.\tools\smoke\dlq-audit-smoke.ps1
```

### `cross-store-fault-smoke.ps1`

依次短暂停止并恢复 MinIO、Qdrant，验证真实连接失败、修复账本、`CRITICAL` 健康状态、恢复后重试和物理资源清理。

```powershell
.\tools\smoke\cross-store-fault-smoke.ps1
```

该脚本会改变容器运行状态；不得与生产流量或其他故障演练并行执行。

### `kb-write-retirement-smoke.ps1`

验证 Execution 公共知识库写入口默认返回 `410 LEGACY_KB_WRITE_RETIRED`，并通过真实 Control→Execution 链路检查分块草稿保存/清除、文档/分块冻结。删除临时知识库后精确核对 PostgreSQL、Qdrant 与 MinIO 全版本零残留。

```powershell
.\tools\smoke\kb-write-retirement-smoke.ps1
```

### `document-move-repair-smoke.ps1`

真实停止并恢复 Qdrant，验证 Control 文档移动失败后目标 `MOVING` 发布屏障、`MOVE_DOCUMENT_POINTS` 持久修复、MinIO 引用保持、单项管理 API 修复和同命令再次提交。脚本只删除自己创建的知识库与精确修复记录，并核对 PostgreSQL、Qdrant、MinIO 全版本零残留。

```powershell
.\tools\smoke\document-move-repair-smoke.ps1
```

该脚本会改变 Qdrant 容器运行状态；不得与生产流量、其他 smoke 或故障演练并行执行。即使中途失败，也应先确认脚本已恢复 Qdrant，再处理后续测试。

### `minio-observability-smoke.ps1`

验证 MinIO Prometheus target、官方容量指标、5 条规则、Prometheus→Alertmanager 连接，以及唯一 synthetic alert 的 firing/resolved 双 webhook。

```powershell
.\tools\smoke\minio-observability-smoke.ps1
```

### `minio-least-privilege-smoke.ps1`

验证 Application 身份对象 put/get/delete、Governance 身份对象读取拒绝，并清理本次唯一测试键的全部版本。

```powershell
.\tools\smoke\minio-least-privilege-smoke.ps1
```

### `minio-bounded-pressure-smoke.ps1`

在 TLS Application 身份下验证单个较大对象、并发对象、同键多版本、Prometheus HTTPS target 和严格前缀清理。默认约写入 33 MiB，脚本总写入安全门禁为 192 MiB。

```powershell
.\tools\smoke\minio-bounded-pressure-smoke.ps1
```

参数和专用压力环境边界见 `docs/operations/minio-bounded-pressure-smoke-v1.zh-CN.md`。该脚本不填满磁盘、不停止节点，也不等同于持续饱和或多节点故障测试。

### `minio-disk-full-smoke.ps1`

启动使用有界 tmpfs 的隔离 MinIO，先建立成功写基线，再要求真实返回 `XMinioStorageFull`，清除填充数据后验证恢复写入。测试不挂载正式 volume，临时容器与 helper 严格清理，并确认正式 MinIO 全程 running。

```powershell
.\tools\smoke\minio-disk-full-smoke.ps1
```

安全边界和实测数据见 `docs/operations/minio-isolated-disk-full-smoke-v1.zh-CN.md`。

## 验收后的清洁检查

至少确认：

- `apf_it_*` 测试 schema 为 0；
- `apf-it-*` 临时 bucket 为 0；
- 活动容量预留、未完成跨存储修复和 firing 告警为 0；
- `v1/system/*-smoke/` 下没有本次测试对象版本；
- 被脚本停止的容器已恢复为运行状态。

不得为清理测试数据删除数据库、共享 volume、整个文档 bucket 或非本次测试前缀。
