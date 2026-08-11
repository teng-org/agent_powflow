# Control Plane 知识库元数据权威入口 v1

日期：2026-08-04

## 1. 范围与结论

本阶段让 Control Plane 接管知识库元数据和租户绑定的公开管理命令。统一 Console 不再直接通过 Execution Plane 创建、重命名、删除知识库或维护绑定；文档上传、切片、总结和 Task 仍由 Execution Plane 执行。

这是 KB/Document/Task 权威迁移的第一阶段，不代表文档与 Task 状态已经迁入 Control Plane，也不代表旧 `kb-admin-web` 已退役。

## 2. 数据所有权

当前 Control、Execution 仍共用 PostgreSQL `public` schema。V9 接管已有 `knowledge_bases` 与 `kb_tenant_bindings`，不会复制存量数据：

- 新建和经 Control 修改的 KB 写入 `metadata_owner=CONTROL`；
- 存量记录默认标记为 `EXECUTION_LEGACY`，仍可由 Control 查询并在首次修改时接管；
- 创建/重命名在租户级 PostgreSQL advisory transaction lock 内执行，保证 Control 命令并发时名称分配稳定；
- 所有查询与修改都使用令牌中的 `tenant_id`，不接受客户端覆盖所有者租户；
- 绑定目标必须是 Control Plane 中真实存在的租户。

## 3. 删除协议

知识库删除会涉及 PostgreSQL 文档/切片/任务、Qdrant collection 和 MinIO 对象，Control Plane 不直接复制这套副作用逻辑：

1. 用户向 Control Plane 发送 `DELETE /api/v1/admin/kbs/{kbId}`；
2. Control 先按令牌租户确认 KB 所有权；
3. Control 使用 `X-Internal-Token` 调用 Execution 的 `DELETE /api/v1/internal/kbs/{tenant_id}/{kb_id}`；
4. Execution 删除业务元数据并执行远端清理；Qdrant/MinIO 失败继续进入既有持久修复账本；
5. Execution 不可用或返回非预期状态时，Control 返回 `502`，不会伪报删除成功。

## 4. 公开接口

- `GET/POST /api/v1/admin/kbs`
- `PATCH/DELETE /api/v1/admin/kbs/{kbId}`
- `PATCH /api/v1/admin/kbs/{kbId}/visibility`
- `PATCH /api/v1/admin/kbs/{kbId}/freeze`
- `GET/PUT /api/v1/admin/kbs/{kbId}/bindings`
- `DELETE /api/v1/admin/kbs/{kbId}/bindings/{targetTenantId}`

内部物理清理接口只接受与 Run 回调相同的 Control 内部令牌。

## 5. 验证证据

- Control Plane：78/78，通过真实 PostgreSQL 空 schema、共享非空 schema、V1→V9 和 Hibernate `validate`，无跳过；
- Execution API 集成：容器内 20/20，通过内部删除令牌、错误租户 404 和成功清理；
- Console：14 个测试文件、46/46，通过 KB 元数据/绑定走 Control、文档操作保留 Execution 的路由契约；
- Endpoint catalog：253/253，100%，无重复、断链或未登记路由；
- 真实 HTTP：创建、`metadata_owner=CONTROL`、重命名、列表复读、删除全部通过，测试 KB 已清理；
- 真实浏览器：Console 知识库与 Execution 文档数据同时加载，0 error / 0 warning。

## 6. 尚未完成

1. 文档元数据、上传意图、Task 与 Task Event 仍以 Execution PostgreSQL 为权威；下一阶段需引入 Control 文档/Task 状态机与可靠 Outbox。
2. Execution 的旧公开 KB 元数据写接口仍为 `kb-admin-web` 兼容保留；完成该前端迁移后必须默认关闭或返回明确迁移响应，才能形成唯一写入口。
3. 当前共享 `public` schema 是迁移桥梁；拆 schema/database 后需通过事件/Outbox 同步执行投影，禁止跨服务直连对方 schema。
4. Console 切片和任务事件当前会一次渲染大量记录，需增加服务端分页或虚拟化。
