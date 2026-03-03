# Agent Canvas + Chat Plane 实施规划（v1）

## 目标

1. 先落地可运行对话最小闭环：SSE、会话、消息、取消。
2. 用节点编排固化流程：`normalize -> retrieval -> prompt -> generation -> persist`。
3. 预留插件接口，后续可插入记忆压缩、工具调用、策略守卫节点。

## 当前落地点

1. `services/chat-plane/runtime/plugins.py`
- `NodePlugin`、`ToolPlugin` 接口。
- `node_registry`、`tool_registry` 全局注册中心。

2. `services/chat-plane/runtime/pipeline.py`
- 内置节点与默认 pipeline。
- SSE token 级输出。
- 取消检查在节点切换和 token 生成阶段生效。

3. `services/chat-plane/api/app/main.py`
- 会话/消息/流式接口。
- `agent-canvas/schema` 输出编排图结构。

## 下一步（agent_canvas）

1. 增加 `pipeline DSL` 持久化（DB）
- 节点类型、参数、边、条件分支。

2. 增加可视化配置
- 在 debug web 增加 canvas 面板：可查看默认图、节点参数、运行时状态。

3. 增加节点扩展
- 记忆节点：短期窗口 + 中期压缩摘要。
- 工具节点：查询、调用外部 API、工作流动作。
- 安全节点：提示词注入检测、内容审计、权限过滤。

4. 增加运行追踪
- 每个节点产出结构化 trace，支持重放与失败诊断。

## 接口对齐建议

1. 对话流统一事件格式：
- `run`
- `stage`
- `token`
- `tool`
- `done`
- `error`

2. 后续接 Spring Boot control-plane 时，chat-plane 仅暴露内部接口，由 control-plane 网关转发并附带 tenant/actor/trace。
