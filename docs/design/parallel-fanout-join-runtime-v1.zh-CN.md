# Workflow 并行 fan-out/join 运行时 v1

- 日期：2026-08-03
- 状态：已实现并通过发布校验、真实存储测试与 Compose 全栈 smoke

## 1. DSL 契约

新增两类可执行节点：

- `parallel.fork`：2–8 条出边，每条边必须使用非空、唯一的 `condition_label` 作为稳定分支名；
- `parallel.join`：至少两条入边、恰好一条出边，`config.answer_branch` 必须明确引用所属 fork 的一个分支名。

v1 采用结构化并行：同一 fork 的所有路径必须在到达 `end` 前汇聚到同一个 join，分支在 join 前不得提前相交；一个 join 只能属于一个 fork。嵌套 fork 明确拒绝，避免在没有持久化分支调度模型时伪装支持任意并行 DAG。

普通节点仍必须恰好一条出边。Control Plane 现在会在发布前以 `NODE_OUTGOING_AMBIGUOUS` 拒绝普通节点多出边；Execution Plane 会独立重新编译并执行相同不变量，修复了此前只能在运行期发现歧义出边的缺口。

## 2. 执行与状态隔离

fork 使用有界线程池真实并发执行分支，最大并发数等于已校验分支数且不超过 8。每个分支从 fork 前状态的独立副本开始，节点仍使用原有重试、租约取消、Provider/Tool 业务幂等和安全 HTTP 边界。

join 按 DSL 出边顺序生成稳定的分支结果命名空间：

```json
{
  "branches": {
    "draft": { "answer": "...", "text": "..." },
    "review": { "answer": "...", "text": "..." }
  }
}
```

只有 `answer_branch` 对应分支的完整状态成为下游主状态；其他分支只存在于 `branches` 命名空间。这样不会出现“最后完成者覆盖 answer”的时间竞争，也不依赖线程完成顺序。

## 3. 失败、取消与审计

- 任一分支失败会触发共享中断信号，其他仍在退避、mock 延迟、Provider 或 HTTP 调用中的分支主动终止；
- 多分支同时失败时，按 fork DSL 分支顺序选择第一个非取消错误作为权威失败，错误格式包含稳定分支名；
- Provider/Tool 幂等键仍包含唯一 `node_id`，Worker/redelivery 不会重复成功副作用；
- 节点事件回调串行化，进度取单调最大值，避免并发完成顺序触发 Control Plane 进度回退；
- 节点 `ordinal` 使用稳定拓扑序，最终 `executed_node_ids` 按 fork 分支声明顺序输出，与线程调度无关；
- 分支节点事件包含 `parallel_branch`，fork 记录分支列表和 join id，join 记录 `answer_branch`、完成分支和各分支输出字段。

新增 metrics：`parallel_fork_count`、`parallel_branch_count`、`parallel_max_concurrency`。Provider/Tool/attempt/retry/citation 统计聚合所有分支。

## 4. 验证证据

- Control Plane：43/43，新增结构化 fork/join 通过、普通节点歧义出边拒绝、无效 `answer_branch` 拒绝；Flyway 空/遗留/V1→V5 与 Hibernate validate 真实 PostgreSQL 测试无跳过；
- Execution：83/83，真实 PostgreSQL + MinIO；新增线程屏障证明两个分支同时执行、确定性主输出/命名空间、单调事件进度、分支失败聚合和缺失 join 拒绝；
- Stack smoke：真实发布 `start → fork → draft/review → join → end`，两个 Provider 节点均执行，主输出选择 `draft`，`review` 输出保留，6 个节点记录全部成功并严格清理；
- Endpoint catalog：230/230，无新增未登记路由。

## 5. 后续边界

下一优先级是人工审批节点的持久化暂停/恢复、审批权限与超时策略。嵌套并行、跨 Worker 分支持久化调度、部分成功容忍策略和分支级补偿属于后续版本，不能以当前进程内有界并发替代。
