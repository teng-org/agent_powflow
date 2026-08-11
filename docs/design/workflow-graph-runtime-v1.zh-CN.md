# Workflow 图运行时 v1

- 日期：2026-08-03
- 状态：已实现并通过 Compose 全栈验证

## 1. 支持范围

运行时现在按 `entry_node_id + edges` 执行有向无环图，不再按数组顺序只取第一个 LLM。当前可执行节点与 Control Plane 注册表严格一致：

| 节点 | 运行语义 |
| --- | --- |
| `start` | 将不可变 Run `input_payload` 初始化为运行状态 |
| `condition.if` | 按字段路径、运算符和值选择 `true`、`false` 或 `default` 边 |
| `parallel.fork` | 将隔离状态并发分发到 2–8 个具名分支 |
| `parallel.join` | 等待结构化分支完成，保留命名空间并按 `answer_branch` 确定主状态 |
| `llm.generate` | 使用当前 `answer/text` 作为输入；支持 mock 或带业务幂等的 `api_strict` |
| `retrieval.search` | 在租户可访问的 KB 中检索，并传播标准引用 |
| `tool.http` | 在默认拒绝的网络策略内执行幂等 HTTP 调用 |
| `end` | 终止当前分支并输出最终 `answer` |

多个 `llm.generate` 可以串联，后一个节点读取前一个节点的答案。条件运算符包括 `truthy`、`falsy`、`equals/eq`、`not_equals/ne`、`contains`、`exists` 和 `in`。

## 2. 发布与运行时双重防御

Control Plane 发布前阻断：

- 未注册、当前无法执行的节点类型；
- 重复节点、环、悬空边和不可达节点；
- 非条件节点的歧义出边；
- 缺少 true/false 或 default 的条件分支；
- 非法条件运算符；
- 越界的 `temperature`、`max_tokens`、`mock_delay_ms` 和类型错误的 LLM 配置。

Execution Plane 会重新编译和校验快照，防止绕过发布接口或历史脏数据静默执行。没有 `edges` 的旧测试/内部载荷按节点数组生成显式的线性兼容边，并在 metrics 标记 `legacy_implicit_edges=true`；正式发布的 Workflow 始终应包含边。

## 3. 状态、分支和终止

条件节点只执行被选中的一条路径；未选分支不会调用 Provider，也不会产生节点事件。结构化 `parallel.fork` 会真实并发执行具名分支，并在共同 `parallel.join` 处确定性汇聚；普通节点仍必须恰好一条出边。v1 不支持嵌套 fork，所有可发布节点必须从入口结构可达，实际路径必须以 `end` 结束。详细契约见 `parallel-fanout-join-runtime-v1.zh-CN.md`。

输出 metrics 增加：

- `executed_node_count`、`executed_node_ids`；
- `provider_call_count`、`provider_cache_hits`；
- `parallel_fork_count`、`parallel_branch_count`、`parallel_max_concurrency`；
- `legacy_implicit_edges`。

## 4. 逐节点事件

Worker 为实际执行路径中的每个节点写入 `NODE_STARTED` 和 `NODE_COMPLETED`：

- event id 由 `run_id + node_id hash + event_type` 稳定生成，Celery retry 不会重复落库；
- Run progress 单调递增并保留 90→100 给最终成功事件；
- payload 只记录节点身份、序号和分支选择，不写入完整运行状态或提示词；
- 条件完成事件记录 `condition_result`、`selected_edge_id`、`selected_label`，支持审计分支决策；
- 回写失败属于基础设施失败，沿用 Celery retry；Provider live claim 冲突同样进入受控重试。

## 5. Console 运行追踪

租户管理员可通过 `/api/v1/admin/runs?status=...` 浏览最近 100 条同租户 Run，再通过 `/api/v1/admin/runs/{runId}` 和 `/api/v1/admin/runs/{runId}/events` 查看详情，不受普通用户接口的 creator 限制，仍由 `/api/v1/admin/**` 角色与 tenant claim 隔离。

Console“运行追踪”页提供最近运行与状态筛选、Run 状态/阶段/进度、实际节点路径、节点耗时、条件分支和 Provider 缓存详情、完整事件时间线，以及输入/输出/指标/引用折叠面板。页面只展示事件中经过最小化的节点元数据，不依赖完整提示词泄露来解释执行路径。

## 6. 验证证据

- Execution unittest：83/83；覆盖并行线程重叠、确定性 join/失败聚合、条件分支、多 LLM 传值、Retrieval、HTTP 安全/Secret 边界、事件、取消、重试、副作用幂等、真实存储与迁移。
- Control Plane Maven：43/43；覆盖结构化并行/歧义出边 fail-closed、Flyway 空/非空 schema 与 Hibernate validate、未知节点/Secret/重试配置、管理员追踪和节点/attempt 记录。
- Console Vitest：23/23；生产构建通过，Run trace 使用权威节点/attempt 记录，工作流管理覆盖草稿、校验、不可变版本和归档删除，跨存储修复具备健康、统一告警和运维面板。
- Endpoint catalog：230/230、100%。
- Stack smoke：真实发布条件双分支图和六节点并行 fork/join 图；条件只执行 true 路径，并行执行两个 Provider 分支且确定性选择主输出，节点记录、管理员追踪、分支审计和清理通过。
- Outbox reliability smoke 与 DLQ audit smoke 在图运行时上复跑通过。

## 7. 后续边界

统一节点适配器、`retrieval.search` 和受限 `tool.http` 已落地，详见 `workflow-node-adapters-retrieval-http-v1.zh-CN.md`。后续优先级为：

1. 本地真实 HTTP Provider/Tool 沙箱中的重试与幂等已验收；继续补第三方测试账号的供应商契约；
2. 人工审批的持久化暂停/恢复已完成；
3. 嵌套并行、跨 Worker 持久化分支调度与部分成功/补偿策略；
4. Workflow 主密钥事务重加密与 JWT 无停机轮换已完成；外部 KMS 仍待接入。
