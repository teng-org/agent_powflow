# Contributing

## Development flow

1. 从最新 `main` 创建短生命周期分支。
2. 保持变更范围单一，数据库变化必须使用不可变迁移。
3. 不提交 `.env`、运行数据、日志、证书、构建产物或第三方依赖目录。
4. 提交前运行与变更风险匹配的测试、密钥卫生检查和 `git diff --check`。
5. Pull Request 说明动机、实现、迁移/回滚方式、测试证据和用户可见影响。

提交信息推荐 Conventional Commits，例如 `feat: add index quality gate`、`fix: preserve active RAG revision`。

## Required checks

- 四个前端应用必须能够执行 `npm ci` 与 `npm run build`。
- Control Plane 使用 Java 21 执行 `mvn test`。
- Python 服务执行语法编译和相关 `unittest`/集成测试。
- 接口变更需同步端点目录、调用方、文档和契约测试。
- UI 变更必须验证深浅色与窄屏；下拉浮层还需遵守 `AGENTS.md` 的强制规范。

## Compatibility and security

- 对外 API 优先做向后兼容的增量变更；破坏性变更必须记录版本与迁移方案。
- 所有工作空间资源读写必须校验当前账户的所有权或成员权限。
- 错误响应不得泄露堆栈、内部地址、SQL、令牌或供应商请求细节。
- 新依赖需说明用途，并检查许可证、维护状态和供应链风险。
