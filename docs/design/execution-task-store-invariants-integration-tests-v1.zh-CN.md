# Execution 任务存储不变量与 PostgreSQL 集成测试 v1

## 目标

本设计约束 Execution Plane 的持久化任务、知识库租户边界和 Celery 重投递行为。任务状态以 PostgreSQL 为权威来源；API、Worker 和事件写入不得绕过同一组身份与状态不变量。

## 任务身份不变量

`task_id` 一旦创建，其 `tenant_id`、`kb_id`、`doc_id`、`task_type` 不可改变。

- 首次创建使用只插入语义，并只产生一条 `TASK_CREATED` 事件。
- 相同身份的重复创建视为幂等成功，不重置状态、阶段、进度或取消标记。
- 不同身份复用同一 `task_id` 必须抛出 `TaskIdentityConflict`。
- 事件写入必须与任务的权威 tenant/KB/document 身份一致，禁止借事件接口伪造归属。

## 状态与进度不变量

允许的主状态转换为：

- `PENDING -> PENDING | RUNNING | CANCELLING | FAILED | CANCELLED`
- `RUNNING -> RUNNING | CANCELLING | SUCCEEDED | FAILED | CANCELLED`
- `CANCELLING -> CANCELLING | FAILED | CANCELLED`
- `SUCCEEDED | FAILED | CANCELLED` 只能原样幂等重放。

终态的状态、阶段、错误信息和进度均不可被后续重投递改写。完全相同的终态重放不重复产生事件；非法转换抛出 `InvalidTaskStateTransition`。

进度采用单调更新：`current`、`total` 和 `pct` 只可增加；任务进入终态后忽略进度写入。进入终态时释放相应的任务去重锁。

## 租户访问边界

- 普通任务轮询 `GET /api/v1/tasks/{task_id}` 只按 `X-Tenant-Id` 返回记录；其他租户得到兼容轮询协议的 `UNKNOWN`，不暴露真实状态。
- 管理任务事件和取消接口必须同时匹配管理员请求上下文中的租户，否则返回 404。
- 任务事件列表按任务和租户双重过滤。
- KB 的读取、重命名、删除、可见性与绑定操作均以 tenant 为边界；不存在或不属于该租户的 KB 不得触发 Qdrant 删除副作用。

## Worker 重投递与取消

文档类 Celery 任务进入业务逻辑前统一调用持久化任务启动门禁：

1. 幂等创建或读取任务；
2. 校验请求身份与权威任务身份一致；
3. 已进入终态则直接返回 `already_terminal`，不重开或重复执行；
4. 已请求取消或处于 `CANCELLING` 时收敛到 `CANCELLED`；
5. 只有通过门禁的任务才进入 `RUNNING` 和后续业务步骤。

该门禁应用于解析、提交切片草稿、文档/知识库总结、移动、标签和删除等持久化任务。

## 隔离 PostgreSQL 测试基座

测试必须显式提供 `EXECUTION_TEST_POSTGRES_DSN`，未提供时跳过真实数据库用例，防止误连开发或生产库。每个测试用例：

1. 生成格式严格为 `apf_it_<32 hex>` 的独立 schema；
2. 通过 `search_path` 让当前 Store 只访问该 schema；
3. 使用生产代码的建表/索引路径建立当前结构；
4. 测试结束时再次校验 schema 名称后精确 `DROP SCHEMA ... CASCADE`；
5. 即使初始化失败也执行同样的受限清理。

CI 的 `python-and-contracts` 作业提供 PostgreSQL 16 service，并设置：

```text
EXECUTION_TEST_POSTGRES_DSN=postgresql://apf:apf@localhost:5432/agentpowflow
```

本地完整容器回归命令：

```powershell
docker exec -e EXECUTION_TEST_POSTGRES_DSN=postgresql://apf:apf@postgres:5432/agentpowflow agentpowflow-execution-api python -m unittest discover -s /app/services/execution-plane/tests -p 'test_*.py' -v
```

## 当前验收基线

- Execution unittest：83/83；
- Store 集成：任务身份、状态/进度终态、租户取消/去重锁、KB 可见性/绑定/变更、Worker 终态/取消重投递；
- FastAPI 集成：KB 管理及任务状态/事件/取消的跨租户保护；
- Stack smoke：额外验证普通任务查询、管理事件、管理取消三类跨租户请求；
- Outbox reliability 与 DLQ audit smoke 均通过；
- 测试完成后不遗留 `apf_it_%` schema。

## 后续边界

Execution PostgreSQL 已由显式 V1–V4 迁移接管，Control Plane 已由 Flyway V1–V5 接管，Chat PostgreSQL 也已由独立 V1/V2 迁移接管；文档 PostgreSQL/MinIO/Qdrant 发布窗口已有故障注入、对象校验和删除补偿账本，仍需继续覆盖真实 Qdrant/MinIO 断网、磁盘满和大文件压力场景。
