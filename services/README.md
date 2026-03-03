# services

后端服务目录。

## 子服务

1. `control-plane`
- 当前控制平面服务（后续会演进为 Spring Boot 权威控制平面）。

2. `execution-plane`
- 知识库执行平面：解析、切片、嵌入、索引、检索、总结与后台任务。

3. `chat-plane`
- 新增对话平面（MVP）：
- SSE 对话流。
- 节点式 pipeline 与插件接口骨架。
- 可选知识库检索调用（对接 execution-plane）。

## 说明

- `chat-plane` 当前为最小可运行版本，使用内存态存储会话与运行状态。
- 生产化阶段会迁移至持久化存储并挂到 control-plane 统一鉴权治理。
