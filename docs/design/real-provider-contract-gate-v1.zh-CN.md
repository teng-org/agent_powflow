# 真实 Provider 契约与费用门禁 v1

- 日期：2026-08-08
- 状态：契约执行器已实现；供应商账号实测待显式授权

## 1. 为什么仍不能宣布 Provider 已验收

`provider-tool-fault-sandbox-v1` 已证明 401、429、`Retry-After`、断连、超时、并发、幂等键和重试分类，但本地服务器不能证明商业供应商的实际响应格式、用量字段、限流头、请求追踪头和账单价格。因此真实 Provider 契约必须独立运行、显式付费授权，不能混入默认 CI 或普通全栈 smoke。

## 2. 执行器

`tools/provider_contract.py` 对一个 OpenAI-compatible 供应商执行且仅执行：

1. 一次最多 8 个输出 token 的 Chat Completion；
2. 一次固定短文本 Embedding；
3. 校验 Chat 内容结构、Embedding 非空有限向量、两类 `usage` 计数；
4. 记录已识别的限流头名称和 request-id 头名称；
5. 按显式供应商单价计算本次估算费用，并在调用前和调用后检查费用上限。

安全约束：

- 没有 `--allow-paid-provider-calls` 必定在联网前失败；
- API Key 只能来自调用者指定的环境变量，不接受命令行明文或配置文件；
- `base_url` 必须是 HTTPS，且 DNS 结果全部为公网地址；
- 单价不能全为 0，单次费用上限必须为正数；
- 不自动重试，避免认证、网络不确定或限流时扩大费用；
- 报告不保存 API Key、提示词、生成正文、向量或原始响应头值；
- Chat 与 Embedding 均携带本次唯一的 `Idempotency-Key`，但不会用同一键主动重复请求。

## 3. 显式执行示例

以下价格仅为占位示例，运行前必须改为供应商当日官方价格；不要把真实 Key 写入脚本或命令历史。

```powershell
$env:APF_PROVIDER_CONTRACT_KEY = '<在当前终端临时注入>'
python .\tools\provider_contract.py `
  --provider '<provider>' `
  --base-url 'https://provider.example/v1' `
  --chat-model '<chat-model>' `
  --embedding-model '<embedding-model>' `
  --api-key-env APF_PROVIDER_CONTRACT_KEY `
  --input-per-million-usd 1 `
  --output-per-million-usd 2 `
  --embedding-per-million-usd 0.1 `
  --max-cost-usd 0.01 `
  --require-rate-limit-headers `
  --allow-paid-provider-calls `
  --report .\artifacts\provider-contract\provider.json
Remove-Item Env:APF_PROVIDER_CONTRACT_KEY
```

供应商不承诺限流头时可移除 `--require-rate-limit-headers`，但报告必须如实记录为空，不能把“未观察到”写成“支持”。

## 4. 当前验证与关闭条件

执行器单元测试覆盖：默认拒绝付费调用、调用前费用上限、成功响应与报告脱敏、缺失 usage 的契约失败。供应商缺口只有在以下证据全部存在后才能关闭：

Chat API Pool 的熔断状态机也已在本阶段修正：OPEN 窗口内的端点不再被 `choose_endpoint` 作为兜底回选；窗口到期进入 HALF_OPEN，只有一个请求可以取得探针槽；成功清零连续失败并关闭熔断，失败重新打开完整窗口，取消则释放探针声明。半开探针声明被明确视为进程内瞬时状态，状态恢复时强制清零，避免探针期间异常重启后端点永久不可用。5 项状态机测试及完整 Chat 39/39（真实隔离 PostgreSQL，0 跳过）通过，测试产生的临时 schema 清理后残留为 0。

Console 的 Chat API 健康表已按后端真实契约展示 CLOSED（正常）、OPEN（已熔断）和 HALF_OPEN（半开探测），并显示当前/最大并发、延迟 EMA、连续失败与累计成功/失败；Console 55/55 及生产构建通过。

- 获得专用测试账号和明确费用授权；
- Chat 与 Embedding 契约报告通过；
- 报告中的模型、token 用量与供应商控制台账单在约定误差内；
- 实际限流头/请求 ID 契约有供应商级记录；
- 将确认过的价格版本和生效时间录入 API Pool；
- 在供应商测试账号环境验证熔断打开、半开恢复和告警 Webhook 的失败重试，且清理测试告警。

在这些外部证据出现前，本文件状态必须保持“供应商账号实测待授权”，项目不能宣称真实 Provider 生产完备。
