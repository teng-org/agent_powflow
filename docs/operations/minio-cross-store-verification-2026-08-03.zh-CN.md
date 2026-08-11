# MinIO 与跨存储一致性验证快照（2026-08-03）

## 变更范围

- Execution PostgreSQL V4：文档对象元数据与 repair payload；
- MinIO：原件、解析文本、SHA-256、读取上限、安全 key；
- Worker：以对象引用替代新任务的整篇正文；
- 删除补偿：Qdrant point/collection 与 MinIO object；
- Console：修复账本筛选、统计、错误查看与手工执行；
- 上传体验：`auto_summary` 默认关闭，Console 显式选择。
- 上传容量：单文件有界分块读取，文件数和整批总字节均有配置上限。

## 自动验证

| 验证 | 结果 |
| --- | --- |
| Python `compileall` | 通过 |
| Execution unittest（真实 PostgreSQL + MinIO） | 83/83 |
| Console Vitest | 18/18 |
| Console production build | 通过 |
| Endpoint catalog | 230/230，100% |
| Stack smoke | 通过 |
| Outbox reliability smoke | 通过 |
| DLQ audit smoke | 通过 |
| Cross-store fault smoke | 通过：真实 MinIO/Qdrant 停止、CRITICAL、恢复、物理删除 |

关键命令：

```powershell
python -m compileall -q services/execution-plane
node tools/audit/verify-endpoint-catalog.mjs --json
cd apps/web-admin
npm test
npm run build
```

Execution 集成测试在 `compose_default` 网络内运行生产 Execution 镜像，显式注入测试 PostgreSQL DSN 和 MinIO endpoint；测试 bucket 使用唯一 `apf-it-*` 名称并严格清理。

## 真实对象生命周期证据

在运行中的 Compose MinIO 上完成：

1. multipart 上传 `evidence-live.txt`；
2. PostgreSQL 文档记录包含 source/extracted object key；
3. Worker 从对象引用读取，最终任务 `SUCCEEDED`、文档 `INDEXED`；
4. 管理下载接口返回与上传内容完全一致的 30 bytes，并返回匹配的 SHA-256；
5. 文档删除后，两个对象读取均返回 `NoSuchKey`；
6. 临时知识库随后删除。

## 最终运行态

- Compose：13/13 服务为 `running`；
- RabbitMQ：8 个现存队列的 `messages/messages_ready/messages_unacknowledged` 全为 0；
- PostgreSQL：无 `apf_it_*` 测试 schema；
- `cross_store_repairs`：无残留记录；
- MinIO：无 `apf-it-*` 测试 bucket。

## 说明与后续

本快照证明本地开发栈的 happy path、上传中断补偿、可注入故障，以及真实 Qdrant/MinIO 停止/恢复。后续增量已补齐隔离单节点磁盘满拒绝/释放恢复、有界大文件/并发、多版本恢复和四存储统一 backup set。修复健康已进入统一告警预览、Console 总览和 Prometheus 文本指标；仍需覆盖持续饱和、多节点/网络分区、生命周期扫描压力、业务端 507/503 与告警提前量，并接入正式外部值班渠道。
