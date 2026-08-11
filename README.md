# Agent Powflow

面向团队和企业的 AI 应用构建平台，提供知识库、RAG、智能体、Workflow、Skill、MCP 和 API 发布的一体化控制台。

> 当前版本：`v0.1.0-beta.1`（邀请制 Pilot）。适合内部验证和小范围受控部署，尚不建议直接承载公开商用、高敏感或关键业务数据。

## 核心能力

- 账户、工作空间、成员邀请与资源隔离
- 知识库文档管理、结构化切片和异步入库
- RAG 2.0：Dense + BM25、RRF、Reranker、Parent/Child Context
- 索引影子构建、Golden Set 评测、激活、回滚和失败清理
- 智能体配置、对话、版本发布及知识库绑定
- Agent Canva Workflow 编排、校验、运行和版本发布
- Skill 管理、MCP 接入与工具调用
- API Deployment、API Key、限流、调用日志和撤销
- 深浅色主题、窄屏渐进布局和统一产品化反馈

## 系统组成

| 组件 | 默认地址 | 职责 |
| --- | --- | --- |
| Web App | `http://localhost:5173` | 用户控制台与工作空间资产 |
| Web Admin | `http://localhost:5174` | 平台管理控制台 |
| Debug Web | `http://localhost:5177` | 受控调试与运行诊断 |
| KB Workbench | `http://localhost:5180` | 集成式知识库文档与 RAG 工作台 |
| Control Plane | `http://localhost:8099` | 身份、权限、资产和发布控制面 |
| Execution Plane | `http://localhost:8091` | 文档处理、检索和 Workflow 执行 |
| Chat Plane | `http://localhost:8092` | 会话、记忆和智能体对话 |

数据与运行基础设施包括 PostgreSQL、Qdrant、RabbitMQ、MinIO 和可选的 MongoDB/可观测性组件。

## 快速启动

前置要求：Docker Desktop（Compose v2）和 PowerShell 7；本地独立构建前端还需要 Node.js 20，Control Plane 本地构建需要 Java 21。

1. 创建本地配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 仅用于本地开发时，检查 `.env` 中的开发凭据和端口；生产部署必须从 `.env.production.example` 生成独立强密钥：

   ```powershell
   .\infra\scripts\new-production-env.ps1
   .\infra\scripts\validate-production-secrets.ps1
   ```

3. 构建并启动完整开发栈：

   ```powershell
   .\infra\scripts\start-dev-all.ps1 -BuildStack
   ```

4. 查看服务状态或停止：

   ```powershell
   docker compose --env-file config\service-ports.env -f infra\compose\docker-compose.stack.yml ps
   .\infra\scripts\stop-dev-all.ps1
   ```

运行数据、日志、本地证书、生成配置和 `.env` 均被 Git 忽略。不要提交真实 API Key、JWT、数据库密码或聊天数据。

## 质量验证

GitHub Actions 在默认分支和 Pull Request 上执行四个前端生产构建、Java 21 Maven 测试、Python/数据库集成测试、端点契约检查、密钥卫生检查和 Compose 配置校验。

本地常用检查：

```powershell
python tools\audit\verify_secret_hygiene.py --include-untracked
node tools\audit\verify-endpoint-catalog.mjs
docker compose --env-file config\service-ports.env -f infra\compose\docker-compose.stack.yml config --quiet
```

## 仓库结构

```text
apps/                    Vue 前端应用
services/control-plane/  Spring Boot 控制面
services/execution-plane FastAPI/Celery 执行面
services/chat-plane/     会话与智能体对话服务
infra/                   Compose、可观测性和运维脚本
config/                  安全默认配置与端口定义
docs/                    架构决策、设计和运维手册
tools/                   契约、审计和冒烟验证工具
```

## 发布与安全

- 版本遵循 Semantic Versioning；Beta 阶段允许在正式版前调整不稳定接口。
- 变更记录见 [CHANGELOG.md](CHANGELOG.md)，本版本说明见 [docs/releases/v0.1.0-beta.1.zh-CN.md](docs/releases/v0.1.0-beta.1.zh-CN.md)。
- 安全报告与生产部署边界见 [SECURITY.md](SECURITY.md)。
- 开发协作规范见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [AGENTS.md](AGENTS.md)。

本仓库当前未声明开源许可证；除非仓库所有者另行授权，不应推定获得复制、分发或商用许可。
