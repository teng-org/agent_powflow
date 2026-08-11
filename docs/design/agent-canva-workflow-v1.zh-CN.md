# Agent Canva Workflow v1 设计与编排规则

## 1. 产品定位

Agent Canva 是 Powflow 工作空间内的 Workflow 可视化工作台。它不是新的执行服务，而是现有 Control Plane Workflow 资产和 Execution Plane 运行时的图形化编辑、校验、发布与调试界面。

第一版目标：让用户不接触 JSON，也能完成“添加节点 → 连接 → 配置 → 检查 → 保存 → 发布 → 测试运行”的完整路径。

## 2. 工作台信息架构

| 区域 | 职责 | 窄屏策略 |
|------|------|----------|
| 顶栏 | 返回、名称、草稿状态、检查、保存、发布 | 隐藏品牌和次级状态，保留主操作 |
| 节点库 | 按流程、AI、工具与协作分组添加节点 | 转为横向滚动的紧凑节点条 |
| 画布 | 节点拖拽、平移、缩放、连线、选择、删除 | 保留完整交互，固定最小可操作高度 |
| 属性面板 | 按节点类型编辑 config，编辑分支标签 | 移到画布下方 |
| 运行面板 | 输入测试数据、运行状态、事件和结果 | 与画布分标签展示，避免挤压 |

## 3. 双模型与序列化边界

### 3.1 执行 DSL（`draft_dsl`）

只保存可执行语义：

```json
{
  "entry_node_id": "start-1",
  "nodes": [{ "id": "start-1", "type": "start", "config": {} }],
  "edges": [{
    "id": "edge-1",
    "source_node_id": "start-1",
    "target_node_id": "llm-1",
    "condition_label": ""
  }]
}
```

### 3.2 画布布局（`draft_layout`）

只保存可复现的视觉状态：

```json
{
  "schema_version": 1,
  "positions": { "start-1": { "x": 100, "y": 160 } },
  "viewport": { "x": 60, "y": 70, "zoom": 0.9 }
}
```

禁止把选中节点、悬停、连线预览、面板开关等临时状态写入 layout。DSL 和 layout 一起进入发布版本 checksum。

## 4. v1 节点目录

| 类型 | 用户名称 | 关键配置 | 入/出约束 |
|------|----------|----------|-----------|
| `start` | 开始 | 无 | 无入边，恰好 1 条出边，全图唯一 |
| `end` | 结束 | 无 | 至少 1 条入边，无出边，至少存在一个 |
| `llm.generate` | 大模型 | prompt、system_prompt、temperature、max_tokens、retry | 恰好 1 条出边 |
| `retrieval.search` | 知识检索 | kb_id、query_field、output_field、top_k、retry | 恰好 1 条出边 |
| `condition.if` | 条件判断 | field、operator、value | `true/false` 或 `default` 分支 |
| `tool.http` | HTTP 请求 | method、url、headers/query/body、auth、timeout、output_field、retry | 恰好 1 条出边 |
| `human.approval` | 人工审批 | title、instructions、assignee_roles、timeout、timeout_action | `approve/reject` 两条出边 |
| `parallel.fork` | 并行分叉 | 无 | 2–8 条有唯一标签的出边 |
| `parallel.join` | 并行汇合 | answer_branch | 至少 2 条入边，恰好 1 条出边 |

## 5. 连接规则

### 5.1 通用规则

1. 连接的 source 和 target 必须存在。
2. 禁止节点连接自身，禁止重复的 source → target 连接。
3. `start` 不接收入边，`end` 不产生出边。
4. 普通节点恰好一条出边；多分支只通过显式条件、审批或并行分叉表达。
5. 新连线不得形成环。v1 是 DAG；循环需在未来 DSL 版本中显式设计最大迭代次数、退出条件和运行预算。
6. 所有节点必须从 `entry_node_id` 可达。
7. 上游 output schema 必须满足下游 input schema 的字段名和类型。

### 5.2 条件分支

- 分支标签只允许 `true`、`false`、`default`，且同一节点内唯一。
- 合法组合为 `true + false`，或至少一个 `default`。
- 新建分支时按 `true → false` 自动命名，用户可在连线属性中调整。

### 5.3 人工审批

- 必须恰好两条出边，标签分别为 `approve` 和 `reject`。
- 审批角色 1–8 个，角色码为 `[A-Za-z0-9_]{1,64}`。
- 超时范围 60–604800 秒，超时动作只能为 `approve` 或 `reject`。

### 5.4 结构化并行

- fork 必须有 2–8 条非空且唯一标签的出边。
- 每条分支在到达 end 或另一个 fork 之前，必须汇入同一个 join。
- 分支在声明的 join 前不得提前合并；v1 不允许嵌套 fork。
- 一个 join 只能归属于一个 fork，`answer_branch` 必须引用该 fork 的一个分支标签。

## 6. 校验分层

| 时机 | 执行方 | 行为 |
|------|--------|------|
| 添加/连接/编辑 | Agent Canva | 即时本地校验，高亮节点/边并禁止明显非法连接 |
| 保存草稿 | Control Plane | 返回结构化 errors/warnings；无效草稿允许保存，方便继续编辑 |
| 发布版本 | Control Plane | 重新执行权威校验；有 error 时 422 拒绝发布 |
| 运行 | Execution Plane | 只接受已发布的不可变 DSL + layout + checksum |

错误必须包含 `nodeId/edgeId/code/message/suggestion`。前端点击错误项应定位并选中对应画布元素。

## 7. 编辑状态与并发

- `revision` 使用数据库乐观锁；保存携带 `expected_revision`。
- 409 表示草稿已被其他会话修改，前端不得自动覆盖，必须提示刷新。
- 发布要求当前画布无未保存更改、最新一次校验通过，并携带当前 revision。
- 已发布 Workflow 仍可继续修改草稿；新发布生成下一个不可变版本。

## 8. 测试运行

1. Workflow 至少发布一个版本。
2. 创建测试会话：`POST /api/v1/conversations`。
3. 创建 Run：`POST /api/v1/conversations/{conversationId}/messages`，提交：

```json
{
  "content": { "text": "用户输入" },
  "workflow_id": "...",
  "workflow_version_no": 1
}
```

4. 轮询 `/api/v1/runs/{runId}` 和 `/api/v1/runs/{runId}/events/history`，直到 `SUCCEEDED/FAILED/CANCELLED/WAITING_APPROVAL`。
5. UI 展示 stage、progress、节点事件、耗时、output_payload、citations 与产品化错误信息。
6. 运行中允许取消，人工审批状态跳转到审批处理入口。

## 9. 安全和权限

- Workflow 列表、读取、保存、校验、发布和 Run 均使用当前登录令牌。
- Control Plane 以 tenant 隔离并检查 `created_by`，用户不能编辑其他用户的 Workflow。
- HTTP 节点只允许服务端沙箱支持的协议、主机、请求头和 Secret 引用；前端不收集或持久化 Secret 明文。
- 知识库选择器只显示当前工作空间对该用户可见的知识库。

## 10. 后续版本

- 变量映射/转换节点与 schema 可视化。
- 可复用子流程和节点分组。
- 受控循环节点（最大迭代、退出条件、预算）。
- 自动布局、框选、多选、复制粘贴和完整撤销栈。
- 节点级断点、单步运行、运行快照对比。
