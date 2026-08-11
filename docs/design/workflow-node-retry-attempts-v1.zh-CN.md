# Workflow 节点重试与 Attempt 审计 v1

- 日期：2026-08-03
- 状态：已实现并通过单元测试与 Compose 全栈验证

## 1. 目标与边界

节点重试用于吸收 Provider、检索存储或受控 HTTP 工具的短暂失败，同时保持 Run 取消、执行失租、网络安全策略和业务幂等边界。重试默认关闭；节点不显式配置时只执行一次。

v1 不承诺 exactly-once。`llm.generate` 与 `tool.http` 继续使用稳定 invocation key；HTTP 写请求继续携带稳定 `Idempotency-Key`。如果下游在响应丢失前已经提交副作用但不支持幂等，显式重试仍可能产生重复副作用。

## 2. DSL 契约

`retry` 只允许出现在 `llm.generate`、`retrieval.search` 和 `tool.http` 的 `config` 中：

| 字段 | 默认值 | 约束 | 语义 |
| --- | ---: | --- | --- |
| `max_attempts` | 1 | 1–5 的整数 | 包含第一次执行；1 表示关闭重试 |
| `initial_delay_ms` | 250 | 0–30000 | 第一次失败后的等待时间 |
| `backoff_multiplier` | 2 | 1–4 | 指数退避倍率 |
| `max_delay_ms` | 5000 | 0–60000，且不得小于初始延迟 | 单次退避上限 |
| `retry_on` | `transient` | `transient` / `any` | 自动重试已识别短暂故障，或显式重试其他非硬失败 |

发布校验会拒绝未知字段、非法范围、非整数 attempt 数、非法策略和不支持重试的节点类型。Runtime 对数值再次收敛到安全上限。

## 3. 故障分类

以下情况永不重试，即使配置 `retry_on=any`：

- Run 已取消或 Worker 已失租；
- Secret 缺失、禁用、无法解密或租户不匹配；
- SSRF、主机 allowlist、私网、重定向、请求头或响应大小策略拒绝；
- Provider invocation 稳定键与请求哈希冲突；
- 明确的参数类型、值或状态路径错误。

`transient` 当前识别：

- Provider invocation 被另一个活跃 attempt 暂时持有；
- HTTP 408、425、429、500、502、503、504；
- HTTP transport 与 timeout 异常。

`any` 只应在调用本身可幂等、下游支持幂等键，或人工确认重复副作用风险后使用。

## 4. 事件与持久化

原有 `NODE_STARTED`、`NODE_COMPLETED`、`NODE_FAILED` 保持不变。仅当 `max_attempts > 1` 时增加：

- `NODE_ATTEMPT_STARTED`
- `NODE_ATTEMPT_SUCCEEDED`
- `NODE_ATTEMPT_FAILED`

Attempt 事件 ID 包含 Run、节点哈希、attempt_no 和事件类型，Celery redelivery 不会重复入库。事件详情只包含 attempt 序号、耗时、故障类型/裁剪消息、是否重试和退避时间，不包含输入状态、Provider 响应正文或 Secret。

Flyway V5 创建 `run_node_attempts`，以 `tenant + run + node + attempt_no` 唯一约束持久化每次尝试。`run_node_executions` 继续表示节点聚合终态；管理员节点查询把 attempts 按序嵌入节点响应。Console 可以展开查看每次尝试的状态、耗时、退避和错误。

## 5. 指标与取消

- `node_attempt_count`：实际执行的节点尝试总数，包含未配置重试的单次节点；
- `node_retry_count`：发生退避并进入下一次尝试的次数；
- 退避使用可中断短睡眠，期间持续检查取消/失租信号；取消不会开启下一次 attempt。

## 6. 验证基线

- Execution unittest：83/83（容器），覆盖恢复成功、取消不重试、并行分支、真实 HTTP 故障、幂等重放、V1–V4 migration、Store/API PostgreSQL、统一告警、MinIO 治理和三存储一致性；
- Control Plane Maven：43/43，覆盖 Flyway/Hibernate schema 验证、结构化并行/DSL fail-closed 与两次 attempt 权威持久化；
- Console Vitest：23/23，覆盖旧事件回退重建 attempts、Workflow 生命周期、跨存储修复健康/交互状态和总览告警跳转；
- Stack smoke：失败 mock 节点执行两次，第一条 attempt 可重试、第二条终止，Run 最终 FAILED 且临时数据严格清理。

## 7. 后续演进

1. 本地真实 HTTP Provider/Tool 沙箱已校验限流、断连、响应丢失和下游幂等行为；继续补第三方供应商契约；
2. 增加按错误码/HTTP 状态白名单配置，并避免依赖字符串错误分类；
3. 支持管理员从指定失败节点恢复时，必须先设计状态快照与上游输出持久化；
4. fan-out/join 落地后，为并行分支定义独立 attempt 和失败聚合规则。
