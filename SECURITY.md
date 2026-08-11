# Security Policy

## Supported versions

当前仅维护最新的 `0.1.x` Pilot 版本。Beta 版本不承诺长期兼容或长期安全支持。

## Reporting a vulnerability

请使用 GitHub 仓库的私密漏洞报告功能联系维护者。不要在公开 Issue、日志、截图或示例配置中提交凭据、个人数据或可直接利用的攻击细节。

报告建议包含：受影响版本、入口和权限前提、最小复现步骤、影响范围及可行的缓解建议。维护者确认前请勿公开披露。

## Deployment boundary

- Pilot 部署应使用邀请制、最小权限和受控网络入口。
- 生产环境必须使用 `.env.production.example` 生成独立强密钥，并通过 `infra/scripts/validate-production-secrets.ps1`。
- 禁止复用开发默认密码；JWT、数据库、对象存储、Webhook 和模型供应商密钥必须分别生成和轮换。
- `debug-web`、数据库管理端口、RabbitMQ/MinIO/Qdrant 管理端不应直接暴露到公网。
- 部署前应启用 TLS、备份、审计、告警和恢复演练，并确认数据保留与隐私合规要求。

本版本适合小范围验证，不应在缺少额外安全评审的情况下承载高敏感或关键业务数据。
