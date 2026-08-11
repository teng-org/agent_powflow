# Control Plane Workflow 生命周期 v1

- 日期：2026-08-02
- 状态：已实现并通过单元测试与 Compose smoke

## 1. 目标

让 Control Plane 成为 Agent Workflow 定义和发布版本的权威存储层，支持前端长期编辑草稿，同时保证执行端只能引用已校验、不可变的发布版本。

## 2. 状态与不变量

状态：`DRAFT | PUBLISHED | ARCHIVED`。

核心不变量：

1. 草稿允许暂时不通过 DSL 校验，便于 Canvas 渐进编辑。
2. 发布前必须通过 `DslValidator`；无 start/end、存在环或端口 schema 不兼容时返回 `422`。
3. 每次发布生成新的 `workflow_versions` 快照，包含 DSL、layout、SHA-256 checksum、发布人和发布时间。
4. 发布版本不可原地修改；后续编辑只修改 `workflows` 中的 draft。
5. `tenant_id` 来自已验证 access token，所有查询都使用 `workflow_id + tenant_id`，跨租户表现为 `404`。
6. 草稿保存、发布和归档支持 `expected_revision` 乐观并发检查，冲突返回 `409`。
7. 归档后禁止修改或发布；只有已归档工作流允许物理删除。

## 3. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/admin/workflows` | 当前租户工作流列表 |
| POST | `/api/v1/admin/workflows` | 创建草稿，返回当前校验结果 |
| GET | `/api/v1/admin/workflows/{workflowId}` | 草稿详情与校验结果 |
| PUT | `/api/v1/admin/workflows/{workflowId}/draft` | 保存草稿，可携带 `expected_revision` |
| POST | `/api/v1/admin/workflows/{workflowId}/validate` | 显式校验草稿 |
| POST | `/api/v1/admin/workflows/{workflowId}/publish` | 校验并发布不可变版本 |
| GET | `/api/v1/admin/workflows/{workflowId}/versions` | 版本列表 |
| GET | `/api/v1/admin/workflows/{workflowId}/versions/{versionNo}` | 版本快照 |
| POST | `/api/v1/admin/workflows/{workflowId}/archive` | 归档并转为只读 |
| DELETE | `/api/v1/admin/workflows/{workflowId}` | 删除已归档工作流及版本 |

## 4. 数据模型

`workflows` 保存身份、租户、状态、当前草稿、最新版本号、创建/更新人和 JPA `row_version`。

`workflow_versions` 保存每次发布的不可变 DSL/layout、版本号、checksum 和发布信息；`workflow_id + version_no` 唯一。

当前 schema 已由 Flyway V1–V5 显式管理，Hibernate 使用 `ddl-auto=validate`；Workflow 核心表位于 V1。

## 5. 验证

- `WorkflowServiceTest` 覆盖有效发布、无效 DSL 拒绝、租户隔离、陈旧 revision 冲突、无效草稿可保存、删除前必须归档。
- `tools/smoke/stack-smoke.ps1` 覆盖真实 PostgreSQL 下的创建、发布、checksum、版本查询、归档和删除清理。
- 统一 Console 已提供列表、草稿编辑、结构化校验结果、发布确认、不可变版本详情、归档和归档后删除；前端 JSON 语法错误会在请求前阻断，保存携带 `expected_revision`。
- Console Vitest 23/23、生产构建和 390px/820px/1280px 浏览器验收通过，详见 `console-workflow-management-v1.zh-CN.md`。

## 6. 后续边界

Run 已保存发布版本身份与执行快照，不允许运行时读取可变 draft；Execution Plane 只加载发布版本，并通过内部事件 API 回传状态。后续为图形化编辑器、fan-out/join 和人工审批扩展现有生命周期契约。
