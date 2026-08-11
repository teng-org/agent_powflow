# Control Plane 文档命令权威 v1

## 1. 范围与结论

Task Authority Phase 2C 将单文档摘要、分块、重索引、草稿提交、移动、标签、删除和知识库摘要统一改为 Control 创建 Task、事件与事务 Outbox，Execution 只负责校验执行侧资源、投递 Celery 和执行 PostgreSQL/MinIO/Qdrant 副作用。Console 的对应写入口已切换到 Control；Execution 原公开接口仅作为兼容入口保留。

本阶段不包含批处理父子任务迁移、非索引上传的持久清理事实和旧 `apps/kb-admin-web` 退役，因此不能表述为全部 Document/Task Authority 完成。

## 2. Control 命令

| 命令 | Control API | Task type | Worker |
| --- | --- | --- | --- |
| KB 摘要 | `POST /api/v1/admin/kbs/{kbId}/summarize` | `KB_SUMMARIZE` | `summarize_kb` |
| 文档摘要 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/summarize` | `DOC_SUMMARIZE` | `summarize_document` |
| 文档分块 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/chunk` | `DOC_CHUNK` | `parse_document` |
| 文档重索引 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/reindex` | `DOC_REINDEX` | `parse_document` |
| 草稿提交 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/commit` | `CHUNK_COMMIT` | `commit_chunk_drafts` |
| 文档移动 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/move` | `DOC_MOVE` | `move_document` |
| 文档标签 | `POST /api/v1/admin/kbs/{kbId}/documents/{docId}/tags` | `DOC_TAG` | `tag_document` |
| 文档删除 | `DELETE /api/v1/admin/kbs/{kbId}/documents/{docId}` | `DOC_DELETE` | `delete_document` |

Control 在同一事务内创建 `PENDING` Task、`TASK_CREATED` 事件和 `WAITING` Outbox。相同命令可返回既有活跃任务；同文档不同写命令互斥。KB、目标 KB、参数和租户归属在 Control 侧 fail closed，Execution 派发时再次核对不可变 Task 身份与实际文档资源。

## 3. 执行与回写不变量

- Worker 通过 `TaskAuthorityBridge` 识别 `state_owner=CONTROL`，所有持久状态和事件回写 Control；旧 Execution-owned Task 仍本地兼容。
- `tenant_id + kb_id + doc_id + task_type` 创建后不可变；Control 的空文件名只允许由第一次 Worker 事件补齐。
- `chunk_overlap=0` 是有效配置，不得被默认值覆盖；配置只在 Execution 命令锁成功后落库。
- 标签支持 `add` 与 `replace`，输入去空、去重。
- 摘要读取 MinIO 解析文本引用；Provider 超时等瞬态失败写入 `TASK_RETRY_SCHEDULED`，最多重试 2 次，重试耗尽后才进入 `FAILED`。
- 文档移动保留 MinIO bucket、object key、checksum、类型和字节数元数据。显式删除会清除对象的全部 MinIO 版本及 delete marker；失败进入跨存储修复账本。

## 4. 真实验收（2026-08-08）

- Control：100/100，真实隔离 PostgreSQL，0 跳过，Flyway V1–V12；
- Execution：142/142，真实隔离 PostgreSQL + TLS MinIO，0 跳过，schema V1–V8；
- Console：51/51；Endpoint catalog：275/275、100%，无重复、悬空或漏登记；
- 确定性全栈 smoke：6 个 Control-owned Task（入库、分块、提交、标签替换、移动、删除）全部成功，59 条 Execution 回调事件，`chunk_overlap=0` 保留；
- 严格清理：两个 KB、文档、Task、事件、Outbox、孤儿 Outbox、隔离测试 schema、两个 Qdrant collection 和两个 MinIO 版本化前缀均为 0。

可重复执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/smoke/document-command-authority-smoke.ps1 -SkipProviderCommands
```

Provider 摘要真实调用曾返回上游读取超时；协议、对象引用、回写和瞬态重试已有自动测试，外部 Provider 可用性不计入确定性 smoke 的通过条件。联调时去掉 `-SkipProviderCommands`，并将超时提高到至少 240 秒。

## 5. 后续顺序

1. 把批处理父 Task、子 Task 和 `task_batches` 编排迁移到 Control，并为部分成功/取消/重放建立权威状态机。
2. 为 `UPLOAD_ONLY` / `UPLOAD_AND_CLEAN` 增加持久命令和清理事实。
3. 观察兼容入口调用量后，退役 Execution 公共写入口与旧 `apps/kb-admin-web`。
4. 将 Execution 巨型 API/Store 按 document、task、retrieval、governance 领域拆分。
