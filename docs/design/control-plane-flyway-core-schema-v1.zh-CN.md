# Control Plane Flyway 核心 Schema 基线 v1

## 问题

Control Plane 已有 Flyway V2–V5，但没有 V1 核心表迁移。过去空库由 Hibernate `ddl-auto=update` 创建 tenant、IAM、Workflow、Conversation、Run 和 Event 表，Flyway 只负责后续可靠性表。因此已有开发库可以运行，但迁移文件无法独立复现完整 schema。

## 当前方案

- V1 `control_plane_core_schema`：tenants、users、roles、user_roles、refresh_tokens、workflows、workflow_versions、conversations、messages、runs、run_events。
- V2：Run dispatch outbox 与 execution lease。
- V3：Provider invocation 与 Run dead letter/audit。
- V4：Workflow Secret 与 node execution。
- V5：Node attempt。
- V6–V8：持久审批、独立审批事实、审批告警与可靠投递。
- V9：知识库元数据与租户绑定权威。
- V10：Task 运营权威。
- V11/V12：Task 派发 Outbox、事件幂等与 Task 删除级联。
- V13：批次取消计数、非负约束和父 Task 索引。

Hibernate 已切换为 `ddl-auto=validate`，只验证实体映射，不再创建或修改数据库对象。Flyway 保持 `validate-on-migrate=true`。

## 共享 schema 与 baseline 0

三个平面当前共用 PostgreSQL `public` schema。全新部署中，Execution 或 Chat 可能先创建自己的表，使 Control 启动时看到“非空 schema”。若 baseline version 为 1，Flyway 会跳过 V1，核心表仍然缺失。

因此新配置使用：

```yaml
spring:
  jpa:
    hibernate:
      ddl-auto: validate
  flyway:
    baseline-on-migrate: true
    baseline-version: 0
    validate-on-migrate: true
```

行为如下：

- 真正空 schema：直接执行 V1–V5；
- 已含其他平面表但没有 Flyway history：记录 baseline 0，再执行 V1–V5；
- 既有开发库已有 baseline 1 和 V2–V5：保留原历史，V1 处于 baseline 以下，不重复执行；
- checksum 不匹配或 Hibernate schema 校验失败：Control Plane 拒绝启动。

V1 使用幂等建表/索引，以支持 baseline 0 接管已有 Hibernate 核心表；后续版本禁止修改，只能追加 V6 及以上迁移。破坏性调整采用 expand/contract。

## 集成测试

`ControlSchemaMigrationIntegrationTest` 使用显式 `CONTROL_TEST_POSTGRES_JDBC_URL` 和严格命名的 `apf_cp_it_<32 hex>` 独立 schema，覆盖：

1. 空 schema 执行 V1–V13，并启动完整 Spring Mock Web 上下文；
2. Hibernate `validate` 验证所有 27 个 JPA repository 对应实体；
3. 已含无关表的共享 schema 从 baseline 0 执行全部迁移；
4. 真实 V1 schema 顺序升级到 V13；
5. 测试结束精确清理隔离 schema。

CI 的 Control Plane job 提供 PostgreSQL 16 service，并显式设置测试 JDBC URL。当前基线为 Maven 109/109，其中迁移专项 6/6。

## 运维约束

- 不允许在生产配置重新启用 `ddl-auto=update/create`。
- 发布前必须先在生产快照副本运行 Flyway migrate 和 Hibernate validate。
- 既有且没有 `flyway_schema_history` 的旧实例将从 baseline 0 接管；应先确认核心表来自兼容版本。
- 回滚优先使用向前修复迁移；涉及删列或数据重写时必须先建立可恢复备份。
- Control 当前仍与其他平面共用 `public` schema；长期应按服务拆分 schema/database，避免表名所有权冲突。
