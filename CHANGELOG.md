# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)，变更按 Keep a Changelog 风格维护。

## [Unreleased]

### Planned

- Pilot 部署加固、容量基线与灾备演练
- API 配额、计量和更完整的租户治理

## [0.1.0-beta.1] - 2026-08-11

### Added

- 工作空间级知识库、智能体、Workflow、Skill、MCP 和 API 发布管理
- Apple 风格深浅色产品界面及窄屏渐进适配
- Agent Canva 可视化编排、DSL 校验、调试和版本发布
- RAG 2.0 混合召回、RRF、真实 Reranker、Parent/Child Context
- RAG 索引版本、影子构建、Golden Set 评测、激活和回滚工作台
- API Key 自动生成、撤销、限流、调用日志及复制反馈
- PostgreSQL 迁移、MinIO 对象治理、审计、备份恢复和可观测性工具

### Changed

- 知识库管理复用统一控制台会话和权限边界，不再独立登录
- 登录、注册和邀请流程产品化，隐藏内部请求调试信息
- 前端通知、卡片、列表、弹窗和下拉组件统一主题与交互规范
- Agent/Workflow 检索配置真实透传至执行链路

### Fixed

- 登录会话短时失效、知识库代理 502、系统总览滚动和导航间距问题
- 暗色模式下卡片、弹窗与原生下拉浮层浅色残留
- Agent、Workflow 等工作空间资产缺少删除闭环
- RAG 生命周期长请求误报 502、NDCG 重复文档计分和安全回滚问题

### Security

- 生产密钥模板与校验、JWT 轮换、Secret Vault、API Key 哈希与吊销
- 工作空间所有权隔离、受控 HTTP 工具、MCP 接入边界和审计记录
- 从发布索引移除聊天运行数据和编译产物

[Unreleased]: https://github.com/twy2020/agent_powflow/compare/v0.1.0-beta.1...HEAD
[0.1.0-beta.1]: https://github.com/twy2020/agent_powflow/releases/tag/v0.1.0-beta.1
