# Control Plane 非索引上传权威与持久清理 v1

日期：2026-08-08
范围：Task Authority Phase 2E，`UPLOAD_ONLY` / `UPLOAD_AND_CLEAN`

## 1. 结论

两种非索引 multipart 模式不再走“无 Task、请求期补偿”的兼容路径。三种上传模式现在统一先创建 Control-owned Task、创建事件和持久上传意图，再由 Execution 暂存对象：

| 模式 | Task 类型 | 成功阶段 | 持久事实 | 是否派发 Worker |
| --- | --- | --- | --- | --- |
| `UPLOAD_ONLY` | `DOC_UPLOAD` | `UPLOADED_RAW` | `DOC_UPLOAD_ARTIFACT / COMPLETED` | 否 |
| `UPLOAD_AND_CLEAN` | `DOC_UPLOAD` | `CLEANED` | `DOC_UPLOAD_ARTIFACT / COMPLETED` | 否 |
| `UPLOAD_CLEAN_CHUNK_EMBED` | `DOC_INGEST` | `QUEUED` 后由 Worker 推进 | `DOC_INGEST_OBJECT / PUBLISHED` | 是 |

二进制和解析正文仍不进入 Control 数据库或 Outbox；Outbox 只保存不可变身份、模式、切片参数和经 Execution 返回并校验的对象引用。

## 2. 状态协议

### 2.1 创建与暂存

Control 在一个事务内写入：

1. `tasks`：`PENDING / UPLOAD_PENDING / state_owner=CONTROL`；
2. `task_events`：`TASK_CREATED`，包含 `process_mode`；
3. `task_dispatch_outbox`：`WAITING_UPLOAD`。

随后 Control 使用内部凭据向 Execution 提交单文件暂存。Execution 对所有模式都要求 `task_id`，并校验：

- Task 必须存在且为 `state_owner=CONTROL`；
- `tenant_id / kb_id / doc_id` 必须完全一致；
- 索引模式只能对应 `DOC_INGEST`，非索引模式只能对应 `DOC_UPLOAD`；
- Task 必须仍为 `PENDING / UPLOAD_PENDING`。

校验完成后，Execution 才允许写 PostgreSQL 文档事实及 MinIO 原件/解析文本。

### 2.2 非索引成功

Execution 返回对象 bucket、key、SHA-256 和字节数后，Control 在一个事务中：

- 校验响应 `process_mode` 与上传意图一致；
- 将对象引用写入意图 payload；
- 将意图置为 `COMPLETED`；
- 将 Task 置为 `SUCCEEDED / UPLOADED_RAW|CLEANED / 100%`；
- 写入唯一 `TASK_UPLOAD_COMPLETED` 事件。

`COMPLETED` 表示对象已经成为有效业务文档且无需派发，不应伪装为 `PUBLISHED`，也不会被 dispatcher 领取。

### 2.3 失败与进程崩溃

| 故障窗口 | 持久状态 | 恢复动作 |
| --- | --- | --- |
| Execution 写入前失败 | `CLEANUP_PENDING` | 精确删除目标文档，幂等收敛为 `CANCELLED` |
| Execution 部分写入后失败 | `CLEANUP_PENDING` | 删除 PostgreSQL 文档、MinIO 全版本及可能的 Qdrant 副本 |
| 暂存成功、Control 完成事务前崩溃 | 仍为 `WAITING_UPLOAD` | 超时扫描改为 `UPLOAD_TIMEOUT / CLEANUP_PENDING` 后执行相同精确清理 |
| 清理服务暂时不可用 | 保持 `CLEANUP_PENDING` | 定时恢复器继续重试，不提前宣称已清理 |

只有 Execution 返回精确清理成功后，Control 才把意图收敛为 `CANCELLED`。因此请求断线或 Control 进程终止不会再产生“无持久意图的孤儿对象”。

## 3. Console

统一 Console 的 multipart 卡片现在支持：

- 三种处理模式显式选择；
- 多文件一次提交；
- 非索引模式隐藏无效的自动总结选项；
- 复用切片区 method/size/overlap 参数作为未来处理元数据；
- 显示接受/跳过文件数量，并在成功后重置原生文件控件；
- 明示所有模式均创建可审计的 Control Task。

桌面端与 390×844 窄屏均无页面级横向溢出；切换模式的 DOM 状态正确，浏览器 warning/error 为 0。

## 4. 验收证据

- Control：109/109，真实隔离 PostgreSQL，Flyway V1→V13 与 V1 接管升级，0 跳过；
- Execution：150/150，真实隔离 PostgreSQL + TLS MinIO，0 跳过；
- Console：53/53，生产构建通过；
- Endpoint catalog：279/279，100%；
- Execution 内部上传契约：11/11；
- 真实冒烟：`tools/smoke/non-index-upload-authority-smoke.ps1`。

真实冒烟同时证明：

- 两种模式产生 2 个 `DOC_UPLOAD` Control Task；
- 2 个 `TASK_UPLOAD_COMPLETED` 事件；
- 2 个 `DOC_UPLOAD_ARTIFACT / COMPLETED` 持久事实；
- Execution 文档状态分别为 `UPLOADED_RAW` 和 `CLEANED`，切片数均为 0；
- 删除测试 KB 后 PostgreSQL、Qdrant、MinIO 全版本严格为 0。

## 5. 后续边界

Phase 2E 关闭的是非索引上传持久权威缺口。仍需继续：

1. 退役 Execution 公共写入口与 `apps/kb-admin-web`；
2. 为移动外部副作用增加专用、可人工触发的持久修复操作；
3. 完成外部摘要 Provider 的限流、熔断、计费和流式差异联调；
4. 补持续饱和、磁盘满和多节点故障演练。
