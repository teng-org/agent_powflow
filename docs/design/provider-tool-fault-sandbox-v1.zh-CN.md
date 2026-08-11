# Provider / HTTP Tool 故障沙箱 v1

## 目的与边界

本阶段建立本地可编程 HTTP 故障服务器，真实经过 Socket、HTTP 协议解析、连接池、流式 SSE 和超时机制，不使用 `MockTransport` 替代网络。它用于稳定复现第三方服务难以按需制造的 401、429、连接中断、读取超时和并发窗口。

该沙箱证明客户端协议与故障语义，不等同于特定商业 Provider 的账号验收。上线前仍需使用供应商测试账号验证其限流头、计费、幂等支持和流式格式。

## Chat Provider 行为

Chat 非流式和流式调用共用以下分类：

- `408/425/429/500/502/503/504`、`httpx.TimeoutException` 和 `httpx.TransportError` 可重试；
- `401/403` 等认证/权限错误不可重试；
- 指数退避由 `chat_provider_retry_initial_ms` 和 `chat_provider_retry_max_ms` 控制；
- 429 的数值型 `Retry-After` 会成为最小等待时间，并限制在 60 秒内；
- 每次失败与成功均记录真实 HTTP status；
- 移除 LLM Client 全局串行锁，由 API Pool 的 `max_concurrency` 控制并发。

配置默认值：

```json
{
  "chat_llm_timeout_seconds": 120,
  "chat_request_retry_max": 2,
  "chat_stream_retry_max": 2,
  "chat_provider_retry_initial_ms": 250,
  "chat_provider_retry_max_ms": 5000
}
```

## HTTP Tool 行为

`SafeHttpExecutor` 仍保持默认拒绝、域名 allowlist、私网拒绝、无重定向、响应体上限和受控认证头。副作用方法携带稳定 `Idempotency-Key`，节点重试仅接受 transient 状态/传输异常；401 属于硬失败。成功结果写入 Provider Invocation 账本，Worker 重投递直接命中缓存，不再次调用下游。

## 自动验证

Chat Provider 沙箱 6 项：

- 非流式 429 后恢复，鉴权头保持一致；
- 流式 429 后恢复且审计记录 429/200；
- 401 只调用一次；
- 真实连接中断后恢复；
- 两次真实读取超时后向上抛出；
- 两个 Provider 请求确实并行进入服务器。

HTTP Tool 沙箱 5 项：

- 429 节点重试使用同一幂等键；
- 401 不重试；
- 成功副作用在重投递时命中缓存；
- 真实连接中断按 transient 分类；
- 真实超时重试有界。

当前回归基线：Chat 31/31，Execution 83/83。
