# Chat SSE 与 Run 可靠性 v1

## 已实现语义

Chat 流式请求先创建并返回 `run_id`，随后输出阶段、trace、token 和终态事件。Run 以 PostgreSQL 为持久化来源，并遵循以下不变量：

1. `RUNNING` 可接受取消请求并进入 `CANCELLING`；同进程活跃 Task 会被立即中断，不必等待 Provider 返回下一段 token，执行协程确认后进入 `CANCELLED`。
2. `COMPLETED`、`FAILED`、`CANCELLED` 均为不可逆终态，迟到的取消或异常不能覆盖终态。
3. Provider 流式调用失败后允许尝试一次受控非流式 fallback；两者均失败时 Run 必须为 `FAILED`，不得用错误提示文本伪装成成功回答。
4. 客户端 TCP/SSE 断开时取消后台协程，Run 进入 `CANCELLED`，错误原因为 `client disconnected`。
5. 用户消息在管线开始时持久化；只有成功完成时才持久化助手消息，因此取消、失败和断线不会留下半成品助手消息。
6. 每个流使用独立 `ContextVar` 保存事件发送器；并发请求不会因为共享 `ChatPipeline` 单例而把事件写入其他请求的队列。

## API 行为

- `POST /api/v1/chat/sessions/{session_id}/messages/stream`：返回 SSE；首个 `run` 事件包含 `run_id`。
- `POST /api/v1/chat/runs/{run_id}/cancel`：仅对所属租户/用户的 `RUNNING` Run 接受请求；`execution_interrupted` 表示本实例活跃协程是否被直接中断，终态 Run 返回 `cancel_requested=false`。
- `GET /api/v1/chat/runs/{run_id}`：查询最终状态、阶段、错误和完成时间。

当前断线策略是取消，而不是续传。若后续需要恢复同一个响应流，应增加持久化事件序列、`Last-Event-ID`/cursor、重连租约及客户端去重协议，不能把当前取消语义描述为断线续传。

## 自动验证

生命周期测试共 8 项：

- 真实 Uvicorn + HTTP/SSE + 隔离 PostgreSQL：主动取消 API、Provider 双失败、TCP 断线；
- Runtime + 隔离 PostgreSQL：主动取消、Provider 双失败、消费者关闭、并发流事件隔离、终态不可逆。

这些测试使用严格命名的 `apf_chat_it_<32 hex>` schema，并在每项测试后精确清理。结合认证 6 项、mock Provider 2 项、schema migration 9 项和 Provider 故障沙箱 6 项，当前 Chat 测试基线为 31/31。
