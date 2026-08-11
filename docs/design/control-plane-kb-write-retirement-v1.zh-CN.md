# Control Plane KB 写入口收口与旧后台退役 v1

- 日期：2026-08-08
- 阶段：Task Authority Phase 2F
- 状态：已实现并完成真实运行态验收

## 1. 目标

知识库公开写操作只允许经 Control Plane 进入。Execution Plane 保留读取接口、内部执行接口和副作用实现，不再作为浏览器或第三方客户端的公开写入口。独立 `apps/kb-admin-web` 从默认部署退役，统一由 `apps/web-admin` Console 承载运营操作。

本阶段关闭 Phase 2E 后剩余的四类直接元数据写入：文档冻结、分块冻结、分块草稿保存、分块草稿清除。

## 2. 边界与接口

Control Plane 新增租户身份约束下的公开接口：

- `PATCH /api/v1/admin/kbs/{kbId}/documents/{docId}/freeze`
- `PATCH /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/freeze`
- `PUT /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/draft`
- `DELETE /api/v1/admin/kbs/{kbId}/documents/{docId}/chunks/{chunkId}/draft`

Control 使用服务内部令牌和令牌中的 `tenant_id` 调用 Execution 对应的 `/api/v1/internal/kbs/{tenantId}/...` 接口。客户端不能提供或覆盖内部租户身份。草稿公开 DTO 只接受非空 `text`，最大 1,000,000 字符；Control 将其规范化为内部 `action=edit`，避免旧 Console 请求缺少 `action` 时返回 422 的契约漂移。

## 3. 旧写入口退役策略

Execution 公共 KB/文档/Task 写依赖在完成认证后检查 `EXECUTION_LEGACY_KB_WRITE_ENABLED`：

- 默认值 `false`：返回 HTTP `410`，错误码 `LEGACY_KB_WRITE_RETIRED`，提示改用 Control Plane；
- 显式值 `true`：只用于短期兼容或回滚，继续执行历史实现；
- KB 读取接口和非 KB 的系统管理员接口不受影响；
- 内部 token 接口不受公开兼容门禁影响。

`kb-admin-web` 使用 Compose profile `legacy-kb-admin`，不会出现在默认 `docker compose config --services` 或默认启动中。需要短期回滚时，必须同时显式启用 profile 和兼容环境变量；仅启用其中一项不能恢复旧写链路。

## 4. Console 实现

统一 Console 的草稿、文档冻结和分块冻结全部切换为 Control 请求。文档表与分块表提供“冻结/解冻”即时操作，仍由 Execution 读取接口回显最终状态。文件上传继续提供三种模式：索引、仅清洗、仅原始上传，并统一创建 Control Task 或持久完成事实。

旧前端源码暂时保留，便于兼容窗口内对照和紧急回滚，但不再接受新功能开发；兼容期结束后可独立删除。

## 5. 验证证据

- Control Plane：110/110，通过真实 PostgreSQL 与 Flyway V1→V13；
- Execution Plane：153/153，通过隔离 PostgreSQL 与 TLS MinIO，0 跳过；
- Web Admin：54/54，生产构建通过；
- Control→Execution 内部契约：13/13；
- Debug Web 端点目录：287/287，无重复、悬空或遗漏；
- 默认 Compose 服务清单不包含 `kb-admin-web`，历史运行容器已停止；
- 浏览器实测统一 Console 可见三种上传模式、文档/分块冻结和草稿操作，未出现旧后台品牌。

真实脚本 `tools/smoke/kb-write-retirement-smoke.ps1` 验证：

1. Execution 公共创建 KB 返回 `410 / LEGACY_KB_WRITE_RETIRED`；
2. Control 创建 KB、文本入库并等待 Control-owned Task 成功；
3. Control 保存/清除分块草稿并由 Execution 读取回显；
4. Control 冻结文档和分块并由 Execution 读取回显；
5. 删除临时 KB 后，PostgreSQL 业务记录和 Outbox、Qdrant collection、MinIO 精确对象的全部版本均为 0。

## 6. 安全、兼容与回滚

- `410` 在认证之后返回，匿名调用仍遵循原认证失败语义，不泄露管理接口状态；
- Control 内部客户端将 404、4xx 和上游不可用映射为稳定的公开错误，不把内部令牌返回给客户端；
- 兼容开关默认关闭且不应写入生产固定配置；每次临时开启都应记录负责人、到期时间和回滚原因；
- 回滚只恢复入口，不改变 Control 已持久化的 Task/事件事实；恢复旧入口期间禁止同一操作从两个前端并发提交。

## 7. 后续工作

本阶段只收口写入口，不代表系统已生产完备。下一优先级为：

1. 为文档移动的 Qdrant/MinIO 外部故障建立专用、可重放的持久修复操作；
2. 使用真实第三方 Provider 测试账号验证流式格式、供应商限流头、计费和熔断差异；
3. 完成多节点/持续饱和/磁盘满演练，以及 Vault/KMS、PITR、异地不可变备份和最小权限拆库；
4. 拆分 Execution 巨型 API/store 模块，并为 Web App 建立自动化 E2E 基线。
