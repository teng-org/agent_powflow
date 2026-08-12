# Changelog

## v0.2.0-beta.2 - 2026-08-12

- 前端生产容器改为 Vite 构建产物与 Nginx 静态服务，不再使用开发服务器。
- 控制台、管理后台、知识库工作台和发布 API 仅绑定宿主机回环地址，由主机反向代理统一终止 TLS。
- 新增生产 HTTPS 域名变量，修复独立子域名下的知识库跳转、跨窗口授权与公开 API 地址生成。
- 修复 Rocky Linux 部署校验对可选空配置的误判，以及内部 MinIO HTTP/TLS 配置不一致。
- 增强服务检查：验证真实模型严格模式、回环端口绑定、生产前端和后端健康状态。
- 新增 Nginx HTTPS/SSE 配置模板和 systemd 服务模板。

本项目遵循 [Semantic Versioning](https://semver.org/)，变更按 Keep a Changelog 风格维护。

## [Unreleased]

### Planned

- Pilot 部署加固、容量基线与灾备演练
- API 配额、计量和更完整的租户治理

## [0.2.0-beta.1] - 2026-08-12

### Added

- OpenAI-compatible Agent API，支持同步响应、SSE、会话连续性和安全记忆诊断
- Agent 短期/长期/无记忆策略、禁止规则、动态日期时间变量及版本化配置快照
- Skill、MCP 与 JavaScript 工具统一目录、权限范围、Schema 校验、受控执行和调用轨迹
- JavaScript 工具工作台，支持创建、测试、发布、版本锁定及 Agent/Workflow 分配
- Agent 多轮工具规划循环，可依据工具结果继续调用或生成最终回复

### Changed

- Chat 与 Workflow 默认改为 `api_strict`，生产预演不再静默使用 deterministic mock
- Agent 调试台直接使用当前草稿参数、真实模型端点和连续调试会话
- 知识库工作台压缩顶部空间并补齐 RAG 索引状态、冻结效果及深色主题
- Control Plane 文本字段迁移为 PostgreSQL 原生 text，降低 Hibernate LOB 兼容风险

### Fixed

- 仅配置 JavaScript 工具且关闭知识库时，Agent 工具循环被提前跳过
- 上传文档和登录后的服务错误提示、深色弹窗/下拉框及知识库工作台残留浅色背景
- RAG 索引生命周期边界、文档元数据清理与调试信息展示问题

### Security

- MCP 目的地址校验、禁止私网/本机解析、禁止重定向和租户级密钥解析
- 写入型 MCP/JavaScript 工具默认禁止，并增加超时、结果预算和失败策略
- 发布 API 外部用户命名空间隔离，避免 API Key 轮换或多调用方之间的记忆串台

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

[Unreleased]: https://github.com/teng-org/agent_powflow/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/teng-org/agent_powflow/compare/v0.1.0-beta.1...v0.2.0-beta.1
[0.1.0-beta.1]: https://github.com/teng-org/agent_powflow/releases/tag/v0.1.0-beta.1
