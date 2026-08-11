# 工程审计工具

## 真实 Provider 契约执行器测试

验证真实商业 Provider 执行器的默认拒绝、费用上限、响应结构和报告脱敏。该测试不联网、不会读取真实 API Key：

```powershell
python -m unittest tools.audit.test_provider_contract -v
```

真实账号调用必须改用 `tools/provider_contract.py` 并显式批准付费调用；见 `docs/design/real-provider-contract-gate-v1.zh-CN.md`。

## 接口目录一致性

验证 `apps/debug-web/src/endpoints.js` 中的调试接口目录是否仍能映射到三个后端平面的真实路由：

```powershell
node .\tools\audit\verify-endpoint-catalog.mjs
```

输出 JSON（用于 CI 或进一步分析）：

```powershell
node .\tools\audit\verify-endpoint-catalog.mjs --json
```

以下情况返回非零退出码：

- endpoint id 重复；
- 目录项指向不存在或方法不一致的后端路由。

未收入目录的后端路由只记录为覆盖率缺口，不阻断命令；内部接口和不适合手工调用的接口可以有意不进入调试目录。
